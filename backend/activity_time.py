"""Activity timestamps: UTC at SSH/API boundaries, legacy platform-local naive DB values.

The existing database stores datetime.now() without a timezone. Keep that storage
convention (no destructive guessing/migration of old remote-local values), but
always compare instants in UTC and return explicit offsets to browsers.
"""
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
CLOCK_SKEW = timedelta(minutes=5)


class ActivityTimeError(ValueError):
    def __init__(self, message):
        self.safe_message = message
        super().__init__(message)


def utc_now():
    return datetime.now(UTC)


def as_utc(value):
    # For naive legacy values, astimezone uses the platform's timezone at that
    # date (including DST), not a fixed offset captured today.
    return value.astimezone(UTC)


def to_db_time(value):
    if value.tzinfo is None:
        raise ActivityTimeError("采集时间缺少时区，拒绝更新；请检查远端 last 输出格式。")
    return value.astimezone().replace(tzinfo=None)


def activity_iso(value):
    return as_utc(value).isoformat().replace("+00:00", "Z") if value is not None else None


def validate_activity(value, now=None):
    now = now or utc_now()
    if value.tzinfo is None:
        raise ActivityTimeError("采集时间缺少时区，拒绝更新；请检查远端 last 输出格式。")
    value = as_utc(value)
    if value > as_utc(now) + CLOCK_SKEW:
        raise ActivityTimeError("远端登录记录时间超前超过5分钟，请检查目标服务器与管理平台的系统时钟/NTP；本次不据此回收账号。")
    return value
