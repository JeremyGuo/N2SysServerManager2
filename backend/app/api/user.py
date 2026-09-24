import hashlib
from activity_time import activity_iso
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from typing import List, Dict
from pydantic import BaseModel, EmailStr, field_validator

from app.database import get_db, User as DBUser, Application, Account, UserStatus, AccountStatus, DeviceUsage, ApplicationPurpose
from validator import getUser, getUserAdmin
from app.api.auth import verify_password, get_password_hash
from usage_service import end_account_usage, lock_usage_user, usage_transaction

router = APIRouter()

class UserUpdate(BaseModel):
    id: int
    realname: str
    mail: EmailStr
    public_key: str
    public_key_revision: str | None = None
    old_password: str = ''
    new_password: str = ''

    @field_validator("new_password")
    @classmethod
    def valid_new_password(cls, value):
        if value and len(value) < 8:
            raise ValueError("Password must be at least 8 characters")
        return value


# Whitelist response fields: never serialize ORM objects with password hashes.
USER_PUBLIC_FIELDS = (
    "id", "username", "realname", "account_name", "mail", "public_key",
    "is_admin", "status", "is_mail_auto_revoke", "is_mail_new_application",
    "is_mail_new_registeration",
)


def protect_last_admin(db: Session, user: DBUser):
    if user.is_admin and user.status == UserStatus.ACTIVE:
        # SQLite ignores FOR UPDATE; acquire its writer lock before counting.
        # This serializes concurrent demotions/graduations as well.
        if db.get_bind().dialect.name == "sqlite":
            db.execute(update(DBUser).where(DBUser.id == user.id).values(id=DBUser.id))
        admins = db.query(DBUser).filter(
            DBUser.is_admin.is_(True), DBUser.status == UserStatus.ACTIVE
        ).order_by(DBUser.id).with_for_update().all()
        if len(admins) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last active administrator")


def update_gateway_accounts(user: DBUser):
    for account in user.accounts:
        if account.server.is_gateway:
            account.is_login_able = user.status == UserStatus.ACTIVE
            account.is_sudo = account.is_login_able and user.is_admin
            account.status = AccountStatus.DIRTY

@router.get("/me")
def read_current_user(
    user: DBUser = Depends(getUser)
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return {**{field: getattr(user, field) for field in USER_PUBLIC_FIELDS},
            "public_key_revision": hashlib.sha256((user.public_key or '').encode()).hexdigest()}

@router.get("/admin")
def read_admin_data(
    admin: DBUser = Depends(getUserAdmin)
):
    if not admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return {"msg": "Hello, admin"}

@router.get("/applications", response_model=List[Dict])
def read_user_applications(
    user: DBUser = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    purposes = {p.application_id: p for p in db.query(ApplicationPurpose).join(
        Application, Application.id == ApplicationPurpose.application_id
    ).filter(Application.user_id == user.id).all()}
    reasons = {r.application_id: r.reason for r in db.query(DeviceUsage).filter(
        DeviceUsage.user_id == user.id, DeviceUsage.status == 'pending'
    ).all() if r.application_id is not None}
    return [
        {
            "id": app.id,
            "host": app.server.host,
            "need_sudo": app.need_sudo,
            "create_date": activity_iso(app.create_date),
            "kind": purposes[app.id].kind if app.id in purposes else 'access',
            "reason": purposes[app.id].reason if app.id in purposes else reasons.get(app.id, '原有申请'),
        }
        for app in user.applications
    ]

@router.get("/accounts", response_model=List[Dict])
def read_user_accounts(
    user: DBUser = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return [
        {
            "id": acct.id,
            "host": acct.server.host,
            "is_sudo": acct.is_sudo,
            "is_gateway": acct.server.is_gateway,
            "is_user_admin": user.is_admin,
            "last_login_date": activity_iso(acct.last_login_date)
        }
        for acct in user.accounts if acct.is_login_able
    ]

@router.get("/users", response_model=List[Dict])
def list_users(
    user_status: str,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    if not admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    query = db.query(DBUser)
    if user_status and user_status != "all":
        mapping = {
            "active": UserStatus.ACTIVE,
            "verifying": UserStatus.VERIFYING,
            "inactive": UserStatus.GRADUATED
        }
        if user_status not in mapping:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid user status")
        query = query.filter(DBUser.status == mapping[user_status])
    users = query.all()
    return [{"id": u.id, "username": u.username, "realname": u.realname, "status": u.status, "is_admin": u.is_admin} for u in users]

@router.post("/user/{user_id}/approve", response_model=dict)
def approve_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    with usage_transaction(db):
        user = lock_usage_user(db, user_id, require_active=False)
        user.status = UserStatus.ACTIVE
        update_gateway_accounts(user)
    return {"msg": "User approved"}

@router.post("/user/{user_id}/revoke-admin", response_model=dict)
def revoke_admin(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    protect_last_admin(db, user)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot revoke your own administrator rights")
    user.is_admin = False
    update_gateway_accounts(user)
    db.commit()
    return {"msg": "Admin rights revoked"}

@router.post("/user/{user_id}/graduate", response_model=dict)
def graduate_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    protect_last_admin(db, user)
    lock_usage_user(db, user.id, require_active=False)
    user.status = UserStatus.GRADUATED
    for account in user.accounts:
        account.is_login_able = False
        account.is_sudo = False
        account.status = AccountStatus.DIRTY
        end_account_usage(db, user.id, account.server_id)
    update_gateway_accounts(user)
    db.commit()
    return {"msg": "User graduated"}

@router.post("/user/{user_id}/grant-admin", response_model=dict)
def grant_admin_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    with usage_transaction(db):
        user = lock_usage_user(db, user_id, require_active=False)
        user.is_admin = True
        update_gateway_accounts(user)
    return {"msg": "Admin rights granted"}

@router.post("/user/{user_id}/restore", response_model=dict)
def restore_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    with usage_transaction(db):
        user = lock_usage_user(db, user_id, require_active=False)
        user.status = UserStatus.ACTIVE
        update_gateway_accounts(user)
    return {"msg": "User restored"}

@router.post("/user/{user_id}/reject", response_model=dict)
def reject_user(user_id: int, admin: DBUser = Depends(getUserAdmin), db: Session = Depends(get_db)):
    with usage_transaction(db):
        user = lock_usage_user(db, user_id, require_active=False)
        if user.id == admin.id or user.is_admin or user.status != UserStatus.VERIFYING:
            raise HTTPException(409, "只能拒绝待审核的非管理员注册，不能删除已激活、已毕业或管理员用户。")
        # Existing remote accounts must first be explicitly revoked/reconciled;
        # deleting their DB rows would orphan remote SSH access.
        if db.query(Account.id).filter(Account.user_id == user.id).first():
            raise HTTPException(409, "该用户仍有关联服务器账号，不能直接拒绝删除；请先核对并处理远端账号。")
        if db.query(DeviceUsage.id).filter(DeviceUsage.user_id == user.id, DeviceUsage.status == 'active').first():
            raise HTTPException(409, "该用户有使用中的登记，请先核对登记与账号状态。")
        application_ids = [a.id for a in user.applications]
        if application_ids:
            db.query(ApplicationPurpose).filter(ApplicationPurpose.application_id.in_(application_ids)).delete(synchronize_session=False)
        db.query(DeviceUsage).filter(DeviceUsage.user_id == user.id).delete(synchronize_session=False)
        db.delete(user)
    return {"msg": "已拒绝并删除待审核注册；如需加入可重新注册。"}


@router.post("/update", response_model=dict)
def update_user(
    data: UserUpdate,
    current_user: DBUser = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    # Permission check before locking, then use the same lock as key sync.
    is_self = current_user.id == data.id
    if not is_self and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    target = lock_usage_user(db, data.id, require_active=False)
    # If editing self, verify old password
    if is_self:
        if not data.old_password or not verify_password(data.old_password, target.password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect password")
    if db.query(DBUser).filter(DBUser.id != target.id, func.lower(DBUser.mail) == str(data.mail).lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email address already registered")
    next_key = data.public_key.strip()
    if next_key != (target.public_key or ''):
        revision = hashlib.sha256((target.public_key or '').encode()).hexdigest()
        if data.public_key_revision != revision:
            raise HTTPException(409, "公钥列表已更新或缺少版本信息（可能由网关同步添加）；请先保留当前草稿并刷新页面核对最新公钥，再提交修改，避免覆盖新增密钥。")
    # Apply updates
    target.realname = data.realname.strip()
    target.mail = str(data.mail).lower()
    needUpdateAccounts = target.public_key != data.public_key.strip()
    target.public_key = data.public_key.strip()
    # Update password if provided
    if data.new_password:
        target.password = get_password_hash(data.new_password)
    if needUpdateAccounts:
        # Update accounts if public key changed
        for account in target.accounts:
            account.status = AccountStatus.DIRTY
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email address already registered")
    return {"msg": "User updated"}
