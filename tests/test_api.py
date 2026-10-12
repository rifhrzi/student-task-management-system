from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import JWT_AUDIENCE, JWT_ISSUER
from app.models import Grade, Submission
from app.services.files import MAX_FILE_SIZE


def send(client, headers, task, data, name="answer.pdf", mime="application/pdf"):
    return client.post(
        f"/tasks/{task['id']}/submissions",
        headers=headers[2],
        files={"file": (name, data, mime)},
        data={"answer_text": "My answer"},
    )


def archive_bytes(entries):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_auth_registration_login_and_role_escalation(client, headers):
    data = {"email": "New.Student@example.com", "full_name": "New", "password": "Password123!"}
    escalated = client.post("/auth/register", json={**data, "role": "lecturer"})
    assert escalated.status_code == 422
    registered = client.post("/auth/register", json=data)
    assert registered.status_code == 201
    assert registered.json()["role"] == "student"
    assert "password" not in registered.text
    assert client.post("/auth/register", json=data).status_code == 409
    login = client.post(
        "/auth/login", data={"username": "NEW.STUDENT@example.com", "password": data["password"]}
    )
    assert login.status_code == 200
    assert login.json()["expires_in"] == 1800
    token_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert (
        client.get("/auth/me", headers=token_headers).json()["email"] == "new.student@example.com"
    )
    assert (
        client.post(
            "/tasks", headers=token_headers, json={"title": "x", "description": "y"}
        ).status_code
        == 403
    )
    for email in ("student@example.com", "missing@example.com"):
        assert (
            client.post("/auth/login", data={"username": email, "password": "wrong"}).status_code
            == 401
        )
    assert client.get("/tasks").status_code == 401


@pytest.mark.parametrize("password", ["S3cr!", "a" * 73, "é" * 37, "Password\x00!"])
def test_password_validation_does_not_echo_secret(client, password):
    response = client.post(
        "/auth/register",
        json={"email": "new@example.com", "full_name": "New", "password": password},
    )
    assert response.status_code == 422
    assert password not in response.text


@pytest.mark.parametrize("case", ["expired", "signature", "missing_exp", "audience", "unknown"])
def test_invalid_tokens(client, case):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "2",
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    secret = get_settings().jwt_secret.get_secret_value()
    if case == "expired":
        claims["exp"] = now - timedelta(seconds=1)
    elif case == "signature":
        secret = "wrong-secret-" * 4
    elif case == "missing_exp":
        del claims["exp"]
    elif case == "audience":
        claims["aud"] = "other-api"
    elif case == "unknown":
        claims["sub"] = "999999"
    token = jwt.encode(claims, secret, algorithm="HS256")
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_task_ownership_patch_and_delete(client, headers, task, pdf):
    url = f"/tasks/{task['id']}"
    assert client.get(url, headers=headers[2]).status_code == 200
    assert client.patch(url, headers=headers[4], json={"title": "Stolen"}).status_code == 403
    assert client.delete(url, headers=headers[2]).status_code == 403
    assert client.delete(url, headers=headers[4]).status_code == 403
    for payload in (
        {},
        {"title": None},
        {"title": "   "},
        {"lecturer_id": 4},
        {"due_at": "2030-01-01T00:00:00"},
    ):
        assert client.patch(url, headers=headers[1], json=payload).status_code == 422
    updated = client.patch(url, headers=headers[1], json={"title": "Updated", "due_at": None})
    assert updated.json()["title"] == "Updated"
    assert updated.json()["description"] == "Solve it"
    submitted = send(client, headers, task, pdf).json()
    assert client.delete(url, headers=headers[1]).status_code == 204
    assert client.get(url, headers=headers[2]).status_code == 404
    assert client.get("/tasks", headers=headers[2]).json() == []
    assert send(client, headers, task, pdf).status_code == 404
    assert client.get(f"/submissions/{submitted['id']}/file", headers=headers[2]).status_code == 404


def test_submission_grade_and_privacy(client, headers, task, pdf):
    response = send(client, headers, task, pdf)
    assert response.status_code == 201, response.text
    submission = response.json()
    assert "stored_filename" not in submission
    url = f"/submissions/{submission['id']}"
    assert client.get(url + "/grade", headers=headers[2]).status_code == 404
    for user_id in (3, 4):
        for suffix in ("", "/file", "/grade"):
            assert client.get(url + suffix, headers=headers[user_id]).status_code == 404
    assert client.put(url + "/grade", headers=headers[2], json={"score": 90}).status_code == 403
    assert client.put(url + "/grade", headers=headers[4], json={"score": 90}).status_code == 403
    for score in (-1, 101, "NaN", "1.001"):
        assert (
            client.put(url + "/grade", headers=headers[1], json={"score": score}).status_code == 422
        )
    first = client.put(url + "/grade", headers=headers[1], json={"score": 80}).json()
    updated = client.put(
        url + "/grade", headers=headers[1], json={"score": 95.5, "feedback": "Good work"}
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == first["id"]
    grade = client.get(url + "/grade", headers=headers[2]).json()
    assert float(grade["score"]) == 95.5 and grade["feedback"] == "Good work"
    assert client.get(url, headers=headers[2]).json()["grade"]["id"] == grade["id"]
    downloaded = client.get(url + "/file", headers=headers[2])
    assert downloaded.content == pdf
    assert downloaded.headers["content-disposition"].startswith("attachment")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    listing = f"/tasks/{task['id']}/submissions"
    assert len(client.get(listing, headers=headers[1]).json()) == 1
    assert client.get(listing, headers=headers[3]).json() == []
    assert client.get(listing, headers=headers[4]).status_code == 403


@pytest.mark.parametrize(
    "name,mime,data,status",
    [
        ("bad.exe", "application/pdf", b"%PDF-1.4\n%%EOF", 415),
        ("fake.pdf", "application/pdf", b"<html>fake</html>", 415),
        ("empty.pdf", "application/pdf", b"", 422),
        ("broken.pdf", "application/pdf", b"%PDF-1.4\n", 415),
        ("wrong.pdf", "application/zip", b"%PDF-1.4\n%%EOF", 415),
        ("broken.zip", "application/zip", b"PK\x03\x04broken", 415),
    ],
)
def test_rejected_uploads(client, headers, task, name, mime, data, status):
    response = send(client, headers, task, data, name, mime)
    assert response.status_code == status, response.text
    assert not list(get_settings().upload_dir.glob("*"))


def test_size_boundaries(client, headers, task):
    prefix, suffix = b"%PDF-1.4\n", b"\n%%EOF"
    exact = prefix + b" " * (MAX_FILE_SIZE - len(prefix) - len(suffix)) + suffix
    assert send(client, headers, task, exact).status_code == 201
    assert send(client, headers, task, exact + b" ").status_code == 413
    assert send(client, headers, task, exact + b" " * (2 * 1024 * 1024)).status_code == 413


def test_chunked_request_body_limit(client, headers, task):
    boundary = "chunked-upload"

    def stream():
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="answer.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        for _ in range(12):
            yield b"x" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        f"/tasks/{task['id']}/submissions",
        content=stream(),
        headers={**headers[2], "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413


def test_zip_and_docx_detection(client, headers, task):
    zipped = archive_bytes({"answer.txt": "Hello"})
    assert send(client, headers, task, zipped, "answer.zip", "application/zip").status_code == 201
    assert (
        send(client, headers, task, zipped, "answer.docx", "application/octet-stream").status_code
        == 415
    )
    docx = archive_bytes(
        {
            "[Content_Types].xml": '<Types><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>',
            "_rels/.rels": "<Relationships/>",
            "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body/></w:document>',
        }
    )
    assert (
        send(client, headers, task, docx, "answer.docx", "application/octet-stream").status_code
        == 201
    )
    assert send(client, headers, task, docx, "answer.zip", "application/zip").status_code == 415
    for entries in ({"../escape.txt": "oops"}, {"a.txt": "a" * (51 * 1024 * 1024)}):
        # Use compression so the expanded-size case is below the 10 MiB upload limit.
        import zipfile

        buf = BytesIO()
        with ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        assert (
            send(client, headers, task, buf.getvalue(), "unsafe.zip", "application/zip").status_code
            == 415
        )


def test_original_filename_never_controls_storage(client, headers, task, pdf):
    response = send(client, headers, task, pdf, "../../answer.pdf")
    assert response.status_code == 201
    assert response.json()["original_filename"] == "answer.pdf"
    names = [file.name for file in get_settings().upload_dir.iterdir()]
    assert len(names) == 1 and len(names[0]) == 36 and names[0] != "answer.pdf"


def test_deadline_and_lecturer_cannot_submit(client, headers, task, pdf):
    response = client.post(
        f"/tasks/{task['id']}/submissions",
        headers=headers[1],
        files={"file": ("answer.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 403
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    client.patch(f"/tasks/{task['id']}", headers=headers[1], json={"due_at": past})
    assert send(client, headers, task, pdf).status_code == 409


def test_database_failure_removes_saved_file(client, headers, task, pdf, monkeypatch):
    def fail_commit(self):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        send(client, headers, task, pdf)
    assert not list(get_settings().upload_dir.glob("*"))


def test_concurrent_submissions_do_not_overwrite(client, headers, task, pdf, engine):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: send(client, headers, task, pdf), range(4)))
    assert [r.status_code for r in responses] == [201] * 4
    assert len({r.json()["id"] for r in responses}) == 4
    files = list(get_settings().upload_dir.iterdir())
    assert len(files) == 4 and all(file.read_bytes() == pdf for file in files)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 4


def test_concurrent_grades_keep_one_record(client, headers, task, pdf, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row locks require PostgreSQL")
    submission = send(client, headers, task, pdf).json()

    def grade(score):
        return client.put(
            f"/submissions/{submission['id']}/grade", headers=headers[1], json={"score": score}
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(grade, [10, 20, 30, 40]))
    assert [r.status_code for r in responses] == [200] * 4
    assert len({r.json()["id"] for r in responses}) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Grade)) == 1


def test_pagination_health_and_openapi(client, headers, task):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/tasks?limit=101", headers=headers[2]).status_code == 422
    assert client.get("/tasks?offset=-1", headers=headers[2]).status_code == 422
    assert client.get("/tasks?offset=1", headers=headers[2]).json() == []
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
    assert client.get("/docs").status_code == 200
