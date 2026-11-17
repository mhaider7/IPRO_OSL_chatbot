"""The search picker inside RagService, with real 312 tools on fixture data and a fake model (R Part 2 review).

Covers what tests/test_routing.py can't: picker-chosen lookups through chat_stream (stages, two lookups), what the
off-topic check is given (the raw message; the lookup only in direct mode), the refusal when a lookup was the only
reason to allow a message and then shows nothing, the clearly-off shortcut, list positions before fixed replies,
"more" after a picker search, and loading the model before the picker in chat().
"""
import json

import pytest

import api.service as service_module
from api import routing
from api.guardrail import Verdict, is_refusal
from api.schemas import ChatRequest
from config.loader import load_config
from web_tools.config import WebToolsConfig
from web_tools.tools.registry import WebTools

from .test_club_tools import FakeFetcher
from .test_event_tools import NOW, feed


def tool(name, **args):
    return {"function": {"name": name, "arguments": args}}


class Guard:
    """Records what the off-topic check is given; answers `decision` (and the real 312-lookup exception)."""
    def __init__(self, decision="allow", clearly_off=False):
        self.decision, self.clearly_off, self.seen = decision, clearly_off, []

    def clearly_off_first(self, latest, history):
        return self.clearly_off

    def check(self, latest, history, router_call=None):
        self.seen.append((latest, router_call and router_call.name))
        if self.decision == "refuse" and router_call is not None:
            return Verdict("allow", "312-lookup")
        return Verdict(self.decision, "test")


@pytest.fixture
def no_answer_model(monkeypatch):
    def boom(**kw):
        raise AssertionError("no answer model here")
    monkeypatch.setattr(service_module, "stream_answer", boom)
    monkeypatch.setattr(service_module, "generate_answer", boom)
    monkeypatch.setattr(service_module, "model_loaded", lambda m: True)
    monkeypatch.setattr(service_module, "retrieve", lambda c, q: [])


def make(monkeypatch, replies, mode="direct", guard=None):
    """`replies` = the fake picker's answer per call, in order (a list of tool-call lists)."""
    sent = []

    def ask(messages, model, timeout):
        sent.append(messages)
        calls = replies[len(sent) - 1] if len(sent) <= len(replies) else []
        return {"content": "", "tool_calls": calls}
    monkeypatch.setattr(routing, "ask_model", ask)
    f = FakeFetcher()
    f.events_result = feed()
    tools = WebTools(WebToolsConfig(min_clubs=1, min_events=1, answer_mode=mode), fetcher=f, now=lambda: NOW)
    guard = guard or Guard()
    return service_module.RagService(load_config(), None, web_tools=tools, guardrail=guard), guard, sent


def stream(svc, text, history=()):
    req = ChatRequest(messages=[*history, {"role": "user", "text": text}])
    return [json.loads(line) for line in svc.chat_stream(req)]


def reply(events):
    return "".join(e.get("token", "") for e in events)


def test_a_club_and_events_both_run_with_both_stages(monkeypatch, no_answer_model):
    svc, guard, _ = make(monkeypatch, [[tool("search_clubs", query="robotics"),
                                        tool("search_events", date_from="2026-11-10", date_to="2026-11-15")]])
    events = stream(svc, "any robotics club or events this week?")
    assert [e["stage"] for e in events if "stage" in e] == ["searching_clubs", "searching_events"]
    text = reply(events)
    assert "Here are clubs on 312" in text and "Here's what's on 312" in text
    assert len(events[-1]["sources"]) == 2


def test_the_check_gets_the_raw_message_and_the_lookup_only_in_direct_mode(monkeypatch, no_answer_model):
    svc, guard, _ = make(monkeypatch, [[tool("search_clubs", query="robotics")]])
    stream(svc, "robotics?")
    assert guard.seen == [("robotics?", "search_clubs")]

    def answer(**kw):
        yield "ok"
    monkeypatch.setattr(service_module, "stream_answer", answer)
    svc, guard, _ = make(monkeypatch, [[tool("search_clubs", query="robotics")]], mode="llm")
    stream(svc, "robotics?")
    assert guard.seen == [("robotics?", None)]   # llm mode: a model writes the answer, so no exception


def test_allowed_only_because_of_a_lookup_that_then_shows_nothing_is_refused(monkeypatch, no_answer_model):
    svc, guard, _ = make(monkeypatch, [[tool("search_events", query="brownies")]], guard=Guard("refuse"))
    monkeypatch.setattr(svc, "_run_live", lambda call: (None, []))   # the lookup crashes
    events = stream(svc, "brownie recipe for our bake sale event")
    assert is_refusal(reply(events)) and events[-1]["sources"] == []
    resp = svc.chat(ChatRequest(messages=[{"role": "user", "text": "brownie recipe for our bake sale event"}]))
    assert is_refusal(resp.reply)


def test_a_clearly_off_first_message_is_refused_before_the_picker(monkeypatch, no_answer_model):
    svc, guard, sent = make(monkeypatch, [[tool("search_events")]], guard=Guard(clearly_off=True))
    assert is_refusal(reply(stream(svc, "write me a poem about the ocean")))
    assert sent == [] and guard.seen == []


def test_a_list_position_wins_over_a_fixed_short_reply(monkeypatch, no_answer_model):
    svc, _, sent = make(monkeypatch, [])
    hist = [{"role": "user", "text": "any robotics clubs?"},
            {"role": "bot", "text": "Here are clubs on 312:\n\n- **Robotics** — Student Organization. "
                                    "[312 page](https://312.iit.edu/robotics/)\n- **Boxing Club** — Student "
                                    "Organization. [312 page](https://312.iit.edu/box/)"}]
    plan = svc._plan("#2 thanks", hist)
    assert [c.name for c in plan.calls] == ["get_club_details"] and plan.calls[0].args["slug"] == "Boxing Club"
    assert sent == []


def test_more_pages_through_the_pickers_earlier_search(monkeypatch, no_answer_model):
    # the earlier "Machine learning?" search came from the picker; the regex router can't recover it
    svc, _, sent = make(monkeypatch, [[tool("search_clubs", query="machine learning")]])
    hist = [{"role": "user", "text": "Machine learning?"},
            {"role": "bot", "text": "Here are clubs on 312:\n\n- **A** — x. [312 page](https://312.iit.edu/a/)\n"
                                    "- **B** — y. [312 page](https://312.iit.edu/b/)"}]
    plan = svc._plan("more", hist)
    assert [(c.name, c.args) for c in plan.calls] == [("search_clubs", {"query": "machine learning", "limit": 5,
                                                                         "offset": 2})]
    assert sent[0][-1] == {"role": "user", "content": "Machine learning?"}   # the picker asked about that message


def test_more_after_two_lists_is_left_to_the_picker(monkeypatch, no_answer_model):
    svc, _, sent = make(monkeypatch, [[tool("search_events", query="AI")]])
    two = "Here are clubs on 312:\n\n- **A** — x.\n\nHere's what's on 312 from Mon to Sun:\n\n- **E** — y."
    plan = svc._plan("more", [{"role": "user", "text": "AI club or AI events?"}, {"role": "bot", "text": two}])
    assert len(sent) == 1 and sent[0][-1]["content"] == "more"   # no re-run of the earlier message
    assert [c.name for c in plan.calls] == ["search_events"]


def test_chat_loads_the_model_before_the_picker(monkeypatch):
    order = []
    monkeypatch.setattr(service_module, "model_loaded", lambda m: order.append("loaded?") or False)
    monkeypatch.setattr(service_module, "load_model", lambda m: order.append("load") or True)
    monkeypatch.setattr(service_module, "retrieve", lambda c, q: [])
    monkeypatch.setattr(service_module, "generate_answer", lambda **kw: "ok")
    svc, _, _ = make(monkeypatch, [[tool("search_handbook", query="start a club")]], mode="llm")
    orig = routing.pick
    monkeypatch.setattr(routing, "pick", lambda *a, **k: order.append("pick") or orig(*a, **k))
    svc.chat(ChatRequest(messages=[{"role": "user", "text": "How do I start a club?"}]))
    assert order[:3] == ["loaded?", "load", "pick"]


def test_a_malformed_tool_call_falls_back():
    p = routing.pick("How do I start a club?", [], "gemma4:e4b",
                     ask=lambda m, model, t: {"content": "", "tool_calls": ["junk"]})
    assert p.source == "fallback"
