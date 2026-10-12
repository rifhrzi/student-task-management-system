def test_ui_shell_and_static_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="id"' in response.text
    assert "Student Task Management System" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    for path in ("/static/app.js", "/static/styles.css", "/static/favicon.svg"):
        assert client.get(path).status_code == 200
    assert client.get("/static/../.env").status_code == 404
    assert "/" not in client.get("/openapi.json").json()["paths"]


def test_submission_names_are_only_available_to_authorized_users(client, headers, task, pdf):
    response = client.post(
        f"/tasks/{task['id']}/submissions",
        headers=headers[2],
        files={"file": ("answer.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201
    submission = response.json()
    assert submission["student"] == {
        "id": 2,
        "full_name": "Student",
        "email": "student@example.com",
    }
    assert "password_hash" not in response.text
    url = f"/submissions/{submission['id']}"
    assert client.get(url, headers=headers[1]).json()["student"]["full_name"] == "Student"
    assert client.get(url, headers=headers[3]).status_code == 404
    assert client.get(url, headers=headers[4]).status_code == 404
