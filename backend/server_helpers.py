"""Read-only SSH collection helpers. Command failures must reach the sync layer."""
import asyncio
import shlex
import re
from datetime import datetime, timedelta, timezone
from activity_time import ActivityTimeError, utc_now, validate_activity

import asyncssh
from sync_errors import command_failure
from account_helpers import validate_account_name


async def _run(conn, command, timeout=3):
    result = await conn.run(command, timeout=timeout)
    if result.exit_status != 0:
        # Never propagate remote stderr (which may echo sensitive input).
        raise command_failure(command, result, collection=True)
    return result.stdout.strip()


async def sshServerGetKernel(conn: asyncssh.SSHClientConnection) -> str:
    return await _run(conn, "uname -r")


async def sshServerGetRelease(conn: asyncssh.SSHClientConnection) -> str:
    release = await _run(conn, "cat /etc/os-release")
    values = dict(line.split("=", 1) for line in release.splitlines() if "=" in line)
    return values.get("PRETTY_NAME", values.get("NAME", "")).strip('"\'')


async def _get_nics(conn, kind):
    # Filter locally: grep exit 1 means no NICs, but also used to hide lspci
    # failures. Missing tooling/permissions must now be reported and retried.
    pci = await _run(conn, "lspci -D")
    nics = []
    for line in pci.splitlines():
        if kind not in line.lower():
            continue
        parts = line.split()
        if len(parts) >= 2:
            nics.append({"pci_address": parts[0], "nic_name": " ".join(parts[1:]), "interface_name": None})
    interfaces = await _run(conn, "ls -1 /sys/class/net")
    for interface in interfaces.splitlines():
        path = shlex.quote(f"/sys/class/net/{interface}/device")
        result = await conn.run(f"readlink -- {path}", timeout=3)
        if result.exit_status != 0:
            continue  # Virtual interfaces (e.g. lo) have no PCI device.
        pci_address = result.stdout.strip().split("/")[-1]
        for nic in nics:
            if nic["pci_address"] == pci_address:
                nic["interface_name"] = interface
                break
    return nics


async def sshServerGetIBNICs(conn: asyncssh.SSHClientConnection) -> list[dict]:
    return await _get_nics(conn, "infiniband")


async def sshServerGetNICs(conn: asyncssh.SSHClientConnection) -> list[dict]:
    return await _get_nics(conn, "ethernet")


_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.]\d+)?(?:Z|[+-]\d{2}:?\d{2})")


def parse_login_history(output: str, user: str, observed_at=None):
    """Return last activity (UTC aware) or None when wtmp contains no records.

    Normal logout is activity; for crash/down/missing logout use known login,
    not the reboot time or a fabricated 'today'. Check every returned session.
    The caller forces LC_ALL=C and ISO format; unknown formats fail closed.
    """
    observed_at = observed_at or utc_now()
    latest = None
    online = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("wtmp begins "):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[0] != user:
            raise ActivityTimeError("无法识别 last 登录记录（用户名或格式不匹配）；请检查 util-linux last 版本，不会据此回收账号。")
        stamps = list(_ISO_TIME.finditer(line))
        if not stamps or len(stamps) > 2:
            raise ActivityTimeError("last 未返回包含时区的 ISO 时间，请确认远端支持 --time-format iso。")
        try:
            login = validate_activity(datetime.fromisoformat(stamps[0].group().replace("Z", "+00:00")), observed_at)
        except ValueError as error:
            if isinstance(error, ActivityTimeError):
                raise
            raise ActivityTimeError("last 登录日期无效，请检查远端系统时钟和登录记录。") from None
        tail = line[stamps[0].end():].strip()
        if tail == "still logged in":
            online = True
            activity = observed_at
        elif tail.startswith("-") and len(stamps) == 2:
            try:
                activity = validate_activity(datetime.fromisoformat(stamps[1].group().replace("Z", "+00:00")), observed_at)
            except ValueError as error:
                if isinstance(error, ActivityTimeError):
                    raise
                raise ActivityTimeError("last 退出日期无效，请检查远端系统时钟和登录记录。") from None
            if activity < login:
                raise ActivityTimeError("last 退出时间早于登录时间，请检查远端时钟/NTP；本次不据此回收账号。")
        elif tail.startswith(("- crash", "- down", "gone - no logout", "- no logout")) and len(stamps) == 1:
            activity = login
        else:
            raise ActivityTimeError("无法识别 last 会话状态，请检查登录记录格式；本次不据此回收账号。")
        latest = activity if latest is None else max(latest, activity)
    return observed_at if online else latest


# Do not use strptime: its month/weekday parsing depends on process-global locale.
_LASTLOG_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_LASTLOG_DATE = re.compile(
    r"(?:^|\s)(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>[0-9]{1,2})\s+(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})\s+"
    r"(?P<zone>UTC|[+-](?:[01][0-9]|2[0-3])[0-5][0-9])\s+(?P<year>[0-9]{4})$",
    re.ASCII,
)
_ACTIVITY_COMMAND_TIMEOUT = 6


def parse_lastlog(output: str, user: str, observed_at=None):
    """Read one conventional C-locale lastlog row, never remote-local/epoch dates.

    Match the complete date suffix rather than tty/hostname column positions:
    either or both optional columns may be empty. Require an explicit numeric
    offset or UTC even though the command also forces TZ=UTC. Header-only output
    (e.g. an unknown account) is not evidence that the user never logged in.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 2 or lines[0].split() != ["Username", "Port", "From", "Latest"]:
        raise ActivityTimeError("无法识别 lastlog 登录记录，请检查 lastlog 版本、账号及读取权限；不会将无效输出视为从未登录。")
    fields = lines[1].split(None, 1)
    if len(fields) != 2 or fields[0] != user:
        raise ActivityTimeError("lastlog 用户名或格式不匹配，请检查账号名是否被截断；本次不据此回收账号。")
    if fields[1] in ("**Never logged in**", "Never logged in"):
        return None
    stamp = _LASTLOG_DATE.search(fields[1])
    if stamp is None:
        raise ActivityTimeError("lastlog 未返回可识别的完整日期和明确时区，请检查 LC_ALL=C、TZ=UTC 及 lastlog 版本。")
    try:
        zone = stamp["zone"]
        offset = timedelta() if zone == "UTC" else timedelta(
            hours=int(zone[1:3]), minutes=int(zone[3:5])) * (1 if zone[0] == "+" else -1)
        value = datetime(int(stamp["year"]), _LASTLOG_MONTHS[stamp["month"]], int(stamp["day"]),
                         int(stamp["hour"]), int(stamp["minute"]), int(stamp["second"]),
                         tzinfo=timezone(offset))
    except ValueError:
        raise ActivityTimeError("lastlog 登录日期无效，请检查远端系统时钟和登录记录；本次不据此回收账号。") from None
    return validate_activity(value, observed_at)


async def _run_activity_command(conn, command, tool):
    # Bound both the SSH process and the await (including channel setup). Do not
    # expose remote output or exception text, and let task cancellation propagate.
    try:
        return await asyncio.wait_for(
            conn.run(command, timeout=_ACTIVITY_COMMAND_TIMEOUT),
            timeout=_ACTIVITY_COMMAND_TIMEOUT,
        )
    except (asyncio.TimeoutError, asyncssh.Error, OSError):
        raise ActivityTimeError(
            f"读取登录活动（{tool}）超时或 SSH 连接失败，请检查网络、远端命令和读取权限；不会将失败视为从未登录。"
        ) from None


async def sshServerGetAccountLoginDate(conn: asyncssh.SSHClientConnection, user: str) -> tuple[bool, datetime | None]:
    user = validate_account_name(user)
    # who checks all current sessions, including a long-lived session older than
    # the newest completed login (which the old head -1 approach missed).
    who_command = "LC_ALL=C TZ=UTC who"
    sessions = await _run_activity_command(conn, who_command, "who")
    if sessions.exit_status != 0:
        raise command_failure(who_command, sessions, collection=True)
    observed_at = utc_now()  # Platform clock, not potentially skewed remote date.
    if any(line.split() and line.split()[0] == user for line in sessions.stdout.splitlines()):
        return True, observed_at
    # -w prevents username truncation; -R removes hostname columns. ISO carries
    # timezone explicitly and is not affected by locale/month names or DST.
    command = f"LC_ALL=C TZ=UTC last -w -R --time-format iso -- {shlex.quote(user)}"
    result = await _run_activity_command(conn, command, "last")
    history_available = result.exit_status == 0
    if history_available:
        # Parse errors/future dates must propagate, not be hidden by lastlog.
        activity = parse_login_history(result.stdout, user, observed_at)
        if activity is not None:
            return True, activity
    # Only absent or unavailable wtmp history uses lastlog; a valid last result
    # remains authoritative (we do not claim to reconcile newer lastlog entries).
    fallback = await _run_activity_command(
        conn, f"LC_ALL=C TZ=UTC lastlog -u {shlex.quote(user)}", "lastlog")
    if fallback.exit_status == 0:
        return True, parse_lastlog(fallback.stdout, user, observed_at)
    if history_available and fallback.exit_status == 127:
        # A successful empty wtmp read is still an unknown date when the optional
        # lastlog command is not installed. Other lastlog errors are actionable.
        return True, None
    raise ActivityTimeError(
        "读取登录记录失败且 lastlog 无可靠结果，请检查 wtmp/lastlog 读取权限、util-linux last 是否安装且支持 --time-format iso、lastlog 是否可用；不会将命令失败视为从未登录。"
    )
