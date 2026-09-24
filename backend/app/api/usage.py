"""Self-reported machine usage, deliberately separate from login permissions."""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy.orm import Session, selectinload

from app.database import (get_db, Server, ServerStatus, User, UserStatus,
                          Account, Application, DeviceUsage, ApplicationPurpose)
from validator import getUser
from activity_time import activity_iso
from usage_service import (usage_transaction, lock_usage_user, find_usage,
                           usable_account, grant_account, register_usage,
                           usage_now, usage_iso, usage_record, pending_sudo_application, cancel_sudo_application, delete_application)

router = APIRouter()


class UsageApply(BaseModel):
    # In particular, never accept a client-supplied uid/user_id.
    model_config = ConfigDict(extra='forbid')
    server_ids: list[StrictInt] = Field(min_length=1, max_length=50)
    need_sudo: StrictBool = False
    reason: str

    @field_validator('server_ids')
    @classmethod
    def unique_ids(cls, value):
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError('server_ids must contain unique positive IDs')
        return value

    @field_validator('reason')
    @classmethod
    def trimmed_reason(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 500:
            raise ValueError('reason must contain 1 to 500 characters after trimming')
        return value


@router.get('/devices')
def list_devices(user: User = Depends(getUser), db: Session = Depends(get_db)):
    servers = db.query(Server).options(selectinload(Server.tags)).order_by(Server.id).all()
    # Hardware store is additive; a checkout without that helper still supports
    # usage and never invents measurements. Only ignore the missing module.
    try:
        from app.hardware_store import hardware_for_servers
    except ModuleNotFoundError as error:
        if error.name != 'app.hardware_store':
            raise
        hardware = {}
    else:
        hardware = hardware_for_servers(db, servers)

    account_users = defaultdict(set)
    sudo_users = defaultdict(set)
    for account in db.query(Account).join(User).filter(
        User.status == UserStatus.ACTIVE, Account.is_login_able.is_(True)
    ).all():
        account_users[account.server_id].add(account.user_id)
        if account.is_sudo:
            sudo_users[account.server_id].add(account.user_id)

    pending = defaultdict(int)
    for application in db.query(Application).all():
        pending[application.server_id] += 1

    my_sudo_requests = {}
    for app, purpose in db.query(Application, ApplicationPurpose).join(
        ApplicationPurpose, Application.id == ApplicationPurpose.application_id
    ).filter(Application.user_id == user.id, ApplicationPurpose.kind == 'sudo').all():
        my_sudo_requests[app.server_id] = {'id': app.id, 'reason': purpose.reason,
                                          'create_date': activity_iso(app.create_date)}
    active_users = defaultdict(list)
    mine = {}
    for record in db.query(DeviceUsage).options(selectinload(DeviceUsage.user)).order_by(DeviceUsage.id).all():
        if record.user_id == user.id:
            mine[record.server_id] = usage_record(record)
        if (record.status == 'active' and record.user is not None
                and record.user.status == UserStatus.ACTIVE
                and record.user_id in account_users[record.server_id]):
            active_users[record.server_id].append({
                'user_id': record.user_id, 'realname': record.user.realname,
                'started_at': usage_iso(record.started_at),
                'confirmed_at': usage_iso(record.confirmed_at),
            })

    return [{
        'id': server.id, 'host': server.host, 'gateway': server.is_gateway,
        'status': server.server_status.value, 'tags': [tag.tag for tag in server.tags],
        'hardware': hardware.get(server.id, {}),
        'usage': {
            'active_users': active_users[server.id], 'pending_count': pending[server.id],
            'account_count': len(account_users[server.id]),
            'my_usage': mine.get(server.id), 'has_account': user.id in account_users[server.id],
            'has_sudo': user.id in sudo_users[server.id], 'my_sudo_request': my_sudo_requests.get(server.id),
        },
    } for server in servers]


@router.post('/apply')
def apply_usage(data: UsageApply, user: User = Depends(getUser), db: Session = Depends(get_db)):
    items = []
    with usage_transaction(db):
        target = lock_usage_user(db, user.id)
        servers = {server.id: server for server in db.query(Server).filter(Server.id.in_(data.server_ids)).all()}
        registrations = {record.server_id: record for record in db.query(DeviceUsage).filter(
            DeviceUsage.user_id == target.id, DeviceUsage.server_id.in_(data.server_ids)
        ).populate_existing().all()}
        pending_ids = {app.server_id for app in db.query(Application).filter(
            Application.user_id == target.id, Application.server_id.in_(data.server_ids)
        ).all()}
        # Validate the ENTIRE batch before inserting accounts, apps or usage.
        for server_id in data.server_ids:
            server = servers.get(server_id)
            if server is None:
                raise HTTPException(404, f'Server {server_id} not found')
            if server.is_gateway:
                raise HTTPException(409, 'Gateway accounts are maintained automatically; select a compute device')
            current = registrations.get(server_id)
            if server_id in pending_ids or (current is not None and current.status in ('pending', 'active')):
                raise HTTPException(409, f'Server {server_id} already has an active registration or pending application')
        for server_id in data.server_ids:
            server = servers[server_id]
            application = None
            if target.is_admin:
                grant_account(db, target, server, data.need_sudo)
                state = 'active'
            elif usable_account(db, target.id, server.id, data.need_sudo):
                state = 'active'
            else:
                application = Application(user_id=target.id, server_id=server.id, need_sudo=data.need_sudo)
                db.add(application)
                db.flush()  # ID for the usage FK; still part of this transaction.
                state = 'pending'
            register_usage(db, target, server, state, data.reason, application, registrations.get(server_id))
            items.append({'server_id': server_id, 'status': state})
    return {'items': items, 'message': '使用登记已保存；待审批申请将在通过后生效。结束使用不会撤销登录权限。'}


def _locked_usage(db, usage_id, user, allow_admin):
    record = db.query(DeviceUsage).filter_by(id=usage_id).first()
    if record is None:
        raise HTTPException(404, 'Usage registration not found')
    if record.user_id != user.id and not (allow_admin and user.is_admin):
        raise HTTPException(403, 'Cannot change another user\'s usage registration')
    lock_usage_user(db, record.user_id, require_active=record.user_id == user.id)
    record = db.query(DeviceUsage).filter_by(id=usage_id).populate_existing().with_for_update().first()
    if record is None:
        raise HTTPException(404, 'Usage registration not found')
    return record


@router.post('/{usage_id}/end')
def end_usage(usage_id: int, user: User = Depends(getUser), db: Session = Depends(get_db)):
    with usage_transaction(db):
        record = _locked_usage(db, usage_id, user, allow_admin=True)
        if record.status != 'active':
            raise HTTPException(409, 'Only an active usage registration can be ended')
        cancel_sudo_application(db, record.user_id, record.server_id)
        record.status = 'ended'
        record.ended_at = usage_now()
        record.application_id = None
        result = usage_record(record)
    return {**result, 'message': '使用已结束，登录权限保持不变。'}


@router.post('/{usage_id}/confirm')
def confirm_usage(usage_id: int, user: User = Depends(getUser), db: Session = Depends(get_db)):
    with usage_transaction(db):
        record = _locked_usage(db, usage_id, user, allow_admin=False)
        if record.status != 'active' or not usable_account(db, user.id, record.server_id):
            raise HTTPException(409, 'Only an active registration with current login permission can be confirmed')
        record.confirmed_at = usage_now()
        result = usage_record(record)
    return result


@router.post('/{usage_id}/cancel')
def cancel_usage(usage_id: int, user: User = Depends(getUser), db: Session = Depends(get_db)):
    with usage_transaction(db):
        record = _locked_usage(db, usage_id, user, allow_admin=True)
        if record.status != 'pending':
            raise HTTPException(409, 'Only a pending usage registration can be cancelled')
        if record.application_id is not None:
            application = db.query(Application).filter_by(
                id=record.application_id, user_id=record.user_id, server_id=record.server_id
            ).first()
            if application is not None:
                delete_application(db, application)
        record.application_id = None
        record.status = 'cancelled'
        record.ended_at = usage_now()
        result = usage_record(record)
    return result


class SudoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def nonempty_reason(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请填写申请 sudo 的原因')
        return value


@router.post('/{usage_id}/sudo')
def request_sudo(usage_id: int, data: SudoRequest, user: User = Depends(getUser), db: Session = Depends(get_db)):
    with usage_transaction(db):
        record = _locked_usage(db, usage_id, user, allow_admin=False)
        if record.status != 'active' or record.server.is_gateway:
            raise HTTPException(409, '仅非网关机器的使用中登记可以申请 sudo；不会自动重新授权或改变登记。')
        account = usable_account(db, user.id, record.server_id)
        if not account:
            raise HTTPException(409, '当前账号已无登录权限，请先重新申请机器。')
        if account.is_sudo:
            raise HTTPException(409, '当前账号已有 sudo 权限，无需重复申请。')
        if db.query(Application.id).filter_by(user_id=user.id, server_id=record.server_id).first():
            raise HTTPException(409, '该机器已有待审批申请，请等待处理或取消后重试。')
        app = Application(user_id=user.id, server_id=record.server_id, need_sudo=True)
        db.add(app)
        db.flush()
        db.add(ApplicationPurpose(application_id=app.id, kind='sudo', reason=data.reason))
        result = {'id': app.id, 'reason': data.reason, 'create_date': activity_iso(app.create_date)}
    return {**result, 'message': 'sudo 升级申请已提交；原使用登记及当前权限保持不变，等待管理员审批。'}


@router.post('/{usage_id}/sudo/cancel')
def cancel_sudo(usage_id: int, user: User = Depends(getUser), db: Session = Depends(get_db)):
    with usage_transaction(db):
        record = _locked_usage(db, usage_id, user, allow_admin=False)
        if pending_sudo_application(db, user.id, record.server_id) is None:
            raise HTTPException(409, '没有待审批的 sudo 升级申请。')
        cancel_sudo_application(db, user.id, record.server_id)
    return {'message': 'sudo 升级申请已取消，原使用登记保持不变。'}
