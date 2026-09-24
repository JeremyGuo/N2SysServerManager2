"""Regression cases for real util-linux last states (ISO output, not field indexes)."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
import sys
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity_time import ActivityTimeError, activity_iso, as_utc, to_db_time
from server_helpers import parse_login_history, sshServerGetAccountLoginDate
import server_helpers
from test_sync import db_env, clean_state, collection_success, detached, fake_connection, FakeSSH, result
import account_sync as sync
from app.database import Account, AccountStatus, Server

NOW = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)


def parse(text):
    return parse_login_history(text, "alice", NOW)


def test_normal_logout_is_last_activity_with_explicit_timezone():
    assert parse("alice pts/1 2026-09-21T08:00:00+08:00 - 2026-09-21T09:00:00+08:00 (01:00)") == datetime(2026,9,21,1,tzinfo=timezone.utc)


def test_dst_offset_is_respected_instead_of_wall_clock_order():
    assert parse("alice pts/1 2025-11-02T01:50:00-04:00 - 2025-11-02T01:10:00-05:00 (00:20)") == datetime(2025,11,2,6,10,tzinfo=timezone.utc)


@pytest.mark.parametrize("state", ["- crash (01:00)", "- down (01:00)", "gone - no logout", "- no logout"])
def test_missing_logout_uses_login_not_today_or_reboot(state):
    assert parse(f"alice pts/1 2026-09-01T07:00:00+00:00 {state}") == datetime(2026,9,1,7,tzinfo=timezone.utc)


def test_older_live_session_is_not_missed_by_first_completed_session():
    history = """alice pts/2 2026-09-21T07:00:00+00:00 - 2026-09-21T08:00:00+00:00 (01:00)
alice pts/1 2026-08-01T08:00:00+00:00 still logged in

wtmp begins 2026-07-01T00:00:00+00:00
"""
    assert parse(history) == NOW


@pytest.mark.parametrize("history", ["", "\n\n", "\nwtmp begins 2026-07-01T00:00:00+00:00\n"])
def test_no_history_is_unknown_not_epoch(history):
    assert parse(history) is None


@pytest.mark.parametrize("history", [
    "alice pts/1 Wed Sep 23 07:00:00 2026 - Wed Sep 23 08:00:00 2026 (01:00)",
    "alice pts/1 2026-09-21T07:00:00 - 2026-09-21T08:00:00 (01:00)",
    "alice pts/1 2026-02-30T07:00:00+00:00 - down",
    "alice pts/1 2026-09-24T07:00:00+00:00 still logged in",
    "alice pts/1 2026-09-21T08:00:00+00:00 - 2026-09-21T07:00:00+00:00 (01:00)",
    "alice pts/1 2026-09-21T07:00:00+00:00 unknown-status",
    "bob pts/1 2026-09-21T07:00:00+00:00 still logged in",
])
def test_invalid_future_or_unknown_records_fail_closed(history):
    with pytest.raises(ActivityTimeError):
        parse(history)


def test_who_checks_exact_username_and_uses_platform_clock(monkeypatch):
    monkeypatch.setattr(server_helpers, "utc_now", lambda: NOW)
    conn = FakeSSH([result("alice pts/10 2026-07-01 08:00 (host)\nalice2 pts/9 2026-01-01 08:00 (host)")])
    assert asyncio.run(sshServerGetAccountLoginDate(conn,"alice")) == (True,NOW)
    assert len(conn.calls)==1


def test_nonmatching_who_then_iso_history_command(monkeypatch):
    monkeypatch.setattr(server_helpers, "utc_now", lambda: NOW)
    conn = FakeSSH([result("alice2 pts/1 2026-09-23 08:00"),result("alice pts/1 2026-09-01T07:00:00+00:00 - down")])
    ok, date = asyncio.run(sshServerGetAccountLoginDate(conn,"alice"))
    assert ok and date == datetime(2026,9,1,7,tzinfo=timezone.utc)
    assert "last -w -R --time-format iso -- alice" in conn.calls[1][0]
    assert "TZ=UTC" in conn.calls[1][0] and "head" not in conn.calls[1][0]


def test_unavailable_last_fails_not_no_history():
    conn=FakeSSH([result(), result(status=1,stderr="unsupported flag secret"), result(status=127)])
    with pytest.raises(ActivityTimeError, match="util-linux") as error:
        asyncio.run(sshServerGetAccountLoginDate(conn,"alice"))
    assert "secret" not in str(error.value)


@contextmanager
def local_zone(zone):
    original=os.environ.get("TZ")
    try:
        os.environ["TZ"]=zone
        time.tzset()
        yield
    finally:
        if original is None: os.environ.pop("TZ",None)
        else: os.environ["TZ"]=original
        time.tzset()


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC", "America/New_York"])
def test_storage_and_api_round_trip_handles_platform_timezone(zone):
    with local_zone(zone):
        stored=to_db_time(NOW)
        assert stored.tzinfo is None
        assert as_utc(stored)==NOW
        assert activity_iso(stored)=="2026-09-23T08:00:00Z"
        assert activity_iso(None) is None


def test_collection_no_history_keeps_grace_and_disallows_revoke(db_env,monkeypatch):
    factory,ids,_=db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    with factory() as db:
        before=db.get(Account,ids.account).last_login_date
    monkeypatch.setattr(sync,"sshServerGetAccountLoginDate",AsyncMock(return_value=(True,None)))
    asyncio.run(sync.syncServer(detached(factory,Server,ids.server)))
    with factory() as db:
        assert db.get(Account,ids.account).last_login_date==before
    assert ids.account not in sync._account_activity_checks


def test_collection_stores_instant_and_invalidates_on_later_failure(db_env,monkeypatch):
    factory,ids,_=db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    timestamp=datetime.now(timezone.utc)-timedelta(hours=1)
    with factory() as db:
        db.get(Account,ids.account).last_login_date=to_db_time(timestamp-timedelta(days=60))
        db.commit()
    monkeypatch.setattr(sync,"sshServerGetAccountLoginDate",AsyncMock(return_value=(True,timestamp)))
    asyncio.run(sync.syncServer(detached(factory,Server,ids.server)))
    with factory() as db:
        assert as_utc(db.get(Account,ids.account).last_login_date)==timestamp
    assert ids.account in sync._account_activity_checks
    monkeypatch.setattr(sync,"sshServerGetKernel",AsyncMock(side_effect=RuntimeError("fail")))
    asyncio.run(sync.syncServer(detached(factory,Server,ids.server)))
    assert ids.account not in sync._account_activity_checks


def test_old_data_not_revoked_until_recent_successful_check(db_env,monkeypatch):
    factory,ids,_=db_env
    timestamp=datetime.now(timezone.utc)-timedelta(days=31)
    def discard(coro,*args): coro.close()
    monkeypatch.setattr(sync,"_spawn",discard)
    with factory() as db:
        account=db.get(Account,ids.account)
        account.last_login_date=to_db_time(timestamp)
        account.status=AccountStatus.ACTIVE
        db.commit()
        stored=account.last_login_date
    sync._watch_cycle()
    with factory() as db: assert db.get(Account,ids.account).is_login_able
    sync._account_activity_checks[ids.account]=(datetime.now(timezone.utc),stored)
    sync._watch_cycle()
    with factory() as db: assert not db.get(Account,ids.account).is_login_able


@pytest.mark.parametrize("stale,changed", [(True,False),(False,True)])
def test_stale_check_or_reauthorization_cannot_revoke(db_env,monkeypatch,stale,changed):
    factory,ids,_=db_env
    now=datetime.now(timezone.utc)
    timestamp=to_db_time(now-timedelta(days=31))
    monkeypatch.setattr(sync,"_spawn",lambda coro,*args:coro.close())
    with factory() as db:
        account=db.get(Account,ids.account)
        account.last_login_date=timestamp
        account.status=AccountStatus.ACTIVE
        db.commit()
    sync._account_activity_checks[ids.account]=(now-timedelta(hours=3) if stale else now, timestamp-timedelta(days=1) if changed else timestamp)
    sync._watch_cycle()
    with factory() as db: assert db.get(Account,ids.account).is_login_able


def test_historical_future_db_value_is_reported_and_not_used_for_revoke(db_env,monkeypatch):
    factory,ids,_=db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    future=to_db_time(datetime.now(timezone.utc)+timedelta(days=10))
    with factory() as db:
        db.get(Account,ids.account).last_login_date=future
        db.commit()
    monkeypatch.setattr(sync,"sshServerGetAccountLoginDate",AsyncMock(return_value=(True,datetime.now(timezone.utc))))
    asyncio.run(sync.syncServer(detached(factory,Server,ids.server)))
    with factory() as db:
        assert db.get(Account,ids.account).last_login_date==future
    assert ids.account not in sync._account_activity_checks
    assert any("历史活动时间超前" in e["message"] for e in sync.get_sync_errors())


def test_api_activity_output_is_always_offset_explicit():
    assert activity_iso(datetime(2026,9,23,8,tzinfo=timezone(timedelta(hours=8))))=="2026-09-23T00:00:00Z"


def test_recent_confirmed_usage_protects_long_running_jobs_from_idle_revoke(db_env,monkeypatch):
    from app.database import DeviceUsage
    factory,ids,_=db_env
    now=datetime.now(timezone.utc)
    stored=to_db_time(now-timedelta(days=40))
    monkeypatch.setattr(sync,"_spawn",lambda coro,*args:coro.close())
    with factory() as db:
        account=db.get(Account,ids.account)
        account.last_login_date=stored
        account.status=AccountStatus.ACTIVE
        db.add(DeviceUsage(user_id=ids.user,server_id=ids.server,status='active',reason='long training job',confirmed_at=now.replace(tzinfo=None)))
        db.commit()
    sync._account_activity_checks[ids.account]=(now,stored)
    sync._watch_cycle()
    with factory() as db:
        assert db.get(Account,ids.account).is_login_able
        record=db.query(DeviceUsage).first()
        record.confirmed_at=(now-timedelta(days=31)).replace(tzinfo=None)
        db.commit()
    sync._watch_cycle()
    with factory() as db:
        assert not db.get(Account,ids.account).is_login_able
        assert db.query(DeviceUsage).first().status=='ended'
