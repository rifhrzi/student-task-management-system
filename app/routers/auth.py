from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.security import DUMMY_HASH, create_access_token, hash_password, verify_password
from app.dependencies import DB, CurrentUser
from app.models import Role, User
from app.schemas import Token, UserRead, UserRegister

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserRead, status_code=201)
def register(payload: UserRegister, db: DB):
    user = User(
        email=str(payload.email),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=Role.student,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered") from None
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    user = db.scalar(select(User).where(User.email == form.username.strip().lower()))
    valid = verify_password(form.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise HTTPException(
            401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"}
        )
    return Token(
        access_token=create_access_token(user.id),
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser):
    return user
