"""Initial password regressions: mocked SSH, no remote systems or database.

The transaction tests run only its shell control flow against temporary files
and stub system commands; sudo/useradd/chpasswd are never executed for real.
"""
import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import account_config as config
import account_helpers as helpers


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.delenv("ACCOUNT_INITIAL_PASSWORD", raising=False)
    monkeypatch.setattr(helpers.asyncssh, "connect", AsyncMock(side_effect=AssertionError("Real SSH forbidden")))
    config.load_account_config()


def test_default_and_custom_startup_snapshots(monkeypatch):
    assert config.get_initial_password() == "Qwe123!@#"
    password = " !@#$'\"`$(not-a-command)\\ 密码 "
    monkeypatch.setenv("ACCOUNT_INITIAL_PASSWORD", password)
    assert config.get_initial_password() == "Qwe123!@#"
    assert config.load_account_config() is None
    assert config.get_initial_password() == password
    assert password not in repr(config._config)
    monkeypatch.setenv("ACCOUNT_INITIAL_PASSWORD", "later-value")
    assert config.get_initial_password() == password
    config.load_account_config()
    assert config.get_initial_password() == "later-value"
    monkeypatch.delenv("ACCOUNT_INITIAL_PASSWORD")
    config.load_account_config()
    assert config.get_initial_password() == "Qwe123!@#"


@pytest.mark.parametrize("password", ["", "secret\nvalue", "secret\rvalue", "secret:value"])
def test_invalid_config_is_rejected_without_secret_or_snapshot_change(monkeypatch, password):
    monkeypatch.setenv("ACCOUNT_INITIAL_PASSWORD", password)
    with pytest.raises(ValueError) as error:
        config.load_account_config()
    assert "secret" not in str(error.value)
    assert config.get_initial_password() == "Qwe123!@#"


def test_nul_is_rejected(monkeypatch):
    # OS environment APIs disallow NUL themselves; also validate injected config.
    monkeypatch.setattr(config.os, "environ", {"ACCOUNT_INITIAL_PASSWORD": "secret\0value"})
    with pytest.raises(ValueError):
        config.load_account_config()


def test_config_must_be_explicitly_loaded(monkeypatch):
    monkeypatch.setattr(config, "_config", None)
    conn = SimpleNamespace(run=AsyncMock())
    with pytest.raises(RuntimeError, match="startup"):
        run(helpers.sshAccountCreate(conn, "alice"))
    conn.run.assert_not_awaited()


@pytest.mark.parametrize("operation", [helpers.sshAccountCreate, helpers.sshAccountInitializePassword])
@pytest.mark.parametrize("account", ["root;id", "-root", "a\n", "../alice", "é"])
def test_password_boundaries_validate_account_before_ssh(operation, account):
    conn = SimpleNamespace(run=AsyncMock())
    with pytest.raises(ValueError):
        run(operation(conn, account))
    conn.run.assert_not_awaited()


# PATH stubs execute only within the test shell; no real account tools are used.
_STUB = '''#!/usr/bin/env python3
import os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_REMOTE"])
cmd, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
if cmd == "stat":
    print("700" if args[1] == "%a" else "0")
elif cmd == "install":
    pathlib.Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif cmd == "flock":
    pass
elif cmd == "getent":
    entry = root / (args[-1] + ".entry")
    if not entry.exists(): sys.exit(2)
    print(entry.read_text())
elif cmd == "useradd":
    entry = root / (args[-1] + ".entry")
    if (root / "useradd-fails").exists(): sys.exit(9)
    if entry.exists(): sys.exit(9)
    token = args[args.index("-c") + 1]
    entry.write_text(f"{args[-1]}:x:1234:1234:{token}:/home/{args[-1]}:/bin/bash")
    if (root / "crash-after-useradd").exists(): sys.exit(1)
elif cmd == "chpasswd":
    # Deliberately echo the password on failure to exercise redaction.
    payload = sys.stdin.read()
    if (root / "password-fails").exists():
        print(payload, file=sys.stderr)
        sys.exit(1)
    with (root / "password-input").open("a") as output:
        output.write(payload)
else:
    raise AssertionError(cmd)
'''


class FakeSSH:
    def __init__(self, root):
        self.root = root
        self.calls = []
        binary = root / "bin"
        binary.mkdir()
        for command in ("stat", "install", "flock", "getent", "useradd", "chpasswd"):
            path = binary / command
            path.write_text(_STUB.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            path.chmod(0o700)
        self.env = {**os.environ, "FAKE_REMOTE": str(root), "PATH": str(binary) + os.pathsep + os.environ["PATH"]}

    @property
    def marker(self):
        return self.root / "n2sys" / "account-passwords" / "alice.pending"

    def existing_account(self, comment="Unrelated owner"):
        (self.root / "alice.entry").write_text(f"alice:x:1234:1234:{comment}:/home/alice:/bin/bash")

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        words = shlex.split(command)
        assert words[:8] == ["sudo", "-n", "timeout", "-k", "2s", "12s", "sh", "-c"]
        assert len(words) == 9
        assert kwargs["timeout"] == 15
        # Drop sudo/timeout, redirect all remote state, and stub account tools.
        script = words[-1].replace("/var/lib/n2sys", str(self.root / "n2sys"))
        result = subprocess.run(["sh", "-c", script], input=kwargs["input"],
                                capture_output=True, text=True, env=self.env, timeout=30)
        return SimpleNamespace(exit_status=result.returncode, stdout=result.stdout, stderr=result.stderr)


@pytest.fixture
def ssh(tmp_path):
    return FakeSSH(tmp_path)


def test_custom_special_characters_are_stdin_only_and_not_reloaded(ssh, monkeypatch):
    password = " !@#$'\"`$(touch /tmp/forbidden)\\密码 "
    monkeypatch.setenv("ACCOUNT_INITIAL_PASSWORD", password)
    config.load_account_config()
    monkeypatch.setenv("ACCOUNT_INITIAL_PASSWORD", "ignored-until-next-start")
    assert run(helpers.sshAccountCreate(ssh, "alice")) == (True, None)
    assert (ssh.root / "password-input").read_text() == f"alice:{password}\n"
    assert not ssh.marker.exists()
    command, kwargs = ssh.calls[0]
    assert kwargs["input"] == f"alice:{password}\n"
    assert password not in command and "ignored-until-next-start" not in command
    assert "userdel" not in command and "-p " not in command
    assert "chpasswd" in command
    assert password not in (ssh.root / "alice.entry").read_text()


def test_failed_password_leaves_marker_and_existing_account_recovery_only(ssh):
    (ssh.root / "password-fails").touch()
    with pytest.raises(RuntimeError) as error:
        run(helpers.sshAccountCreate(ssh, "alice"))
    assert "Qwe123!@#" not in str(error.value)
    assert ssh.marker.exists()
    original_entry = (ssh.root / "alice.entry").read_text()
    assert ssh.marker.read_text().strip() in original_entry
    assert not (ssh.root / "password-input").exists()
    (ssh.root / "password-fails").unlink()
    assert run(helpers.sshAccountInitializePassword(ssh, "alice")) == (True, None)
    assert not ssh.marker.exists()
    assert (ssh.root / "alice.entry").read_text() == original_entry
    assert (ssh.root / "password-input").read_text() == "alice:Qwe123!@#\n"
    # A later config update or sync must not reset a completed account.
    assert run(helpers.sshAccountInitializePassword(ssh, "alice")) == (True, None)
    assert (ssh.root / "password-input").read_text() == "alice:Qwe123!@#\n"


def test_crash_immediately_after_useradd_is_recoverable(ssh):
    (ssh.root / "crash-after-useradd").touch()
    with pytest.raises(RuntimeError):
        run(helpers.sshAccountCreate(ssh, "alice"))
    assert ssh.marker.exists() and (ssh.root / "alice.entry").exists()
    (ssh.root / "crash-after-useradd").unlink()
    assert run(helpers.sshAccountCreate(ssh, "alice")) == (True, None)
    assert not ssh.marker.exists()


def test_failed_useradd_does_not_initialize_unrelated_account_on_retry(ssh):
    (ssh.root / "useradd-fails").touch()
    with pytest.raises(RuntimeError):
        run(helpers.sshAccountCreate(ssh, "alice"))
    assert ssh.marker.exists() and not (ssh.root / "alice.entry").exists()
    ssh.existing_account()
    (ssh.root / "useradd-fails").unlink()
    with pytest.raises(RuntimeError):
        run(helpers.sshAccountInitializePassword(ssh, "alice"))
    assert ssh.marker.exists()
    assert not (ssh.root / "password-input").exists()


@pytest.mark.parametrize("operation", [helpers.sshAccountCreate, helpers.sshAccountInitializePassword])
def test_unrelated_existing_password_is_unchanged(ssh, operation):
    ssh.existing_account()
    assert run(operation(ssh, "alice")) == (True, None)
    assert not ssh.marker.exists()
    assert not (ssh.root / "password-input").exists()


def test_recovery_does_not_create_missing_account(ssh):
    with pytest.raises(RuntimeError):
        run(helpers.sshAccountInitializePassword(ssh, "alice"))
    assert not (ssh.root / "alice.entry").exists()


@pytest.mark.parametrize("error", [TimeoutError("Qwe123!@#"), ConnectionError("Qwe123!@#")])
def test_ssh_exceptions_do_not_expose_password(error):
    conn = SimpleNamespace(run=AsyncMock(side_effect=error))
    with pytest.raises(type(error) if isinstance(error, TimeoutError) else RuntimeError) as caught:
        run(helpers.sshAccountCreate(conn, "alice"))
    assert "Qwe123!@#" not in str(caught.value)
    assert caught.value.__suppress_context__
