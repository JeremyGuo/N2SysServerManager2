"""Usage API tests: isolated SQLite, real cookie auth, no main/watcher/SSH.

Run .venv/bin/python3 -m pytest backend/tests/test_usage.py from the repo root.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if 'app.database' not in sys.modules:
    with patch.dict(os.environ, {'DATABASE_URL': 'sqlite://'}):
        bootstrap_engine = create_engine('sqlite://', poolclass=StaticPool,
                                         connect_args={'check_same_thread': False})
        with patch('sqlalchemy.create_engine', return_value=bootstrap_engine):
            from app import database
else:
    from app import database
with patch.dict(os.environ, {'SECRET_KEY': 'usage-tests-only-secret-32-characters-long'}):
    from app.api import auth, application, usage
from app.database import (Base, get_db, User, UserStatus, Server, ServerStatus,
                          Account, AccountStatus, Application, DeviceUsage, ServerTag)
from usage_service import end_account_usage, usage_now, usage_iso


@pytest.fixture
def setup():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    def make_user(name, **kwargs):
        user = User(username=name, realname=name.title(), account_name=name,
                    mail=f'{name}@example.com', public_key='private-key-marker',
                    password='private-password-marker', status=UserStatus.ACTIVE, **kwargs)
        db.add(user)
        return user
    admin = make_user('admin', is_admin=True)
    member = make_user('member')
    other = make_user('other')
    servers = [Server(host=f'compute-{i}', port=22, server_status=ServerStatus.ACTIVE) for i in range(3)]
    gateway = Server(host='gateway', port=22, server_status=ServerStatus.ACTIVE, is_gateway=True)
    db.add_all([*servers, gateway])
    db.commit()
    app = FastAPI()
    app.include_router(usage.router, prefix='/usage')
    app.include_router(application.router, prefix='/app')
    def override_db():
        try:
            yield db
        finally:
            db.rollback()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        def login(user=member):
            client.cookies.clear()
            client.cookies.set('access_token', auth.create_access_token(
                {'sub': user.username, 'id': user.id}, timedelta(minutes=5)))
        yield db, client, admin, member, other, servers, gateway, login
    db.close()
    engine.dispose()


def apply(client, *servers, **changes):
    body = {'server_ids': [s.id for s in servers], 'reason': '  model training  '}
    body.update(changes)
    return client.post('/usage/apply', json=body)


def current(db, user, server):
    return db.query(DeviceUsage).filter_by(user_id=user.id, server_id=server.id).one()


def test_auth_and_permission_boundaries(setup):
    db, client, admin, member, other, servers, _, login = setup
    for method, path, payload in [('get', '/usage/devices', None),
                                 ('post', '/usage/apply', {'server_ids': [servers[0].id], 'reason': 'test'}),
                                 *[('post', f'/usage/1/{action}', None) for action in ('end', 'confirm', 'cancel')]]:
        response = getattr(client, method)(path, **({'json': payload} if payload is not None else {}))
        assert response.status_code == 401
    login()
    assert client.get('/usage/devices').status_code == 200
    assert apply(client, servers[0], user_id=other.id).status_code == 422
    assert apply(client, servers[0], uid=other.id).status_code == 422
    assert apply(client, servers[0]).status_code == 200
    record = current(db, member, servers[0])
    application_id, record_id = record.application_id, record.id
    assert client.post(f'/app/{application_id}/approve').status_code == 403
    assert client.post(f'/app/{application_id}/reject').status_code == 403
    login(other)
    for action in ('end', 'confirm', 'cancel'):
        assert client.post(f'/usage/{record_id}/{action}').status_code == 403
    login(admin)
    assert client.post(f'/usage/{record_id}/confirm').status_code == 403
    assert client.post(f'/usage/{record_id}/cancel').status_code == 200
    login()
    member.status = UserStatus.GRADUATED
    db.commit()
    assert client.get('/usage/devices').status_code == 401
    assert apply(client, servers[1]).status_code == 401
    for action in ('end', 'confirm', 'cancel'):
        assert client.post(f'/usage/{record_id}/{action}').status_code == 401


@pytest.mark.parametrize('changes', [
    {'server_ids': []}, {'server_ids': list(range(1, 52))}, {'server_ids': [1, 1]},
    {'server_ids': [True]}, {'server_ids': ['1']}, {'server_ids': [0]},
    {'reason': ''}, {'reason': ' \n\t '}, {'reason': 'a' * 501}, {'need_sudo': 'true'},
])
def test_validation(setup, changes):
    db, client, _, _, _, servers, _, login = setup
    login()
    assert apply(client, servers[0], **changes).status_code == 422
    assert db.query(Application).count() == db.query(DeviceUsage).count() == 0


def test_mixed_batch_atomic_validation_for_member_and_admin(setup):
    db, client, admin, member, _, servers, gateway, login = setup
    for actor in (member, admin):
        login(actor)
        for bad_id, expected in ((gateway.id, 409), (99999, 404)):
            assert apply(client, server_ids=[servers[0].id, bad_id]).status_code == expected
            assert db.query(Account).count() == db.query(Application).count() == db.query(DeviceUsage).count() == 0


def test_pending_duplicate_rolls_back_other_batch_items(setup):
    db, client, _, member, _, servers, _, login = setup
    login()
    legacy = Application(user=member, server=servers[1], need_sudo=False)
    db.add(legacy)
    db.commit()
    assert apply(client, servers[0], servers[1]).status_code == 409
    assert db.query(DeviceUsage).count() == 0
    assert db.query(Application).count() == 1
    db.delete(legacy)
    db.commit()
    assert apply(client, servers[0]).status_code == 200
    assert apply(client, servers[1], servers[0]).status_code == 409
    assert db.query(DeviceUsage).count() == db.query(Application).count() == 1


def test_existing_permissions_activate_without_extra_application_or_demotion(setup):
    db, client, _, member, _, servers, _, login = setup
    account = Account(user=member, server=servers[0], is_sudo=True,
                      status=AccountStatus.ACTIVE, last_login_date=datetime(2020, 1, 1))
    db.add(account)
    db.commit()
    login()
    response = apply(client, servers[0], servers[1])
    assert response.status_code == 200
    assert response.json()['items'] == [
        {'server_id': servers[0].id, 'status': 'active'},
        {'server_id': servers[1].id, 'status': 'pending'},
    ]
    assert set(response.json()) == {'items', 'message'}
    assert db.query(Application).count() == 1
    assert account.is_sudo and account.status == AccountStatus.ACTIVE
    assert account.last_login_date == datetime(2020, 1, 1)
    record = current(db, member, servers[0])
    assert record.reason == 'model training'
    assert record.started_at.tzinfo is None
    assert record.application_id is None
    assert apply(client, servers[0]).status_code == 409


def test_insufficient_or_revoked_account_requires_application(setup):
    db, client, _, member, _, servers, _, login = setup
    db.add_all([Account(user=member, server=servers[0], is_sudo=False),
                Account(user=member, server=servers[1], is_sudo=True, is_login_able=False)])
    db.commit()
    login()
    assert apply(client, servers[0], need_sudo=True).json()['items'][0]['status'] == 'pending'
    assert apply(client, servers[1]).json()['items'][0]['status'] == 'pending'
    assert db.query(Application).count() == 2
    assert not db.query(Account).filter_by(server_id=servers[1].id).one().is_login_able


def test_admin_grants_immediately_and_preserves_sudo(setup):
    db, client, admin, _, _, servers, _, login = setup
    account = Account(user=admin, server=servers[0], is_sudo=True, is_login_able=False)
    db.add(account)
    db.commit()
    login(admin)
    assert all(item['status'] == 'active' for item in apply(client, *servers).json()['items'])
    assert db.query(Account).count() == db.query(DeviceUsage).count() == 3
    assert db.query(Application).count() == 0
    assert account.is_sudo and account.is_login_able and account.status == AccountStatus.DIRTY


def test_lifecycle_end_preserves_permission_and_reuses_row(setup):
    db, client, admin, member, _, servers, _, login = setup
    account = Account(user=member, server=servers[0], is_sudo=True, status=AccountStatus.ACTIVE)
    db.add(account)
    db.commit()
    login()
    apply(client, servers[0])
    record = current(db, member, servers[0])
    record_id = record.id
    old = usage_now() - timedelta(days=90)
    record.confirmed_at = old
    db.commit()
    # Age alone is not evidence of physical idleness or ended usage.
    data = client.get('/usage/devices').json()[0]['usage']
    assert data['my_usage']['status'] == 'active' and len(data['active_users']) == 1
    assert client.post(f'/usage/{record_id}/cancel').status_code == 409
    confirmed = client.post(f'/usage/{record_id}/confirm')
    assert confirmed.status_code == 200 and confirmed.json()['confirmed_at'].endswith('Z')
    assert record.confirmed_at > old
    ended = client.post(f'/usage/{record_id}/end')
    assert ended.status_code == 200 and ended.json()['ended_at'].endswith('Z')
    assert account.is_sudo and account.is_login_able and account.status == AccountStatus.ACTIVE
    for action in ('end', 'confirm', 'cancel'):
        assert client.post(f'/usage/{record_id}/{action}').status_code == 409
    assert apply(client, servers[0], reason='new task').status_code == 200
    assert current(db, member, servers[0]).id == record_id
    assert record.reason == 'new task' and record.ended_at is None
    assert db.query(DeviceUsage).count() == 1
    login(admin)
    assert client.post(f'/usage/{record_id}/end').status_code == 200


def test_cancel_pending_removes_application_atomically_and_reapply(setup):
    db, client, _, member, _, servers, _, login = setup
    login()
    apply(client, servers[0])
    record = current(db, member, servers[0])
    record_id, application_id = record.id, record.application_id
    for action in ('end', 'confirm'):
        assert client.post(f'/usage/{record_id}/{action}').status_code == 409
    with patch.object(db, 'commit', side_effect=SQLAlchemyError('secret database detail')):
        assert client.post(f'/usage/{record_id}/cancel').status_code == 500
    assert record.status == 'pending' and db.get(Application, application_id) is not None
    assert client.post(f'/usage/{record_id}/cancel').json()['status'] == 'cancelled'
    assert record.application_id is None and db.query(Application).count() == 0
    assert record.ended_at is not None
    assert client.post(f'/usage/{record_id}/cancel').status_code == 409
    apply(client, servers[0])
    assert record.id == record_id and record.status == 'pending' and record.ended_at is None


def test_approval_rejection_gateway_rules_and_legacy_shape(setup):
    db, client, admin, member, _, servers, gateway, login = setup
    login()
    apply(client, servers[0], need_sudo=True, reason='research')
    record = current(db, member, servers[0])
    requested_at = record.requested_at
    application_id = record.application_id
    login(admin)
    with patch.object(db, 'commit', side_effect=SQLAlchemyError('secret')):
        assert client.post(f'/app/{application_id}/approve').status_code == 500
    assert record.status == 'pending' and db.query(Account).count() == 0
    response = client.post(f'/app/{application_id}/approve')
    assert response.status_code == 200 and set(response.json()) == {'account_id'}
    assert record.status == 'active' and record.application_id is None
    assert record.reason == 'research' and record.requested_at == requested_at
    assert db.query(Account).one().is_sudo
    assert db.query(Application).count() == 0
    login()
    legacy_response = client.post('/app/submit', json={
        'uid': member.id, 'server_id': servers[1].id, 'need_sudo': False})
    assert legacy_response.status_code == 201
    assert set(legacy_response.json()) == {'id', 'host', 'need_sudo', 'create_date'}
    rejected = current(db, member, servers[1])
    login(admin)
    assert client.post(f'/app/{rejected.application_id}/reject').status_code == 204
    assert rejected.status == 'rejected' and rejected.application_id is None and rejected.ended_at
    assert db.query(Application).count() == 0
    login()
    assert apply(client, servers[1]).status_code == 200
    assert rejected.status == 'pending' and rejected.ended_at is None
    login(admin)
    response = client.post('/app/submit', json={'uid': member.id, 'server_id': servers[1].id, 'need_sudo': False})
    assert response.status_code == 201 and set(response.json()) == {'account_id', 'host', 'is_sudo'}
    assert rejected.status == 'active' and db.query(Application).count() == 0
    gateway_app = Application(user=member, server=gateway, need_sudo=True)
    db.add(gateway_app)
    db.commit()
    assert client.post(f'/app/{gateway_app.id}/approve').status_code == 200
    assert not db.query(Account).filter_by(server_id=gateway.id).one().is_sudo
    assert db.query(DeviceUsage).filter_by(server_id=gateway.id).count() == 0


def test_legacy_approval_adds_usage_and_duplicate_does_not_reset_it(setup):
    db, client, admin, member, _, servers, _, login = setup
    apps = [Application(user=member, server=servers[0], need_sudo=sudo) for sudo in (True, False)]
    db.add_all(apps)
    db.commit()
    ids = [item.id for item in apps]
    login(admin)
    assert client.post(f'/app/{ids[0]}/approve').status_code == 200
    record = current(db, member, servers[0])
    record_id, started_at = record.id, record.started_at
    assert record.reason == '原有申请'
    assert client.post(f'/app/{ids[1]}/approve').status_code == 200
    assert record.id == record_id and record.started_at == started_at
    assert db.query(DeviceUsage).count() == db.query(Account).count() == 1
    assert db.query(Account).one().is_sudo
    login()
    assert client.post('/app/submit', json={'uid': member.id, 'server_id': servers[0].id, 'need_sudo': False}).status_code == 409


def test_devices_privacy_counts_stale_permissions_and_dates(setup):
    db, client, admin, member, other, servers, gateway, login = setup
    db.add_all([Account(user=member, server=servers[0]), Account(user=member, server=servers[0]),
                Account(user=other, server=servers[0]), Account(user=admin, server=servers[0]),
                ServerTag(server=servers[0], tag='research')])
    db.commit()
    login()
    apply(client, servers[0], reason='private-member-reason')
    apply(client, servers[1])
    login(other)
    apply(client, servers[0], reason='private-other-reason')
    other_usage = current(db, other, servers[0])
    other_usage.confirmed_at = usage_now() - timedelta(days=90)
    # This unlinked stale row is NOT a real pending application.
    db.add(DeviceUsage(user=admin, server=servers[1], status='pending'))
    db.commit()
    login()
    data = client.get('/usage/devices').json()
    first = next(item for item in data if item['id'] == servers[0].id)
    assert set(first) == {'id', 'host', 'gateway', 'status', 'tags', 'hardware', 'usage'}
    summary = first['usage']
    assert set(summary) == {'active_users', 'pending_count', 'account_count', 'my_usage', 'has_account', 'has_sudo', 'my_sudo_request'}
    assert summary['account_count'] == 3  # Distinct permitted people, not account rows.
    assert {u['user_id'] for u in summary['active_users']} == {member.id, other.id}
    assert summary['has_account'] and first['tags'] == ['research']
    assert first['status'] == 'active'
    assert all(set(u) == {'user_id', 'realname', 'started_at', 'confirmed_at'} for u in summary['active_users'])
    assert all(u['started_at'].endswith('Z') for u in summary['active_users'])
    assert next(item for item in data if item['id'] == servers[1].id)['usage']['pending_count'] == 1
    for forbidden in ('private-password-marker', 'private-key-marker', '@example.com', 'private-other-reason', 'username', 'public_key', 'password', 'account_name'):
        assert forbidden not in str(data)
    for account in db.query(Account).filter_by(user_id=member.id):
        account.is_login_able = False
    other.status = UserStatus.GRADUATED
    db.commit()
    summary = client.get('/usage/devices').json()[0]['usage']
    assert summary['active_users'] == [] and summary['account_count'] == 1 and not summary['has_account']
    # A read must not mutate usage history or fabricate an end/idle signal.
    record = current(db, member, servers[0])
    assert record.status == 'active' and summary['my_usage']['status'] == 'active'
    assert client.post(f'/usage/{record.id}/confirm').status_code == 409
    assert client.post(f'/usage/{record.id}/end').status_code == 200


def test_account_revoke_helper_is_idempotent_and_does_not_commit(setup):
    db, client, admin, _, _, servers, _, login = setup
    login(admin)
    apply(client, servers[0])
    record = current(db, admin, servers[0])
    with patch.object(db, 'commit') as commit:
        end_account_usage(db, admin.id, servers[0].id, 'revoked')
        assert record.status == 'ended' and record.ended_at is not None
        ended_at = record.ended_at
        end_account_usage(db, admin.id, servers[0].id)
        assert record.ended_at == ended_at
        commit.assert_not_called()
    db.rollback()
    assert record.status == 'active' and record.ended_at is None
    assert db.query(Account).one().is_login_able


@pytest.mark.parametrize('error,status_code', [
    (SQLAlchemyError('secret'), 500),
    (IntegrityError('statement', {}, Exception('unique collision')), 409),
])
def test_failed_batch_commit_rolls_back_accounts_applications_and_usage(setup, error, status_code):
    db, client, admin, member, _, servers, _, login = setup
    for actor in (member, admin):
        login(actor)
        with patch.object(db, 'commit', side_effect=error):
            response = apply(client, *servers)
        assert response.status_code == status_code
        assert 'secret' not in response.text and 'statement' not in response.text
        assert db.query(Account).count() == db.query(Application).count() == db.query(DeviceUsage).count() == 0


def test_utc_usage_serialization_does_not_treat_naive_as_local():
    naive = datetime(2025, 3, 1, 12, 0, 0)
    assert usage_iso(naive) == '2025-03-01T12:00:00Z'
    assert usage_iso(naive.replace(tzinfo=timezone(timedelta(hours=8)))) == '2025-03-01T04:00:00Z'
    assert usage_iso(None) is None


def test_administrator_approval_is_not_a_user_confirmation(setup):
    db, client, admin, member, _, servers, _, login = setup
    login()
    apply(client, servers[0])
    record = current(db, member, servers[0])
    assert record.confirmed_at is None
    login(admin)
    assert client.post(f'/app/{record.application_id}/approve').status_code == 200
    assert record.started_at is not None and record.confirmed_at is None
    login()
    assert client.post(f'/usage/{record.id}/confirm').status_code == 200
    assert record.confirmed_at is not None


def test_usage_endpoint_forwards_normalized_hardware(setup):
    db, client, _, _, _, servers, _, login = setup
    from app.hardware_store import hardware_for_servers
    login()
    expected = hardware_for_servers(db, servers)
    devices = client.get('/usage/devices').json()
    for server in servers:
        data = next(item for item in devices if item['id'] == server.id)
        assert data['hardware'] == expected[server.id]
        assert all(section['data'] is None for section in data['hardware'].values())


def test_permission_revocation_routes_end_usage_in_same_transaction(setup):
    from app.api import account, user
    db, client, admin, member, _, servers, _, login = setup
    client.app.include_router(account.router, prefix='/account')
    client.app.include_router(user.router, prefix='/user')
    accounts = [Account(user=member, server=server) for server in servers[:2]]
    db.add_all(accounts)
    db.commit()
    login()
    assert apply(client, *servers[:2]).status_code == 200
    login(admin)
    assert client.put(f'/account/{accounts[0].id}/revoke').status_code == 204
    assert current(db, member, servers[0]).status == 'ended'
    assert not accounts[0].is_login_able
    assert client.post(f'/user/user/{member.id}/graduate').status_code == 200
    assert current(db, member, servers[1]).status == 'ended'
    assert not accounts[1].is_login_able


def test_concurrent_duplicate_apply_has_one_registration(tmp_path):
    """Use independent sessions/connections, not StaticPool's shared transaction."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine = create_engine(f'sqlite:///{tmp_path}/usage-race.db',
                           connect_args={'check_same_thread': False, 'timeout': 5})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        member = User(username='racer', realname='Racer', account_name='racer',
                      mail='racer@example.com', public_key='', password='unused',
                      status=UserStatus.ACTIVE)
        server = Server(host='race-compute', port=22, server_status=ServerStatus.ACTIVE)
        db.add_all([member, server])
        db.commit()
        token = auth.create_access_token({'sub': member.username, 'id': member.id}, timedelta(minutes=5))
        server_id = server.id
    app = FastAPI()
    app.include_router(usage.router, prefix='/usage')
    def override_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    start = Barrier(2)
    def submit():
        with TestClient(app) as client:
            client.cookies.set('access_token', token)
            start.wait(timeout=5)
            return client.post('/usage/apply', json={'server_ids': [server_id], 'reason': 'race'}).status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            assert sorted(f.result(timeout=10) for f in futures) == [200, 409]
        with factory() as db:
            assert db.query(DeviceUsage).count() == db.query(Application).count() == 1
    finally:
        engine.dispose()


def test_usage_registration_is_not_gated_by_last_ssh_reachability(setup):
    db, client, _, _, _, servers, _, login = setup
    login()
    servers[0].server_status = ServerStatus.UNKNOWN
    servers[1].server_status = ServerStatus.UNABLE_TO_REACH
    db.commit()
    response = apply(client, servers[0], servers[1])
    assert response.status_code == 200
    assert len(response.json()['items']) == 2
    assert db.query(Application).count() == 2


def test_pending_reason_is_visible_to_approver_not_other_users(setup):
    db, client, admin, member, other, servers, _, login = setup
    login(member)
    assert apply(client, servers[0], reason='private research reason').status_code == 200
    login(admin)
    pending=client.get('/app/pendings')
    assert pending.status_code==200
    assert pending.json()[0]['reason']=='private research reason'
    login(other)
    assert 'private research reason' not in client.get('/usage/devices').text
