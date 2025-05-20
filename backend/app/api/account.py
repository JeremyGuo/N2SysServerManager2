from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db, Account, User, AccountStatus
from validator import getUserAdmin

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
    acct.is_sudo = not acct.is_sudo
    acct.status = AccountStatus.DIRTY
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
    acct.is_login_able = False
    acct.is_sudo = False
    acct.status = AccountStatus.DIRTY
    db.commit()
    return {"msg": "Account revoked"}
