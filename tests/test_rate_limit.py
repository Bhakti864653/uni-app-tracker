import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app import RATE_LIMIT_MAX_ATTEMPTS, is_rate_limited


def test_allows_calls_under_the_limit():
    key = "test:allow"
    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        assert is_rate_limited(key) is False


def test_blocks_the_call_that_exceeds_the_limit():
    key = "test:block"
    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        is_rate_limited(key)
    assert is_rate_limited(key) is True


def test_different_keys_have_independent_budgets():
    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        is_rate_limited("test:user-a")
    assert is_rate_limited("test:user-b") is False


def test_demo_login_is_rate_limited_per_ip(client):
    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        response = client.get("/demo-login")
        assert response.status_code == 302
    response = client.get("/demo-login")
    assert response.status_code == 429
