import json
import os
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal, Base, Server, Switch, SwitchPort, ServerInterface, Connection, ServerStatus
from typing import Dict

def main():
    # ensure tables exist
    db: Session = SessionLocal()
    try:
        base_dir = os.path.dirname(__file__)
        # load old JSON data
        with open(os.path.join(base_dir, '_old_data_server.json')) as f:
            old_servers = json.load(f)
        with open(os.path.join(base_dir, '_old_data_switch.json')) as f:
            old_switches = json.load(f)
        with open(os.path.join(base_dir, '_old_data_switch_port.json')) as f:
            old_ports = json.load(f)
        with open(os.path.join(base_dir, '_old_data_server_interface.json')) as f:
            old_ifaces = json.load(f)

        # maps from old id to new ORM object
        server_map : Dict[int, Server] = {}
        switch_map : Dict[int, Switch] = {}
        port_map : Dict[int, SwitchPort] = {}
        iface_map : Dict[int, ServerInterface] = {}

        # migrate servers
        for o in old_servers:
            srv = db.query(Server).filter_by(host=o['host']).first()
            if not srv:
                print(f"Server not found in DB. error")
                os._exit(1)
            server_map[o['id']] = srv

        # migrate switches
        for o in old_switches:
            sw = db.query(Switch).filter_by(name=o['name']).first()
            if not sw:
                print(f"Switch not found in DB. error")
                os._exit(1)
            switch_map[o['id']] = sw

        # migrate switch ports
        for o in old_ports:
            parent = switch_map.get(o.get('switch_id'))
            if not parent:
                continue
            phy_row = o.get('phy_row')
            phy_col = o.get('phy_col')
            if not phy_row or not phy_col:
                print(f"Switch port {o['id']} has no phy_row or phy_col. error")
                os._exit(1)
            sp = db.query(SwitchPort).filter_by(
                switch_id=parent.id,
                phy_row=phy_row,
                phy_col=phy_col
            ).first()
            if not sp:
                print(f"Switch port {o['id']} not found in DB. error")
                os._exit(1)
            port_map[o['id']] = sp

        # migrate server interfaces
        for o in old_ifaces:
            srv = server_map.get(o.get('server_id'))
            if not srv:
                continue
            pciaddr = o.get('pci_address')
            if not pciaddr:
                print(f"Server interface {o['id']} has no pci_address. error")
                os._exit(1)
            si = db.query(ServerInterface).filter_by(
                server_id=srv.id,
                pci_address=pciaddr
            ).first()
            if not si:
                print(f"Server interface {o['id']} not found in DB. error")
                os._exit(1)
            iface_map[o['id']] = si

        # create connections between switch ports and server interfaces
        for o in old_ifaces:
            iface = iface_map.get(o['id'])
            if not iface:
                continue
            if not o.get('switch_port_id'):
                continue
            port = port_map.get(o.get('switch_port_id'))
            if not port:
                continue
            if port.conn_id is not None or iface.conn_id is not None:
                if port.conn_id != iface.conn_id:
                    print(f"Switch port {port.id} and server interface {iface.id} have different connection IDs. error")
                    os._exit(1)
                continue
            new_conn = Connection()
            db.add(new_conn); db.commit(); db.refresh(new_conn)
            iface.conn_id = new_conn.id
            port.conn_id = new_conn.id
            db.commit()
        
        # create connections between server interfaces
        for o1 in old_ifaces:
            if not o1.get('direct_conn_id'):
                continue
            for o2 in old_ifaces:
                if o1['id'] == o2['id']:
                    continue
                if not o2.get('direct_conn_id') or o1['direct_conn_id'] != o2['direct_conn_id']:
                    continue
                iface1 = iface_map.get(o1['id'])
                iface2 = iface_map.get(o2['id'])
                if not iface1 or not iface2:
                    continue    
                if iface1.conn_id is not None or iface2.conn_id is not None:
                    if iface1.conn_id != iface2.conn_id:
                        print(f"Server interface {iface1.id} and {iface2.id} have different connection IDs. error")
                        os._exit(1)
                    continue
                new_conn = Connection()
                db.add(new_conn); db.commit(); db.refresh(new_conn)
                iface1.conn_id = new_conn.id    
                iface2.conn_id = new_conn.id
                db.commit()

        # create connections between switch ports
        for o1 in old_ports:
            if not o1.get('direct_switch_id'):
                continue
            for o2 in old_ports:
                if o1['id'] == o2['id']:
                    continue
                if not o2.get('direct_switch_id') or o1['direct_switch_id'] != o2['direct_switch_id']:
                    continue
                port1 = port_map.get(o1['id'])
                port2 = port_map.get(o2['id'])
                if not port1 or not port2:
                    continue    
                if port1.conn_id is not None or port2.conn_id is not None:
                    if port1.conn_id != port2.conn_id:
                        print(f"Switch port {port1.id} and {port2.id} have different connection IDs. error")
                        os._exit(1)
                    continue
                new_conn = Connection()
                db.add(new_conn); db.commit(); db.refresh(new_conn)
                port1.conn_id = new_conn.id    
                port2.conn_id = new_conn.id
                db.commit()

    finally:
        db.close()


if __name__ == '__main__':
    main()