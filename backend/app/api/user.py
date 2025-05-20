from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from pydantic import BaseModel, EmailStr

from app.database import get_db, User as DBUser, Application, Account, UserStatus, AccountStatus
from validator import getUser, getUserAdmin
from app.api.auth import verify_password, get_password_hash

router = APIRouter()

class UserUpdate(BaseModel):
    id: int
    realname: str
    mail: EmailStr
    public_key: str
    old_password: str = ''
    new_password: str = ''

@router.get("/me")
def read_current_user(
    user: DBUser = Depends(getUser)
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

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
    return [
        {
            "id": app.id,
            "host": app.server.host,
            "need_sudo": app.need_sudo,
            "create_date": app.create_date
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
            "last_login_date": acct.last_login_date
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
    return [{"id": u.id, "username": u.username, "status": u.status, "is_admin": u.is_admin} for u in users]

@router.post("/user/{user_id}/approve", response_model=dict)
def approve_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.status = UserStatus.ACTIVE
    db.commit()
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
    user.is_admin = False
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
    user.status = UserStatus.GRADUATED
    db.commit()
    return {"msg": "User graduated"}

@router.post("/user/{user_id}/grant-admin", response_model=dict)
def grant_admin_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.is_admin = True
    db.commit()
    return {"msg": "Admin rights granted"}

@router.post("/user/{user_id}/restore", response_model=dict)
def restore_user(
    user_id: int,
    admin: DBUser = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.status = UserStatus.ACTIVE
    db.commit()
    return {"msg": "User restored"}

@router.post("/update", response_model=dict)
def update_user(
    data: UserUpdate,
    current_user: DBUser = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    target = db.query(DBUser).filter(DBUser.id == data.id).first()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    # Permission check: self or admin
    is_self = current_user.id == data.id
    if not is_self and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    # If editing self, verify old password
    if is_self:
        if not data.old_password or not verify_password(data.old_password, target.password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect password")
    # Apply updates
    target.realname = data.realname.strip()
    target.mail = data.mail
    needUpdateAccounts = target.public_key != data.public_key.strip()
    target.public_key = data.public_key.strip()
    # Update password if provided
    if data.new_password:
        target.password = get_password_hash(data.new_password)
    if needUpdateAccounts:
        # Update accounts if public key changed
        for account in target.accounts:
            account.status = AccountStatus.DIRTY
    db.commit()
    return {"msg": "User updated"}
