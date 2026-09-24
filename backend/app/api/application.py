from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, Server, Application, User, DeviceUsage, ApplicationPurpose, AccountStatus
from validator import getUser, getUserAdmin
from logger import logger
from typing import List, Dict
from usage_service import (usage_transaction, lock_usage_user, find_usage,
                           grant_account, register_usage, activate_application_usage,
                           reject_application_usage, delete_application, usable_account)

router = APIRouter()


class ApplicationCreate(BaseModel):
    server_id: int
    need_sudo: bool
    uid: int


@router.post('/submit', response_model=dict)
def submit_application(
    app_in: ApplicationCreate,
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')
    if not user.is_admin and app_in.uid != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Cannot submit an application for another user')
    with usage_transaction(db):
        target = lock_usage_user(db, app_in.uid)
        srv = db.query(Server).filter(Server.id == app_in.server_id).first()
        if not srv:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Server not found')
        pending = db.query(Application).filter(
            Application.user_id == target.id, Application.server_id == srv.id
        ).all()
        registration = find_usage(db, target.id, srv.id)
        if not user.is_admin:
            if pending or (registration is not None and registration.status in ('pending', 'active')):
                raise HTTPException(status.HTTP_409_CONFLICT, 'An application or usage registration for this server already exists')
            if srv.is_gateway:
                raise HTTPException(status.HTTP_409_CONFLICT, 'Gateway accounts are maintained automatically; sudo follows administrator status')
        logger.info(f'User {user.username} is submitting an application for server {app_in.server_id}')
        if user.is_admin:
            new_acc = grant_account(db, target, srv, app_in.need_sudo)
            if not srv.is_gateway:
                if registration is not None and registration.status == 'active':
                    # An administrator regrant is not a fresh use confirmation.
                    registration.application_id = None
                elif pending:
                    # Preserve the requester's reason and request timestamp.
                    activate_application_usage(db, pending[0])
                else:
                    registration = register_usage(db, target, srv, 'active', '原有申请', existing=registration)
                    if target.id != user.id:
                        registration.confirmed_at = None
            for pending_app in pending:
                delete_application(db, pending_app)
            db.flush()
            result = {'account_id': new_acc.id, 'host': srv.host, 'is_sudo': new_acc.is_sudo}
        else:
            # Retain the legacy application's 201 shape and permission workflow.
            new_app = Application(user_id=target.id, server_id=srv.id, need_sudo=app_in.need_sudo)
            db.add(new_app)
            db.flush()
            register_usage(db, target, srv, 'pending', '原有申请', new_app, registration)
            result = {
                'id': new_app.id, 'host': srv.host, 'need_sudo': new_app.need_sudo,
                'create_date': new_app.create_date.isoformat(),
            }
    return JSONResponse(content=result, status_code=status.HTTP_201_CREATED)


@router.get('/pendings', response_model=List[Dict])
def list_pending(
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    apps = db.query(Application).all()
    reasons = {r.application_id: r.reason for r in db.query(DeviceUsage).filter(DeviceUsage.status == 'pending').all()
               if r.application_id is not None}
    purposes = {p.application_id: p for p in db.query(ApplicationPurpose).all()}
    return [
        {
            'id': app.id, 'user_id': app.user_id, 'realname': app.user.realname,
            'username': app.user.username, 'server_id': app.server_id,
            'host': app.server.host, 'need_sudo': app.need_sudo,
            'create_date': app.create_date.isoformat(),
            'reason': purposes[app.id].reason if app.id in purposes else reasons.get(app.id, '原有申请'),
            'kind': purposes[app.id].kind if app.id in purposes else 'access',
        }
        for app in apps
    ]


def _locked_application(db, app_id, require_active=True):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Application not found')
    lock_usage_user(db, app.user_id, require_active=require_active)
    # Cancel/reject/approve may have removed the application while waiting.
    app = db.query(Application).filter(Application.id == app_id).populate_existing().with_for_update().first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Application not found')
    return app


@router.post('/{app_id}/approve', response_model=dict)
def approve_application(
    app_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    with usage_transaction(db):
        app = _locked_application(db, app_id)
        purpose = db.get(ApplicationPurpose, app.id)
        if purpose is not None and purpose.kind == 'sudo':
            usage = find_usage(db, app.user_id, app.server_id)
            acct = usable_account(db, app.user_id, app.server_id)
            if not usage or usage.status != 'active' or app.server.is_gateway or not acct:
                raise HTTPException(409, '使用登记或账号状态已改变，不能批准该 sudo 升级；请拒绝过期申请。')
            acct.is_sudo = True
            acct.status = AccountStatus.DIRTY
            # A permission upgrade is not a new registration/login/confirmation.
        else:
            acct = grant_account(db, app.user, app.server, app.need_sudo)
            activate_application_usage(db, app)
        delete_application(db, app)
        db.flush()
        account_id = acct.id
    return {'account_id': account_id}


@router.post('/{app_id}/reject', status_code=204)
def reject_application(
    app_id: int,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    with usage_transaction(db):
        app = _locked_application(db, app_id, require_active=False)
        purpose = db.get(ApplicationPurpose, app.id)
        if purpose is None or purpose.kind != 'sudo':
            reject_application_usage(db, app)
        delete_application(db, app)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
