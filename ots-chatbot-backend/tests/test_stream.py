"""Streaming replies, status stages, model warm-up and keep_alive.

Everything here except the `integration` test replaces the Ollama calls with fakes, so
it runs without Ollama and gives the same result every time.
"""
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import api.service as service_module
from api.main import create_app
from api.schemas import ChatRequest
from config.loader import load_config
from llm import ollama_client

CHUNK = {"text": "Reserve rooms through the 312 platform.", "source": "handbook.pdf",
         "page_start": 5, "page_end": 5}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def fake_ollama(monkeypatch):
    """Fake the pieces chat_stream uses. Returns a log of what was called, in order."""
    log = []
    state = {"chunks": [CHUNK], "loaded": True, "tokens": ["Use ", "the 312 platform."]}

    def retrieve(collection, query):
        log.append("retrieve")
        return state["chunks"]

    def model_loaded(model):
        log.append("model_loaded")
        return state["loaded"]

    def load_model(model):
        log.append("load_model")
        return True

    def stream_answer(**kwargs):
        log.append("stream_answer")
        yield from state["tokens"]

    monkeypatch.setattr(service_module, "retrieve", retrieve)
    monkeypatch.setattr(service_module, "model_loaded", model_loaded)
    monkeypatch.setattr(service_module, "load_model", load_model)
    monkeypatch.setattr(service_module, "stream_answer", stream_answer)
    return log, state


def run_stream(log):
    """Consume chat_stream line by line, logging each event between the fake calls."""
    service = service_module.RagService(load_config(), collection=None)
    request = ChatRequest(messages=[{"role": "user", "text": "How do I book a room?"}])
    events = []
    for line in service.chat_stream(request):
        assert line.endswith("\n")
        event = json.loads(line)
        events.append(event)
        log.append(f"stage:{event['stage']}" if "stage" in event else "token" if "token" in event else "done")
    return events


def assert_one_kind_per_line(events):
    """Every line is exactly one kind of event, and `done` comes last, once."""
    for event in events:
        if "done" in event:
            assert "stage" not in event and "token" not in event
        else:
            assert set(event) in ({"stage"}, {"token"})
    assert [("done" in e) for e in events].count(True) == 1 and "done" in events[-1]


def stages(events):
    return [e["stage"] for e in events if "stage" in e]


# ── Stream stages ───────────────────────────────────────────────

def test_stream_reports_searching_then_writing_when_model_loaded(fake_ollama):
    log, _ = fake_ollama
    events = run_stream(log)
    assert stages(events) == ["searching", "writing"]
    assert "load_model" not in log
    # "searching" is sent before the search runs, "writing" before the model is asked
    assert log.index("stage:searching") < log.index("retrieve")
    assert log.index("stage:writing") < log.index("stream_answer")
    assert_one_kind_per_line(events)
    assert "".join(e["token"] for e in events if "token" in e) == "Use the 312 platform."
    assert events[-1] == {"done": True, "escalate": False, "escalation_reason": None, "offer_options": True}


def test_stream_loads_the_model_between_loading_and_writing(fake_ollama):
    log, state = fake_ollama
    state["loaded"] = False
    events = run_stream(log)
    assert stages(events) == ["searching", "loading", "writing"]
    assert log.index("stage:loading") < log.index("load_model") < log.index("stage:writing")
    assert_one_kind_per_line(events)


def test_stream_says_nothing_extra_when_ollama_cannot_be_asked(fake_ollama):
    log, state = fake_ollama
    state["loaded"] = None   # /api/ps unreachable
    events = run_stream(log)
    assert stages(events) == ["searching", "writing"]
    assert "load_model" not in log


def test_stream_empty_retrieval_still_calls_the_model(fake_ollama):
    """No handbook match: still stream via the LLM (greetings / persona / off-topic)."""
    log, state = fake_ollama
    state["chunks"] = []
    events = run_stream(log)
    assert stages(events) == ["searching", "writing"]
    assert "stream_answer" in log
    assert_one_kind_per_line(events)
    assert events[-1] == {"done": True, "escalate": False, "escalation_reason": None, "offer_options": True}


def test_old_client_still_reads_the_whole_reply(fake_ollama):
    """main before the stages existed only looked at `token` and `done`."""
    log, _ = fake_ollama
    text, done = "", None
    for event in run_stream(log):
        if event.get("token") is not None:
            text += event["token"]
        elif event.get("done"):
            done = event
    assert text == "Use the 312 platform." and done is not None


def test_stream_endpoint_sends_ndjson_with_stages(client, fake_ollama):
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "text": "How do I book a room?"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert stages(events) == ["searching", "writing"]
    assert_one_kind_per_line(events)


# ── Warm-up ─────────────────────────────────────────────────────

def test_warmup_endpoint_returns_at_once_and_starts_one_warm_up(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service_module, "warm_up_in_background", lambda model: calls.append(model))
    r = client.post("/api/warmup")
    assert r.status_code == 202
    assert r.json() == {"status": "accepted"}
    assert calls == [load_config().model]


def test_warm_up_runs_one_load_at_a_time(monkeypatch):
    release, loads = threading.Event(), []

    def slow_load(model):
        loads.append(model)
        release.wait(5)
        return True

    monkeypatch.setattr(ollama_client, "load_model", slow_load)
    ollama_client.warm_up_in_background("m")
    ollama_client.warm_up_in_background("m")   # while the first is still loading: skipped
    time.sleep(0.2)
    assert loads == ["m"]

    release.set()
    deadline = time.time() + 5
    while ollama_client._warming.locked() and time.time() < deadline:
        time.sleep(0.02)
    assert not ollama_client._warming.locked()
    ollama_client.warm_up_in_background("m")   # finished, so a new one may start
    time.sleep(0.2)
    assert loads == ["m", "m"]
    deadline = time.time() + 5
    while ollama_client._warming.locked() and time.time() < deadline:
        time.sleep(0.02)


# ── Ollama client ───────────────────────────────────────────────

class FakeResponse:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data or {}, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("GET", "http://x"),
                                        response=httpx.Response(self.status_code))

    def json(self):
        return self._data


@pytest.mark.parametrize("raw, sent", [
    ("60m", "60m"), ("2h", "2h"), ("-1", -1), (" -1 ", -1), ("3600", 3600), ("0", 0),
])
def test_keep_alive_numbers_are_sent_as_numbers(raw, sent):
    # Ollama rejects "-1" as text ("missing unit in duration") but accepts -1
    assert ollama_client._keep_alive(raw) == sent


@pytest.mark.parametrize("model, loaded, expected", [
    ("llama3.2", ["llama3.2:latest"], True),
    ("llama3.2:latest", ["llama3.2:latest"], True),
    ("llama3.2", ["llama3.2:1b"], False),
    ("llama3.2", [], False),
])
def test_model_loaded_matches_ollama_names(monkeypatch, model, loaded, expected):
    monkeypatch.setattr(ollama_client._client, "get",
                        lambda url, **kw: FakeResponse({"models": [{"name": n} for n in loaded]}))
    assert ollama_client.model_loaded(model) is expected


def test_model_loaded_is_none_when_ollama_is_unreachable(monkeypatch):
    def refuse(url, **kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(ollama_client._client, "get", refuse)
    assert ollama_client.model_loaded("llama3.2") is None


def test_load_model_never_generates_and_matches_real_requests(monkeypatch):
    sent = {}

    def post(url, json):
        sent.update(url=url, body=json)
        return FakeResponse({"done_reason": "load"})

    monkeypatch.setattr(ollama_client._client, "post", post)
    assert ollama_client.load_model("llama3.2") is True
    assert sent["url"].endswith("/api/generate")
    assert sent["body"] == {"model": "llama3.2", "options": ollama_client.GENERATE_OPTIONS,
                            "keep_alive": ollama_client.KEEP_ALIVE}   # no prompt: nothing generated


def test_load_model_reports_failure_when_ollama_is_down(monkeypatch):
    def refuse(url, json):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(ollama_client._client, "post", refuse)
    assert ollama_client.load_model("llama3.2") is False


def test_requests_use_ipv4_and_keep_alive(monkeypatch):
    # "localhost" costs ~2 s per connection on Windows (IPv6 tried first)
    assert ollama_client.OLLAMA_BASE.startswith("http://127.0.0.1:")
    sent = {}

    class FakeStream:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def raise_for_status(self):
            pass
        def iter_lines(self):
            yield json.dumps({"message": {"content": "Hi"}, "done": False})
            yield ""
            yield json.dumps({"message": {"content": ""}, "done": True})

    def stream(method, url, json):
        sent.update(json)
        return FakeStream()

    monkeypatch.setattr(ollama_client._client, "stream", stream)
    tokens = list(ollama_client.stream_answer("q", [CHUNK], "system", "llama3.2"))
    assert tokens == ["Hi"]
    assert sent["keep_alive"] == ollama_client.KEEP_ALIVE
    assert sent["options"] == ollama_client.GENERATE_OPTIONS


# ── Real model ──────────────────────────────────────────────────

@pytest.mark.integration
def test_real_stream_reports_stages_before_the_reply(client):
    r = client.post("/api/chat/stream",
                    json={"messages": [{"role": "user", "text": "How do I register a student org?"}]})
    assert r.status_code == 200
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert_one_kind_per_line(events)
    first_token = next(i for i, e in enumerate(events) if "token" in e)
    assert stages(events[:first_token]) in (["searching", "writing"], ["searching", "loading", "writing"])
    assert not stages(events[first_token:])   # no stage after the reply has started
    text = "".join(e["token"] for e in events if "token" in e)
    assert text and "Ollama is not running" not in text and "not found" not in text
