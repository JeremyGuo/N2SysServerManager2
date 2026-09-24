"""Read-only login fallback tests. Fake SSH only; no app/database imports."""
import asyncio
from datetime import datetime, timedelta, timezone
import locale
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server_helpers
from activity_time import ActivityTimeError
from server_helpers import parse_lastlog, sshServerGetAccountLoginDate
from sync_errors import SyncCommandError

NOW = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
LOGIN = datetime(2026, 9, 23, 9, 10, 20, tzinfo=timezone.utc)
HEADER = "Username         Port     From             Latest\n"
DATE = "Tue Sep 23 09:10:20 +0000 2026"
HISTORY = "alice pts/1 2026-09-01T07:00:00+00:00 - down"


def result(stdout="", status=0, stderr=""):
    return SimpleNamespace(stdout=stdout, exit_status=status, stderr=stderr)


class FakeSSH:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        assert self.responses, "Unexpected SSH command"
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture(autouse=True)
def platform_clock_and_no_real_ssh(monkeypatch):
    monkeypatch.setattr(server_helpers, "utc_now", lambda: NOW)

    async def forbidden(*args, **kwargs):
        pytest.fail("Real SSH forbidden")

    monkeypatch.setattr(server_helpers.asyncssh, "connect", forbidden)


def collect(responses, user="alice"):
    conn = FakeSSH(responses)
    value = asyncio.run(sshServerGetAccountLoginDate(conn, user))
    return value, conn


@pytest.mark.parametrize("columns", ["pts/0 example.org", "pts/0", "example.org", "", "pts/0 2001:db8::1"])
@pytest.mark.parametrize("zone", ["+0000", "UTC"])
def test_full_date_suffix_ignores_missing_tty_or_host_columns(columns, zone):
    # Weekday spelling is recognized but not used to reinterpret the date.
    output = HEADER + f"alice    {columns}    {DATE.replace('+0000', zone)}\n"
    assert parse_lastlog(output, "alice", NOW) == LOGIN


@pytest.mark.parametrize("name", ["alice", "a_very_long_account_name_123456789", "user-name", "_service"])
def test_exact_requested_username_is_supported(name):
    assert parse_lastlog(HEADER + f"{name} {DATE}", name, NOW) == LOGIN


@pytest.mark.parametrize("zone, expected", [
    ("+0800", LOGIN - timedelta(hours=8)),
    ("-0400", LOGIN + timedelta(hours=4)),
    ("+0530", LOGIN - timedelta(hours=5, minutes=30)),
])
def test_explicit_numeric_offset_is_converted_to_utc(zone, expected):
    value = parse_lastlog(HEADER + f"alice pts/0 host {DATE.replace('+0000', zone)}", "alice", NOW)
    assert value == expected
    assert value.tzinfo is timezone.utc


@pytest.mark.parametrize("month, number", list(server_helpers._LASTLOG_MONTHS.items()))
def test_month_map_does_not_use_global_locale(month, number, monkeypatch):
    def forbidden(*args):
        pytest.fail("Process-global locale must not be read or changed")
    monkeypatch.setattr(locale, "setlocale", forbidden)
    assert parse_lastlog(HEADER + f"alice Mon {month}  2 03:04:05 UTC 2025", "alice", NOW) == datetime(
        2025, number, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires tzset")
@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "America/New_York"])
def test_platform_timezone_cannot_change_lastlog_instant(zone):
    original = os.environ.get("TZ")
    try:
        os.environ["TZ"] = zone
        time.tzset()
        assert parse_lastlog(HEADER + f"alice {DATE}", "alice", NOW) == LOGIN
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


@pytest.mark.parametrize("marker", ["**Never logged in**", "Never logged in"])
def test_recognized_never_logged_in_is_none_not_epoch(marker):
    assert parse_lastlog(HEADER + f"alice    {marker}\n", "alice", NOW) is None


@pytest.mark.parametrize("output", [
    "", HEADER, "alice " + DATE,
    HEADER + "bob " + DATE,
    HEADER + "alice2 " + DATE,
    HEADER + "alice",
    HEADER + "alice never logged in",
    HEADER + "alice **Never logged in** unrecognized",
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 2026",  # No explicit timezone.
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 CST 2026",  # Ambiguous zone.
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 +2400 2026",
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 +0060 2026",
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 +00:00 2026",  # Not conventional lastlog.
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 -0000 2026 trailing",
    HEADER + "alice pts/0 host Tue Feb 30 09:10:20 UTC 2026",
    HEADER + "alice pts/0 host Tue Sep 23 24:10:20 UTC 2026",
    HEADER + "alice pts/0 host Tue Sep 23 09:60:20 UTC 2026",
    HEADER + "alice pts/0 host Tue Sep 23 09:10:60 UTC 2026",
    HEADER + "alice pts/0 host Tue Sep 23 09:10:20 UTC 0000",
    HEADER + "alice pts/0 host Tue Sep 25 09:10:20 UTC 2026",  # Future.
    HEADER + "alice pts/0 host Tue Foo 23 09:10:20 UTC 2026",
    HEADER + "alice pts/0 host Tue Sept 23 09:10:20 UTC 2026",
    HEADER + "alice pts/0 host 1790154620",  # Raw epoch is not a date format.
    HEADER + "alice " + DATE + "\nbob " + DATE,
    HEADER + "alice " + DATE + "\nalice **Never logged in**",
    "warning secret\n" + HEADER + "alice " + DATE,
])
def test_malformed_mismatched_or_future_lastlog_fails_closed(output):
    with pytest.raises(ActivityTimeError) as error:
        parse_lastlog(output, "alice", NOW)
    assert "secret" not in str(error.value)


def test_truncated_username_cannot_supply_a_different_accounts_record():
    with pytest.raises(ActivityTimeError, match="用户名"):
        parse_lastlog(HEADER + "longacco " + DATE, "longaccountname", NOW)


@pytest.mark.parametrize("empty", ["", "\n\n", "\nwtmp begins 2026-07-01T00:00:00+00:00\n"])
def test_empty_last_uses_lastlog_and_all_commands_are_read_only_and_bounded(empty):
    value, conn = collect([result(), result(empty), result(HEADER + "alice pts/1 host " + DATE)])
    assert value == (True, LOGIN)
    assert conn.calls == [
        ("LC_ALL=C TZ=UTC who", {"timeout": 6}),
        ("LC_ALL=C TZ=UTC last -w -R --time-format iso -- alice", {"timeout": 6}),
        ("LC_ALL=C TZ=UTC lastlog -u alice", {"timeout": 6}),
    ]


@pytest.mark.parametrize("status", [1, 2, 126, 127])
def test_unavailable_or_failing_last_can_use_reliable_lastlog(status):
    value, conn = collect([result(), result(status=status, stderr="secret"), result(HEADER + "alice " + DATE)])
    assert value == (True, LOGIN)
    assert len(conn.calls) == 3


@pytest.mark.parametrize("last_status", [0, 1, 127])
def test_reliable_lastlog_never_is_none_even_when_last_unavailable(last_status):
    value, _ = collect([result(), result(status=last_status), result(HEADER + "alice **Never logged in**")])
    assert value == (True, None)


def test_valid_empty_last_and_uninstalled_lastlog_preserves_unknown_date():
    value, _ = collect([result(), result(), result(status=127, stderr="secret: command not found")])
    assert value == (True, None)


@pytest.mark.parametrize("last_status, lastlog_status", [(0, 1), (0, 126), (0, 2), (1, 127), (127, 127), (1, 1), (127, 126)])
def test_unreliable_fallback_is_actionable_and_never_exposes_stderr(last_status, lastlog_status):
    conn = FakeSSH([result(), result(status=last_status, stderr="last-secret"),
                    result(status=lastlog_status, stderr="lastlog-secret")])
    with pytest.raises(ActivityTimeError, match="util-linux") as error:
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert "lastlog" in str(error.value)
    assert "secret" not in str(error.value)
    assert len(conn.calls) == 3


@pytest.mark.parametrize("last_status", [0, 1, 127])
@pytest.mark.parametrize("fallback", ["", HEADER, HEADER + "alice secret invalid", HEADER + "alice Fri Sep 25 09:10:20 UTC 2026"])
def test_invalid_fallback_is_not_treated_as_never(last_status, fallback):
    with pytest.raises(ActivityTimeError) as error:
        collect([result(), result(status=last_status), result(fallback)])
    assert "secret" not in str(error.value)


def test_valid_last_remains_authoritative_without_querying_newer_lastlog():
    value, conn = collect([result("alice2 pts/0 host"), result(HISTORY), result(HEADER + "alice " + DATE)])
    assert value == (True, datetime(2026, 9, 1, 7, tzinfo=timezone.utc))
    assert len(conn.calls) == 2
    assert len(conn.responses) == 1


@pytest.mark.parametrize("history", [
    "alice pts/0 Wed Sep 23 07:00:00 2026 - down",
    "alice pts/0 2026-09-23T07:00:00 - down",
    "alice pts/0 2026-02-30T07:00:00+00:00 - down",
    "alice pts/0 2026-09-25T07:00:00+00:00 still logged in",
    "alice pts/0 2026-09-01T07:00:00+00:00 - 2026-09-25T07:00:00+00:00 (24+00:00)",
    "alice pts/0 2026-09-23T08:00:00+00:00 - 2026-09-23T07:00:00+00:00 (01:00)",
    "bob pts/0 2026-09-23T07:00:00+00:00 - down",
    HISTORY + "\nalice pts/0 2026-09-25T07:00:00+00:00 - down",
])
def test_invalid_or_future_last_is_never_hidden_by_lastlog(history):
    conn = FakeSSH([result(), result(history), result(HEADER + "alice " + DATE)])
    with pytest.raises(ActivityTimeError):
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert len(conn.calls) == 2
    assert len(conn.responses) == 1


def test_live_who_uses_platform_now_without_history_commands():
    value, conn = collect([result("alice pts/10 2026-07-01 08:00 (host)")])
    assert value == (True, NOW)
    assert len(conn.calls) == 1


def test_failed_who_is_not_hidden_by_last_or_lastlog():
    conn = FakeSSH([result("alice pts/0 host", status=1, stderr="who-secret"), result(HISTORY)])
    with pytest.raises(SyncCommandError) as error:
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert "who-secret" not in str(error.value)
    assert len(conn.calls) == 1


@pytest.mark.parametrize("user", ["alice;id", "alice\nwho", "-alice", "", "a" * 33, None, "Alice"])
def test_account_validation_precedes_every_ssh_command(user):
    conn = FakeSSH([])
    with pytest.raises(ValueError, match="Invalid Linux account name"):
        asyncio.run(sshServerGetAccountLoginDate(conn, user))
    assert conn.calls == []


@pytest.mark.parametrize("stage", [0, 1, 2])
@pytest.mark.parametrize("failure", [TimeoutError("timeout-secret"), OSError("transport-secret")])
def test_command_transport_errors_are_safe_and_never_none(stage, failure):
    conn = FakeSSH([result()] * stage + [failure])
    with pytest.raises(ActivityTimeError) as error:
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert "secret" not in str(error.value)
    assert len(conn.calls) == stage + 1


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_external_wait_for_bounds_commands_even_if_conn_ignores_timeout(stage, monkeypatch):
    monkeypatch.setattr(server_helpers, "_ACTIVITY_COMMAND_TIMEOUT", 0.01)

    class HungSSH(FakeSSH):
        cancelled = False

        async def run(self, command, **kwargs):
            if len(self.calls) < stage:
                return await super().run(command, **kwargs)
            self.calls.append((command, kwargs))
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    conn = HungSSH([result()] * stage)
    with pytest.raises(ActivityTimeError, match="超时"):
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert conn.cancelled
    assert len(conn.calls) == stage + 1


def test_task_cancellation_propagates_without_running_fallback():
    conn = FakeSSH([result(), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sshServerGetAccountLoginDate(conn, "alice"))
    assert len(conn.calls) == 2
