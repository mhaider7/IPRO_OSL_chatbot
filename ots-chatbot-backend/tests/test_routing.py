"""Search picker (api/routing.py): the model's tool calls -> what runs; failures fall back to the regex router.

The model is always a fake here (tests/conftest.py makes the real call fail for every other test).
"""
import json
from datetime import date

import pytest

import api.service as service_module
from api import routing
from api.schemas import ChatRequest
from config.loader import load_config
from web_tools.render import render_for_user

MON = date(2026, 11, 16)


def tool(name, **args):
    return {"function": {"name": name, "arguments": args}}


def fake(*calls, content=""):
    seen = {}

    def ask(messages, model, timeout):
        seen.update(messages=messages, model=model, timeout=timeout)
        return {"content": content, "tool_calls": list(calls)}
    return ask, seen


def pick(latest, history=(), *calls, **kw):
    ask, seen = fake(*calls, **kw)
    return routing.pick(latest, list(history), "gemma4:e4b", today=MON, ask=ask), seen


def u(t): return {"role": "user", "text": t}
def b(t): return {"role": "bot", "text": t}


# ---- reading the model's answer ---------------------------------------------------------------------------
def test_handbook_search_with_the_models_standalone_text():
    p, _ = pick("Funding?", [u("How do I start a club?"), b("Fill in the form...")],
                tool("search_handbook", query="funding for a new student club"))
    assert p.source == "model" and p.family == "handbook" and p.handbook_query == "funding for a new student club"


def test_a_312_lookup_gets_hawks_default_limit_and_a_details_fallback():
    p, _ = pick("ML?", [], tool("get_club_details", slug="ML"))
    assert [c.name for c in p.calls] == ["get_club_details"]
    assert p.calls[0].fallback.name == "search_clubs" and p.calls[0].fallback.args["query"] == "ML"
    p, _ = pick("events tomorrow?", [], tool("search_events", date_from="2026-11-17", date_to="2026-11-17"))
    assert p.calls[0].args["limit"] == 6 and p.calls[0].stage == "searching_events"


def test_arguments_sent_as_a_json_string_are_read():
    call = {"function": {"name": "search_clubs", "arguments": json.dumps({"query": "chess"})}}
    p, _ = pick("chess club?", [], call)
    assert p.calls[0].args == {"query": "chess", "limit": 5}


def test_two_lookups_when_the_student_asks_for_a_club_and_events():
    p, _ = pick("Is there an AI club or any AI events this week?", [],
                tool("search_clubs", query="AI"), tool("search_events", query="AI", date_from="2026-11-16",
                                                        date_to="2026-11-22"))
    assert [c.name for c in p.calls] == ["search_clubs", "search_events"] and p.family == "clubs+events"


def test_duplicates_are_merged_and_two_lookups_must_be_a_club_and_events():
    same = tool("search_clubs", query="AI")
    p, _ = pick("x", [], same, same, tool("search_events", query="AI"))
    assert [(c.name, c.args["query"]) for c in p.calls] == [("search_clubs", "AI"), ("search_events", "AI")]
    # two different club searches = a fan-out: that kind is dropped, the events search stays
    p, _ = pick("x", [], same, tool("search_events", query="AI"), tool("search_clubs", query="robots"))
    assert [c.name for c in p.calls] == ["search_events"] and "several clubs lookups" in p.reason


def test_a_fan_out_of_event_details_falls_back_to_the_router():
    # "any of those online?" after an events list: gemma called get_event_details once per listed event (review)
    hist = [u("what's happening this week"),
            b("Here's what's on 312:\n- **A** — x. [event page](https://312.iit.edu/rsvp?id=401001)\n"
              "- **B** — y. [event page](https://312.iit.edu/rsvp?id=401002)")]
    p, _ = pick("any of those online?", hist, tool("get_event_details", event_id_or_url="401001"),
                tool("get_event_details", event_id_or_url="401002"))
    assert p.source == "fallback" and "several events lookups" in p.reason


def test_handbook_and_312_together_keep_the_handbook_search_only():
    p, _ = pick("food rules and free food events", [], tool("search_handbook", query="food at events"),
                tool("search_events", tags=["Free Food"]))
    assert p.calls == [] and p.handbook_query == "food at events"


def test_no_tool_means_no_search_and_the_models_text_is_thrown_away():
    p, _ = pick("ugh what a day", [], content="Sorry to hear that! I can look it up for you.")
    assert p.source == "model" and p.family == "none" and p.handbook_query is None and p.calls == []


def test_invalid_arguments_are_dropped_and_nothing_usable_falls_back_to_the_router():
    p, _ = pick("events next week", [], tool("search_events", date_from="2026-11-30", date_to="2026-11-23"))
    assert p.source == "fallback" and "invalid arguments" in p.reason
    assert p.calls and p.calls[0].name == "search_events"   # the regex router's own events search


def test_an_unknown_tool_falls_back():
    p, _ = pick("How do I start a club?", [], tool("browse_web", url="https://example.com"))
    assert p.source == "fallback" and p.family == "handbook" and p.handbook_query == "How do I start a club?"


def test_a_failed_call_falls_back_to_the_router_and_todays_search_text():
    def broken(messages, model, timeout):
        raise TimeoutError("too slow")
    hist = [u("What's the deadline to re-register?"), b("Re-registration is due in Week 4.")]
    p = routing.pick("and what if we miss it?", hist, "gemma4:e4b", today=MON, ask=broken)
    assert p.source == "fallback" and "TimeoutError" in p.reason
    assert p.handbook_query.startswith("Re-registration is due")   # today's follow-up text (api.conversation)


def test_the_router_choice_is_always_kept_for_the_log():
    p, _ = pick("any clubs about chess?", [], tool("search_handbook", query="chess"))
    assert p.source == "model" and p.router is not None and p.router.name == "search_clubs"


def test_asking_back_gets_one_more_try_told_not_to_ask():
    replies = [{"content": "What date range are you interested in?", "tool_calls": None},
               {"content": "", "tool_calls": [tool("search_events", date_from="2026-11-16", date_to="2026-11-30")]}]
    sent = []

    def ask(messages, model, timeout):
        sent.append(messages[0]["content"])
        return replies[len(sent) - 1]
    p = routing.pick("upcoming events", [], "gemma4:e4b", today=MON, ask=ask)
    assert p.family == "events" and len(sent) == 2 and routing.NUDGE in sent[1] and routing.NUDGE not in sent[0]


def test_right_after_the_support_reply_the_second_try_has_no_search_default():
    sent = []

    def ask(messages, model, timeout):
        sent.append(messages[0]["content"])
        return {"content": "I'm sorry, I didn't mean to repeat myself. What would you like to talk about?",
                "tool_calls": None}
    p = routing.pick("why do you keep saying that", [], "gemma4:e4b", today=MON, ask=ask, after_support=True)
    assert p.family == "none" and len(sent) == 2
    assert routing.NUDGE_AFTER_SUPPORT in sent[1] and "call search_handbook" not in sent[1]


def test_a_short_text_reply_is_accepted_as_no_search_without_another_try():
    calls = []

    def ask(messages, model, timeout):
        calls.append(1)
        return {"content": "Hi there!", "tool_calls": None}
    assert routing.pick("hey hawk", [], "gemma4:e4b", today=MON, ask=ask).family == "none" and len(calls) == 1


def test_the_calendar_names_the_weeks_and_weekends():
    cal = routing.calendar_lines(MON)
    assert "This weekend: Sat 2026-11-21 to Sun 2026-11-22" in cal
    assert "Next week: Mon 2026-11-23 to Sun 2026-11-29" in cal
    assert "The week after next: Mon 2026-11-30 to Sun 2026-12-06" in cal
    sunday = routing.calendar_lines(date(2026, 11, 22))
    assert "This weekend: Sun 2026-11-22 to Sun 2026-11-22" in sunday and "Next week: Mon 2026-11-23" in sunday


# ---- what the model is sent ------------------------------------------------------------------------------
def test_the_prompt_has_todays_date_and_a_calendar_built_in_code():
    _, seen = pick("events this weekend?", [], tool("search_events"))
    system = seen["messages"][0]["content"]
    assert "Today is Monday, November 16, 2026" in system
    assert "Sat 2026-11-21" in system and "Mon 2026-11-16  (today)" in system and "Sun 2026-12-06" in system
    assert "{" not in system   # every placeholder filled


def test_only_the_last_messages_go_to_the_model_each_cut_to_a_length():
    hist = [u(f"q{i}") if i % 2 == 0 else b("x" * 5000) for i in range(10)]
    _, seen = pick("and?", hist, tool("search_handbook", query="x"))
    turns = seen["messages"][1:-1]
    assert len(turns) == routing.HISTORY_MESSAGES
    assert max(len(t["content"]) for t in turns) == routing.HISTORY_CHARS
    assert [t["role"] for t in turns][:2] == ["user", "assistant"]
    assert seen["messages"][-1] == {"role": "user", "content": "and?"}


# ---- in the service ------------------------------------------------------------------------------------------
@pytest.fixture
def pipeline(monkeypatch):
    log = {"queries": [], "model": 0}
    monkeypatch.setattr(service_module, "retrieve", lambda c, q: log["queries"].append(q) or [
        {"text": "x", "source": "student_org_handbook.pdf", "page_start": 4, "page_end": 4}])
    monkeypatch.setattr(service_module, "model_loaded", lambda m: True)

    def answer(**kw):
        log["model"] += 1
        yield "Answer."
    monkeypatch.setattr(service_module, "stream_answer", answer)
    return log


def svc_with(monkeypatch, ask):
    monkeypatch.setattr(routing, "ask_model", ask)
    cfg = load_config()
    cfg.web_tools = {"enabled": False}   # no 312 fetches here; lookups are tested in tests/webtools
    return service_module.RagService(cfg, collection=None, guardrail=None)


def run(service, text, history=()):
    req = ChatRequest(messages=[*history, {"role": "user", "text": text}])
    return [json.loads(line) for line in service.chat_stream(req)]


def test_a_later_message_searches_with_the_pickers_text_and_todays_text_merged(monkeypatch, pipeline):
    merged = []
    monkeypatch.setattr(service_module, "retrieve_merged", lambda c, qs: merged.append(qs) or [])
    ask, _ = fake(tool("search_handbook", query="funding for a new student club"))
    run(svc_with(monkeypatch, ask), "Funding?", [u("How do I start a club?"), b("Fill in the form.")])
    assert merged == [["funding for a new student club", "Funding?"]] and pipeline["model"] == 1


def test_a_first_message_searches_with_the_students_own_words(monkeypatch, pipeline):
    # R Part 4 + review 3: the picker's rewrites of standalone questions found worse passages for 6 A1 questions,
    # so a first message is searched exactly as today, even when the picker wrote a text
    ask, _ = fake(tool("search_handbook", query="event submission deadline"))
    run(svc_with(monkeypatch, ask), "How far in advance do I need to submit an event?")
    assert pipeline["queries"] == ["How far in advance do I need to submit an event?"]


def test_none_still_searches_with_todays_text(monkeypatch, pipeline):
    ask, _ = fake(content="Hi!")
    run(svc_with(monkeypatch, ask), "hey hawk")
    assert pipeline["queries"] == ["hey hawk"]


def test_fixed_replies_and_the_support_text_never_call_the_picker(monkeypatch, pipeline):
    def must_not_run(messages, model, timeout):
        raise AssertionError("the search picker must not run")
    service = svc_with(monkeypatch, must_not_run)
    events = run(service, "thanks", [u("How do I reserve a room?"), b("Use 312.")])
    assert pipeline["queries"] == [] and "welcome" in "".join(e.get("token", "") for e in events).lower()
    events = run(service, "I want to end my life")
    assert "988" in "".join(e.get("token", "") for e in events)


def test_an_event_link_skips_the_picker(monkeypatch):
    def must_not_run(messages, model, timeout):
        raise AssertionError("the search picker must not run")
    monkeypatch.setattr(routing, "ask_model", must_not_run)
    service = service_module.RagService(load_config(), collection=None)
    plan = service._plan("what is https://312.iit.edu/rsvp?id=385553", [])
    assert [c.name for c in plan.calls] == ["get_event_details"]


def test_picker_off_in_the_config_means_the_regex_router(monkeypatch, pipeline):
    def must_not_run(messages, model, timeout):
        raise AssertionError("the search picker must not run")
    service = svc_with(monkeypatch, must_not_run)
    service.config.search_picker = {"enabled": False}
    run(service, "How do I start a club?")
    assert pipeline["queries"] == ["How do I start a club?"]


# ---- "nothing found" says what was searched ------------------------------------------------------------
def test_no_results_say_what_was_searched_when_it_is_short():
    env = {"ok": True, "retrieved_at": None, "searched": "AI",
           "data": {"window": {"from": "2026-11-23", "to": "2026-11-29"}, "events": [], "total_matches": 0}}
    assert "I didn't find any events matching 'AI' from Mon Nov 23 to Sun Nov 29." in render_for_user(env)
    env["searched"] = "Is there an AI club or any AI events I could attend this week?"
    assert "I didn't find any matching events from Mon Nov 23 to Sun Nov 29." in render_for_user(env)
    clubs = {"ok": True, "retrieved_at": None, "searched": "knitting",
             "data": {"clubs": [], "total_matches": 0, "returned": 0, "offset": 0}}
    assert render_for_user(clubs).startswith("I didn't find any clubs matching 'knitting'.")


# ---- the merged handbook search (retrieval/chroma_setup.retrieve_merged) -------------------------------------
class FakeCollection:
    """query() answers per text from a fixed table: text -> [(doc, path, distance), ...]."""
    def __init__(self, table):
        self.table = table

    def query(self, query_texts, n_results):
        rows = [self.table[q][:n_results] for q in query_texts]
        return {"documents": [[d for d, _, _ in r] for r in rows],
                "metadatas": [[{"path": p, "page_start": 1} for _, p, _ in r] for r in rows],
                "distances": [[x for _, _, x in r] for r in rows]}


def test_merged_search_keeps_the_closest_three_of_both_texts_each_once():
    from retrieval.chroma_setup import retrieve_merged
    col = FakeCollection({
        "event submission deadline": [("A", "Events > Deadlines", 0.59), ("B", "Events > Forms", 0.60),
                                      ("C", "Funding", 0.70)],
        "How far in advance do I need to submit an event?": [("D", "Events > Lead time", 0.54),
                                                             ("A", "Events > Deadlines", 0.58),
                                                             ("E", "Rooms", 0.80)]})
    got = retrieve_merged(col, ["event submission deadline", "How far in advance do I need to submit an event?"])
    assert [c["text"] for c in got] == ["D", "A", "B"]   # by distance, A once, E dropped (> 0.75)


def test_merged_search_with_one_text_is_the_plain_search():
    from retrieval.chroma_setup import retrieve_merged
    col = FakeCollection({"x": [("A", "p", 0.5), ("B", "q", 0.9)]})
    assert [c["text"] for c in retrieve_merged(col, ["x", "x"])] == ["A"]


# ---- date, time and type filters written by the model ------------------------------------------------------
def test_the_prompt_tells_the_model_about_time_from_and_the_feeds_types():
    text = routing.system_prompt(MON)
    assert "time_from" in text and '"17:00"' in text
    assert '"Career Preparation"' in text and '"Free Food"' in text
    assert "{event_types}" not in text and "{event_tags}" not in text


def test_date_time_and_type_filters_pass_through_unchanged():
    p, _ = pick("career events tomorrow after 5pm", (), tool(
        "search_events", date_from="2026-11-17", date_to="2026-11-17", time_from="17:00",
        event_type="Career Preparation"))
    assert p.source == "model" and len(p.calls) == 1
    a = p.calls[0].args
    assert (a["date_from"], a["time_from"], a["event_type"]) == ("2026-11-17", "17:00", "Career Preparation")
    assert "query" not in a


def test_time_words_the_model_leaves_in_query_are_dropped():
    p, _ = pick("events tomorrow after 5pm", (), tool(
        "search_events", query="events after 5pm", date_from="2026-11-17", date_to="2026-11-17", time_from="17:00"))
    assert "query" not in p.calls[0].args and p.calls[0].args["time_from"] == "17:00"


def test_a_real_topic_stays_in_query_next_to_the_filters():
    p, _ = pick("robotics events tomorrow after 5pm", (), tool(
        "search_events", query="robotics after 5pm", date_from="2026-11-17", date_to="2026-11-17",
        time_from="17:00"))
    assert p.calls[0].args["query"] == "robotics after 5pm"   # kept as written; event_tokens ignores the time words


def test_a_follow_up_can_change_only_the_time_filter():
    hist = [u("what's on tomorrow?"), b("Here's what's on 312 on Tue Nov 17: ...")]
    p, seen = pick("anything later, after 7?", hist, tool(
        "search_events", date_from="2026-11-17", date_to="2026-11-17", time_from="19:00"))
    assert p.calls[0].args["time_from"] == "19:00"
    assert seen["messages"][-2]["content"].startswith("Here's what's on 312")   # the earlier answer was sent along
