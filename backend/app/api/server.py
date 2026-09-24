from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from fastapi.responses import JSONResponse

from app.database import get_db, Server, ServerTag, User, ServerInterface, InterfaceTag, Connection, SwitchPort
from validator import getUserAdmin, getUser
from pydantic import BaseModel, Field
import os

from app.hardware_store import hardware_for_servers, hardware_for_server

router = APIRouter()

class ServerCreate(BaseModel):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$")
    port: int = Field(ge=1, le=65535)
    proxyServerId: int | None = None
    isGateway: bool = False

@router.post("/add", response_model=dict)
def add_server(
    server_in: ServerCreate,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    if server_in.proxyServerId is not None and not db.get(Server, server_in.proxyServerId):
        raise HTTPException(404, "所选代理服务器不存在，请刷新服务器列表。")
    if db.query(Server).filter_by(host=server_in.host, port=server_in.port).first():
        raise HTTPException(409, "相同主机和 SSH 端口的服务器已存在。")
    srv = Server(
        host=server_in.host,
        port=server_in.port,
        proxy_server_id=server_in.proxyServerId,
        is_gateway=server_in.isGateway
    )
    db.add(srv); db.commit(); db.refresh(srv)
    return {"id": srv.id, "host": srv.host, "port": srv.port, "isGateway": srv.is_gateway}

@router.get("/search", response_model=List[Dict])
def search_servers_by_interface_manufacturer(
    manufacturer: str,
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    interfaces = (
        db.query(ServerInterface)
          .join(Server)
          .filter(ServerInterface.manufacturer.ilike(f"%{manufacturer}%"))
          .all()
    )
    return [
        {
            "server_id": iface.server.id,
            "host": iface.server.host,
            "interface_id": iface.id,
            "manufacturer": iface.manufacturer
        }
        for iface in interfaces
    ]

@router.get("/list", response_model=List[Dict])
def list_servers(
    user: User = Depends(getUser),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    servers = db.query(Server).all()
    hardware = hardware_for_servers(db, [s.id for s in servers])
    return [
        {
            "id": s.id,
            "host": s.host,
            "port": s.port,
            "gateway": s.is_gateway,
            "os": s.os_version,
            "kernel": s.kernel_version,
            "hardware": hardware[s.id],
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
        "hardware": hardware_for_server(db, srv.id),
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

class ServerRefreshIn(BaseModel):
    server_id: int

@router.post("/refresh", response_model=dict)
async def refresh_server(
    data: ServerRefreshIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db),
):
    if not db.get(Server, data.server_id):
        raise HTTPException(404, "服务器不存在。")
    if os.getenv("SYNC_ENABLED", "true").lower() != "true":
        raise HTTPException(503, "后台 SSH 同步已关闭，请管理员设置 SYNC_ENABLED=true 后重启服务。")
    from account_sync import request_server_refresh
    request_server_refresh(data.server_id)
    return {"msg": "已请求刷新，将在下一次同步周期采集（约30秒）；请稍后刷新页面查看状态。"}