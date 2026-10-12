from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import UploadFile

from app.core.config import Settings
from app.core.security import verify_password
from app.services.files import save_upload


def test_null_or_oversize_password_login_is_rejected(client):
    for password in ("Password\x00!", "a" * 73, "é" * 37):
        response = client.post(
            "/auth/login", data={"username": "student@example.com", "password": password}
        )
        assert response.status_code == 401


def test_uuid_collision_never_overwrites(tmp_path, pdf):
    existing = tmp_path / ("a" * 32 + ".pdf")
    existing.write_bytes(b"existing answer")
    upload = UploadFile(BytesIO(pdf), filename="answer.pdf")
    with patch("app.services.files.uuid4", return_value=SimpleNamespace(hex="a" * 32)):
        with pytest.raises(FileExistsError):
            save_upload(upload, tmp_path)
    assert existing.read_bytes() == b"existing answer"


def test_partial_write_failure_removes_new_file(tmp_path, pdf):
    upload = UploadFile(BytesIO(pdf), filename="answer.pdf")
    with patch("app.services.files.inspect_content", return_value="application/pdf"):
        with patch.object(upload.file, "read", side_effect=OSError("disk error")):
            with pytest.raises(OSError, match="disk error"):
                save_upload(upload, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_secret_placeholder_fails_startup():
    with pytest.raises(ValueError, match="Generate a random JWT_SECRET"):
        Settings(jwt_secret="REPLACE_WITH_RANDOM_SECRET_LONG_ENOUGH", _env_file=None)


def test_lecturer_cli_hashes_password(client, engine, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app import cli
    from app.models import Role, User

    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cli, "SessionLocal", factory)
    monkeypatch.setattr("sys.argv", ["app.cli", "cli.lecturer@example.com", "--name", "CLI Dosen"])
    monkeypatch.setattr(cli, "getpass", lambda prompt: "CliPassword123!")
    cli.main()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "cli.lecturer@example.com"))
        assert user.role == Role.lecturer
        assert user.password_hash != "CliPassword123!"
        assert verify_password("CliPassword123!", user.password_hash)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
