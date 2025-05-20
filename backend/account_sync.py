import asyncio
from app.database import Account, SessionLocal, AccountStatus, Server, User, UserStatus
from logger import logger
from sqlalchemy.orm import make_transient
from account_helpers import *
import asyncssh
import copy
from contextlib import asynccontextmanager

syncing_accounts = {}
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
        if finished:
            break
        await asyncio.sleep(1)
    logger.info("All tasks finished. Stopping watcher.")

@asynccontextmanager
async def getConnection(srv: Server):
    try:
        db = SessionLocal()
        server = db.query(Server).filter(Server.id == srv.id).first()
        if not server:
            raise Exception(f"Server {server.id} not found in database while getting connection.")
        if not server.proxy_server:
            conn = await asyncio.wait_for(asyncssh.connect(host=server.host, port=server.port, known_hosts=None), timeout=3) # TODO: add known_hosts
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
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=server.host,
                    port=server.port,
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
        else:
            result, err = await sshAccountUnsudo(conn, user.account_name)
            if not result:
                raise Exception(f"Error making account {user.account_name} no sudo on {server.host}: {err}")

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
    except Exception as e:
        logger.error(f"Error processing clear transactions account {account.id}: {e}")
    syncing_accounts[account.id] = False

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
            
            # Routine 4 - TODO - auto revoke account if the user is inactive for a long time

            # Routine 5 - TODO - collect usage data from the servers
    
            await asyncio.sleep(30)
    except Exception as e:
        logger.error(f"Error in watchAccountSync: {e}")
