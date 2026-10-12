import argparse
from getpass import getpass

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Role, User
from app.schemas import UserRegister


def main():
    parser = argparse.ArgumentParser(description="Create a lecturer account locally")
    parser.add_argument("email")
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass("Password (8 characters minimum, 72 UTF-8 bytes maximum): ")
    if password != getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match\n")
    try:
        data = UserRegister(email=args.email, full_name=args.name, password=password)
    except ValidationError as exc:
        parser.exit(1, "\n".join(error["msg"] for error in exc.errors()) + "\n")
    with SessionLocal() as db:
        db.add(
            User(
                email=str(data.email),
                full_name=data.full_name,
                password_hash=hash_password(data.password),
                role=Role.lecturer,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            parser.exit(1, "Email already registered; existing roles were not changed\n")
    print(f"Lecturer created: {data.email}")


if __name__ == "__main__":
    main()
