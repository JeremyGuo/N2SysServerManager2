from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict
from fastapi.responses import JSONResponse

from app.database import get_db, Server, ServerTag, User, ServerInterface, InterfaceTag, Switch, SwitchPort, Connection
from validator import getUserAdmin, getUser
from pydantic import BaseModel
from typing import Optional

from logger import logger

router = APIRouter()

def inconsistent_connection(conn_id):
    logger.error(f"Inconsistent connection {conn_id}")
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Connection {conn_id} is inconsistent: expected exactly two valid endpoints. Disconnect and reconnect its ports/interfaces, or ask an administrator to repair dangling connection references.",
    )


def getPeer(conn: Connection, iface: Optional[ServerInterface] = None,
            sp: Optional[SwitchPort] = None) -> ServerInterface | SwitchPort:
    endpoints = list(conn.interfaces) + list(conn.switch_ports)
    source = iface if iface is not None else sp
    if len(endpoints) != 2 or source is None:
        raise inconsistent_connection(conn.id)
    peers = [peer for peer in endpoints
             if not (type(peer) is type(source) and peer.id == source.id)]
    if len(peers) != 1:
        raise inconsistent_connection(conn.id)
    return peers[0]


def endpoint_connection(endpoint):
    if endpoint.conn_id is not None and endpoint.conn is None:
        raise inconsistent_connection(endpoint.conn_id)
    return endpoint.conn


def delete_connections(db: Session, connections):
    # Two endpoints can share one old connection. Delete it only once, and
    # explicitly detach ALL peers (also works when SQLite FK actions are off).
    unique = {conn.id: conn for conn in connections if conn is not None}
    for conn in unique.values():
        for endpoint in list(conn.interfaces) + list(conn.switch_ports):
            endpoint.conn = None
    db.flush()
    for conn in unique.values():
        db.delete(conn)
    db.flush()


def replace_connection(db: Session, endpoint_a, endpoint_b):
    try:
        delete_connections(db, [endpoint_connection(endpoint_a), endpoint_connection(endpoint_b)])
        conn = Connection()
        db.add(conn)
        endpoint_a.conn = conn
        endpoint_b.conn = conn
        db.flush()
        connection_id = conn.id
        db.commit()
        return {"connection_id": connection_id}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Connection replacement failed")
        raise HTTPException(500, "Failed to replace connection; previous connections were preserved")


def disconnect_endpoint(db: Session, endpoint):
    conn = endpoint_connection(endpoint)
    if conn is None:
        return
    try:
        delete_connections(db, [conn])
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Connection deletion failed")
        raise HTTPException(500, "Failed to disconnect; previous connection was preserved")


# Connect two switch ports
class ConnectSwitchPortsIn(BaseModel):
    port_a_id: int
    port_b_id: int

@router.post("/switch_port/connect", response_model=dict)
def connect_switch_ports(
    data: ConnectSwitchPortsIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    if data.port_a_id == data.port_b_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot connect the same port")
    pa = db.query(SwitchPort).filter(SwitchPort.id == data.port_a_id).first()
    pb = db.query(SwitchPort).filter(SwitchPort.id == data.port_b_id).first()
    if not pa or not pb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Switch port not found")
    logger.info(f"Connecting switch ports {pa.id} and {pb.id}")
    return replace_connection(db, pa, pb)

# Connect switch port and server interface
class ConnectPortInterfaceIn(BaseModel):
    switch_port_id: int
    interface_id: int

@router.post("/switch_port/interface/connect", response_model=dict)
def connect_port_interface(
    data: ConnectPortInterfaceIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    sp = db.query(SwitchPort).filter(SwitchPort.id == data.switch_port_id).first()
    iface = db.query(ServerInterface).filter(ServerInterface.id == data.interface_id).first()
    if not sp or not iface:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port or interface not found")
    logger.info(f"Connecting switch port {sp.id} and interface {iface.id}")
    return replace_connection(db, sp, iface)

# Connect two server interfaces
class ConnectInterfacesIn(BaseModel):
    interface_a_id: int
    interface_b_id: int

@router.post("/interface/connect", response_model=dict)
def connect_interfaces(
    data: ConnectInterfacesIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    if data.interface_a_id == data.interface_b_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot connect the same interface")
    ia = db.query(ServerInterface).filter(ServerInterface.id == data.interface_a_id).first()
    ib = db.query(ServerInterface).filter(ServerInterface.id == data.interface_b_id).first()
    if not ia or not ib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Interface not found {data.interface_a_id} {data.interface_b_id}")
    logger.info(f"Connecting interfaces {ia.id} and {ib.id}")
    return replace_connection(db, ia, ib)

# Disconnect a server interface by ID
class DisconnectInterfaceIn(BaseModel):
    interface_id: int

@router.post("/interface/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_interface(
    data: DisconnectInterfaceIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    iface = db.query(ServerInterface).filter(ServerInterface.id == data.interface_id).first()
    if not iface:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Interface not connected")
    disconnect_endpoint(db, iface)

# Disconnect a switch port by ID
class DisconnectSwitchPortIn(BaseModel):
    switch_port_id: int

@router.post("/switch_port/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_switch_port(
    data: DisconnectSwitchPortIn,
    admin: User = Depends(getUserAdmin),
    db: Session = Depends(get_db)
):
    sp = db.query(SwitchPort).filter(SwitchPort.id == data.switch_port_id).first()
    if not sp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Port not connected")
    disconnect_endpoint(db, sp)

# List devices and their connections
@router.get("/devices", response_model=List[Dict])
def list_devices(
    db: Session = Depends(get_db),
    user: User = Depends(getUser)
):
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    switches = db.query(Switch).all()
    result = []
    for sw in switches:
        ports = []
        for sp in sw.ports:
            peer = None
            if sp.conn_id is not None:
                conn = db.query(Connection).filter(Connection.id == sp.conn_id).first()
                if not conn:
                    raise inconsistent_connection(sp.conn_id)
                # look for peer switch port
                peer_port = getPeer(conn, sp=sp)
                if isinstance(peer_port, ServerInterface):
                    peer = {"id": peer_port.id, "type": "interface", "name": peer_port.interface, "manufacturer": peer_port.manufacturer, "pci_address": peer_port.pci_address, "server_host": peer_port.server.host}
                elif isinstance(peer_port, SwitchPort):
                    # calculate switch port number as col_index * num_row + row_index
                    peer_port_num = peer_port.phy_col * peer_port.switch.num_row + peer_port.phy_row + 1
                    peer = {"id": peer_port.id, "type": "switch_port", "name": f"{peer_port.switch.name} {peer_port_num}"}
            # calculate switch port number as col_index * num_row + row_index
            port_num = sp.phy_col * sw.num_row + sp.phy_row + 1
            ports.append({"id": sp.id, "name": port_num, "phy_row": sp.phy_row, "phy_col": sp.phy_col, "tag": sp.tag, "connected_to": peer})
        result.append({"id": sw.id, "name": sw.name, "num_row": sw.num_row, "num_col": sw.num_col, "ports": ports})
    return result