import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@localhost/unused"
os.environ["JWT_SECRET"] = "test-only-secret-" * 4

from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        if not make_url(test_url).database.endswith("_test"):
            raise RuntimeError("TEST_DATABASE_URL database name must end in _test")
        env = {**os.environ, "DATABASE_URL": test_url}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
        result = create_engine(test_url, pool_pre_ping=True)
    else:
        path = tmp_path_factory.mktemp("database") / "tests.db"
        result = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

        @event.listens_for(result, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(result)
    yield result
    result.dispose()


@pytest.fixture(scope="session")
def password_hash():
    return hash_password("Password123!")


@pytest.fixture
def client(engine, tmp_path, monkeypatch, password_hash):
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def db_override():
        with factory() as db:
            yield db

    with factory() as db:
        db.add_all(
            [
                User(
                    id=1,
                    email="lecturer@example.com",
                    full_name="Lecturer",
                    password_hash=password_hash,
                    role=Role.lecturer,
                ),
                User(
                    id=2,
                    email="student@example.com",
                    full_name="Student",
                    password_hash=password_hash,
                    role=Role.student,
                ),
                User(
                    id=3,
                    email="other.student@example.com",
                    full_name="Other Student",
                    password_hash=password_hash,
                    role=Role.student,
                ),
                User(
                    id=4,
                    email="other.lecturer@example.com",
                    full_name="Other Lecturer",
                    password_hash=password_hash,
                    role=Role.lecturer,
                ),
            ]
        )
        db.commit()
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text

            db.execute(text("SELECT setval(pg_get_serial_sequence('users','id'), 4)"))
            db.commit()
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def headers():
    return {i: {"Authorization": f"Bearer {create_access_token(i)}"} for i in range(1, 5)}


@pytest.fixture
def task(client, headers):
    response = client.post(
        "/tasks", headers=headers[1], json={"title": "Assignment", "description": "Solve it"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def pdf():
    return Path("postman/fixtures/answer.pdf").read_bytes()
