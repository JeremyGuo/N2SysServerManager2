import asyncio
from app.database import Account, SessionLocal, AccountStatus, Server, User
from logger import logger
from sqlalchemy.orm import make_transient
from account_helpers import *
import asyncssh
import copy

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

async def getConnection(srv: Server):
    db = SessionLocal()
    server = db.query(Server).filter(Server.id == srv.id).first()
    if not server:
        raise Exception(f"Server {server.id} not found in database while getting connection.")
    if not server.proxy_server:
        return await asyncio.wait_for(asyncssh.connect(host=server.host, port=server.port), timeout=3)
    else:
        return await asyncio.wait_for(
            asyncssh.connect(
                host=server.host,
                port=server.port,
                proxy_command=['ssh', '-W', '%h:%p', f'{server.proxy_server.host}:{server.proxy_server.port}'],
            ),
            timeout=6
        )



async def doSyncAccount(user: User, server: Server, account: Account):
    logger.info(f"Connecting to server {server.host}")
    conn = await getConnection(server)
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
    syncing_accounts[account.id] = False

async def watchAccountSync():
    try:
        while start_watcher:
            # Routine 1 - Check if there are any accounts to sync
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
            # Routine 2 - Add all users to the serve
    
            await asyncio.sleep(30)
    except Exception as e:
        logger.error(f"Error in watchAccountSync: {e}")
