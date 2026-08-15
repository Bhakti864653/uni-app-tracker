import sys
import importlib
import pytest

TEST_CSRF_TOKEN = "test-csrf-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    if "app" in sys.modules:
        app_module = importlib.reload(sys.modules["app"])
    else:
        import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["csrf_token"] = TEST_CSRF_TOKEN
        yield test_client


@pytest.fixture
def csrf_token():
    return TEST_CSRF_TOKEN


def reset_csrf(client):
    # login/logout/signup call session.clear() for security (avoids session
    # fixation), which also wipes the CSRF token - re-seed it so later
    # requests in the same test can keep using the fixed test token.
    with client.session_transaction() as sess:
        sess["csrf_token"] = TEST_CSRF_TOKEN


@pytest.fixture
def logged_in_client(client, csrf_token):
    client.post("/signup", data={
        "email": "student@example.com",
        "password": "testpassword123",
        "confirm_password": "testpassword123",
        "csrf_token": csrf_token,
    })
    reset_csrf(client)
    return client
