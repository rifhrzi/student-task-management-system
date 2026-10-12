from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import Role, User

DB = Annotated[Session, Depends(get_db)]
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def current_user(db: DB, token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    error = HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = decode_access_token(token)
    except (InvalidTokenError, ValueError, TypeError):
        raise error from None
    user = db.get(User, user_id)
    if user is None:
        raise error
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def lecturer_only(user: CurrentUser) -> User:
    if user.role != Role.lecturer:
        raise HTTPException(403, "Lecturer role required")
    return user


def student_only(user: CurrentUser) -> User:
    if user.role != Role.student:
        raise HTTPException(403, "Student role required")
    return user


Lecturer = Annotated[User, Depends(lecturer_only)]
Student = Annotated[User, Depends(student_only)]
