"""Self-contained sync regressions (pytest only, no pytest-asyncio).

Run from repository root: python3 -m pytest backend/tests/test_sync.py
Requires existing backend requirements plus pytest. All SSH and database access
is isolated; neither a configured production database nor real SSH is used.
"""
import asyncio
import datetime
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from contextlib import asynccontextmanager

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

import account_helpers as helpers
import account_sync as sync
import server_helpers
from app.database import Base, Server, User, UserStatus, Account, AccountStatus, ServerStatus


def run(coroutine):
    return asyncio.run(coroutine)


class FakeSSH:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.closed = False
        self.waited = False

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        response = self.responses.pop(0) if self.responses else result()
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


def result(stdout="", status=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=status)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    import account_config
    monkeypatch.setattr(account_config, "_config", None)
    monkeypatch.delenv("ACCOUNT_INITIAL_PASSWORD", raising=False)
    account_config.load_account_config()
    monkeypatch.setattr(sync.asyncssh, "connect", AsyncMock(side_effect=AssertionError("Real SSH forbidden")))
    monkeypatch.setattr(sync, "semaphore", asyncio.Semaphore(20))
    # Hardware has its own collector suite; don't consume fake login SSH responses.
    hardware = {name: {"status": "ok", "data": None, "error": None, "collected_at": None, "checked_at": None}
                for name in ("cpu", "memory", "gpus", "disks", "network")}
    monkeypatch.setattr(sync, "collect_hardware", AsyncMock(return_value=hardware))
    sync.syncing_accounts.clear()
    sync._account_activity_checks.clear()
    sync._gateway_key_next_check.clear()
    sync.last_server_collect_date.clear()
    sync.last_server_collecting.clear()
    sync._refresh_requested.clear()
    sync._sync_errors.clear()
    sync._sync_tasks.clear()
    sync._worker_tasks.clear()
    sync.start_watcher = False
    sync._watcher_task = None
    yield
    assert not sync._sync_tasks
    assert sync._watcher_task is None or sync._watcher_task.done()


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
        server = Server(host="node.test", port=22)
        user = User(username="alice", realname="Alice", account_name="alice", mail="alice@example.com",
                    password="not-a-real-password", public_key="ssh-ed25519 AAAA fake", status=UserStatus.ACTIVE)
        db.add_all([server, user])
        db.flush()
        account = Account(server_id=server.id, user_id=user.id, status=AccountStatus.UPDATING)
        db.add(account)
        db.commit()
        ids = SimpleNamespace(server=server.id, user=user.id, account=account.id)
    yield factory, ids, sessions
    for session in sessions:
        assert session.close.called, "Sync leaked a DB session"
    engine.dispose()


def detached(factory, cls, identifier):
    with factory() as db:
        row = db.get(cls, identifier)
        db.expunge(row)
        return row


def fake_connection(monkeypatch, conn=None):
    conn = conn or FakeSSH()

    @asynccontextmanager
    async def get_connection(server):
        try:
            yield conn
        finally:
            conn.close()
            await conn.wait_closed()

    monkeypatch.setattr(sync, "getConnection", get_connection)
    return conn


def collection_success(monkeypatch):
    for name, value in [("sshServerGetKernel", "6.1"), ("sshServerGetRelease", "Test Linux"),
                        ("sshServerGetNICs", []), ("sshServerGetIBNICs", []),
                        ("sshServerGetAccountLoginDate", (True, datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)))]:
        monkeypatch.setattr(sync, name, AsyncMock(return_value=value))


@pytest.mark.parametrize("name", ["root;id", "$(id)", "-root", "a/b", "a b", "a\n", "A", "a" * 33, "é", ""])
def test_all_account_boundaries_reject_legacy_unsafe_names(name):
    functions = [helpers.sshAccountIsExists, helpers.sshAccountCreate, helpers.sshAccountGetAuthorizedKeys,
                 helpers.sshAccountIsEnabled, helpers.sshAccountEnable, helpers.sshAccountDisable,
                 helpers.sshAccountSudo, helpers.sshAccountIsSudo, helpers.sshAccountUnsudo,
                 server_helpers.sshServerGetAccountLoginDate]
    for function in functions:
        conn = FakeSSH()
        args = [conn, name] + (["key"] if function is helpers.sshAccountEnable else [])
        with pytest.raises(ValueError):
            run(function(*args))
        assert conn.calls == []


def test_public_keys_and_initial_password_use_stdin_only():
    key = 'ssh-ed25519 AAAA comment $(touch /tmp/pwn); "`id`"'
    conn = FakeSSH()
    run(helpers.sshAccountCreate(conn, "alice"))
    run(helpers.sshAccountEnable(conn, "alice", key))
    commands = "\n".join(command for command, _ in conn.calls)
    assert key not in commands and "123456" not in commands and "Qwe123!@#" not in commands
    assert "chpasswd" in commands
    assert any(kwargs.get("input") == "alice:Qwe123!@#\n" for _, kwargs in conn.calls)
    assert any(kwargs.get("input") == key + "\n" for _, kwargs in conn.calls)
    assert "chmod 600" in commands and "-m 700" in commands


def test_revoke_blocks_login_and_removes_sudo_without_authorized_keys():
    conn = FakeSSH([result(), result("sudo:x:27:alice,bob"), result(), result()])
    assert run(helpers.sshAccountDisable(conn, "alice")) == (True, None)
    commands = [command for command, _ in conn.calls]
    assert "-L -e 1 -s /usr/sbin/nologin" in commands[0]
    assert any("gpasswd -d alice sudo" in command for command in commands)
    assert any("if test -e" in command for command in commands)


def test_revoked_missing_account_is_not_created(monkeypatch):
    fake_connection(monkeypatch)
    monkeypatch.setattr(sync, "sshAccountIsExists", AsyncMock(return_value=False))
    create = AsyncMock()
    monkeypatch.setattr(sync, "sshAccountCreate", create)
    run(sync.doSyncAccount(SimpleNamespace(account_name="alice"), SimpleNamespace(id=1),
                           SimpleNamespace(is_login_able=False)))
    create.assert_not_awaited()


def test_existing_keys_are_merged_not_replaced(monkeypatch):
    fake_connection(monkeypatch)
    monkeypatch.setattr(sync, "sshAccountIsExists", AsyncMock(return_value=True))
    monkeypatch.setattr(sync, "sshAccountGetAuthorizedKeys", AsyncMock(return_value="old-key\nshared-key"))
    enable = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(sync, "sshAccountEnable", enable)
    monkeypatch.setattr(sync, "sshAccountUnsudo", AsyncMock(return_value=(True, None)))
    run(sync.doSyncAccount(SimpleNamespace(account_name="alice", public_key="shared-key\nnew-key"),
                           SimpleNamespace(id=1), SimpleNamespace(is_login_able=True, is_sudo=False)))
    assert enable.await_args.args[2] == "old-key\nshared-key\nnew-key"


def test_interface_names_are_single_quoted_arguments():
    name = "eth0;$(touch /tmp/pwn)"
    conn = FakeSSH([result("0000:01:00.0 Ethernet controller: Test"), result(name), result("../../../0000:01:00.0")])
    assert run(server_helpers.sshServerGetNICs(conn))[0]["interface_name"] == name
    command = conn.calls[-1][0]
    assert shlex.split(command) == ["readlink", "--", f"/sys/class/net/{name}/device"]
    assert shlex.quote(f"/sys/class/net/{name}/device") in command


def test_remote_command_errors_are_not_silently_successful():
    for function, args in [(helpers.sshAccountIsExists, ["alice"]),
                           (helpers.sshAccountGetAuthorizedKeys, ["alice"]),
                           (server_helpers.sshServerGetKernel, []),
                           (server_helpers.sshServerGetNICs, [])]:
        with pytest.raises(RuntimeError) as error:
            run(function(FakeSSH([result(status=1, stderr="password=secret ssh-ed25519 PRIVATE")]), *args))
        assert "secret" not in str(error.value)


@pytest.mark.parametrize("with_proxy", [False, True])
def test_connections_and_db_close_on_body_exception(db_env, monkeypatch, with_proxy):
    factory, ids, _ = db_env
    if with_proxy:
        with factory() as db:
            proxy = Server(host="jump.test", port=2222)
            db.add(proxy)
            db.flush()
            db.get(Server, ids.server).proxy_server_id = proxy.id
            db.commit()
    connections = [FakeSSH(), FakeSSH()] if with_proxy else [FakeSSH()]
    connect = AsyncMock(side_effect=connections)
    monkeypatch.setattr(sync.asyncssh, "connect", connect)
    monkeypatch.delenv("SSH_KNOWN_HOSTS", raising=False)
    monkeypatch.delenv("SSH_INSECURE_SKIP_HOST_KEY_CHECK", raising=False)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with sync.getConnection(SimpleNamespace(id=ids.server)):
                raise RuntimeError("body failed")

    run(scenario())
    assert all(conn.closed and conn.waited for conn in connections)
    assert all("known_hosts" not in call.kwargs for call in connect.await_args_list)
    if with_proxy:
        assert connect.await_args_list[1].kwargs["tunnel"] is connections[0]


def test_known_hosts_options_require_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("SSH_KNOWN_HOSTS", "/tmp/test_known_hosts")
    monkeypatch.setenv("SSH_INSECURE_SKIP_HOST_KEY_CHECK", "false")
    assert sync._known_hosts_options() == {"known_hosts": "/tmp/test_known_hosts"}
    monkeypatch.setenv("SSH_INSECURE_SKIP_HOST_KEY_CHECK", "true")
    assert sync._known_hosts_options() == {"known_hosts": None}


def test_jump_host_closes_when_target_connect_fails(db_env, monkeypatch):
    factory, ids, _ = db_env
    with factory() as db:
        proxy = Server(host="jump.test", port=22)
        db.add(proxy)
        db.flush()
        db.get(Server, ids.server).proxy_server_id = proxy.id
        db.commit()
    tunnel = FakeSSH()
    monkeypatch.setattr(sync.asyncssh, "connect", AsyncMock(side_effect=[tunnel, ConnectionError("secret")]))

    async def scenario():
        with pytest.raises(ConnectionError):
            async with sync.getConnection(SimpleNamespace(id=ids.server)):
                pytest.fail("Unexpected connection")

    run(scenario())
    assert tunnel.closed and tunnel.waited


def test_missing_server_closes_session(db_env):
    async def scenario():
        with pytest.raises(RuntimeError, match="no longer exists"):
            async with sync.getConnection(SimpleNamespace(id=999)):
                pytest.fail("Unexpected connection")
    run(scenario())


def test_detached_server_status_and_inner_error_persist_then_clear(db_env, monkeypatch):
    factory, ids, _ = db_env
    server = detached(factory, Server, ids.server)
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    monkeypatch.setattr(sync, "sshServerGetKernel", AsyncMock(side_effect=RuntimeError("password=secret")))
    run(sync.syncServer(server))
    with factory() as db:
        assert db.get(Server, ids.server).server_status == ServerStatus.NO_PERMISSION
    assert sync.get_sync_errors()[0]["scope"] == "server"
    assert "secret" not in str(sync.get_sync_errors())
    assert ids.server not in sync.last_server_collect_date
    collection_success(monkeypatch)
    run(sync.syncServer(server))
    with factory() as db:
        assert db.get(Server, ids.server).server_status == ServerStatus.ACTIVE
        assert db.get(Server, ids.server).kernel_version == "6.1"
    assert sync.get_sync_errors() == []


def test_connection_failure_status_is_committed(db_env):
    factory, ids, _ = db_env
    run(sync.syncServer(detached(factory, Server, ids.server)))
    with factory() as db:
        assert db.get(Server, ids.server).server_status == ServerStatus.UNABLE_TO_REACH
    assert sync.get_sync_errors()[0]["message"].startswith("SSH connection failed")


def test_login_history_inner_failures_remain_visible_and_due(db_env, monkeypatch):
    factory, ids, _ = db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    monkeypatch.setattr(sync, "sshServerGetAccountLoginDate", AsyncMock(return_value=(False, "secret")))
    run(sync.syncServer(detached(factory, Server, ids.server)))
    assert "login history" in sync.get_sync_errors()[0]["message"]
    with factory() as db:
        assert db.get(Server, ids.server).server_status == ServerStatus.NO_PERMISSION
    assert ids.server not in sync.last_server_collect_date


def test_account_failure_retries_and_clears_latest_error(db_env, monkeypatch):
    factory, ids, _ = db_env
    account = detached(factory, Account, ids.account)
    server = detached(factory, Server, ids.server)
    user = detached(factory, User, ids.user)
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock(side_effect=RuntimeError("ssh-rsa SECRET password=secret")))
    run(sync.syncAccount(user, server, account))
    with factory() as db:
        assert db.get(Account, ids.account).status == AccountStatus.DIRTY
        db.get(Account, ids.account).status = AccountStatus.UPDATING
        db.commit()
    assert sync.get_sync_errors()[0]["scope"] == "account"
    assert "SECRET" not in str(sync.get_sync_errors())
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    run(sync.syncAccount(user, server, account))
    with factory() as db:
        assert db.get(Account, ids.account).status == AccountStatus.ACTIVE
    assert sync.get_sync_errors() == []


def test_concurrent_dirty_edit_is_preserved(db_env, monkeypatch):
    factory, ids, _ = db_env

    async def edit_during_sync(*args):
        with factory() as db:
            db.get(Account, ids.account).status = AccountStatus.DIRTY
            db.commit()

    monkeypatch.setattr(sync, "doSyncAccount", edit_during_sync)
    run(sync.syncAccount(detached(factory, User, ids.user), detached(factory, Server, ids.server),
                         detached(factory, Account, ids.account)))
    with factory() as db:
        assert db.get(Account, ids.account).status == AccountStatus.DIRTY


def test_error_registry_is_bounded_sanitized_and_copied():
    for identifier in range(sync.MAX_SYNC_ERRORS + 3):
        sync._record_error("account", identifier, "Account synchronization failed", RuntimeError("password=secret ssh-ed25519 ABCD"))
    errors = sync.get_sync_errors()
    assert len(errors) == sync.MAX_SYNC_ERRORS
    assert errors[0]["id"] == 3
    assert "secret" not in str(errors) and "ABCD" not in str(errors)
    datetime.datetime.fromisoformat(errors[0]["occurred_at"])
    errors[0]["message"] = "changed"
    assert sync.get_sync_errors()[0]["message"] != "changed"
    sync._record_error("account", 3, "Retry failed")
    assert len(sync.get_sync_errors()) == sync.MAX_SYNC_ERRORS
    assert sync.get_sync_errors()[-1]["id"] == 3


def test_watcher_retries_iteration_errors_and_tracks_work(monkeypatch):
    calls = []
    worker_done = []
    monkeypatch.setattr(sync, "WATCH_INTERVAL", 0.001)

    async def worker():
        await asyncio.sleep(0.01)
        worker_done.append(True)

    def cycle():
        calls.append(True)
        if len(calls) == 1:
            sync._spawn(worker(), "server", 1)
            raise RuntimeError("password=secret")

    monkeypatch.setattr(sync, "_watch_cycle", cycle)

    async def scenario():
        task = sync.startWatcher()
        assert sync.startWatcher() is task
        await asyncio.sleep(0)
        assert sync.get_sync_errors()[0]["scope"] == "watcher"
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.001)
        await sync.stopWatcher()
        assert task.done()
        assert len(calls) >= 2 and worker_done == [True]
        assert sync.get_sync_errors() == []
        assert not sync._sync_tasks

    run(scenario())


def test_watch_cycle_bool_log_and_real_refresh_scheduling(db_env, monkeypatch, caplog):
    factory, ids, _ = db_env
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    sync.syncing_accounts[ids.account] = True
    sync.last_server_collect_date[ids.server] = datetime.datetime.now()

    async def scenario():
        sync._watch_cycle()  # Used to crash joining True, not account IDs.
        assert not sync._sync_tasks
        sync.request_server_refresh(ids.server)
        sync._watch_cycle()
        await sync.stopWatcher()
        assert sync.sshServerGetKernel.await_count == 1

    run(scenario())
    assert "Dump syncing accounts: " + str(ids.account) in caplog.text


def test_refresh_during_collection_is_not_lost(db_env, monkeypatch):
    factory, ids, _ = db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)

    async def refresh_while_collecting(conn):
        sync.request_server_refresh(ids.server)
        return "6.1"

    monkeypatch.setattr(sync, "sshServerGetKernel", refresh_while_collecting)
    run(sync.syncServer(detached(factory, Server, ids.server)))
    assert ids.server not in sync.last_server_collect_date


def test_explicit_gateway_revocation_is_not_undone(db_env, monkeypatch):
    factory, ids, _ = db_env
    with factory() as db:
        db.get(Server, ids.server).is_gateway = True
        account = db.get(Account, ids.account)
        account.is_login_able = False
        account.status = AccountStatus.ACTIVE
        db.commit()
    sync.last_server_collect_date[ids.server] = datetime.datetime.now()

    async def scenario():
        sync._watch_cycle()
        await sync.stopWatcher()

    run(scenario())
    with factory() as db:
        assert db.get(Account, ids.account).is_login_able is False


def test_session_closes_when_commit_fails(db_env, monkeypatch):
    factory, ids, _ = db_env
    session = factory()
    session.commit = Mock(side_effect=RuntimeError("secret"))
    session.close = Mock(wraps=session.close)
    session.rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(sync, "SessionLocal", lambda: session)
    monkeypatch.setattr(sync, "doSyncAccount", AsyncMock())
    run(sync.syncAccount(detached(factory, User, ids.user), detached(factory, Server, ids.server),
                         detached(factory, Account, ids.account)))
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    assert "persistence failed" in sync.get_sync_errors()[0]["message"]


def test_shutdown_awaits_failed_worker_and_retrieves_exception(monkeypatch):
    async def failed_worker():
        await asyncio.sleep(0)
        raise RuntimeError("password=secret")

    async def scenario():
        task = sync._spawn(failed_worker(), "server", 12)
        await sync.stopWatcher()
        assert task.done()
        assert not sync._worker_tasks
        assert sync.get_sync_errors()[0]["id"] == 12
        assert "secret" not in str(sync.get_sync_errors())

    run(scenario())


def test_connection_closes_on_task_cancellation(db_env, monkeypatch):
    _, ids, _ = db_env
    conn = FakeSSH()
    monkeypatch.setattr(sync.asyncssh, "connect", AsyncMock(return_value=conn))

    async def scenario():
        entered = asyncio.Event()

        async def worker():
            async with sync.getConnection(SimpleNamespace(id=ids.server)):
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(worker())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert conn.closed and conn.waited


def test_no_process_exit_for_missing_account_or_server_id(db_env, monkeypatch):
    monkeypatch.setattr(os, "_exit", Mock(side_effect=AssertionError("Process exit forbidden")))
    run(sync.syncAccount(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(id=None)))
    run(sync.syncServer(SimpleNamespace(id=None)))
    assert {error["scope"] for error in sync.get_sync_errors()} == {"server", "account"}
    os._exit.assert_not_called()


def test_watch_cycle_closes_database_on_query_error(db_env, monkeypatch):
    factory, _, _ = db_env
    session = factory()
    session.query = Mock(side_effect=RuntimeError("secret"))
    session.close = Mock(wraps=session.close)
    session.rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(sync, "SessionLocal", lambda: session)
    with pytest.raises(RuntimeError):
        sync._watch_cycle()
    session.close.assert_called_once()
    session.rollback.assert_called_once()


def test_failed_direct_collection_clears_recent_due_timestamp(db_env):
    factory, ids, _ = db_env
    sync.last_server_collect_date[ids.server] = datetime.datetime.now()
    run(sync.syncServer(detached(factory, Server, ids.server)))
    assert ids.server not in sync.last_server_collect_date


def test_pending_password_initialization_failure_stops_key_enable(monkeypatch):
    fake_connection(monkeypatch)
    monkeypatch.setattr(sync, "sshAccountIsExists", AsyncMock(return_value=True))
    recovery = AsyncMock(side_effect=RuntimeError("Pending initialization failed"))
    monkeypatch.setattr(sync, "sshAccountInitializePassword", recovery)
    enable = AsyncMock()
    monkeypatch.setattr(sync, "sshAccountEnable", enable)
    with pytest.raises(RuntimeError):
        run(sync.doSyncAccount(SimpleNamespace(account_name="alice",public_key="key"),SimpleNamespace(id=1),
                               SimpleNamespace(is_login_able=True,is_sudo=False)))
    recovery.assert_awaited_once()
    enable.assert_not_awaited()


def test_revocation_never_initializes_password(monkeypatch):
    fake_connection(monkeypatch)
    monkeypatch.setattr(sync, "sshAccountIsExists", AsyncMock(return_value=True))
    monkeypatch.setattr(sync, "sshAccountDisable", AsyncMock(return_value=(True,None)))
    recovery=AsyncMock()
    monkeypatch.setattr(sync, "sshAccountInitializePassword", recovery)
    run(sync.doSyncAccount(SimpleNamespace(account_name="alice"),SimpleNamespace(id=1),SimpleNamespace(is_login_able=False)))
    recovery.assert_not_awaited()
