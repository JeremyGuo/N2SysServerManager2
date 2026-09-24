"""Persistence, API/AI wiring, and additive-schema upgrade checks."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from test_sync import db_env, clean_state, detached, fake_connection, collection_success
from app.database import Base, Server, ServerHardware, DeviceUsage
from app.hardware_store import (SECTIONS, save_hardware, hardware_for_servers, hardware_for_server,
                                normalized_snapshot, failed_snapshot)
from app.api.ai import inventory_snapshot
import account_sync as sync


def snapshot():
    now = datetime.now(timezone.utc).isoformat()
    data = {
        'cpu': {'model': 'AMD EPYC test', 'sockets': 2, 'cores': 64, 'threads': 128},
        'memory': {'total_bytes': 256 * 2**30, 'available_bytes': 128 * 2**30},
        'gpus': [{'name': 'NVIDIA test', 'vendor': 'NVIDIA', 'memory_total_bytes': 80 * 2**30, 'pci_address': '0000:01:00.0'}],
        'disks': [{'name': 'nvme0n1', 'model': 'Test NVMe', 'size_bytes': 2 * 2**40, 'type': 'disk', 'rotational': False}],
        'network': [{'name': 'eth0', 'mac': None, 'speed_mbps': 100000, 'state': 'up', 'pci_address': '0000:02:00.0', 'error': None}],
    }
    return {name: {'status': 'ok', 'data': data[name], 'error': None, 'collected_at': now, 'checked_at': now} for name in SECTIONS}


def test_unknown_snapshot_not_zero(db_env):
    factory, ids, _ = db_env
    with factory() as db:
        hardware = hardware_for_server(db, ids.server)
        assert set(hardware) == set(SECTIONS)
        assert all(item['data'] is None and item['status'] == 'unknown' and item['stale'] for item in hardware.values())


def test_partial_failure_keeps_last_good_data_with_explicit_old_time(db_env):
    factory, ids, _ = db_env
    original = snapshot()
    with factory() as db:
        save_hardware(db, ids.server, original)
        db.commit()
        update = snapshot()
        update['gpus'] = failed_snapshot('GPU driver unavailable')['gpus']
        update['cpu']['data']['cores'] = 96
        save_hardware(db, ids.server, update)
        db.commit()
        values = hardware_for_server(db, ids.server)
        assert values['gpus']['data'] == original['gpus']['data']
        assert values['gpus']['collected_at'] == original['gpus']['collected_at']
        assert values['gpus']['status'] == 'error' and values['gpus']['stale']
        assert values['cpu']['data']['cores'] == 96 and not values['cpu']['stale']
        recovery = snapshot()
        recovery['gpus']['data'] = []  # Proven removal/no GPU is a valid observation.
        save_hardware(db, ids.server, recovery)
        db.commit()
        assert hardware_for_server(db, ids.server)['gpus']['data'] == []


def test_hardware_staleness_without_mutating_saved_snapshot():
    original = snapshot()
    original['cpu']['collected_at'] = (datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
    preserved = deepcopy(original)
    assert normalized_snapshot(original)['cpu']['stale'] is True
    assert original == preserved


def test_hardware_partials_do_not_break_existing_collection(db_env, monkeypatch):
    factory, ids, _ = db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    data = snapshot()
    data['gpus'] = failed_snapshot('GPU driver unavailable')['gpus']
    monkeypatch.setattr(sync, 'collect_hardware', AsyncMock(return_value=data))
    asyncio.run(sync.syncServer(detached(factory, Server, ids.server)))
    with factory() as db:
        assert db.get(Server, ids.server).kernel_version == '6.1'
        assert hardware_for_server(db, ids.server)['cpu']['data']['threads'] == 128
        assert hardware_for_server(db, ids.server)['gpus']['data'] is None
    assert any(error['scope'] == 'hardware' for error in sync.get_sync_errors())
    assert ids.server in sync.last_server_collect_date  # no tight retries for optional tools
    monkeypatch.setattr(sync, 'collect_hardware', AsyncMock(return_value=snapshot()))
    asyncio.run(sync.syncServer(detached(factory, Server, ids.server)))
    assert not any(error['scope'] == 'hardware' for error in sync.get_sync_errors())


def test_hardware_failure_does_not_prevent_login_collection(db_env, monkeypatch):
    factory, ids, _ = db_env
    fake_connection(monkeypatch)
    collection_success(monkeypatch)
    monkeypatch.setattr(sync, 'collect_hardware', AsyncMock(side_effect=ValueError('untrusted secret')))
    asyncio.run(sync.syncServer(detached(factory, Server, ids.server)))
    with factory() as db:
        assert db.get(Server, ids.server).os_version == 'Test Linux'
        hardware = hardware_for_server(db, ids.server)
        assert all(s['status']=='error' for s in hardware.values())
        assert 'untrusted secret' not in str(hardware)


def test_ai_context_contains_hardware_but_not_user_or_ipmi(db_env):
    factory, ids, _ = db_env
    with factory() as db:
        db.get(Server, ids.server).ipmi = 'private-ipmi'
        save_hardware(db, ids.server, snapshot())
        db.commit()
        context = inventory_snapshot(db)
        hardware = context['servers'][0]['hardware']
        assert hardware['cpu']['data']['model'] == 'AMD EPYC test'
        assert hardware['gpus']['data'][0]['memory_total_bytes'] == 80 * 2**30
        assert hardware['network']['data'][0]['speed_mbps'] == 100000
        assert 'private-ipmi' not in str(context) and 'public_key' not in str(context)


def test_additive_tables_on_existing_schema_preserve_data(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/old.db')
    old_tables = [t for t in Base.metadata.sorted_tables if t.name not in ('server_hardware', 'device_usage', 'application_purpose')]
    Base.metadata.create_all(engine, tables=old_tables)
    with Session(engine) as db:
        server = Server(host='existing-host', port=22)
        db.add(server)
        db.commit()
        identifier = server.id
    old_columns = {name: [c['name'] for c in inspect(engine).get_columns(name)] for name in inspect(engine).get_table_names()}
    Base.metadata.create_all(engine)  # Same as production startup, repeat-safe.
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert db.get(Server, identifier).host == 'existing-host'
        assert db.scalars(select(ServerHardware)).all() == []
        assert db.scalars(select(DeviceUsage)).all() == []
    for name, columns in old_columns.items():
        assert columns == [c['name'] for c in inspect(engine).get_columns(name)]
    engine.dispose()
