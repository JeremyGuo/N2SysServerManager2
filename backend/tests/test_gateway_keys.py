"""Gateway regressions: mocked SSH and syntax-only shell checks, no real keys.

The fixed RFC 8032 public test vector has no locally generated/stored private
material. The emitted remote script is NEVER executed (sh -n only).
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import datetime
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if "app.database" not in sys.modules:
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
        bootstrap_engine = create_engine("sqlite://", poolclass=StaticPool)
        with patch("sqlalchemy.create_engine", return_value=bootstrap_engine):
            from app import database
else:
    from app import database

import account_sync as sync
import gateway_keys as keys
from app.database import Base, Account, AccountStatus, Server, User, UserStatus


def _ssh_string(value):
    return struct.pack(">I", len(value)) + value


PUBLIC = "ssh-ed25519 " + base64.b64encode(
    _ssh_string(b"ssh-ed25519") + _ssh_string(bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"))).decode()


def run(awaitable):
    return asyncio.run(awaitable)


def response(stdout=PUBLIC, code=0, stderr=""):
    return SimpleNamespace(stdout=stdout, exit_status=code, stderr=stderr)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sync.asyncssh, "connect", AsyncMock(side_effect=AssertionError("Real SSH forbidden")))
    monkeypatch.setattr(sync, "semaphore", asyncio.Semaphore(20))
    monkeypatch.delenv("GATEWAY_KEY_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("SYNC_ENABLED", raising=False)
    for state in (sync.syncing_accounts, sync._account_activity_checks,
                  sync.last_server_collect_date, sync.last_server_collecting,
                  sync._refresh_requested, sync._sync_errors, sync._sync_tasks,
                  sync._worker_tasks, sync._gateway_key_next_check):
        state.clear()
    sync.start_watcher = False
    sync._watcher_task = None
    yield
    assert not sync._sync_tasks
    assert not sync._worker_tasks
    sync._gateway_key_next_check.clear()


@pytest.fixture
def db_env(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    sessions = []

    def tracked_session():
        session = factory()
        session.close = Mock(wraps=session.close)
        sessions.append(session)
        return session

    monkeypatch.setattr(sync, "SessionLocal", tracked_session)
    with factory() as db:
        user = User(username="alice", realname="Alice", account_name="alice", mail="a@example.com",
                    password="unused", public_key="manual-key keep comment\n", status=UserStatus.ACTIVE)
        gateway = Server(host="gateway.invalid", port=22, is_gateway=True)
        worker = Server(host="worker.invalid", port=22)
        db.add_all([user, gateway, worker])
        db.flush()
        account = Account(user_id=user.id, server_id=gateway.id, is_login_able=True, status=AccountStatus.ACTIVE)
        updating = Account(user_id=user.id, server_id=worker.id, is_login_able=True, status=AccountStatus.UPDATING)
        revoked = Account(user_id=user.id, server_id=worker.id, is_login_able=False, status=AccountStatus.ACTIVE)
        db.add_all([account, updating, revoked])
        db.commit()
        ids = SimpleNamespace(user=user.id, gateway=gateway.id, worker=worker.id,
                              account=account.id, updating=updating.id, revoked=revoked.id)
    for server_id in (ids.gateway, ids.worker):
        sync.last_server_collect_date[server_id] = datetime.datetime.now()
    sync.syncing_accounts[ids.updating] = True
    yield factory, ids, sessions
    for session in sessions:
        assert session.close.called, "Database session leaked"
    engine.dispose()


def mock_connection(monkeypatch, sessions=(), during_connect=None, during_command=None):
    async def command(*args, **kwargs):
        assert all(session.close.called for session in sessions), "DB session spans SSH command"
        if during_command:
            during_command()
        return response()

    conn = SimpleNamespace(run=AsyncMock(side_effect=command), close=Mock(), wait_closed=AsyncMock())

    @asynccontextmanager
    async def connect(server):
        assert all(session.close.called for session in sessions), "DB session spans connect"
        if during_connect:
            during_connect()
        try:
            yield conn
        finally:
            conn.close()
            await conn.wait_closed()

    monkeypatch.setattr(sync, "getConnection", connect)
    return conn


@pytest.mark.parametrize("account", ["a;id", "$(id)", "alice\n", "A", "-root", "../x", "é", "a" * 33, "", None])
def test_strict_names_fail_before_network(account):
    conn = SimpleNamespace(run=AsyncMock())
    with pytest.raises(ValueError):
        run(keys.sshGatewayPublicKey(conn, account))
    conn.run.assert_not_awaited()


def test_script_syntax_and_safe_primitives():
    command = keys.gateway_key_command("alice")
    words = shlex.split(command)
    assert words[:8] == ["sudo", "-n", "-H", "-u", "alice", "sh", "-c", words[-1]]
    wrapper = words[-1]
    assert "PATH=/usr/bin:/bin" in wrapper
    script = shlex.split(wrapper)[-1]
    # -n parses only. No filesystem, sudo, SSH or keygen operation is executed.
    for source in (command, wrapper, script):
        parsed = subprocess.run(["sh", "-n"], input=source, text=True, capture_output=True, timeout=3)
        assert parsed.returncode == 0, parsed.stderr
    for primitive in ("timeout -k 2s 20s",):
        assert primitive in wrapper
    for primitive in ('getent passwd "$account"', 'test "$uid" != 0', 'realpath -e', 'test ! -L .ssh',
                      'test ! -L "$1"', 'stat -c %h', 'exec 9< .', 'flock -w 3 9',
                      'mktemp -d', 'trap cleanup 0', 'trap \'exit 47\' HUP INT TERM',
                      'ln -T -- "$work/key" ./id_ed25519', 'chmod 700 .', 'chmod 600 ./id_ed25519',
                      "ssh-keygen -q -t ed25519 -N ''", "ssh-keygen -y -P ''", 'SSH_ASKPASS_REQUIRE=never',
                      'exec </dev/null 2>/dev/null', 'printf \'%s %s\\n\' "$derived_type" "$derived_blob"'):
        assert primitive in script
    for forbidden in ("/home/", "cat ", "tee ", "mv ", "chown ", "curl ", "apt ", "rm -rf", '>/id_ed25519'):
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        assert forbidden not in code
    assert 'ln -T -- "$work/derived.pub" ./id_ed25519.pub' in script
    assert 'if test ! -e ./id_ed25519 && test -e ./id_ed25519.pub; then exit 46' in script
    assert 'test "$actual_type" = "$derived_type" && test "$actual_blob" = "$derived_blob"' in script


def test_helper_fetches_only_validated_public_output():
    conn = SimpleNamespace(run=AsyncMock(return_value=response(PUBLIC + " untrusted $(comment)\n")))
    assert run(keys.sshGatewayPublicKey(conn, "alice")) == PUBLIC
    conn.run.assert_awaited_once()
    assert conn.run.await_args.kwargs == {"timeout": 25}
    assert PUBLIC not in conn.run.await_args.args[0]


@pytest.mark.parametrize("value", [None, "", "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-rsa AAAA",
                                      "ssh-ed25519 AAAA", PUBLIC + "\n" + PUBLIC,
                                      PUBLIC + "\rcomment", PUBLIC + "\0", PUBLIC + " x" * 4096])
def test_public_output_rejects_invalid_multiline_private_and_oversized(value):
    with pytest.raises(keys.GatewayKeyError) as error:
        keys.validate_gateway_public_key(value)
    assert PUBLIC not in str(error.value)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("code, hint", [(40, "系统工具"), (41, "主目录"), (42, "符号链接"), (43, "flock"),
                                        (44, "磁盘"), (45, "加密"), (46, "不匹配"), (47, "磁盘"),
                                        (124, "超时"), (1, "sudo")])
def test_controlled_actionable_exit_failures(code, hint):
    conn = SimpleNamespace(run=AsyncMock(return_value=response("SECRET", code, "PRIVATE password=secret")))
    with pytest.raises(keys.GatewayKeyError, match=hint) as error:
        run(keys.sshGatewayPublicKey(conn, "alice"))
    assert "SECRET" not in str(error.value) and "secret" not in str(error.value)


@pytest.mark.parametrize("error", [TimeoutError("PRIVATE secret"), RuntimeError("PRIVATE secret")])
def test_network_exceptions_never_disclose_raw_text(error):
    conn = SimpleNamespace(run=AsyncMock(side_effect=error))
    with pytest.raises(keys.GatewayKeyError) as caught:
        run(keys.sshGatewayPublicKey(conn, "alice"))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("prefix", ["", 'restrict ', 'command="echo hello",no-pty '])
def test_dedup_ignores_comments_and_preserves_manual_options(prefix):
    original = 'manual invalid legacy data\n' + prefix + PUBLIC + ' old-comment\n\n'
    assert keys.append_gateway_public_key(original, PUBLIC + " new-comment") == (original, False)
    assert keys.append_gateway_public_key("manual-key\n", PUBLIC) == ("manual-key\n" + PUBLIC, True)
    assert keys.append_gateway_public_key("manual-key", PUBLIC) == ("manual-key\n" + PUBLIC, True)
    assert keys.append_gateway_public_key(None, PUBLIC) == (PUBLIC, True)


def test_append_locks_fresh_user_preserves_keys_and_marks_updating_dirty(db_env, monkeypatch):
    factory, ids, sessions = db_env

    def profile_edit():
        with factory() as db:
            db.get(User, ids.user).public_key += "another-manual-key\n"
            db.commit()

    conn = mock_connection(monkeypatch, sessions, during_command=profile_edit)
    lock = Mock(wraps=sync.lock_usage_user)
    monkeypatch.setattr(sync, "lock_usage_user", lock)
    monkeypatch.setattr(sync.time, "monotonic", lambda: 100)
    run(sync.syncGatewayKey(ids.account))
    lock.assert_called_once()
    with factory() as db:
        assert db.get(User, ids.user).public_key == "manual-key keep comment\nanother-manual-key\n" + PUBLIC
        assert db.get(Account, ids.account).status == AccountStatus.DIRTY
        assert db.get(Account, ids.updating).status == AccountStatus.DIRTY
        assert db.get(Account, ids.revoked).status == AccountStatus.ACTIVE
    assert sync._gateway_key_next_check[ids.account] == 100 + 14400
    assert sync.get_sync_errors() == []
    conn.close.assert_called_once()
    conn.wait_closed.assert_awaited_once()
    # The account worker must preserve the DIRTY marker after stale success.
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    run(sync.syncAccount(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(id=ids.updating)))
    with factory() as db:
        assert db.get(Account, ids.updating).status == AccountStatus.DIRTY


def change_eligibility(factory, ids, change):
    with factory() as db:
        account = db.get(Account, ids.account)
        if change == "revoke":
            account.is_login_able = False
        elif change == "inactive":
            db.get(User, ids.user).status = UserStatus.GRADUATED
        elif change == "verifying":
            db.get(User, ids.user).status = UserStatus.VERIFYING
        elif change == "non-gateway":
            db.get(Server, ids.gateway).is_gateway = False
        elif change == "deleted-account":
            db.delete(account)
        elif change == "deleted-user":
            db.delete(db.get(User, ids.user))
        elif change == "deleted-server":
            db.delete(db.get(Server, ids.gateway))
        elif change == "rename":
            db.get(User, ids.user).account_name = "renamed"
        elif change == "dirty":
            account.status = AccountStatus.DIRTY
        elif change == "updating":
            account.status = AccountStatus.UPDATING
        db.commit()


@pytest.mark.parametrize("change", ["revoke", "inactive", "verifying", "non-gateway", "deleted-account",
                                     "deleted-user", "deleted-server", "dirty", "updating"])
def test_ineligible_preflight_never_opens_ssh(db_env, monkeypatch, change):
    factory, ids, _ = db_env
    change_eligibility(factory, ids, change)
    connect = Mock(side_effect=AssertionError("Ineligible SSH forbidden"))
    monkeypatch.setattr(sync, "getConnection", connect)
    run(sync.syncGatewayKey(ids.account))
    connect.assert_not_called()
    assert sync.get_sync_errors() == []


@pytest.mark.parametrize("phase", ["connect", "command"])
@pytest.mark.parametrize("change", ["revoke", "inactive", "verifying", "non-gateway", "deleted-account",
                                     "deleted-user", "deleted-server", "rename"])
def test_reauthorize_after_each_network_await(db_env, monkeypatch, phase, change):
    factory, ids, sessions = db_env
    edit = lambda: change_eligibility(factory, ids, change)
    conn = mock_connection(monkeypatch, sessions, during_connect=edit if phase == "connect" else None,
                           during_command=edit if phase == "command" else None)
    run(sync.syncGatewayKey(ids.account))
    if phase == "connect":
        conn.run.assert_not_awaited()
    with factory() as db:
        user = db.get(User, ids.user)
        assert user is None or user.public_key == "manual-key keep comment\n"
    assert sync.get_sync_errors() == []


@pytest.mark.parametrize("change", ["dirty", "updating"])
def test_status_change_during_fetch_still_marks_future_reconcile(db_env, monkeypatch, change):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions, during_command=lambda: change_eligibility(factory, ids, change))
    run(sync.syncGatewayKey(ids.account))
    with factory() as db:
        assert PUBLIC in db.get(User, ids.user).public_key
        assert db.get(Account, ids.account).status == AccountStatus.DIRTY


def test_existing_public_identity_is_success_without_dirtying(db_env, monkeypatch):
    factory, ids, sessions = db_env
    with factory() as db:
        db.get(User, ids.user).public_key = PUBLIC + " manual-comment\n"
        db.commit()
    mock_connection(monkeypatch, sessions)
    run(sync.syncGatewayKey(ids.account))
    with factory() as db:
        assert db.get(User, ids.user).public_key == PUBLIC + " manual-comment\n"
        assert db.get(Account, ids.account).status == AccountStatus.ACTIVE
        assert db.get(Account, ids.updating).status == AccountStatus.UPDATING


@pytest.mark.parametrize("global_enabled, feature_enabled, expected", [
    (None, None, True), ("true", "false", False), ("false", "true", False),
    ("false", None, False), ("TRUE", "TRUE", True)])
def test_optional_flag_cannot_override_global_watcher(monkeypatch, global_enabled, feature_enabled, expected):
    for name, value in (("SYNC_ENABLED", global_enabled), ("GATEWAY_KEY_SYNC_ENABLED", feature_enabled)):
        if value is not None:
            monkeypatch.setenv(name, value)
    assert sync.gateway_key_sync_enabled() is expected


def test_failure_five_minute_retry_success_four_hour_recheck_and_dedup_workers(db_env, monkeypatch):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions)
    helper = AsyncMock(side_effect=[keys.GatewayKeyError("private"), PUBLIC, PUBLIC])
    monkeypatch.setattr(sync, "sshGatewayPublicKey", helper)
    clock = [10]
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock[0])

    def schedule():
        with factory() as db:
            sync._schedule_gateway_keys(db)

    async def scenario():
        schedule()
        task = sync._worker_tasks[("gateway-key", ids.account)]
        schedule()
        assert sync._worker_tasks[("gateway-key", ids.account)] is task
        await sync.stopWatcher()
        assert helper.await_count == 1
        assert sync._gateway_key_next_check[ids.account] == 310
        assert sync.get_sync_errors()[0]["scope"] == "gateway-key"
        assert sync.get_sync_errors()[0]["id"] == ids.account
        clock[0] = 309
        schedule()
        assert not sync._sync_tasks
        clock[0] = 310
        schedule()
        await sync.stopWatcher()
        assert helper.await_count == 2
        assert sync.get_sync_errors() == []
        assert sync._gateway_key_next_check[ids.account] == 14710
        with factory() as db:
            db.get(Account, ids.account).status = AccountStatus.ACTIVE
            db.commit()
        clock[0] = 14709
        schedule()
        assert not sync._sync_tasks
        clock[0] = 14710
        schedule()
        await sync.stopWatcher()
        assert helper.await_count == 3
    run(scenario())


def test_cycle_schedules_only_after_account_provisioning(db_env, monkeypatch):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions)
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    with factory() as db:
        db.get(Account, ids.account).status = AccountStatus.DIRTY
        db.commit()

    async def scenario():
        sync._watch_cycle()
        assert ("account", ids.account) in sync._worker_tasks
        assert ("gateway-key", ids.account) not in sync._worker_tasks
        await sync.stopWatcher()
        with factory() as db:
            assert db.get(Account, ids.account).status == AccountStatus.ACTIVE
        sync._watch_cycle()
        assert ("gateway-key", ids.account) in sync._worker_tasks
        await sync.stopWatcher()
    run(scenario())


def test_gateway_admin_rule_dirty_precedes_key_scheduling(db_env, monkeypatch):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions)
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    with factory() as db:
        db.get(User, ids.user).is_admin = True
        db.commit()

    async def scenario():
        sync._watch_cycle()
        assert ("account", ids.account) in sync._worker_tasks
        assert ("gateway-key", ids.account) not in sync._worker_tasks
        await sync.stopWatcher()
    run(scenario())


def test_disable_does_not_schedule_or_run_worker(db_env, monkeypatch):
    factory, ids, _ = db_env
    monkeypatch.setenv("GATEWAY_KEY_SYNC_ENABLED", "false")
    helper = AsyncMock()
    monkeypatch.setattr(sync, "sshGatewayPublicKey", helper)

    async def scenario():
        with factory() as db:
            sync._schedule_gateway_keys(db)
        assert not sync._sync_tasks
        await sync.syncGatewayKey(ids.account)
    run(scenario())
    helper.assert_not_awaited()


def test_reauthorization_after_semaphore_wait(db_env, monkeypatch):
    factory, ids, sessions = db_env
    conn = mock_connection(monkeypatch, sessions)
    sem = asyncio.Semaphore(0)
    monkeypatch.setattr(sync, "semaphore", sem)

    async def scenario():
        sync._spawn(sync.syncGatewayKey(ids.account), "gateway-key", ids.account)
        await asyncio.sleep(0)
        change_eligibility(factory, ids, "revoke")
        sem.release()
        await sync.stopWatcher()
    run(scenario())
    conn.run.assert_not_awaited()


def test_shutdown_waits_for_gateway_worker_and_connection_cleanup(db_env, monkeypatch):
    _, ids, sessions = db_env
    conn = mock_connection(monkeypatch, sessions)

    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def fetch(*args):
            entered.set()
            await finish.wait()
            return PUBLIC

        monkeypatch.setattr(sync, "sshGatewayPublicKey", fetch)
        worker = sync._spawn(sync.syncGatewayKey(ids.account), "gateway-key", ids.account)
        await entered.wait()
        stop = asyncio.create_task(sync.stopWatcher())
        await asyncio.sleep(0)
        assert not stop.done() and not worker.cancelled()
        finish.set()
        await stop
        assert worker.done() and not sync._sync_tasks and not sync._worker_tasks
    run(scenario())
    conn.close.assert_called_once()
    conn.wait_closed.assert_awaited_once()


def test_cancelled_worker_closes_and_has_bounded_retry(db_env, monkeypatch):
    _, ids, sessions = db_env
    conn = mock_connection(monkeypatch, sessions)
    monkeypatch.setattr(sync.time, "monotonic", lambda: 25)

    async def scenario():
        entered = asyncio.Event()

        async def fetch(*args):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(sync, "sshGatewayPublicKey", fetch)
        worker = sync._spawn(sync.syncGatewayKey(ids.account), "gateway-key", ids.account)
        await entered.wait()
        worker.cancel()
        await sync.stopWatcher()
        assert worker.cancelled()
    run(scenario())
    assert sync._gateway_key_next_check[ids.account] == 325
    assert sync.get_sync_errors()[0]["scope"] == "gateway-key"
    conn.close.assert_called_once()


def test_deleted_state_and_errors_are_pruned(db_env):
    factory, ids, _ = db_env
    sync._gateway_key_next_check[ids.account] = 900
    sync._gateway_key_next_check[999] = 900
    sync._record_error("gateway-key", 999, "Controlled diagnostic")
    change_eligibility(factory, ids, "revoke")
    with factory() as db:
        sync._schedule_gateway_keys(db)
    assert not sync._gateway_key_next_check
    assert sync.get_sync_errors() == []


def test_commented_public_key_is_not_an_installed_identity():
    original = "# " + PUBLIC + " comment"
    assert keys.append_gateway_public_key(original, PUBLIC) == (original + "\n" + PUBLIC, True)


def test_invalid_base64_cannot_be_normalized_to_a_valid_key():
    with pytest.raises(keys.GatewayKeyError):
        keys.validate_gateway_public_key(PUBLIC + "!")


def test_gateway_unknown_errors_have_controlled_actionable_reasons():
    sync._record_error("gateway-key", 17, "Gateway public-key synchronization failed",
                       ConnectionError("PRIVATE password=secret"))
    error = sync.get_sync_errors()[0]
    assert error["scope"] == "gateway-key" and error["id"] == 17
    assert "sudo" in error["message"] and "5 分钟" in error["message"]
    assert "PRIVATE" not in error["message"] and "secret" not in error["message"]


def test_append_commit_failure_rolls_back_and_retries(db_env, monkeypatch):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions)
    original = sync.SessionLocal
    calls = []

    def failing_persist_session():
        session = original()
        calls.append(session)
        if len(calls) == 3:  # Preflight, after connect, then locked persistence.
            session.commit = Mock(side_effect=RuntimeError("PRIVATE secret"))
            session.rollback = Mock(wraps=session.rollback)
        return session

    monkeypatch.setattr(sync, "SessionLocal", failing_persist_session)
    monkeypatch.setattr(sync.time, "monotonic", lambda: 50)
    run(sync.syncGatewayKey(ids.account))
    calls[-1].rollback.assert_called_once()
    assert sync._gateway_key_next_check[ids.account] == 350
    with factory() as db:
        assert db.get(User, ids.user).public_key == "manual-key keep comment\n"
        assert db.get(Account, ids.account).status == AccountStatus.ACTIVE
        assert db.get(Account, ids.updating).status == AccountStatus.UPDATING
    error = sync.get_sync_errors()[0]
    assert error["scope"] == "gateway-key" and error["id"] == ids.account
    assert "secret" not in error["message"]


def test_lock_rereads_public_key_instead_of_using_stale_snapshot(db_env, monkeypatch):
    factory, ids, sessions = db_env
    mock_connection(monkeypatch, sessions)
    original = sync.lock_usage_user

    def profile_finishes_before_lock(db, user_id, **kwargs):
        with factory() as writer:
            writer.get(User, user_id).public_key += "late-manual-key\n"
            writer.commit()
        return original(db, user_id, **kwargs)

    monkeypatch.setattr(sync, "lock_usage_user", profile_finishes_before_lock)
    run(sync.syncGatewayKey(ids.account))
    with factory() as db:
        assert db.get(User, ids.user).public_key == "manual-key keep comment\nlate-manual-key\n" + PUBLIC
