from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from fastapi.responses import JSONResponse

from app.database import get_db, Server, ServerTag, User, ServerInterface, InterfaceTag, Connection, SwitchPort
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
        proxy_server_id=server_in.get("proxyServerId", None),
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
            "tags": [t.tag for t in s.tags],
            "proxy": {"id": s.proxy_server.id, "host": s.proxy_server.host, "port": s.proxy_server.port} if s.proxy_server else None
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
    result = {
        "id": srv.id,
        "host": srv.host,
        "port": srv.port,
        "is_gateway": srv.is_gateway,
        "proxy_server_id": srv.proxy_server_id,
        "proxy_server": {"id": srv.proxy_server.id, "host": srv.proxy_server.host, "port": srv.proxy_server.port} if srv.proxy_server else None,
        "server_status": srv.server_status.value,
        "is_separated_home": srv.is_mounted_home,
        "os_version": srv.os_version,
        "kernel_version": srv.kernel_version,
        "ipmi": srv.ipmi,
        "tags": [{"id": t.id, "tag": t.tag} for t in srv.tags],
        "interfaces": []
    }
    for i in srv.interfaces:
        peer_interface = None
        peer_switch = None
        if i.conn_id:
            conn = db.query(Connection).get(i.conn_id)
            if conn:
                # try find other interface
                for pi in conn.interfaces:
                    if pi.id != i.id:
                        peer_interface = {
                            "id": pi.id,
                            "server_id": pi.server_id,
                            "server_host": pi.server.host,
                            "interface": pi.interface,
                            "manufacturer": pi.manufacturer,
                            "pci_address": pi.pci_address
                        }
                        break
                # else check switch ports
                if not peer_interface and conn.switch_ports:
                    sp = conn.switch_ports[0]
                    peer_switch = {
                        "switch_id": sp.switch_id,
                        "switch_name": sp.switch.name,
                        "phy_row": sp.phy_row,
                        "phy_col": sp.phy_col,
                        "port_num": sp.phy_col * sp.switch.num_row + sp.phy_row + 1
                    }
        result["interfaces"].append({
            "id": i.id,
            "interface": i.interface,
            "pci_address": i.pci_address,
            "manufacturer": i.manufacturer,
            "tags": [{"id": tg.id, "tag": tg.tag} for tg in i.tags],
            "peer_interface": peer_interface,
            "peer_switch": peer_switch
        })
    return result

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

class InterfaceTagAddIn(BaseModel):
    interface_id: int
    tag: str

@router.post("/interface/tag/add", response_model=dict)
def add_interface_tag(
    data: InterfaceTagAddIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    iface = db.query(ServerInterface).filter(ServerInterface.id == data.interface_id).first()
    if not iface:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interface not found")
    tag = InterfaceTag(interface_id=iface.id, tag=data.tag.strip())
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "tag": tag.tag}

class InterfaceTagRemoveIn(BaseModel):
    tag_id: int

@router.post("/interface/tag/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_interface_tag(
    data: InterfaceTagRemoveIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    tag = db.query(InterfaceTag).filter(InterfaceTag.id == data.tag_id).first()
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