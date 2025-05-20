from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
import os
from logger import logger
import hashlib

from app.database import get_db, User, UserStatus

router = APIRouter()

# 密码和JWT配置
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

def verify_password(plain, hashed):
    # return pwd_ctx.verify(plain, hashed)
    return hashlib.sha256(plain.encode()).hexdigest() == hashed

def get_password_hash(password: str):
    # return pwd_ctx.hash(password)
    return hashlib.sha256(password.encode()).hexdigest()

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy() 
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# 请求与响应模型
class UserCreate(BaseModel):
    username: str
    realname: str
    account_name: str
    mail: EmailStr
    password: str
    public_key: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post("/register", response_model=Token, status_code=201)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # 检查重复
    user_in.username = user_in.username.strip().lower()
    if db.query(User).filter((User.username==user_in.username)|(User.account_name==user_in.account_name)).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "用户名或真实姓名已存在")
    # 创建用户
    user = User(
        username=user_in.username,
        realname=user_in.realname,
        account_name=user_in.account_name,
        mail=user_in.mail,
        public_key=user_in.public_key,
        password=get_password_hash(user_in.password)
    )
    db.add(user)
    db.commit()
    return JSONResponse(
        content={"msg": "注册成功"},
        status_code=status.HTTP_201_CREATED
    )

@router.post("/login", response_model=Token)
def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    form.username = form.username.strip().lower()
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户未激活或已禁用")
    access = create_access_token(
        {"sub": user.username, "id": user.id},
        timedelta(minutes=ACCESS_EXPIRE_MINUTES)
    )
    response.set_cookie(
        key="access_token",
        value=access,
        httponly=True,
        max_age=ACCESS_EXPIRE_MINUTES * 60,
        expires=ACCESS_EXPIRE_MINUTES * 60,
    )
    return {"access_token": access}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    # logger.info("用户登出")
    return JSONResponse(
        content={"msg": "登出成功"},
        status_code=status.HTTP_200_OK
    )