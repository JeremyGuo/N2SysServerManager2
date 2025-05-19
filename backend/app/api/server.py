from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from fastapi.responses import JSONResponse

from app.database import get_db, Server, Application as DBApplication, Account, User
from validator import getUserAdmin, getUser

router = APIRouter()

@router.post("/add", response_model=dict)
def add_server(
    server_in: dict,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    srv = Server(
        host=server_in["host"],
        port=server_in["port"],
        is_gateway=server_in.get("isGateway", False)
    )
    db.add(srv); db.commit(); db.refresh(srv)
    return {"id": srv.id, "host": srv.host, "port": srv.port, "isGateway": srv.is_gateway}

@router.get("/list", response_model=List[Dict])
def list_servers(
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    servers = db.query(Server).all()
    return [
        {
            "id": s.id,
            "host": s.host,
            "port": s.port,
            "gateway": s.is_gateway,
            "os": s.os_version,
            "kernel": s.kernel_version,
            "tags": [t.tag for t in s.tags]
        }
        for s in servers
    ]