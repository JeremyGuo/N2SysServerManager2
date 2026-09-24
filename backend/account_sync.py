"""Background reconciliation with explicit task/resource ownership.

SSH verifies known_hosts by default. SSH_KNOWN_HOSTS selects a file; only the
explicit SSH_INSECURE_SKIP_HOST_KEY_CHECK=true setting disables verification.
"""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
import datetime
import os
import threading
import time
from types import SimpleNamespace

import asyncssh
from sync_errors import SyncCommandError
from gateway_keys import GatewayKeyError, sshGatewayPublicKey, append_gateway_public_key
from activity_time import ActivityTimeError, as_utc, to_db_time, utc_now, validate_activity
from app.database import Account, SessionLocal, AccountStatus, Server, User, UserStatus, ServerStatus, ServerInterface, DeviceUsage
from logger import logger
from app.hardware_store import save_hardware, failed_snapshot
from hardware_helpers import collect_hardware
from usage_service import end_account_usage, lock_usage_user
from account_helpers import (
    validate_account_name, sshAccountIsExists, sshAccountCreate, sshAccountInitializePassword,
    sshAccountGetAuthorizedKeys, sshAccountEnable, sshAccountDisable,
    sshAccountSudo, sshAccountUnsudo,
)
from server_helpers import (
    sshServerGetKernel, sshServerGetRelease, sshServerGetNICs,
    sshServerGetIBNICs, sshServerGetAccountLoginDate,
)

concurrent_tasks = 20
semaphore = asyncio.Semaphore(concurrent_tasks)
syncing_accounts = {}
last_server_collect_date = {}
last_server_collecting = {}
# Only fresh, successful observations can authorize automatic inactivity revocation.
# Empty after restart so stale DB values are never used before SSH collection.
_account_activity_checks = {}
start_watcher = False
_watcher_task = None
_sync_tasks = set()
_worker_tasks = {}
_state_lock = threading.RLock()
_refresh_requested = set()
_sync_errors = OrderedDict()
MAX_SYNC_ERRORS = 200
WATCH_INTERVAL = 30
# Monotonic deadlines: successful public-key observations recheck every 4h;
# failures are retained for a bounded 5-minute backoff (not every watcher tick).
GATEWAY_KEY_RECHECK_SECONDS = 4 * 60 * 60
GATEWAY_KEY_RETRY_SECONDS = 5 * 60
_gateway_key_next_check = {}


def get_sync_errors():
    """Return copies of latest failures, oldest first; never raw SSH/DB text."""
    with _state_lock:
        return [dict(error) for error in _sync_errors.values()]


def _record_error(scope, identifier, operation, error=None):
    # Use only controlled text. Regex redaction of arbitrary exception strings
    # cannot reliably remove passwords, public keys, or SQL bound parameters.
    kind = type(error).__name__ if error is not None else ""
    if kind not in {"TimeoutError", "ConnectionError", "ValueError", "RuntimeError",
                    "PermissionDenied", "HostKeyNotVerifiable", "OSError",
                    "OperationalError", "IntegrityError", "CancelledError"}:
        kind = "Error" if error is not None else ""
    hints = {
        "TimeoutError": "连接或远端命令超时，请检查主机地址、端口、网络及服务器负载。",
        "PermissionDenied": "SSH 认证被拒绝，请检查服务账号、私钥和目标 authorized_keys。",
        "HostKeyNotVerifiable": "SSH 主机密钥未受信任或已变化；请核对指纹并更新后端 known_hosts。",
        "OSError": "无法访问目标主机，请检查 DNS、网络、端口和 SSH 服务。",
        "OperationalError": "数据库连接失败或正忙，请检查数据库服务和磁盘。",
        "IntegrityError": "数据库关联约束冲突，请检查账号与设备记录。",
        "ValueError": "账号名称或远端采集数据格式不符合预期，请检查登记信息与系统工具版本。",
    }
    reason = error.safe_message if isinstance(error, (SyncCommandError, ActivityTimeError, GatewayKeyError)) else hints.get(kind, "")
    if scope == "gateway-key" and error is not None and not reason:
        reason = "请检查数据库服务、目标账号、SSH 网络及非交互 sudo 配置；网关公钥同步将在 5 分钟后重试。"
    message = operation + (f" ({kind})" if kind else "") + (f"：{reason}" if reason else "")
    identifier = identifier if isinstance(identifier, int) else None
    with _state_lock:
        key = (scope, identifier)
        _sync_errors.pop(key, None)
        _sync_errors[key] = {
            "scope": scope, "id": identifier, "message": message,
            "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        while len(_sync_errors) > MAX_SYNC_ERRORS:
            _sync_errors.popitem(last=False)
    logger.error("%s %s: %s", scope, identifier, message)


def _clear_error(scope, identifier):
    with _state_lock:
        _sync_errors.pop((scope, identifier), None)


def request_server_refresh(server_id):
    """Synchronous, non-blocking API hook; force real collection next cycle.

    A refresh requested during an in-flight collection is retained for another
    collection, rather than overwritten by that task's completion timestamp.
    """
    with _state_lock:
        last_server_collect_date.pop(server_id, None)
        _refresh_requested.add(server_id)


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _snapshot(row, *fields):
    return SimpleNamespace(**{field: getattr(row, field) for field in fields})


def _task_finished(task, scope, identifier):
    _sync_tasks.discard(task)
    key = (scope, identifier)
    if _worker_tasks.get(key) is task:
        _worker_tasks.pop(key, None)
        if scope == "account":
            syncing_accounts.pop(identifier, None)
        elif scope == "server":
            last_server_collecting.pop(identifier, None)
    if task.cancelled():
        return
    error = task.exception()  # Retrieve every exception, including watcher exit.
    if error is not None:
        _record_error(scope, identifier, "Background task failed", error)


def _spawn(coroutine, scope, identifier):
    task = asyncio.create_task(coroutine)
    _sync_tasks.add(task)
    _worker_tasks[(scope, identifier)] = task
    task.add_done_callback(lambda done: _task_finished(done, scope, identifier))
    return task


def startWatcher():
    """Idempotently start and retain the watcher task on the running loop."""
    global start_watcher, _watcher_task
    if _watcher_task is not None and not _watcher_task.done():
        return _watcher_task
    start_watcher = True
    _watcher_task = asyncio.get_running_loop().create_task(watchAccountSync())
    _watcher_task.add_done_callback(lambda task: _task_finished(task, "watcher", None))
    return _watcher_task


async def stopWatcher():
    """Stop scheduling, then await all work (including SSH close/DB cleanup)."""
    global start_watcher, _watcher_task
    start_watcher = False
    watcher = _watcher_task
    if watcher is not None:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    # Keep strong references until awaited. Do not cancel workers: an account
    # may be half-configured remotely; commands have bounded SSH timeouts.
    while _sync_tasks:
        await asyncio.gather(*tuple(_sync_tasks), return_exceptions=True)
    if _watcher_task is watcher:
        _watcher_task = None
    logger.info("All sync tasks finished. Watcher stopped.")


def _known_hosts_options():
    if os.getenv("SSH_INSECURE_SKIP_HOST_KEY_CHECK", "").lower() == "true":
        return {"known_hosts": None}
    path = os.getenv("SSH_KNOWN_HOSTS")
    return {"known_hosts": os.path.expanduser(path)} if path else {}


@asynccontextmanager
async def getConnection(srv: Server):
    # Resolve relationships while attached, but release DB before awaiting SSH.
    with _session() as db:
        server = db.query(Server).filter(Server.id == srv.id).first()
        if server is None:
            raise RuntimeError("Server no longer exists")
        host, port = server.host, server.port
        proxy = server.proxy_server
        proxy_address = (proxy.host, proxy.port) if proxy is not None else None
    conn = None
    tunnel = None
    options = _known_hosts_options()
    try:
        if proxy_address:
            # An explicit SSH connection verifies the jump host as well and
            # avoids generating SSH config files from untrusted host strings.
            tunnel = await asyncio.wait_for(asyncssh.connect(
                host=proxy_address[0], port=proxy_address[1], **options), timeout=6)
        kwargs = dict(options)
        if tunnel is not None:
            kwargs["tunnel"] = tunnel
        conn = await asyncio.wait_for(asyncssh.connect(host=host, port=port, **kwargs), timeout=6)
        yield conn
    finally:
        # Close both even when a command, cancellation, or close itself fails.
        try:
            if conn is not None:
                try:
                    conn.close()
                finally:
                    await conn.wait_closed()
        finally:
            if tunnel is not None:
                try:
                    tunnel.close()
                finally:
                    await tunnel.wait_closed()


async def doSyncAccount(user: User, server: Server, account: Account):
    validate_account_name(user.account_name)
    async with semaphore:
        async with getConnection(server) as conn:
            exists = await sshAccountIsExists(conn, user.account_name)
            if not account.is_login_able:
                # Never create a missing account solely to revoke it.
                if exists:
                    success, _ = await sshAccountDisable(conn, user.account_name)
                    if not success:
                        raise RuntimeError("Account revocation failed")
                return
            if not exists:
                success, _ = await sshAccountCreate(conn, user.account_name)
                if not success:
                    raise RuntimeError("Account creation failed")
            else:
                # A useradd can succeed before password initialization fails.
                # Recover ONLY a root-marked creation owned by this platform;
                # unrelated/completed existing accounts are never reset.
                success, _ = await sshAccountInitializePassword(conn, user.account_name)
                if not success:
                    raise RuntimeError("Pending account initialization failed")
            old_keys = await sshAccountGetAuthorizedKeys(conn, user.account_name)
            # Preserve existing keys: production accounts may use keys which
            # were installed independently of this service.
            keys = list(dict.fromkeys(key for key in (old_keys + "\n" + (user.public_key or "")).splitlines() if key.strip()))
            success, _ = await sshAccountEnable(conn, user.account_name, "\n".join(keys))
            if not success:
                raise RuntimeError("Account enable failed")
            operation = sshAccountSudo if account.is_sudo else sshAccountUnsudo
            success, _ = await operation(conn, user.account_name)
            if not success:
                raise RuntimeError("Account sudo update failed")


async def syncAccount(user: User, server: Server, account: Account):
    success = False
    identifier = account.id
    try:
        if not identifier:
            raise ValueError("Missing account id")
        await doSyncAccount(user, server, account)
        success = True
    except asyncio.CancelledError as error:
        _record_error("account", identifier, "Account synchronization interrupted", error)
        raise
    except Exception as error:
        _record_error("account", identifier, "Account synchronization failed", error)
    finally:
        try:
            with _session() as db:
                row = db.query(Account).filter(Account.id == identifier).first()
                if row is None:
                    raise RuntimeError("Account no longer exists")
                # Preserve a concurrent edit's DIRTY marker, not stale success.
                if row.status != AccountStatus.DIRTY:
                    row.status = AccountStatus.ACTIVE if success else AccountStatus.DIRTY
                db.commit()
            if success:
                _clear_error("account", identifier)
        except Exception as error:
            _record_error("account", identifier, "Account status persistence failed", error)
        finally:
            syncing_accounts.pop(identifier, None)


def _set_server_status(identifier, status):
    with _session() as db:
        row = db.query(Server).filter(Server.id == identifier).first()
        if row is None:
            raise RuntimeError("Server no longer exists")
        row.server_status = status
        db.commit()


async def _collect_hardware(conn, server):
    # Optional tools/driver failures stay local to each category, not fatal to
    # OS/interface/login-history collection or to other hardware categories.
    try:
        snapshot = await collect_hardware(conn)
    except Exception:
        snapshot = failed_snapshot("硬件采集发生内部错误，请管理员检查采集器及系统工具。")
    with _session() as db:
        if not db.get(Server, server.id):
            raise RuntimeError("Server no longer exists")
        save_hardware(db, server.id, snapshot)
        db.commit()
    failures = [f"{name}: {item.get('error') or '无法完整采集'}" for name, item in snapshot.items()
                if item.get("status") != "ok" or item.get("error")]
    if failures:
        # Collector errors are controlled text, never raw command/stderr.
        _record_error("hardware", server.id, "；".join(failures))
    else:
        _clear_error("hardware", server.id)


async def _collect_server(conn, server):
    # Collect before opening a transaction; no DB session spans SSH awaits.
    kernel = await sshServerGetKernel(conn)
    release = await sshServerGetRelease(conn)
    nics = await sshServerGetNICs(conn)
    ib_nics = await sshServerGetIBNICs(conn)
    with _session() as db:
        row = db.query(Server).filter(Server.id == server.id).first()
        if row is None:
            raise RuntimeError("Server no longer exists")
        row.kernel_version, row.os_version = kernel, release
        for nic in nics + ib_nics:
            interface = db.query(ServerInterface).filter(
                ServerInterface.server_id == server.id,
                ServerInterface.pci_address == nic["pci_address"],
            ).first()
            if interface is None:
                interface = ServerInterface(server_id=server.id, pci_address=nic["pci_address"])
                db.add(interface)
            interface.interface = nic["interface_name"] or "No Name"
            interface.manufacturer = nic["nic_name"]
            db.flush()
        accounts = [
            (account.id, account.user.account_name if account.user else None)
            for account in db.query(Account).filter(
                Account.server_id == server.id, Account.is_login_able == True).all()
        ]
        db.commit()
    failed = False
    for identifier, name in accounts:
        _account_activity_checks.pop(identifier, None)
        try:
            status, login_date = await sshServerGetAccountLoginDate(conn, name)
            if not status:
                raise RuntimeError("Login history command failed")
            # None means no available wtmp record, not 1970 or today's midnight.
            # Keep the existing/grace value, but do not auto-revoke on absent evidence.
            if login_date is None:
                continue
            date = validate_activity(login_date)
            with _session() as db:
                row = db.query(Account).filter(Account.id == identifier).first()
                if row is None:
                    raise RuntimeError("Account no longer exists")
                if row.last_login_date is not None and as_utc(row.last_login_date) > utc_now() + datetime.timedelta(minutes=5):
                    raise ActivityTimeError("数据库中的历史活动时间超前，请管理员核对旧时间记录与平台时区；不会自动覆盖或据此回收账号。")
                if row.last_login_date is None or as_utc(row.last_login_date) < date:
                    row.last_login_date = to_db_time(date)
                db.commit()
                _account_activity_checks[identifier] = (utc_now(), row.last_login_date)
        except Exception as error:
            failed = True
            _record_error("server", server.id, "Account login history collection failed", error)
    return not failed


async def syncServer(server: Server):
    identifier = server.id
    connected = False
    with _state_lock:
        _refresh_requested.discard(identifier)
        # A failed collection must remain due even if called explicitly before
        # the previous successful collection's normal hourly deadline.
        last_server_collect_date.pop(identifier, None)
    try:
        if not identifier:
            raise ValueError("Missing server id")
        # A failed current collection invalidates older successful checks too.
        with _session() as db:
            for (account_id,) in db.query(Account.id).filter(Account.server_id == identifier).all():
                _account_activity_checks.pop(account_id, None)
        async with semaphore:
            async with getConnection(server) as conn:
                connected = True
                await _collect_hardware(conn, server)
                if not await _collect_server(conn, server):
                    _set_server_status(identifier, ServerStatus.NO_PERMISSION)
                    return  # Inner failures remain visible and immediately due.
            _set_server_status(identifier, ServerStatus.ACTIVE)
        with _state_lock:
            if identifier not in _refresh_requested:
                last_server_collect_date[identifier] = datetime.datetime.now()
        _clear_error("server", identifier)
    except asyncio.CancelledError as error:
        _record_error("server", identifier, "Server collection interrupted", error)
        raise
    except Exception as error:
        _record_error("server", identifier, "Server data collection failed" if connected else "SSH connection failed", error)
        if not connected and identifier:
            try:
                with _session() as db:
                    if db.get(Server, identifier):
                        save_hardware(db, identifier, failed_snapshot("SSH 连接失败，硬件未更新；旧值如有保留，不代表当前状态。"))
                        db.commit()
            except Exception as snapshot_error:
                _record_error("hardware", identifier, "硬件采集失败状态无法保存", snapshot_error)
        try:
            _set_server_status(identifier, ServerStatus.NO_PERMISSION if connected else ServerStatus.UNABLE_TO_REACH)
        except Exception as status_error:
            _record_error("server", identifier, "Server status persistence failed", status_error)
    finally:
        last_server_collecting.pop(identifier, None)


def gateway_key_sync_enabled():
    """Optional gate; cannot override the global SYNC_ENABLED watcher switch."""
    return (os.getenv("SYNC_ENABLED", "true").lower() == "true"
            and os.getenv("GATEWAY_KEY_SYNC_ENABLED", "true").lower() == "true")


def _gateway_accounts(db, *, provisioned=True):
    query = db.query(Account).join(User).join(Server, Account.server_id == Server.id).filter(
        User.status == UserStatus.ACTIVE, Account.is_login_able.is_(True), Server.is_gateway.is_(True))
    if provisioned:
        query = query.filter(Account.status == AccountStatus.ACTIVE)
    return query


def _gateway_key_target(identifier):
    # Fresh authorization after any semaphore wait. No attached ORM objects or
    # transactions escape this function into network operations.
    with _session() as db:
        account = _gateway_accounts(db).filter(Account.id == identifier).first()
        if account is None:
            return None
        validate_account_name(account.user.account_name)
        return SimpleNamespace(id=account.id, user_id=account.user_id,
                               server_id=account.server_id, account_name=account.user.account_name)


def _persist_gateway_public_key(target, public_key):
    with _session() as db:
        # Serialize append/merge with per-user permission writers; SQLite needs a
        # write lock too. Re-read desired state and keys AFTER acquiring it.
        if db.query(User.id).filter(User.id == target.user_id).first() is None:
            return False
        user = lock_usage_user(db, target.user_id, require_active=False)
        account = _gateway_accounts(db, provisioned=False).filter(
            Account.id == target.id, Account.user_id == target.user_id,
            Account.server_id == target.server_id).populate_existing().first()
        if account is None or user.account_name != target.account_name:
            return False
        merged, changed = append_gateway_public_key(user.public_key, public_key)
        if changed:
            user.public_key = merged
            # Include UPDATING: in-flight sync's stale success must preserve a
            # future reconciliation with the newly appended key.
            db.query(Account).filter(Account.user_id == user.id, Account.is_login_able.is_(True)).update(
                {Account.status: AccountStatus.DIRTY}, synchronize_session=False)
        db.commit()
        return True


async def syncGatewayKey(identifier):
    """Public-only gateway worker; use _spawn(..., 'gateway-key', account.id)."""
    delay = GATEWAY_KEY_RETRY_SECONDS
    try:
        if not gateway_key_sync_enabled():
            return
        async with semaphore:
            target = _gateway_key_target(identifier)
            if target is None:
                _clear_error("gateway-key", identifier)
                return
            async with getConnection(SimpleNamespace(id=target.server_id)) as conn:
                # Connection establishment awaits too; reject revocation,
                # deletion, renaming or reprovisioning which occurred there.
                if not gateway_key_sync_enabled() or _gateway_key_target(identifier) != target:
                    _clear_error("gateway-key", identifier)
                    return
                public_key = await sshGatewayPublicKey(conn, target.account_name)
            if _persist_gateway_public_key(target, public_key):
                delay = GATEWAY_KEY_RECHECK_SECONDS
            _clear_error("gateway-key", identifier)
    except asyncio.CancelledError as error:
        _record_error("gateway-key", identifier, "Gateway public-key synchronization interrupted", error)
        raise
    except Exception as error:
        _record_error("gateway-key", identifier, "Gateway public-key synchronization failed", error)
    finally:
        _gateway_key_next_check[identifier] = time.monotonic() + delay


def _schedule_gateway_keys(db):
    if not gateway_key_sync_enabled():
        return
    # Keep retry/recheck state for eligible but currently DIRTY/UPDATING rows;
    # remove deleted/revoked/inactive/non-gateway rows to avoid unbounded state.
    eligible = {row.id: row for row in _gateway_accounts(db, provisioned=False).all()}
    for identifier in tuple(_gateway_key_next_check):
        if identifier not in eligible and ("gateway-key", identifier) not in _worker_tasks:
            _gateway_key_next_check.pop(identifier, None)
            _clear_error("gateway-key", identifier)
    now = time.monotonic()
    for identifier, account in eligible.items():
        if account.status != AccountStatus.ACTIVE or syncing_accounts.get(identifier):
            continue
        if ("gateway-key", identifier) in _worker_tasks:
            continue
        if now < _gateway_key_next_check.get(identifier, 0):
            continue
        _spawn(syncGatewayKey(identifier), "gateway-key", identifier)


def _watch_cycle():
    active = [str(identifier) for identifier, running in syncing_accounts.items() if running]
    if active:
        logger.info("Dump syncing accounts: %s", ",".join(active))
    with _session() as db:
        # Apply desired-state rules before taking worker snapshots, so a user
        # revoked in this cycle cannot be re-enabled by an older snapshot.
        gateways = db.query(Server).filter(Server.is_gateway == True).all()
        active_users = db.query(User).filter(User.status == UserStatus.ACTIVE).all()
        for gateway in gateways:
            for user in active_users:
                account = db.query(Account).filter(Account.user_id == user.id, Account.server_id == gateway.id).first()
                if account is None:
                    db.add(Account(user_id=user.id, server_id=gateway.id, is_sudo=user.is_admin,
                                   is_login_able=True, status=AccountStatus.DIRTY))
                elif account.is_login_able and account.is_sudo != user.is_admin:
                    # Preserve explicit revocation rather than undoing it.
                    account.is_sudo = user.is_admin
                    account.status = AccountStatus.DIRTY
        # The per-user loop uses populate_existing(); flush gateway changes
        # first so its refresh cannot discard provisioning/permission rules.
        db.flush()
        now = utc_now()
        cutoff = now - datetime.timedelta(days=30)
        # Serialize per-user with apply/confirm/revoke APIs, and re-read after
        # acquiring the lock. A confirmation racing with this cycle must not be
        # ignored because a stale snapshot was taken before the lock.
        for user_id, in db.query(User.id).order_by(User.id).all():
            target = lock_usage_user(db, user_id, require_active=False)
            confirmed_servers = {item.server_id for item in db.query(DeviceUsage).filter(
                DeviceUsage.user_id == user_id, DeviceUsage.status == 'active',
                DeviceUsage.confirmed_at >= cutoff.replace(tzinfo=None)
            ).populate_existing().all()}
            accounts = db.query(Account).filter(Account.user_id == user_id).populate_existing().all()
            for account in accounts:
                inactive = target.status == UserStatus.GRADUATED
                check = _account_activity_checks.get(account.id)
                fresh = (check is not None and datetime.timedelta(0) <= now - check[0] <= datetime.timedelta(minutes=5)
                         and check[1] == account.last_login_date)
                expired = (fresh and not account.server.is_gateway and not target.is_admin
                           and account.server_id not in confirmed_servers
                           and account.last_login_date is not None and as_utc(account.last_login_date) < cutoff)
                if (inactive or expired) and account.is_login_able:
                    account.is_login_able = False
                    account.is_sudo = False
                    account.status = AccountStatus.DIRTY
                    end_account_usage(db, account.user_id, account.server_id)
            db.flush()
        db.commit()

        # Recover UPDATING accounts from a previous process/crashed iteration.
        accounts = db.query(Account).filter(Account.status.in_([AccountStatus.DIRTY, AccountStatus.UPDATING])).all()
        for account in accounts:
            if syncing_accounts.get(account.id):
                continue
            identifier = account.id
            user = _snapshot(account.user, "id", "username", "account_name", "public_key")
            server = _snapshot(account.server, "id", "host", "port")
            snapshot = _snapshot(account, "id", "is_login_able", "is_sudo")
            account.status = AccountStatus.UPDATING
            db.commit()
            syncing_accounts[identifier] = True
            _spawn(syncAccount(user, server, snapshot), "account", identifier)

        # Only provisioned ACTIVE gateway accounts can generate personal keys.
        # This runs after gateway rules and account worker scheduling above.
        _schedule_gateway_keys(db)

        for server in db.query(Server).all():
            with _state_lock:
                last = last_server_collect_date.get(server.id)
            if (last is None or last < datetime.datetime.now() - datetime.timedelta(hours=1)) and not last_server_collecting.get(server.id):
                snapshot = _snapshot(server, "id", "host", "port")
                last_server_collecting[server.id] = True
                _spawn(syncServer(snapshot), "server", server.id)


async def watchAccountSync():
    while start_watcher:
        try:
            _watch_cycle()
            _clear_error("watcher", None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _record_error("watcher", None, "Synchronization loop failed; will retry", error)
        # Catch per iteration, not outside the loop: a transient DB failure must
        # not permanently stop reconciliation.
        await asyncio.sleep(WATCH_INTERVAL)
