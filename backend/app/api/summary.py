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
                    "sudo": acct.is_sudo,
                    "lastLogin": acct.last_login_date.strftime("%Y-%m-%d %H:%M:%S")
                }
                for acct in srv.accounts if acct.is_login_able
            ]
        })
    return JSONResponse(
        content=result,
        status_code=status.HTTP_200_OK
    )
