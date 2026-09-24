"""SSH account operations. Treat legacy database values as untrusted input."""
import re
import shlex
import uuid

import asyncssh
from account_config import get_initial_password
from sync_errors import SyncCommandError, command_failure

_ACCOUNT_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z", re.ASCII)


def validate_account_name(account: str) -> str:
    if not isinstance(account, str) or not _ACCOUNT_NAME.fullmatch(account):
        # Do not include the input: it may contain credentials or shell code.
        raise ValueError("Invalid Linux account name")
    return account


def _account(account):
    return shlex.quote(validate_account_name(account))


async def _run(conn, command, **kwargs):
    result = await conn.run(command, timeout=3, **kwargs)
    if result.exit_status != 0:
        # SSH stderr can contain commands, keys, and other credentials.
        raise command_failure(command, result)
    return result


async def sshAccountIsExists(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    result = await conn.run(f"getent passwd {_account(account)}", timeout=3)
    if result.exit_status == 2:  # getent: requested key not found
        return False
    if result.exit_status != 0:
        raise RuntimeError("Could not check remote account")
    return True


# One root transaction owns the marker, account creation and password update.
# The marker is written BEFORE useradd, and its random, non-secret provenance
# tag is also the new account's GECOS. Thus even a crash immediately after
# useradd is recoverable, while a colliding/pre-existing account is never reset.
# The root-owned directory and flock serialize retries across backend workers.
_PASSWORD_TRANSACTION = r'''
set -eu
account=ACCOUNT
create=CREATE
token=TOKEN
state=/var/lib/n2sys/account-passwords
for directory in /var/lib/n2sys "$state"; do
    test ! -L "$directory"
    if test -e "$directory"; then
        test -d "$directory"
        test "$(stat -c %u "$directory")" = 0
        test "$(stat -c %a "$directory")" = 700
    fi
    install -d -m 700 -o root -g root "$directory"
done
exec 9>"$state/.lock"
flock -w 2 9
marker="$state/$account.pending"
if entry=$(getent passwd "$account"); then
    exists=1
else
    status=$?
    test "$status" = 2
    exists=0
fi
if test -e "$marker"; then
    test ! -L "$marker"
    test -f "$marker"
    token=$(cat -- "$marker")
    test -n "$token"
    if test "$exists" = 1; then
        comment=$(printf '%s\n' "$entry" | cut -d: -f5)
        test "$comment" = "$token"
    fi
elif test "$exists" = 1; then
    exit 0
else
    test "$create" = 1
    umask 077
    printf '%s\n' "$token" > "$marker"
fi
if test "$exists" = 0; then
    test "$create" = 1
    useradd -m -d "/home/$account" -s /bin/bash -c "$token" -- "$account"
fi
# This shell is already root via non-interactive sudo. No password appears in
# shell source, argv, marker files, or account metadata: chpasswd reads stdin.
chpasswd
rm -- "$marker"
'''


async def _initialize_password(conn, account: str, *, create: bool):
    name = _account(account)
    password = get_initial_password()
    script = (_PASSWORD_TRANSACTION.replace("ACCOUNT", name)
              .replace("CREATE", "1" if create else "0")
              .replace("TOKEN", shlex.quote("n2sys-" + uuid.uuid4().hex)))
    # Bound the transaction remotely as well: a local SSH timeout alone does
    # not guarantee that a hung useradd/chpasswd or its lock is released.
    command = f"sudo -n timeout -k 2s 12s sh -c {shlex.quote(script)}"
    try:
        result = await conn.run(command, input=f"{account}:{password}\n", timeout=15)
    except TimeoutError:
        raise TimeoutError("Remote account password initialization timed out") from None
    except Exception:
        # SSH exceptions may embed stdin/output; never propagate their text.
        raise RuntimeError("Remote account password initialization failed") from None
    if result.exit_status != 0:
        raise SyncCommandError("初始化 Linux 账号密码", result.exit_status)
    return True, None


async def sshAccountCreate(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """Create with the startup password, or resume only our pending creation."""
    return await _initialize_password(conn, account, create=True)


async def sshAccountInitializePassword(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """Recover a pending creation; existing accounts without a marker are untouched.

    Call on the existing-account path of login-enabled sync, before installing
    keys. Never call for revocation: revoked accounts must not gain a password.
    A marker/account provenance mismatch fails closed for operator inspection.
    """
    return await _initialize_password(conn, account, create=False)


async def sshAccountGetAuthorizedKeys(conn: asyncssh.SSHClientConnection, account: str) -> str:
    _account(account)
    path = shlex.quote(f"/home/{account}/.ssh/authorized_keys")
    backup = shlex.quote(f"/home/{account}/.ssh/authorized_keys.n2sysbackup")
    # Missing files are normal, but permission/read errors must not silently
    # discard previously installed keys.
    result = await _run(conn, f"sudo sh -c {shlex.quote(f'if test -e {path}; then cat -- {path}; elif test -e {backup}; then cat -- {backup}; fi')}")
    return result.stdout.strip()


async def sshAccountIsEnabled(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    name = _account(account)
    result = await conn.run(f"sudo test -f /home/{name}/.ssh/authorized_keys", timeout=3)
    if result.exit_status == 1:
        return False
    if result.exit_status != 0:
        raise RuntimeError("Could not check authorized keys")
    entry = await _run(conn, f"getent passwd {name}")
    return entry.stdout.strip().split(":")[-1] not in ("/bin/false", "/usr/sbin/nologin", "/sbin/nologin")


async def sshAccountEnable(conn: asyncssh.SSHClientConnection, account: str, authorized_keys: str) -> tuple[bool, str]:
    name = _account(account)
    directory = shlex.quote(f"/home/{account}/.ssh")
    path = shlex.quote(f"/home/{account}/.ssh/authorized_keys")
    await _run(conn, f"sudo install -d -m 700 -o {name} {directory}")
    # Key comments/options are arbitrary text: never interpolate into a shell.
    await _run(conn, f"sudo tee {path} >/dev/null", input=authorized_keys.rstrip("\n") + "\n")
    await _run(conn, f"sudo chown {name} {path}")
    await _run(conn, f"sudo chmod 600 {path}")
    entry = await _run(conn, f"getent passwd {name}")
    if entry.stdout.strip().split(":")[-1] in ("/bin/false", "/usr/sbin/nologin", "/sbin/nologin"):
        # Undo our account expiry and restore key-only access. Do not unlock an
        # old password (older versions installed a publicly known password).
        await _run(conn, f"sudo usermod -e '' -p '*' -s /bin/bash -- {name}")
    return True, None


async def sshAccountDisable(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    name = _account(account)
    # Block password and public-key login even with AuthorizedKeysCommand,
    # alternate authorized_keys paths, or missing ~/.ssh/authorized_keys.
    await _run(conn, f"sudo usermod -L -e 1 -s /usr/sbin/nologin -- {name}")
    await sshAccountUnsudo(conn, account)
    path = shlex.quote(f"/home/{account}/.ssh/authorized_keys")
    backup = shlex.quote(f"/home/{account}/.ssh/authorized_keys.n2sysbackup")
    await _run(conn, f"sudo sh -c {shlex.quote(f'if test -e {path}; then mv -- {path} {backup}; fi')}")
    return True, None


async def sshAccountSudo(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    await _run(conn, f"sudo usermod -aG sudo -- {_account(account)}")
    return True, None


async def sshAccountIsSudo(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    _account(account)
    # No pipeline: otherwise cut can mask getent/permission failures.
    result = await conn.run("getent group sudo", timeout=3)
    if result.exit_status == 2:
        return False
    if result.exit_status != 0:
        raise RuntimeError("Could not check sudo group")
    return account in result.stdout.strip().split(":")[-1].split(",")


async def sshAccountUnsudo(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    name = _account(account)
    if await sshAccountIsSudo(conn, account):
        await _run(conn, f"sudo gpasswd -d {name} sudo")
    return True, None
