from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
import os
import hashlib
import hmac
import re

from app.database import get_db, User, UserStatus

router = APIRouter()

# New passwords use a salted, adaptive hash; legacy SHA-256 hashes are upgraded
# only after a successful login by an active user.
pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], pbkdf2_sha256__default_rounds=600_000)
SECRET_KEY = os.getenv("SECRET_KEY", "")
if (len(SECRET_KEY.strip()) < 32 or SECRET_KEY.strip().lower() in
        {"change-me", "your-secret-key", "changeme", "secret"}):
    raise RuntimeError("Set SECRET_KEY to a private random value of at least 32 characters in .env")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))


def is_legacy_password(hashed: str) -> bool:
    return isinstance(hashed, str) and re.fullmatch(r"[0-9a-fA-F]{64}", hashed) is not None


def verify_password(plain, hashed):
    if is_legacy_password(hashed):
        return hmac.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), hashed.lower())
    try:
        return pwd_ctx.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str):
    return pwd_ctx.hash(password)


def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + expires_delta})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


class UserCreate(BaseModel):
    username: str
    realname: str
    account_name: str
    mail: EmailStr
    password: str = Field(min_length=8)
    public_key: str

    @field_validator("username")
    @classmethod
    def valid_username(cls, value):
        value = value.strip().lower()
        if not value:
            raise ValueError("Username must not be empty")
        return value

    @field_validator("account_name")
    @classmethod
    def valid_account_name(cls, value):
        # Deliberately reject whitespace, shell syntax, paths, and option prefixes.
        if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value) is None:
            raise ValueError("Linux account name must be 1-32 lowercase letters, digits, underscores or hyphens, starting with a letter or underscore")
        return value


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", status_code=201)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(func.lower(User.mail) == str(user_in.mail).lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email address already registered")
    if db.query(User).filter((User.username == user_in.username) | (User.account_name == user_in.account_name)).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Username or Linux account name already exists")
    user = User(
        username=user_in.username, realname=user_in.realname,
        account_name=user_in.account_name, mail=str(user_in.mail).lower(),
        public_key=user_in.public_key, password=get_password_hash(user_in.password)
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username, Linux account name or email already registered")
    return JSONResponse(content={"msg": "注册成功"}, status_code=status.HTTP_201_CREATED)


@router.post("/login", response_model=Token)
def login(response: Response, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username.strip().lower()).first()
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户未激活或已禁用")
    if is_legacy_password(user.password) or pwd_ctx.needs_update(user.password):
        user.password = get_password_hash(form.password)
        db.commit()
    access = create_access_token({"sub": user.username, "id": user.id}, timedelta(minutes=ACCESS_EXPIRE_MINUTES))
    response.set_cookie(key="access_token", value=access, httponly=True,
                        max_age=ACCESS_EXPIRE_MINUTES * 60, expires=ACCESS_EXPIRE_MINUTES * 60)
    return {"access_token": access}


@router.post("/logout")
def logout(response: Response):
    # Returning a dict lets FastAPI preserve this response's cookie headers.
    response.delete_cookie("access_token")
    return {"msg": "登出成功"}
