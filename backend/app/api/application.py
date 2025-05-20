from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, Server, Application, User, Account, AccountStatus
from validator import getUser
from logger import logger
from validator import getUserAdmin
from typing import List, Dict
from datetime import datetime

router = APIRouter()

class ApplicationCreate(BaseModel):
    server_id: int
    need_sudo: bool
    uid: int

@router.post("/submit", response_model=dict)
def submit_application(
    app_in: ApplicationCreate,
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    logger.info(f"User {user.username} is submitting an application for server {app_in.server_id}")
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    srv = db.query(Server).filter(Server.id == app_in.server_id).first()
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Server not found")

    # admin immediate account creation
    if user.is_admin:
        target = db.query(User).filter(User.id == app_in.uid).first()
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Target user not found")
        new_acc = db.query(Account).filter(Account.user_id == app_in.uid, Account.server_id == app_in.server_id).first()
        if new_acc:
            new_acc.is_sudo = app_in.need_sudo
            new_acc.is_login_able = True
            new_acc.status = AccountStatus.DIRTY
            new_acc.last_login_date = datetime.now()
        else:
            new_acc = Account(
                user_id=app_in.uid,
                server_id=app_in.server_id,
                is_sudo=app_in.need_sudo
            )
            db.add(new_acc)
        db.commit()
        db.refresh(new_acc)
        return JSONResponse(
            content={
                "account_id": new_acc.id,
                "host": srv.host,
                "is_sudo": new_acc.is_sudo
            },
            status_code=status.HTTP_201_CREATED
        )

    # regular user application flow
    new_app = Application(
        user_id=app_in.uid,
        server_id=app_in.server_id,
        need_sudo=app_in.need_sudo
    )
    db.add(new_app)
    db.commit()
    db.refresh(new_app)
    return JSONResponse(
        content={
            "id": new_app.id,
            "host": srv.host,
            "need_sudo": new_app.need_sudo,
            "create_date": new_app.create_date.isoformat()
        },
        status_code=status.HTTP_201_CREATED
    )

@router.get("/pendings", response_model=List[Dict])
def list_pending(
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    apps = db.query(Application).all()
    return [
        {
          "id": app.id,
          "user_id": app.user_id,
          "realname": app.user.realname,
          "username": app.user.username,
          "server_id": app.server_id,
          "host": app.server.host,
          "need_sudo": app.need_sudo,
          "create_date": app.create_date.isoformat()
        }
        for app in apps
    ]

@router.post("/{app_id}/approve", response_model=dict)
def approve_application(
    app_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    app : Application = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    acct = db.query(Account).filter(Account.user_id == app.user_id, Account.server_id == app.server_id).first()
    if acct:
        acct.is_sudo = app.need_sudo
        acct.is_login_able = True
        acct.status = AccountStatus.DIRTY
        acct.last_login_date = datetime.now()
    else:
        acct = Account(user_id=app.user_id, server_id=app.server_id, is_sudo=app.need_sudo)
        db.add(acct)
    db.delete(app)
    db.commit()
    db.refresh(acct)
    return {"account_id": acct.id}

@router.post("/{app_id}/reject", status_code=204)
def reject_application(
    app_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    app = db.query(Application).get(app_id)
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    db.delete(app); db.commit()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content={})
