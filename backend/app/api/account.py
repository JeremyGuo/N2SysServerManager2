from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db, Account, User, AccountStatus
from validator import getUserAdmin
from usage_service import end_account_usage, lock_usage_user, cancel_sudo_application

router = APIRouter()

@router.put("/{account_id}/sudo", status_code=status.HTTP_204_NO_CONTENT)
def toggle_sudo(
    account_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    if acct.server.is_gateway:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Gateway sudo is derived from the user's administrator status; change that status instead")
    lock_usage_user(db, acct.user_id, require_active=False)
    db.refresh(acct)
    acct.is_sudo = not acct.is_sudo
    acct.status = AccountStatus.DIRTY
    if acct.is_sudo:
        cancel_sudo_application(db, acct.user_id, acct.server_id)
    db.commit()
    return {"msg": "Sudo status toggled"}

@router.put("/{account_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_account(
    account_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    if acct.server.is_gateway:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Gateway accounts are maintained automatically and cannot be revoked individually; graduate the user instead")
    lock_usage_user(db, acct.user_id, require_active=False)
    db.refresh(acct)
    acct.is_login_able = False
    acct.is_sudo = False
    acct.status = AccountStatus.DIRTY
    end_account_usage(db, acct.user_id, acct.server_id)
    db.commit()
    return {"msg": "Account revoked"}
