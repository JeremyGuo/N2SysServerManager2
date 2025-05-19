from typing import Optional
from fastapi import Depends, Cookie
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.database import get_db, User
from app.api.auth import SECRET_KEY, ALGORITHM

async def getUser(
    access_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    if not access_token:
        return None
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        username: str = sub
        if not username:
            return None
    except JWTError:
        return None
    return db.query(User).filter(User.username == username).first()

async def getUserAdmin(
    user: Optional[User] = Depends(getUser)
) -> Optional[User]:
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Not enough permissions",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
