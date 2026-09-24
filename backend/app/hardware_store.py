"""Latest hardware snapshot, retaining last good values across partial failures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.database import ServerHardware

SECTIONS = ("cpu", "memory", "gpus", "disks", "network")


def unknown_section():
    return {"status": "unknown", "data": None, "error": "尚未采集硬件信息，请管理员启用 SSH 同步或请求刷新。",
            "collected_at": None, "checked_at": None, "stale": True}


def normalized_snapshot(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    result = {}
    for name in SECTIONS:
        section = deepcopy((snapshot or {}).get(name) or unknown_section())
        section.setdefault("status", "unknown")
        section.setdefault("data", None)
        section.setdefault("error", None)
        section.setdefault("checked_at", None)
        section.setdefault("collected_at", None)
        try:
            collected = datetime.fromisoformat(section["collected_at"].replace("Z", "+00:00"))
            recent = collected.tzinfo is not None and timedelta(0) <= now - collected <= timedelta(hours=2)
        except (ValueError, TypeError, AttributeError):
            recent = False
        section["stale"] = section["status"] != "ok" or not recent
        result[name] = section
    return result


def hardware_for_servers(db, server_ids=None):
    """Return {server_id: normalized_snapshot}; accepts IDs or Server objects.

    With no IDs, returns all persisted snapshots. Callers must use an unknown
    snapshot for uncollected servers, not infer zero hardware.
    """
    ids = None if server_ids is None else [getattr(item, "id", item) for item in server_ids]
    query = db.query(ServerHardware)
    if ids is not None:
        if not ids:
            return {}
        query = query.filter(ServerHardware.server_id.in_(ids))
    rows = {row.server_id: normalized_snapshot(row.snapshot) for row in query.all()}
    if ids is not None:
        for identifier in ids:
            rows.setdefault(identifier, normalized_snapshot({}))
    return rows


def hardware_for_server(db, server_id):
    return hardware_for_servers(db, [server_id])[server_id]


def save_hardware(db, server_id, incoming):
    """No commit: full failures keep old values; partial observations stay explicit."""
    row = db.get(ServerHardware, server_id)
    if row is None:
        row = ServerHardware(server_id=server_id, snapshot={})
        db.add(row)
    previous = row.snapshot or {}
    merged = {}
    for name in SECTIONS:
        current = deepcopy(incoming.get(name) or unknown_section())
        if current.get("status") != "ok" and current.get("data") is None:
            old = previous.get(name) or {}
            current["data"] = deepcopy(old.get("data"))
            current["collected_at"] = old.get("collected_at")
        merged[name] = current
    row.snapshot = merged
    return row


def failed_snapshot(reason):
    checked = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {name: {"status": "error", "data": None, "error": reason,
                   "collected_at": None, "checked_at": checked} for name in SECTIONS}
