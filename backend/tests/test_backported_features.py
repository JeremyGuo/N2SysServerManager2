"""Local-only API regressions for features learned from the deployed version."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from test_usage import setup, apply, current
from app.api import user as user_api, summary
from app.database import User, UserStatus, Account, AccountStatus, Application, DeviceUsage, ApplicationPurpose


@pytest.fixture
def features(setup):
    setup[1].app.include_router(user_api.router, prefix='/user')
    setup[1].app.include_router(summary.router, prefix='/summary')
    return setup


def make_pending(db, name='pending'):
    item=User(username=name, realname='Pending Real Name', account_name=name, mail=f'{name}@example.com',
              password='private-hash', public_key='private-key-marker', status=UserStatus.VERIFYING)
    db.add(item);db.commit()
    return item


def test_reject_user_auth_state_and_related_account_protection(features):
    db,client,admin,member,_,servers,_,login=features
    pending=make_pending(db)
    path=f'/user/user/{pending.id}/reject'
    assert client.post(path).status_code==401
    login(member)
    assert client.post(path).status_code==403
    login(admin)
    assert client.post(f'/user/user/{admin.id}/reject').status_code==409
    assert client.post(f'/user/user/{member.id}/reject').status_code==409
    assert client.post('/user/user/99999/reject').status_code==404
    account=Account(user=pending,server=servers[0],is_login_able=False)
    db.add(account);db.commit()
    assert client.post(path).status_code==409
    assert db.get(User,pending.id) is not None
    db.delete(account);db.commit()
    identifier=pending.id
    assert client.post(path).status_code==200
    assert db.get(User,identifier) is None
    assert client.post(path).status_code==404


def test_reject_pending_cleans_only_pending_registration_and_rolls_back_failure(features,monkeypatch):
    db,client,admin,_,_,servers,_,login=features
    target=make_pending(db)
    identifier=target.id
    application=Application(user=target,server=servers[0],need_sudo=False)
    db.add(application);db.flush()
    db.add(DeviceUsage(user_id=identifier,server_id=servers[0].id,status='pending',application_id=application.id))
    db.add(ApplicationPurpose(application_id=application.id,kind='sudo',reason='legacy'))
    db.commit();login(admin)
    original_commit=db.commit
    monkeypatch.setattr(db,'commit',lambda: (_ for _ in ()).throw(IntegrityError('test',{},Exception())))
    assert client.post(f'/user/user/{identifier}/reject').status_code==409
    monkeypatch.setattr(db,'commit',original_commit)
    assert db.get(User,identifier) is not None
    assert db.query(DeviceUsage).count()==1 and db.query(ApplicationPurpose).count()==1
    assert client.post(f'/user/user/{identifier}/reject').status_code==200
    assert db.get(User,identifier) is None
    assert db.query(Application).count()==db.query(DeviceUsage).count()==db.query(ApplicationPurpose).count()==0


def test_realname_and_linux_account_summary_are_whitelisted(features):
    db,client,admin,member,_,servers,_,login=features
    db.add(Account(user=member,server=servers[0]));db.commit();login(admin)
    people=client.get('/user/users?user_status=all').json()
    assert next(x for x in people if x['id']==member.id)['realname']==member.realname
    row=client.get('/summary/get').json()[0]['users'][0]
    assert row['account_name']==member.account_name and row['username']==member.username
    assert not any(x in str(people)+str(row) for x in ['private-key-marker','private-hash','password','@example.com'])


def registered(features):
    db,client,admin,member,other,servers,gateway,login=features
    account=Account(user=member,server=servers[0],is_sudo=False,status=AccountStatus.ACTIVE,last_login_date=datetime(2020,1,1))
    db.add(account);db.commit();login(member)
    assert apply(client,servers[0]).status_code==200
    usage=current(db,member,servers[0])
    return account,usage


def test_sudo_upgrade_does_not_end_or_reset_usage_and_approval_changes_only_permission(features):
    db,client,admin,member,_,servers,_,login=features
    account,record=registered(features)
    dates=(record.requested_at,record.started_at,record.confirmed_at,account.last_login_date)
    response=client.post(f'/usage/{record.id}/sudo',json={'reason':'need tracing'})
    assert response.status_code==200
    app_id=response.json()['id']
    assert record.status=='active' and not account.is_sudo
    usage=client.get('/usage/devices').json()[0]['usage']
    assert usage['my_sudo_request']['reason']=='need tracing' and usage['has_sudo'] is False
    assert client.post(f'/usage/{record.id}/sudo',json={'reason':'duplicate'}).status_code==409
    login(admin)
    pending=client.get('/app/pendings').json()[0]
    assert pending['kind']=='sudo' and pending['reason']=='need tracing'
    assert client.post(f'/app/{app_id}/approve').status_code==200
    assert record.status=='active' and account.is_sudo and account.status==AccountStatus.DIRTY
    assert (record.requested_at,record.started_at,record.confirmed_at,account.last_login_date)==dates
    assert db.query(ApplicationPurpose).count()==0
    login(member)
    assert client.get('/usage/devices').json()[0]['usage']['my_sudo_request'] is None
    assert client.post(f'/usage/{record.id}/sudo',json={'reason':'already have'}).status_code==409


@pytest.mark.parametrize('action',['reject','cancel','end','revoke'])
def test_sudo_request_lifecycle_cleans_metadata_without_accidental_grant(features,action):
    from usage_service import end_account_usage
    db,client,admin,member,_,servers,_,login=features
    account,record=registered(features)
    response=client.post(f'/usage/{record.id}/sudo',json={'reason':'temporary'})
    app_id=response.json()['id']
    if action=='reject':
        login(admin);assert client.post(f'/app/{app_id}/reject').status_code==204
    elif action=='cancel':
        assert client.post(f'/usage/{record.id}/sudo/cancel').status_code==200
    elif action=='end':
        assert client.post(f'/usage/{record.id}/end').status_code==200
    else:
        account.is_login_able=False
        end_account_usage(db,member.id,servers[0].id);db.commit()
    assert db.query(Application).count()==db.query(ApplicationPurpose).count()==0
    assert not account.is_sudo
    assert record.status==('ended' if action in ('end','revoke') else 'active')


def test_sudo_upgrade_permissions_invalid_values_and_stale_approval(features):
    db,client,admin,member,other,servers,_,login=features
    account,record=registered(features)
    path=f'/usage/{record.id}/sudo'
    for body in [{'reason':''},{'reason':'   '},{'reason':'x'*501},{'reason':'valid','uid':other.id}]:
        assert client.post(path,json=body).status_code==422
    login(other)
    assert client.post(path,json={'reason':'no'}).status_code==403
    assert client.post(path+'/cancel').status_code==403
    login(admin)
    assert client.post(path,json={'reason':'on behalf'}).status_code==403
    login(member)
    app_id=client.post(path,json={'reason':'need root'}).json()['id']
    login(other)
    assert 'need root' not in client.get('/usage/devices').text
    account.is_login_able=False;db.commit()
    login(admin)
    assert client.post(f'/app/{app_id}/approve').status_code==409
    assert not account.is_sudo and not account.is_login_able
    assert client.post(f'/app/{app_id}/reject').status_code==204


def test_profile_stale_key_edit_cannot_overwrite_gateway_append(features):
    from app.api.auth import get_password_hash
    db,client,_,member,_,_,_,login=features
    member.password=get_password_hash('test-current-password')
    member.public_key='old-public'
    db.commit();login(member)
    loaded=client.get('/user/me').json()
    member.public_key+='\ngateway-new-public'
    db.commit()
    body={'id':member.id,'realname':member.realname,'mail':member.mail,'public_key':'old-public\nmanual-edit',
          'old_password':'test-current-password','public_key_revision':loaded['public_key_revision']}
    assert client.post('/user/update',json=body).status_code==409
    assert 'gateway-new-public' in member.public_key
    loaded=client.get('/user/me').json()
    body['public_key']=loaded['public_key']+'\nmanual-edit'
    body['public_key_revision']=loaded['public_key_revision']
    assert client.post('/user/update',json=body).status_code==200
    assert 'gateway-new-public' in member.public_key and 'manual-edit' in member.public_key
