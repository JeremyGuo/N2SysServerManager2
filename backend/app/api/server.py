from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from fastapi.responses import JSONResponse

from app.database import get_db, Server, ServerTag, User
from validator import getUserAdmin, getUser
from pydantic import BaseModel

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

@router.get("/{server_id}", response_model=dict)
def get_server_detail(
    server_id: int,
    user = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    srv = db.query(Server).filter(Server.id == server_id).first()
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")
    return {
        "id": srv.id,
        "host": srv.host,
        "port": srv.port,
        "is_gateway": srv.is_gateway,
        "server_status": srv.server_status.value,
        "is_separated_home": srv.is_mounted_home,
        "os_version": srv.os_version,
        "kernel_version": srv.kernel_version,
        "ipmi": srv.ipmi,
        "tags": [{"id": t.id, "tag": t.tag} for t in srv.tags],
        "interfaces": [
            {
                "id": i.id,
                "interface": i.interface,
                "pci_address": i.pci_address,
                "tags": [{"id": tg.id, "tag": tg.tag} for tg in i.tags]
            }
            for i in srv.interfaces
        ]
    }

class ServerTagAddIn(BaseModel):
    server_id: int
    tag: str

@router.post("/tag/add", response_model=dict)
def add_server_tag(
    data: ServerTagAddIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    srv = db.query(Server).filter(Server.id == data.server_id).first()
    if not srv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    tag = ServerTag(server_id=srv.id, tag=data.tag.strip())
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "tag": tag.tag}

class ServerTagRemoveIn(BaseModel):
    tag_id: int

@router.post("/tag/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_server_tag(
    data: ServerTagRemoveIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    tag = db.query(ServerTag).filter(ServerTag.id == data.tag_id).first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    db.delete(tag)
    db.commit()
    return {}

class ServerIpmiIn(BaseModel):
    server_id: int
    ipmi: str

@router.post("/ipmi", response_model=dict)
def update_ipmi(
    data: ServerIpmiIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    srv = db.query(Server).filter(Server.id == data.server_id).first()
    if not srv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    srv.ipmi = data.ipmi.strip()
    db.commit()
    return {"msg": "IPMI updated"}

@router.post("/refresh", response_model=dict)
def refresh_server(
    admin: User = Depends(getUserAdmin)
):
    # TODO: 调用实际刷新逻辑
    return {"msg": "Server refresh triggered"}