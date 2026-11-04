import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def msg(text, role="user"):
    return {"role": role, "text": text}


def test_health(client):
    r = client.get("/api/health").json()
    assert r == {"status": "ok"}


def test_info(client):
    r = client.get("/api/info").json()
    assert r["short_name"] == "OSL"
    assert "@" in r["email"]


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Hawk" in r.text


@pytest.mark.parametrize("payload", [
    {"messages": []},
    {"messages": [msg("   ")]},
    {"messages": [msg("x" * 4001)]},
    {"messages": [msg("hello", "bot")]},
    {"messages": [msg("hello")] * 61},
    {"messages": [msg("hello")], "extra_field": "bad"},
])
def test_bad_chat_input_returns_422(client, payload):
    r = client.post("/api/chat", json=payload)
    assert r.status_code == 422


def test_cors_allows_local_origin(client):
    headers = {"Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST"}
    r = client.options("/api/chat", headers=headers)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_cors_blocks_external_origin(client):
    headers = {"Origin": "https://example.com", "Access-Control-Request-Method": "POST"}
    r = client.options("/api/chat", headers=headers)
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.integration
def test_chat_returns_valid_shape(client):
    r = client.post("/api/chat", json={"messages": [msg("How do I register a student org?")]}).json()
    assert set(r) == {"reply", "escalate", "escalation_reason", "offer_options"}
    assert isinstance(r["reply"], str) and len(r["reply"]) > 0
    # ollama_client turns connection/model errors into reply text, so rule those out.
    assert "Ollama is not running" not in r["reply"]
    assert "not found in Ollama" not in r["reply"]
    assert r["escalate"] is False


@pytest.mark.integration
def test_escalate_returns_draft_or_question(client):
    r = client.post("/api/escalate", json={"messages": [msg("I need to contact OSL")]}).json()
    assert (r["email"] is None) != (r["clarifying_question"] is None)
