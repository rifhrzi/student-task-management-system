from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=True)
DUMMY_HASH = password_context.hash("constant-time-missing-user")
JWT_ISSUER = "student-task-api"
JWT_AUDIENCE = "student-task-api"


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if "\x00" in password or len(password.encode("utf-8")) > 72:
        return False
    return password_context.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> int:
    claims = jwt.decode(
        token,
        get_settings().jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["sub", "exp", "iat", "iss", "aud"]},
    )
    return int(claims["sub"])
