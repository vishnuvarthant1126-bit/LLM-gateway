import pytest
from fastapi.testclient import TestClient

from app.main import create_app

BODY = {"messages": [{"role": "user", "content": "hello"}]}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def h(key="big-key"):
    return {"X-API-Key": key}


def test_requires_api_key(client):
    assert client.post("/chat", json=BODY).status_code == 401
    assert client.post("/chat", json=BODY, headers=h("wrong")).status_code == 401


def test_bearer_token_also_works(client):
    r = client.post("/chat", json=BODY, headers={"Authorization": "Bearer big-key"})
    assert r.status_code == 200


def test_happy_path(client):
    r = client.post("/chat", json=BODY, headers=h())
    assert r.status_code == 200
    d = r.json()
    assert d["provider"] == "mock-primary" and d["fallback_used"] is False
    assert "hello" in d["content"] and d["usage"]["prompt_tokens"] > 0
    assert r.headers["X-Gateway-Provider"] == "mock-primary"


def test_validation_rejects_bad_body(client):
    assert client.post("/chat", json={"messages": []}, headers=h()).status_code == 422
    bad_role = {"messages": [{"role": "hacker", "content": "x"}]}
    assert client.post("/chat", json=bad_role, headers=h()).status_code == 422


def test_rate_limit_returns_429_with_retry_after(client):
    codes = [client.post("/chat", json=BODY, headers=h("test-key")).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]
    r = client.post("/chat", json=BODY, headers=h("test-key"))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_rate_limit_is_per_key(client):
    for _ in range(6):
        client.post("/chat", json=BODY, headers=h("test-key"))
    assert client.post("/chat", json=BODY, headers=h("big-key")).status_code == 200


def test_fallback_via_admin_endpoint(client):
    assert client.post("/admin/providers/mock-primary/mode", json={"mode": "down"}, headers=h()).status_code == 200
    d = client.post("/chat", json=BODY, headers=h()).json()
    assert d["provider"] == "mock-secondary" and d["fallback_used"] is True


def test_503_when_everything_is_down(client):
    for n in ("mock-primary", "mock-secondary", "mock-tertiary"):
        client.post(f"/admin/providers/{n}/mode", json={"mode": "down"}, headers=h())
    r = client.post("/chat", json=BODY, headers=h())
    assert r.status_code == 503 and r.json()["attempts"]


def test_admin_requires_key_and_validates(client):
    assert client.post("/admin/providers/mock-primary/mode", json={"mode": "down"}).status_code == 401
    assert client.post("/admin/providers/nope/mode", json={"mode": "down"}, headers=h()).status_code == 404
    assert client.post("/admin/providers/mock-primary/mode", json={"mode": "explode"}, headers=h()).status_code == 422


def test_metrics_and_reset(client):
    client.post("/chat", json=BODY, headers=h())
    m = client.get("/metrics").json()
    assert m["succeeded"] == 1 and m["success_rate"] == 100.0 and len(m["providers"]) == 3
    client.post("/admin/reset", headers=h())
    assert client.get("/metrics").json()["total_requests"] == 0


def test_dashboard_and_health(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/dashboard")
    assert r.status_code == 200 and "LLM Gateway" in r.text
