import os
from sqlalchemy import create_engine, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Mapped, mapped_column, relationship
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Optional
from enum import Enum

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    raise ValueError("DATABASE_URL environment variable is not set")

# SQLite specific check_same_thread argument
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ServerStatus(Enum):
    ACTIVE = 'active'
    UNABLE_TO_REACH = 'unable_to_connect'
    BAD_KEY = 'bad_key'
    NO_PERMISSION = 'no_permission'
    SSH_ERROR = 'ssh_error'
    UNKNOWN = 'unknown'

class Server(Base):
    __tablename__ = 'server'

    id : Mapped[int] = mapped_column(primary_key=True)
    host : Mapped[str] = mapped_column()
    port : Mapped[int] = mapped_column()

    is_gateway : Mapped[bool] = mapped_column(default=False)
    server_status : Mapped[ServerStatus] = mapped_column(default=ServerStatus.UNKNOWN)
    
    # Data collected from the servers
    is_mounted_home : Mapped[bool] = mapped_column(default=False)
    kernel_version : Mapped[str] = mapped_column(default="")
    os_version : Mapped[str] = mapped_column(default="")

    # User-defined data
    ipmi : Mapped[str] = mapped_column(default="")

    # Relationships
    applications : Mapped[List["Application"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    accounts : Mapped[List["Account"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    tags : Mapped[List["ServerTag"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    interfaces : Mapped[List["ServerInterface"]] = relationship(back_populates="server", cascade="all, delete-orphan")

class Application(Base):
    __tablename__ = 'application'

    id : Mapped[int] = mapped_column(primary_key=True)
    need_sudo : Mapped[bool] = mapped_column()
    create_date : Mapped[datetime] = mapped_column(default=datetime.now)

    user_id : Mapped[int] = mapped_column(ForeignKey("user.id"))
    user : Mapped["User"] = relationship(back_populates="applications")
    server_id : Mapped[int] = mapped_column(ForeignKey("server.id"))
    server : Mapped["Server"] = relationship(back_populates="applications")

class UserStatus(Enum):
    VERIFYING = 'verifying'
    ACTIVE = 'active'
    GRADUATED = 'inactive'

class User(Base):
    __tablename__ = 'user'

    id : Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    realname : Mapped[str] = mapped_column()
    account_name : Mapped[str] = mapped_column(unique=True)
    mail : Mapped[str] = mapped_column(unique=True)
    is_admin : Mapped[bool] = mapped_column(default=False)
    public_key : Mapped[str] = mapped_column()
    status : Mapped[UserStatus] = mapped_column(default=UserStatus.VERIFYING)
    password : Mapped[str] = mapped_column()

    is_mail_auto_revoke : Mapped[bool] = mapped_column(default=True)
    is_mail_new_application : Mapped[bool] = mapped_column(default=True)
    is_mail_new_registeration : Mapped[bool] = mapped_column(default=True)

    applications : Mapped[List["Application"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    accounts : Mapped[List["Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class AccountStatus(Enum):
    DIRTY = 'dirty' # Need to be updated
    UPDATING = 'updating' # Updating to the latest version
    ACTIVE = 'active' # Working normally

class Account(Base):
    __tablename__ = 'account'

    id : Mapped[int] = mapped_column(primary_key=True)
    is_sudo : Mapped[bool] = mapped_column(default=False)
    is_login_able : Mapped[bool] = mapped_column(default=True)
    status : Mapped[AccountStatus] = mapped_column(default=AccountStatus.DIRTY)

    # Automatically collected data
    last_login_date : Mapped[datetime] = mapped_column(default=datetime.now)
    
    # Relationships
    user_id : Mapped[int] = mapped_column(ForeignKey("user.id"))
    user : Mapped["User"] = relationship(back_populates="accounts")
    server_id : Mapped[int] = mapped_column(ForeignKey("server.id"))
    server : Mapped["Server"] = relationship(back_populates="accounts")

class ServerTag(Base):
    __tablename__ = 'server_tag'
    id : Mapped[int] = mapped_column(primary_key=True)
    tag : Mapped[str] = mapped_column()

    server_id : Mapped[int] = mapped_column(ForeignKey("server.id"))
    server : Mapped["Server"] = relationship(back_populates="tags")

class InterfaceTag(Base):
    __tablename__ = "interface_tag"
    id : Mapped[int] = mapped_column(primary_key=True)
    tag : Mapped[str] = mapped_column()

    interface_id : Mapped[int] = mapped_column(ForeignKey("server_interface.id"))
    interface : Mapped["ServerInterface"] = relationship(back_populates="tags")

class Connection(Base):
    __tablename__ = 'connection'
    id : Mapped[int] = mapped_column(primary_key=True)
    interfaces : Mapped[List["ServerInterface"]] = relationship(back_populates="conn")
    switch_ports : Mapped[List["SwitchPort"]] = relationship(back_populates="conn")

class ServerInterface(Base):
    __tablename__ = 'server_interface'
    id : Mapped[int] = mapped_column(primary_key=True)
    interface : Mapped[str] = mapped_column()
    manufacturer : Mapped[str] = mapped_column()
    pci_address : Mapped[str] = mapped_column()

    server_id : Mapped[int] = mapped_column(ForeignKey("server.id"))
    server : Mapped["Server"] = relationship("Server", back_populates="interfaces")

    conn : Mapped[Optional["Connection"]] = relationship(back_populates="interfaces", cascade="all, delete")
    conn_id : Mapped[Optional[int]] = mapped_column(ForeignKey("connection.id"))

    tags : Mapped[List["InterfaceTag"]] = relationship(back_populates="interface", cascade="all, delete-orphan")

class Switch(Base):
    __tablename__ = 'switch'
    id: Mapped[int] = mapped_column(primary_key=True)
    num_row: Mapped[int] = mapped_column()
    num_col: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column()

    ports: Mapped[List["SwitchPort"]] = relationship(back_populates="switch", cascade="all, delete-orphan")

class SwitchPort(Base):
    __tablename__ = 'switch_port'
    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(default="")
    phy_row: Mapped[int] = mapped_column()
    phy_col: Mapped[int] = mapped_column()

    switch_id: Mapped[int] = mapped_column(ForeignKey("switch.id"))
    switch : Mapped["Switch"] = relationship(back_populates="ports")

    conn_id : Mapped[Optional[int]] = mapped_column(ForeignKey("connection.id"))
    conn : Mapped[Optional["Connection"]] = relationship(back_populates="switch_ports", cascade="all, delete")
