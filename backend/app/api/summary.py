from activity_time import activity_iso
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Dict

from app.database import get_db, Server, User
from validator import getUser

router = APIRouter()

@router.get("/get", response_model=List[Dict])
def get_summary(
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    servers = db.query(Server).all()
    result = []
    for srv in servers:
        result.append({
            "id": srv.id,
            "host": srv.host,
            "status": srv.server_status.value,
            "isGateway": srv.is_gateway,
            "isMounted": srv.is_mounted_home,
            "users": [
                {
                    "id": acct.id,
                    "user": acct.user.realname,
                    "username": acct.user.username,
                    "account_name": acct.user.account_name,
                    "sudo": acct.is_sudo,
                    "isAdmin": acct.user.is_admin,
                    "lastLogin": activity_iso(acct.last_login_date)
                }
                for acct in srv.accounts if acct.is_login_able
            ]
        })
    return JSONResponse(
        content=result,
        status_code=status.HTTP_200_OK
    )


@router.get("/sync-errors")
async def sync_errors(user: User = Depends(getUser)):
    if not user:
        raise HTTPException(401, "请先登录。")
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可查看后台同步错误。")
    from account_sync import get_sync_errors
    return get_sync_errors()
