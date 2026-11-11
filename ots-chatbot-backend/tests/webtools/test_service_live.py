"""Live lookups inside RagService: stages, prompt injection, sources, and failure containment."""
import json

import pytest

import api.service as service_module
from api.schemas import ChatRequest
from config.loader import load_config
from web_tools.fetch.client import FetchResult
from web_tools.tools.registry import WebTools
from web_tools.config import WebToolsConfig

from .test_club_tools import FakeFetcher, URL
from .test_event_tools import EVENTS_URL, NOW, feed

CHUNK = {"text": "Reserve rooms through 312.", "source": "handbook.pdf", "page_start": 5, "page_end": 5}


@pytest.fixture
def llm(monkeypatch):
    seen = {}

    def stream_answer(**kw):
        seen.update(kw)
        yield "ok"

    def generate_answer(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(service_module, "retrieve", lambda c, q: [CHUNK])
    monkeypatch.setattr(service_module, "model_loaded", lambda m: True)
    monkeypatch.setattr(service_module, "stream_answer", stream_answer)
    monkeypatch.setattr(service_module, "generate_answer", generate_answer)
    return seen


def service(fetcher=None, mode="llm"):
    tools = WebTools(WebToolsConfig(min_clubs=1, answer_mode=mode), fetcher=fetcher or FakeFetcher())
    return service_module.RagService(load_config(), None, web_tools=tools)


def stream(svc, text):
    req = ChatRequest(messages=[{"role": "user", "text": text}])
    return [json.loads(line) for line in svc.chat_stream(req)]


def test_club_question_adds_stage_context_and_source(llm):
    events = stream(service(), "What clubs are there for robotics?")
    stages = [e["stage"] for e in events if "stage" in e]
    assert stages == ["searching_clubs", "writing"]
    assert "Facts from 312.iit.edu, current as of Tue Nov 10, 2:00 PM Central." in llm["live_context"]
    assert "**Robotics**" in llm["live_context"]
    done = events[-1]
    assert done["sources"] == [f"(live data from {URL})"]   # handbook not searched for live lookups


def test_policy_question_skips_live_lookup(llm):
    svc = service()
    events = stream(svc, "How do I start a club?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching", "writing"]
    assert "live_context" not in llm
    assert svc._web_tools.fetcher.calls == 0


def test_tool_failure_does_not_break_the_stream_and_tells_the_model(llm):
    f = FakeFetcher()
    f.result = FetchResult(ok=False, url=URL, error_code="timeout", error_message="slow")
    events = stream(service(f), "Any clubs about robotics?")
    assert "token" in events[-2] and "done" in events[-1]
    assert "unavailable" in llm["live_context"]
    assert events[-1]["sources"] == []


def test_unexpected_crash_in_live_path_is_contained(llm, monkeypatch):
    svc = service()
    monkeypatch.setattr(svc._web_tools, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    events = stream(svc, "Any clubs about robotics?")
    assert "done" in events[-1] and "live_context" not in llm


def test_details_question_uses_details_tool_and_falls_back_to_search(llm):
    svc = service()
    stream(svc, "Tell me about the 33rd Street Productions club")
    assert "**33rd Street Productions**" in llm["live_context"]
    llm.clear()
    stream(svc, "Tell me about the Robotic Hamster Club")  # no such club -> fuzzy fallback
    assert "No club named 'Robotic Hamster' was found" in llm["live_context"]
    assert "closest matches only" in llm["live_context"]
    llm.clear()
    stream(svc, "Tell me about the Quidditch Club")  # nothing similar at all
    assert "No matching clubs found" in llm["live_context"]


def test_non_streaming_chat_also_gets_live_context(llm):
    svc = service()
    resp = svc.chat(ChatRequest(messages=[{"role": "user", "text": "recommend a club for robotics"}]))
    assert "live_context" in llm and any("live data" in s for s in resp.sources)


def test_disabled_in_config_means_no_lookup(llm):
    cfg = load_config()
    cfg.web_tools = {"enabled": False}
    svc = service_module.RagService(cfg, None)
    events = stream(svc, "Any clubs about robotics?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching", "writing"]


def test_event_question_routes_to_events_with_stage_and_source(llm):
    f = FakeFetcher()
    f.events_result = feed()
    svc = service_module.RagService(
        load_config(), None,
        web_tools=WebTools(WebToolsConfig(min_clubs=1, min_events=1, answer_mode="llm"), fetcher=f, now=lambda: NOW))
    events = stream(svc, "What events are happening this week?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching_events", "writing"]
    assert "[event page](https://312.iit.edu/rsvp?id=" in llm["live_context"]
    assert f"(live data from {EVENTS_URL})" in events[-1]["sources"]


def test_event_policy_question_stays_with_the_handbook(llm):
    svc = service()
    events = stream(svc, "How do I host an event on campus?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching", "writing"]
    assert "live_context" not in llm


# ---- direct mode (default): the rendered result IS the answer; the model is never called --------------

@pytest.fixture
def no_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the model must not be called in direct mode")
    monkeypatch.setattr(service_module, "stream_answer", boom)
    monkeypatch.setattr(service_module, "generate_answer", boom)
    monkeypatch.setattr(service_module, "model_loaded", boom)
    monkeypatch.setattr(service_module, "retrieve", boom)   # and no handbook search either


def direct_events_service():
    f = FakeFetcher()
    f.events_result = feed()
    return service_module.RagService(
        load_config(), None,
        web_tools=WebTools(WebToolsConfig(min_clubs=1, min_events=1), fetcher=f, now=lambda: NOW))


def answer_text(events):
    return "".join(e["token"] for e in events if "token" in e)


def test_direct_mode_streams_clubs_without_model_or_handbook(no_model):
    events = stream(service(mode="direct"), "Any clubs about robotics?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching_clubs"]
    text = answer_text(events)
    assert "**Robotics**" in text and "**AI and Robotics**" in text     # nothing silently dropped
    assert "[website](https://illinoistechrobotics.org/)" in text
    assert events[-1]["done"] and events[-1]["sources"] == [f"(live data from {URL})"]


def test_direct_mode_events_answer(no_model):
    events = stream(direct_events_service(), "What events are happening this week?")
    text = answer_text(events)
    assert text.startswith("Here's what's on 312 from **")
    assert "(Central time, as of Tue Nov 10, 8:00 AM Central)" in text
    assert "[event page](https://312.iit.edu/rsvp?id=" in text
    assert "Facts from" not in text and "NOTE" not in text           # prompt-only wording never reaches students


def test_direct_mode_failure_message_is_friendly(no_model):
    f = FakeFetcher()
    f.result = FetchResult(ok=False, url=URL, error_code="timeout", error_message="slow")
    text = answer_text(stream(service(f, mode="direct"), "Any clubs about robotics?"))
    assert text.startswith("I couldn't reach the 312 platform just now")
    assert "https://312.iit.edu/club_signup?view=all" in text


def test_direct_mode_stale_note(no_model):
    f = FakeFetcher(stale=True, stale_reason="timeout")
    text = answer_text(stream(service(f, mode="direct"), "Any clubs about robotics?"))
    assert "I couldn't refresh from 312 just now, so this may be out of date." in text


def test_direct_mode_near_match_is_labelled(no_model):
    text = answer_text(stream(service(mode="direct"), "Tell me about the Robotic Hamster Club"))
    assert "I couldn't find a club named 'Robotic Hamster'" in text and "closest matches" in text


def test_direct_mode_non_streaming_chat(no_model):
    resp = service(mode="direct").chat(ChatRequest(messages=[{"role": "user", "text": "Any clubs about robotics?"}]))
    assert "**Robotics**" in resp.reply and resp.sources == [f"(live data from {URL})"]


def test_invalid_answer_mode_rejected():
    with pytest.raises(ValueError):
        WebToolsConfig(answer_mode="telepathy")
