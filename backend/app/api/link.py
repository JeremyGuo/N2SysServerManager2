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

def getPeer(conn : Connection, iface : Optional[ServerInterface] = None, sp : Optional[SwitchPort] = None) -> ServerInterface | SwitchPort:
    for peer in conn.interfaces:
        if not iface or peer.id != iface.id:
            return peer
    for peer in conn.switch_ports:
        if not sp or peer.id != sp.id:
            return peer
    import os
    logger.fatal(f"Peer not found for connection {conn.id}, interface {iface.id if iface else None}, switch port {sp.id if sp else None}, DB is inconsistent")
    os._exit(1)

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
    if pa.conn:
        db.delete(pa.conn)
        logger.info(f"Automatically deleting connection {pa.conn.id} for switch port {pa.id}")
    if pb.conn:
        db.delete(pb.conn)
        logger.info(f"Automatically deleting connection {pb.conn.id} for switch port {pb.id}")
    db.commit()
    db.refresh(pa)
    db.refresh(pb)

    conn = Connection()
    db.add(conn); db.commit(); db.refresh(conn)
    pa.conn_id = conn.id
    pb.conn_id = conn.id
    db.commit()
    return {"connection_id": conn.id}

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
    if sp.conn:
        db.delete(sp.conn)
        logger.info(f"Automatically deleting connection {sp.conn.id} for switch port {sp.id}")
    if iface.conn:
        db.delete(iface.conn)
        logger.info(f"Automatically deleting connection {iface.conn.id} for interface {iface.id}")
    db.commit()
    db.refresh(sp)
    db.refresh(iface)

    conn = Connection()
    db.add(conn); db.commit(); db.refresh(conn)
    sp.conn_id = conn.id
    iface.conn_id = conn.id
    db.commit()
    return {"connection_id": conn.id}

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
    if ia.conn:
        db.delete(ia.conn)
        logger.info(f"Automatically deleting connection {ia.conn.id} for interface {ia.id}")
    if ib.conn:
        db.delete(ib.conn)
        logger.info(f"Automatically deleting connection {ib.conn.id} for interface {ib.id}")
    db.commit()
    db.refresh(ia)
    db.refresh(ib)

    conn = Connection()
    db.add(conn); db.commit(); db.refresh(conn)
    ia.conn_id = conn.id
    ib.conn_id = conn.id
    db.commit()
    return {"connection_id": conn.id}

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
    conn = iface.conn
    if conn:
        db.delete(conn)
        db.commit()

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
    conn = sp.conn
    if conn:
        db.delete(conn)
        db.commit()

# List devices and their connections
@router.get("/devices", response_model=List[Dict])
def list_devices(
    db: Session = Depends(get_db),
    user: User = Depends(getUser)
):
    switches = db.query(Switch).all()
    result = []
    for sw in switches:
        ports = []
        for sp in sw.ports:
            peer = None
            if sp.conn_id:
                conn = db.query(Connection).filter(Connection.id == sp.conn_id).first()
                if not conn:
                    logger.fatal(f"Connection {sp.conn_id} not found for switch port {sp.id}, DB is inconsistent")
                    import os
                    os._exit(1)
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