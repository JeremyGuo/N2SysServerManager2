import asyncio
from app.database import Account, SessionLocal, AccountStatus, Server, User, UserStatus, ServerStatus, ServerInterface
from logger import logger
from sqlalchemy.orm import make_transient
from account_helpers import *
from server_helpers import *
import asyncssh
import copy
from contextlib import asynccontextmanager
import datetime
# async semaphore to limit the number of concurrent tasks

concurrent_tasks = 20
semaphore = asyncio.Semaphore(concurrent_tasks)

syncing_accounts = {}
last_server_collect_date = {}
last_server_collecting = {}
start_watcher = False

def startWatcher():
    global start_watcher
    start_watcher = True
    loop = asyncio.get_event_loop()
    loop.create_task(watchAccountSync())

async def stopWatcher():
    global start_watcher
    start_watcher = False
    waiting_ticks = 0
    while True:
        logger.info(f"Waiting for tasks to finish. Waiting {waiting_ticks} seconds.")
        waiting_ticks += 1
        finished = True
        for account_id in syncing_accounts:
            if syncing_accounts[account_id]:
                finished = False
        for server_id in last_server_collecting:
            if last_server_collecting[server_id]:
                finished = False
        if finished:
            break
        await asyncio.sleep(1)
    logger.info("All tasks finished. Stopping watcher.")

@asynccontextmanager
async def getConnection(srv: Server):
    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == srv.id).first()
        if not server:
            raise Exception(f"Server {server.id} not found in database while getting connection.")
        if not server.proxy_server:
            host = server.host
            port = server.port
            db.close()
            logger.info(f"Connecting to server {host}:{port} directly.")
            conn = await asyncio.wait_for(asyncssh.connect(host=host, port=port, known_hosts=None), timeout=3) # TODO: add known_hosts
            yield conn
            conn.close()
        else:
            logger.info(f"Connecting to server {server.host}:{server.port} through proxy {server.proxy_server.host}:{server.proxy_server.port}")
            # generate ssh config
            ssh_config_path = './ssh/'
            ssh_config_file = f"{ssh_config_path}ssh_config_{server.id}"
            # mkdir if not exists
            import os
            if not os.path.exists(ssh_config_path):
                os.makedirs(ssh_config_path)
            with open(ssh_config_file, 'w') as f:
                f.write(f"Host {server.proxy_server.host}\n")
                f.write(f"    HostName {server.proxy_server.host}\n")
                f.write(f"    Port {server.proxy_server.port}\n")
                f.write(f"\n")
                f.write(f"Host {server.host}\n")
                f.write(f"    HostName {server.host}\n")
                f.write(f"    Port {server.port}\n")
                f.write(f"    ProxyJump {server.proxy_server.host}:{server.proxy_server.port}\n")
            default_config_path = os.path.expanduser("~/.ssh/config")
            host = server.host
            port = server.port
            db.close()
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=host,
                    port=port,
                    known_hosts=None,
                    config=[default_config_path, ssh_config_file]
                ),
                timeout=6
            )
            yield conn
            conn.close()
    except Exception as e:
        logger.error(f"Error connecting to server {srv.host}: {e}")
        raise e

async def doSyncAccount(user: User, server: Server, account: Account):
    async with semaphore:
        logger.info(f"Connecting to server {server.host}")
        async with getConnection(server) as conn:
            logger.info(f"Connected to server {server.host}")
            # Step 1 - Check if the account exists if not create it
            if not await sshAccountIsExists(conn, user.account_name):
                logger.info(f"Account {user.account_name} does not exist. Creating it.")
                result, err = await sshAccountCreate(conn, user.account_name)
                if not result:
                    raise Exception(f"Error creating account {user.account_name} on {server.host}: {err}")
            else:
                logger.info(f"Account {user.account_name} already exists. Skipping creation.")
            # Step 2 - Make the account the same loginable as the account
            if account.is_login_able:
                old_authorized_keys = await sshAccountGetAuthorizedKeys(conn, user.account_name)
                new_authorized_keys = user.public_key.split("\n")
                # Merge the keys
                final_keys = old_authorized_keys.split("\n")
                for key in new_authorized_keys:
                    if key not in final_keys:
                        final_keys.append(key)
                final_keys = [key for key in final_keys if key.strip() != ""]
                logger.info(f"Enabling account {user.account_name} on {server.host} with {len(final_keys)} keys.")
                final_keys = "\n".join(final_keys)
                result, err = await sshAccountEnable(conn, user.account_name, final_keys)
                if not result:
                    raise Exception(f"Error enabling account {user.account_name} on {server.host}: {err}")
            else:
                result, err = await sshAccountDisable(conn, user.account_name)
                if not result:
                    raise Exception(f"Error disabling account {user.account_name} on {server.host}: {err}")
                return
            # Step 3 - Make the account sudoable if needed
            if account.is_sudo:
                result, err = await sshAccountSudo(conn, user.account_name)
                if not result:
                    raise Exception(f"Error making account {user.account_name} sudoable on {server.host}: {err}")
            elif await sshAccountIsSudo(conn, user.account_name):
                result, err = await sshAccountUnsudo(conn, user.account_name)
                if not result:
                    raise Exception(f"Error making account {user.account_name} no sudo on {server.host}: {err}")
            else:
                logger.info(f"Account {user.account_name} is not sudoable on {server.host}. Skipping.")

async def syncAccount(user : User, server : Server, account: Account):
    if not account.id:
        logger.fatal(f"Account {account.id} not found in database, this will cause a crash.")
        import os
        os._exit(1)
    try:
        logger.info(f"Syncing account {account.id} for user {user.username} on server {server.host}")
        success = False
        try:
            await doSyncAccount(user, server, account)
            success = True
        except Exception as e:
            logger.error(f"Error syncing account {account.id}: {e}")
        logger.info(f"Finished syncing account {account.id} for user {user.username} on server {server.host}")
        
        db = SessionLocal()
        account_db = db.query(Account).filter(Account.id == account.id).first()
        if not account_db:
            logger.error(f"Account {account.id} not found in database.")
        elif account_db.status == AccountStatus.DIRTY:
            logger.warning(f"Account {account.id} is dirty during updating.")
        else:
            account_db.status = AccountStatus.ACTIVE if success else AccountStatus.DIRTY
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"Error processing clear transactions account {account.id}: {e}")
    syncing_accounts[account.id] = False

async def syncServer(server: Server):
    try:
        if not server.id:
            logger.fatal(f"Server {server.id} not found in database, this will cause a crash.")
            import os
            os._exit(1)
        async with semaphore:
            async with getConnection(server) as conn:
                try:
                    db = SessionLocal()
                    server_db = db.query(Server).filter(Server.id == server.id).first()
                    if not server_db:
                        logger.error(f"Server {server.id} not found in database.")
                        return
                    server_db.server_status = ServerStatus.ACTIVE
                    last_server_collect_date[server.id] = datetime.datetime.now()
                    db.commit()
                    db.close()

                    # Step 1 - Get the kernel version
                    kernel_version = await sshServerGetKernel(conn)
                    logger.info(f"Collecting kernel version from server {server.host} {kernel_version}")
                    db = SessionLocal()
                    server_db = db.query(Server).filter(Server.id == server.id).first()
                    if not server_db:
                        logger.error(f"Server {server.id} not found in database.")
                        return
                    server_db.kernel_version = kernel_version
                    db.commit()
                    db.close()

                    # Step 2 - Get the release version
                    os_version = await sshServerGetRelease(conn)
                    logger.info(f"Collecting release version from server {server.host} {os_version}")   
                    db = SessionLocal() 
                    server_db = db.query(Server).filter(Server.id == server.id).first()
                    if not server_db:
                        logger.error(f"Server {server.id} not found in database.")
                        return
                    server_db.os_version = os_version
                    db.commit()
                    db.close()

                    # Step 3 - Get the NICs
                    nics = await sshServerGetNICs(conn)
                    ib_nics = await sshServerGetIBNICs(conn)
                    logger.info(f"Collecting NICs from server {server.host} {nics}")
                    logger.info(f"Collecting IB NICs from server {server.host} {ib_nics}")
                    db = SessionLocal()
                    server_db = db.query(Server).filter(Server.id == server.id).first()
                    for nic in nics:
                        old_interface = db.query(ServerInterface).filter(
                            ServerInterface.server_id == server.id,
                            ServerInterface.pci_address == nic["pci_address"]
                        ).first()
                        if old_interface:
                            old_interface.interface = nic["interface_name"] if nic["interface_name"] else "No Name Eth"
                            old_interface.manufacturer = nic["nic_name"]
                        else:
                            new_interface = ServerInterface(
                                pci_address=nic["pci_address"],
                                interface=nic["interface_name"] if nic["interface_name"] else "No Name Eth",
                                manufacturer=nic["nic_name"],
                                server_id=server.id
                            )
                            db.add(new_interface)
                        db.commit()
                    for ib_nic in ib_nics:
                        old_interface = db.query(ServerInterface).filter(
                            ServerInterface.server_id == server.id,
                            ServerInterface.pci_address == ib_nic["pci_address"]
                        ).first()
                        if old_interface:
                            old_interface.interface = ib_nic["interface_name"] if ib_nic["interface_name"] else "No Name IB"
                            old_interface.manufacturer = ib_nic["nic_name"]
                        else:
                            new_interface = ServerInterface(
                                pci_address=ib_nic["pci_address"],
                                interface=ib_nic["interface_name"] if ib_nic["interface_name"] else "No Name IB",
                                manufacturer=ib_nic["nic_name"],
                                server_id=server.id
                            )
                            db.add(new_interface)
                        db.commit()
                    db.close()

                    # Step 4 - Get the account login history
                    db = SessionLocal()
                    accounts = db.query(Account).filter(Account.server_id == server.id, Account.is_login_able == True).all()
                    for account in accounts:
                        db.refresh(account); db.expunge(account); make_transient(account)
                    db.close()
                    for account in accounts:
                        db = SessionLocal()
                        try:
                            user = db.query(User).filter(User.id == account.user_id).first()
                            if not user:
                                db.close()
                                raise Exception(f"User {account.user_id} not found in database.")
                            account_name =  user.account_name
                            db.close()
                            status, login_date = await sshServerGetAccountLoginDate(conn, account_name)
                            if not status:
                                logger.error(f"Error collecting login date from server {server.host} for account {account_name}: {login_date}")
                                continue
                            # convert from +%Y-%m-%d %H:%M:%S to datetime
                            logger.info(f"Collecting login date from server {server.host} for account {account_name} {login_date}")
                            date = datetime.datetime.strptime(login_date, "%Y-%m-%d %H:%M:%S")
                            db = SessionLocal()
                            account_db = db.query(Account).filter(Account.id == account.id).first()
                            if not account_db:
                                db.close()
                                raise Exception(f"Account {account.id} not found in database.")
                            # update if date is newer
                            if account_db.last_login_date < date:
                                account_db.last_login_date = date
                                db.commit()
                            db.close()
                        except Exception as e:
                            logger.error(f"Error collecting login history for account {account.id} on server {server.host}: {str(e)}")
                            continue
                except Exception as e:
                    logger.error(f"Error collecting data from server {server.host}: {e}")
                    server.server_status = ServerStatus.NO_PERMISSION
    except Exception as e:
        logger.error(f"Error collecting data from server {server.host}: {e}")
        server.server_status = ServerStatus.UNABLE_TO_REACH
    finally:
        last_server_collecting[server.id] = False

async def watchAccountSync():
    try:
        while start_watcher:
            # Routine 1 - Check if there are any accounts to sync
            dumping_sync_accounts = [syncing_accounts[k] for k in syncing_accounts if syncing_accounts[k]]
            if len(dumping_sync_accounts) > 0:
                logger.info("Dump syncing accounts: " + ",".join(dumping_sync_accounts))
            db = SessionLocal()
            accounts = db.query(Account).filter(Account.status == AccountStatus.DIRTY).all()
            for account in accounts:
                if account.id not in syncing_accounts or not syncing_accounts[account.id]:
                    syncing_accounts[account.id] = True
                    account.status = AccountStatus.UPDATING
                    server = account.server
                    user = account.user
                    db.commit()
                    
                    db.refresh(account); db.expunge(account); make_transient(account)
                    db.refresh(server); db.expunge(server); make_transient(server)
                    db.refresh(user); db.expunge(user); make_transient(user)
                    
                    # Start the sync process
                    asyncio.create_task(syncAccount(user, server, account))
            
            gateways = db.query(Server).filter(Server.is_gateway == True).all()
            # Routine 2 - Admin user should have root permissions on gateway server, all active users should have account on gateway server
            active_users = db.query(User).filter(User.status == UserStatus.ACTIVE)
            for gateway in gateways:
                # Check if the user has an account on the gateway server
                for user in active_users:
                    account = db.query(Account).filter(Account.user_id == user.id, Account.server_id == gateway.id).first()
                    if not account:
                        # Create the account
                        new_account = Account(
                            user_id=user.id,
                            server_id=gateway.id,
                            is_sudo=user.is_admin,
                            is_login_able=True,
                            status=AccountStatus.DIRTY
                        )
                        db.add(new_account)
                        db.commit()
                        db.refresh(new_account)
                        logger.info(f"Automatically created account {new_account.id} for user {user.username} on gateway server {gateway.host}")
                    elif not account.is_login_able or account.is_sudo != user.is_admin:
                        # Update the account
                        account.is_login_able = True
                        account.is_sudo = user.is_admin
                        account.status = AccountStatus.DIRTY
                        db.commit()
                        logger.info(f"Automatically updated account {account.id} for user {user.username} on gateway server {gateway.host}")

            # Routine 3 - Disable inactive users accounts
            inactive_users = db.query(User).filter(User.status == UserStatus.GRADUATED)
            inactive_accounts = db.query(Account).filter(Account.user_id.in_([user.id for user in inactive_users])).all()
            for account in inactive_accounts:
                if account.is_login_able:
                    account.is_login_able = False
                    account.status = AccountStatus.DIRTY
                    db.commit()
                    logger.info(f"Automatically disabled account {account.id} for user {account.user.username} on server {account.server.host}")
            
            # Routine 4 - auto revoke account if the user is inactive for a long time
            accounts = db.query(Account).filter(Account.is_login_able == True).all()
            for account in accounts:
                if account.server.is_gateway:
                    continue
                if account.user.is_admin:
                    continue
                if account.last_login_date < datetime.datetime.now() - datetime.timedelta(days=30):
                    account.is_login_able = False
                    account.status = AccountStatus.DIRTY
                    db.commit()
                    logger.info(f"Automatically disabled account {account.id} for user {account.user.username} on server {account.server.host}")

            # Routine 5 - collect usage data from the servers
            servers = db.query(Server).all()
            for server in servers:
                if server.id not in last_server_collect_date or last_server_collect_date[server.id] < datetime.datetime.now() - datetime.timedelta(hours=1):
                    if server.id not in last_server_collecting or not last_server_collecting[server.id]:
                        last_server_collecting[server.id] = True
                        db.refresh(server); db.expunge(server); make_transient(server)
                        asyncio.create_task(syncServer(server))

            db.close()
            await asyncio.sleep(30)
    except Exception as e:
        logger.error(f"Error in watchAccountSync: {e}")
