from fastapi import FastAPI
import uvicorn
from app.database import engine, Base
from app.api.auth import router as auth_router
from app.api.summary import router as summary_router
from app.api.server import router as server_router
from app.api.user import router as user_router
from app.api.application import router as app_router
from app.api.switch import router as switch_router
from app.api.account import router as account_router
from logger import logger
from contextlib import asynccontextmanager
from account_sync import startWatcher, stopWatcher

@asynccontextmanager
async def lifespan(app: FastAPI):
    # if there is no users in db, add a ADMIN db with username admin and password admin
    from app.database import SessionLocal, User, UserStatus, Account, AccountStatus
    from app.api import auth

    db = SessionLocal()
    if not db.query(User).first():
        admin = User(
            username="admin",
            realname="admin",
            account_name="admin",
            mail="admin@localhost",
            password=auth.get_password_hash("admin"),
            public_key="",
            is_admin=True,
            status=UserStatus.ACTIVE
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        logger.info("No users found in the database. Added admin user with username 'admin' and password 'admin'.")
    # Modify all accounts with status UPDATING to DIRTY
    for account in db.query(Account).filter(Account.status == AccountStatus.UPDATING).all():
        account.status = AccountStatus.DIRTY
        db.commit()
        logger.info(f"Updated account {account.id} status from UPDATING to DIRTY.")
    startWatcher()
    yield
    await stopWatcher()

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="N2SysManager Backend", lifespan=lifespan)

# Include Routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(summary_router, prefix="/summary", tags=["summary"])
app.include_router(user_router, prefix="/user", tags=["user"])
app.include_router(app_router, prefix="/app", tags=["application"])
app.include_router(server_router, prefix="/server", tags=["server"])
app.include_router(switch_router, prefix="/switch", tags=["switch"])
app.include_router(account_router, prefix="/account", tags=["account"])

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)