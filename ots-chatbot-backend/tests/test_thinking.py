"""`think: false` for models that can "think" (llm/ollama_client.py `thinks` / `think_fields`).

Thinking models (qwen3.5, gemma4) get "think": false on every answer and on the off-topic check's one-word
call; other models (llama3.2) get no "think" field at all, so their requests are exactly as before. No real
Ollama: the shared client is faked. conftest.py treats every model as non-thinking by default.
"""
from contextlib import contextmanager

import httpx
import pytest

from api import guardrail
from llm import ollama_client


class FakeResponse:
    def __init__(self, data, lines=()):
        self._data, self._lines = data, lines

    def raise_for_status(self):
        pass

    def json(self):
        return self._data

    def iter_lines(self):
        return iter(self._lines)


@pytest.fixture
def thinking(monkeypatch):
    """qwen3.5:4b thinks, llama3.2 doesn't (as Ollama 0.40 reports them, checked 2026-11-16)."""
    monkeypatch.setattr(ollama_client, "_thinking", {"qwen3.5:4b": True, "llama3.2": False})


@pytest.fixture
def sent(monkeypatch):
    """Record the body of every /api/chat request (post and stream)."""
    bodies = []

    def post(url, json=None, **kw):
        bodies.append(json)
        return FakeResponse({"message": {"content": "ON"}})

    @contextmanager
    def stream(method, url, json=None, **kw):
        bodies.append(json)
        yield FakeResponse({}, lines=['{"message": {"content": "Hi"}, "done": false}', '{"done": true}'])

    monkeypatch.setattr(ollama_client._client, "post", post)
    monkeypatch.setattr(ollama_client._client, "stream", stream)
    return bodies


@pytest.mark.parametrize("model, expected", [("qwen3.5:4b", {"think": False}), ("llama3.2", {})])
def test_only_thinking_models_get_think_false(thinking, model, expected):
    assert ollama_client.think_fields(model) == expected


@pytest.mark.parametrize("model, has_think", [("qwen3.5:4b", True), ("llama3.2", False)])
def test_answers_and_the_off_topic_check_send_think_false_only_to_thinking_models(thinking, sent, model, has_think):
    ollama_client.generate_answer("q", [], "system", model)
    list(ollama_client.stream_answer("q", [], "system", model))
    assert guardrail.classify_with_model("How do I book a room?", model) == "ON"
    assert len(sent) == 3
    for body in sent:
        assert ("think" in body) is has_think
        if has_think:
            assert body["think"] is False


def test_load_model_never_sends_think(thinking, monkeypatch):
    bodies = []
    monkeypatch.setattr(ollama_client._client, "post", lambda url, json=None, **kw: bodies.append(json) or
                        FakeResponse({}))
    assert ollama_client.load_model("qwen3.5:4b") is True
    assert "think" not in bodies[0]   # loading generates nothing, so there is nothing to think about


# ── Asking Ollama (the real `thinks`, kept by conftest.py as `thinks_from_ollama`) ──

@pytest.fixture
def asked(monkeypatch):
    monkeypatch.setattr(ollama_client, "_thinking", {})
    calls = []
    answers = {"qwen3.5:4b": ["completion", "tools", "thinking"], "llama3.2": ["completion", "tools"]}

    def post(url, json=None, **kw):
        calls.append((url, json["model"]))
        return FakeResponse({"capabilities": answers[json["model"]]})

    monkeypatch.setattr(ollama_client._client, "post", post)
    return calls


def test_ollama_is_asked_once_per_model_and_the_answer_is_remembered(asked):
    real = ollama_client.thinks_from_ollama
    assert real("qwen3.5:4b") is True and real("qwen3.5:4b") is True
    assert real("llama3.2") is False
    assert asked == [(f"{ollama_client.OLLAMA_BASE}/api/show", "qwen3.5:4b"),
                     (f"{ollama_client.OLLAMA_BASE}/api/show", "llama3.2")]


def test_if_ollama_cant_be_asked_no_think_field_is_sent_and_it_is_asked_again_later(monkeypatch):
    monkeypatch.setattr(ollama_client, "_thinking", {})
    calls = []

    def refuse(url, **kw):
        calls.append(url)
        raise httpx.ConnectError("Ollama is not running")

    monkeypatch.setattr(ollama_client._client, "post", refuse)
    assert ollama_client.thinks_from_ollama("qwen3.5:4b") is False
    assert ollama_client.thinks_from_ollama("qwen3.5:4b") is False
    assert len(calls) == 2 and ollama_client._thinking == {}


def test_a_model_without_a_capabilities_list_is_not_a_thinking_model(monkeypatch):
    monkeypatch.setattr(ollama_client, "_thinking", {})
    monkeypatch.setattr(ollama_client._client, "post", lambda url, **kw: FakeResponse({"details": {}}))
    assert ollama_client.thinks_from_ollama("old-model") is False
