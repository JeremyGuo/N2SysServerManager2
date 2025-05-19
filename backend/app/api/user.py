from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict

from app.database import get_db, User as DBUser, Application, Account, UserStatus
from validator import getUser, getUserAdmin

router = APIRouter()

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
