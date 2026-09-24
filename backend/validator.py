from typing import Optional
from fastapi import Depends, Cookie, HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.database import get_db, User, UserStatus
from app.api.auth import SECRET_KEY, ALGORITHM


def unauthenticated():
    return HTTPException(status_code=401, detail="Not authenticated or user is inactive",
                         headers={"WWW-Authenticate": "Bearer"})


async def getUser(access_token: Optional[str] = Cookie(None), db: Session = Depends(get_db)) -> User:
    if not access_token:
        raise unauthenticated()
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise unauthenticated()
    except JWTError:
        raise unauthenticated()
    user = db.query(User).filter(User.username == username).first()
    if not user or user.status != UserStatus.ACTIVE:
        raise unauthenticated()
    # Keep compatibility with older tokens that only carry a subject.
    if "id" in payload and payload["id"] != user.id:
        raise unauthenticated()
    return user


async def getUserAdmin(user: Optional[User] = Depends(getUser)) -> User:
    if not user or user.status != UserStatus.ACTIVE:
        raise unauthenticated()
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
