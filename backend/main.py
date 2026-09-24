import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn
from app.database import engine, Base, SessionLocal, User, UserStatus, Account, AccountStatus
from app.api.auth import router as auth_router, get_password_hash
from app.api.summary import router as summary_router
from app.api.server import router as server_router
from app.api.user import router as user_router
from app.api.application import router as app_router
from app.api.switch import router as switch_router
from app.api.account import router as account_router
from app.api.link import router as link_router
from app.api.ai import router as ai_router
from app.api.usage import router as usage_router
from app.errors import install_error_handlers
from logger import logger
from account_sync import startWatcher, stopWatcher
from account_config import load_account_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_account_config()  # Snapshot after dotenv load, before starting any SSH work.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(User).first():
            password = os.getenv("INITIAL_ADMIN_PASSWORD", "")
            if len(password) < 12:
                raise RuntimeError("空数据库首次启动需设置 INITIAL_ADMIN_PASSWORD（至少12位）；不再创建默认 admin/admin。")
            db.add(User(
                username="admin", realname="admin", account_name="admin",
                mail="admin@localhost", password=get_password_hash(password),
                public_key="", is_admin=True, status=UserStatus.ACTIVE,
            ))
            logger.info("Created initial admin user. Set its public key before enabling SSH synchronization.")
        db.query(Account).filter(Account.status == AccountStatus.UPDATING).update(
            {Account.status: AccountStatus.DIRTY}, synchronize_session=False
        )
        db.commit()
    sync_enabled = os.getenv("SYNC_ENABLED", "true").lower() == "true"
    if sync_enabled:
        startWatcher()
    try:
        yield
    finally:
        if sync_enabled:
            await stopWatcher()


app = FastAPI(title="N2SysManager Backend", lifespan=lifespan)
install_error_handlers(app)
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(summary_router, prefix="/summary", tags=["summary"])
app.include_router(user_router, prefix="/user", tags=["user"])
app.include_router(app_router, prefix="/app", tags=["application"])
app.include_router(server_router, prefix="/server", tags=["server"])
app.include_router(switch_router, prefix="/switch", tags=["switch"])
app.include_router(account_router, prefix="/account", tags=["account"])
app.include_router(link_router, prefix="/link", tags=["link"])
app.include_router(ai_router, prefix="/ai", tags=["ai"])
app.include_router(usage_router, prefix="/usage", tags=["usage"])

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=3876)
