"""Personal gateway keys: private material never leaves the target account.

Only this public-key operation is exposed. No root-owned key writes, downloads,
key rotation, tool installation, or local key generation are performed here.
"""
import shlex

import asyncssh

from account_helpers import validate_account_name

MAX_PUBLIC_KEY_BYTES = 4096
REMOTE_TIMEOUT = 25

_REASONS = {
    "tools": "网关缺少 timeout/flock/ssh-keygen/getent 等系统工具，请管理员安装并检查 PATH。",
    "home": "网关账号或主目录不安全/不可用，请检查非 root 账号、getent 主目录及目录所有权和符号链接。",
    "files": "网关 .ssh 或密钥文件存在符号链接、硬链接、所有权或类型风险，请用户/管理员检查；未覆盖原私钥。",
    "lock": "网关密钥目录正忙，稍后重试；请检查占用 flock 的进程。",
    "generate": "无法生成网关 Ed25519 密钥，请检查目标用户的目录权限、磁盘空间及 ssh-keygen。",
    "private": "已有私钥无法非交互读取，可能已加密或损坏；请用户检查，平台不会替换原私钥。",
    "pair": "网关公私钥缺失或不匹配，请用户检查 id_ed25519 与 .pub 文件；平台不会覆盖已有密钥。",
    "filesystem": "网关密钥文件操作失败，请检查目录权限、磁盘空间及并发文件修改。",
    "public": "网关返回的公钥不是有效的单行 Ed25519 公钥或超过长度限制，请检查 .pub 文件。",
    "timeout": "网关密钥操作超时，请检查服务器负载、文件系统和锁占用；稍后会重试。",
    "ssh": "网关密钥 SSH 命令失败，请检查网络、非交互 sudo 权限及目标用户。",
    "command": "网关密钥命令失败，请检查 sudo -n -H -u 的授权、系统工具和目标账号。",
}
_EXIT_REASONS = {40: "tools", 41: "home", 42: "files", 43: "lock", 44: "generate",
                 45: "private", 46: "pair", 47: "filesystem", 124: "timeout", 137: "timeout"}


class GatewayKeyError(RuntimeError):
    """Only allowlisted diagnostic text may reach the error center/logs."""

    def __init__(self, reason):
        self.safe_message = _REASONS.get(reason, _REASONS["command"])
        super().__init__(self.safe_message)


def validate_gateway_public_key(value):
    """Bound and validate public-only SSH output, discard untrusted comments."""
    if not isinstance(value, str) or len(value) > MAX_PUBLIC_KEY_BYTES:
        raise GatewayKeyError("public")
    line = value.strip()
    if any(char in line for char in ("\n", "\r", "\0")):
        raise GatewayKeyError("public")
    fields = line.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise GatewayKeyError("public")
    candidate = " ".join(fields[:2])
    try:
        if len(value.encode("utf-8")) > MAX_PUBLIC_KEY_BYTES:
            raise ValueError()
        # Reject junk accepted by permissive base64 decoders before parsing.
        if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
               for char in fields[1]):
            raise ValueError()
        key = asyncssh.import_public_key(candidate)
        canonical = key.export_public_key("openssh").decode("ascii").split()
        if canonical[0] != "ssh-ed25519":
            raise ValueError()
        if fields[1] != canonical[1]:
            raise ValueError()
        return " ".join(canonical[:2])
    except Exception:
        raise GatewayKeyError("public") from None


def append_gateway_public_key(existing, public_key):
    """Return (text, changed); preserve manual keys/comments/options verbatim.

    Identity is key type + base64, never the comment. Authorized-keys options
    (including quoted spaces) may precede a key; comments aren't scanned as keys.
    """
    public_key = validate_gateway_public_key(public_key)
    identity = public_key.split()
    existing = existing or ""
    for line in existing.splitlines():
        if line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if fields[:2] == identity:
            return existing, False
        try:
            fields = shlex.split(line, comments=False)
        except ValueError:
            continue  # Preserve malformed legacy/manual lines too.
        if fields[:2] == identity or fields[1:3] == identity:
            return existing, False
    return existing + ("\n" if existing and not existing.endswith("\n") else "") + public_key, True


# The caller invokes this ONLY under sudo -n -H -u ACCOUNT, never as root.
# Directory flock avoids opening a user-controlled lock-file symlink. Working
# relative to the pinned directory also avoids ancestor replacement after cd.
# ln -T publishes each file atomically without overwriting an existing name.
# If interrupted between publications, the next attempt derives the missing
# public key from the existing private key, never generates a replacement.
_KEY_TRANSACTION = r'''
set -eu
exec </dev/null 2>/dev/null
umask 077
for tool in flock ssh-keygen getent stat realpath mktemp ln chmod mkdir rm rmdir id wc; do
    command -v "$tool" >/dev/null || exit 40
done
account=ACCOUNT
uid=$(id -u) || exit 41
test "$uid" != 0 || exit 41
entry=$(getent passwd "$account") || exit 41
IFS=: read -r name password owner group gecos home login <<ENTRY
$entry
ENTRY
test "$name" = "$account" && test "$owner" = "$uid" || exit 41
case "$home" in /*) ;; *) exit 41 ;; esac
test "$home" != / && test -d "$home" && test ! -L "$home" || exit 41
test "$(realpath -e -- "$home")" = "$home" || exit 41
test "$(stat -c %u -- "$home")" = "$uid" || exit 41
cd -P -- "$home" || exit 41
test ! -L .ssh || exit 42
if test ! -e .ssh; then
    mkdir -m 700 -- .ssh || exit 47
fi
test -d .ssh && test "$(stat -c %u -- .ssh)" = "$uid" || exit 42
cd -P -- .ssh || exit 42
exec 9< .
flock -w 3 9 || exit 43
chmod 700 . || exit 47
check_file() {
    test ! -L "$1" || exit 42
    if test -e "$1"; then
        test -f "$1" || exit 42
        test "$(stat -c %u -- "$1")" = "$uid" || exit 42
        test "$(stat -c %h -- "$1")" = 1 || exit 42
    fi
}
check_file ./id_ed25519
check_file ./id_ed25519.pub
# An orphan public key must not be silently replaced by a different pair.
if test ! -e ./id_ed25519 && test -e ./id_ed25519.pub; then exit 46; fi
work=$(mktemp -d .n2sys-gateway-key.XXXXXXXX) || exit 47
cleanup() {
    rm -f -- "$work/key" "$work/key.pub" "$work/derived.pub"
    rmdir -- "$work"
}
trap cleanup 0
trap 'exit 47' HUP INT TERM
if test ! -e ./id_ed25519; then
    ssh-keygen -q -t ed25519 -N '' -C n2sys-gateway -f "$work/key" >/dev/null || exit 44
    chmod 600 "$work/key" "$work/key.pub" || exit 47
    ln -T -- "$work/key" ./id_ed25519 || exit 47
    rm -f -- "$work/key" || exit 47
fi
check_file ./id_ed25519
chmod 600 ./id_ed25519 || exit 47
# -P '' and closed stdin guarantee encrypted keys fail, never prompt/askpass.
derived=$(SSH_ASKPASS_REQUIRE=never SSH_ASKPASS=/bin/false DISPLAY= ssh-keygen -y -P '' -f ./id_ed25519) || exit 45
read -r derived_type derived_blob derived_comment <<PUBLIC
$derived
PUBLIC
test "$derived_type" = ssh-ed25519 || exit 45
case "$derived_blob" in ''|*[!A-Za-z0-9+/=]*) exit 45 ;; esac
test "${#derived_blob}" -le 1024 || exit 45
printf '%s %s\n' "$derived_type" "$derived_blob" > "$work/derived.pub" || exit 47
chmod 600 "$work/derived.pub" || exit 47
if test ! -e ./id_ed25519.pub; then
    ln -T -- "$work/derived.pub" ./id_ed25519.pub || exit 47
    rm -f -- "$work/derived.pub" || exit 47
else
    check_file ./id_ed25519.pub
    test "$(wc -c < ./id_ed25519.pub)" -le 4096 || exit 46
    read -r actual_type actual_blob actual_comment < ./id_ed25519.pub || exit 46
    test "$actual_type" = "$derived_type" && test "$actual_blob" = "$derived_blob" || exit 46
fi
check_file ./id_ed25519.pub
chmod 600 ./id_ed25519.pub || exit 47
# Never stream a user's file: even a concurrent .pub symlink swap cannot
# expose private contents. Return only the bounded fields derived by keygen -y.
printf '%s %s\n' "$derived_type" "$derived_blob"
'''


def gateway_key_command(account):
    """Build a quoted, noninteractive, remotely bounded target-user command."""
    name = shlex.quote(validate_account_name(account))
    script = _KEY_TRANSACTION.replace("ACCOUNT", name)
    wrapper = ("PATH=/usr/bin:/bin; export PATH; "
               "command -v timeout >/dev/null 2>&1 || exit 40; "
               "exec timeout -k 2s 20s sh -c " + shlex.quote(script))
    return f"sudo -n -H -u {name} sh -c {shlex.quote(wrapper)}"


async def sshGatewayPublicKey(conn, account):
    """Fetch/create id_ed25519 on the gateway; return only validated public key."""
    command = gateway_key_command(account)
    try:
        result = await conn.run(command, timeout=REMOTE_TIMEOUT)
    except TimeoutError:
        raise GatewayKeyError("timeout") from None
    except Exception:
        raise GatewayKeyError("ssh") from None
    if result.exit_status != 0:
        raise GatewayKeyError(_EXIT_REASONS.get(result.exit_status, "command"))
    return validate_gateway_public_key(result.stdout)
