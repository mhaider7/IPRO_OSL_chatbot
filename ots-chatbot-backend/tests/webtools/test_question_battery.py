"""Regression battery: real-world phrasings of event and club questions.

Routing + argument extraction are pure, so they are asserted exactly; matching is asserted against
small synthetic data. (The same phrasings were run against the live feed when these were written.)
"""
from datetime import date, datetime

import pytest

from web_tools.config import WebToolsConfig
from web_tools.fetch.client import FetchResult
from web_tools.router import parse_dates, route
from web_tools.tools.events import CHI, EVENTS_URL, event_tokens
from web_tools.tools.registry import WebTools

from .test_club_tools import FakeFetcher

TUE = date(2026, 11, 10)


def r(msg, known=None):
    return route(msg, known, today=TUE)


# ---- events: should route to search_events, with these filters ---------------------------------------
EVENT_CASES = [
    ("what events are happening today?", {"date_from": "2026-11-10", "date_to": "2026-11-10"}),
    ("anything going on tonight?", {"date_from": "2026-11-10", "date_to": "2026-11-10", "time_from": "17:00"}),
    ("is there anything to do tonight?", {"time_from": "17:00"}),
    ("what's happening tomorrow", {"date_from": "2026-11-11"}),
    ("any events this weekend?", {"date_from": "2026-11-14", "date_to": "2026-11-15"}),
    ("events next week", {"date_from": "2026-11-16", "date_to": "2026-11-22"}),
    ("what events are in December?", {"date_from": "2026-12-01", "date_to": "2026-12-31"}),
    ("any events on November 20?", {"date_from": "2026-11-20", "date_to": "2026-11-20"}),
    ("what's going on this Friday?", {"date_from": "2026-11-13", "date_to": "2026-11-13"}),
    ("events next Friday", {"date_from": "2026-11-20", "date_to": "2026-11-20"}),
    ("events on Tuesday", {"date_from": "2026-11-10", "date_to": "2026-11-10"}),
    ("is there free food today?", {"tags": ["Free Food"], "date_from": "2026-11-10"}),
    ("free pizza events this week", {"tags": ["Free Food"], "date_to": "2026-11-15"}),
    ("career events coming up", {"event_type": "Career Preparation"}),
    ("any workshops this week?", {"event_type": "Workshop", "date_to": "2026-11-15"}),
    ("meetings this week", {"event_type": "Meeting"}),
    ("sports events this weekend", {"event_type": "Athletic/Sports"}),
    ("online events this week", {"location_type": "online"}),
    ("virtual career fair", {"location_type": "online", "event_type": "Career Preparation"}),
    ("social events tomorrow", {"event_type": "Social", "date_from": "2026-11-11"}),
    ("any fundraisers coming up?", {"event_type": "Fundraiser"}),
    ("is there a hackathon coming up?", {}),
    ("events for new students", {"tags": ["New Students"]}),
    ("honors college events", {"tags": ["Honors College Experience"]}),
    ("events happening on campus right now", {"date_from": "2026-11-10", "date_to": "2026-11-10"}),
    ("what events is the ACM hosting?", {}),
    ("events by the Robotics Club", {}),
    ("upcoming events", {}),
]


@pytest.mark.parametrize("msg,expected", EVENT_CASES)
def test_event_questions_route_with_filters(msg, expected):
    call = r(msg)
    assert call is not None and call.name == "search_events", msg
    for k, v in expected.items():
        assert call.args.get(k) == v, f"{msg!r}: {k}={call.args.get(k)!r}, wanted {v!r}"


def test_filler_only_questions_send_no_free_text_query():
    for msg in ["anything going on tonight?", "what's happening tomorrow", "upcoming events", "career events coming up",
                "what events are happening today?", "any events this weekend?"]:
        assert "query" not in r(msg).args, msg


@pytest.mark.parametrize("msg,tokens", [
    ("events about AI", ["ai"]), ("what events is the ACM hosting?", ["acm"]),
    ("events by the Robotics Club", ["robotic"]), ("is there a hackathon coming up?", ["hackathon"]),
    ("what's going on", []),
])
def test_event_query_tokens_drop_filler(msg, tokens):
    assert event_tokens(msg) == tokens


# ---- clubs: should route to the club tools --------------------------------------------------------------
@pytest.mark.parametrize("msg", [
    "what clubs are there for robotics?", "any music clubs?", "are there dance clubs?", "gaming or esports clubs?",
    "what sports clubs does IIT have?", "volunteering clubs", "religious or faith groups", "engineering clubs",
    "cultural clubs", "clubs for international students", "are there any fraternities or sororities?",
    "any coding clubs?", "art clubs", "business clubs", "what clubs are there?", "recommend a club for photography",
    "clubs related to biology", "residence hall groups", "hiking or outdoors club", "any pre-med clubs?",
    "I like anime, any clubs?", "what student organizations focus on entrepreneurship?",
])
def test_club_discovery_questions_route(msg):
    call = r(msg)
    assert call is not None and call.name == "search_clubs", msg


@pytest.mark.parametrize("msg,name", [
    ("tell me about the Boxing Club", "Boxing"), ("who runs the Robotics club?", "Robotics"),
    ("how can I join the Chess Club?", "Chess"), ("what is the Society of Women Engineers?", "Society of Women Engineers"),
])
def test_club_detail_questions_route(msg, name):
    call = r(msg)
    assert call.name == "get_club_details" and call.args["slug"] == name


def test_known_club_name_without_club_word_routes():
    assert r("contact info for the Alpha Sigma Alpha", {"alpha sigma alpha"}).name == "get_club_details"


# ---- handbook questions must stay out of live lookups ------------------------------------------------------
@pytest.mark.parametrize("msg", [
    "How do I start a club?", "how do I host an event?", "How can I host an event on campus?",
    "Can we plan an event in the MTCC?", "what funding can my org get for an event?", "When is club re-registration due?",
    "how do I reserve a room?", "what is the Finance Board?", "hello", "how do I drop a class?",
    "How do I get approval to organize an event?", "what are the rules for hosting a fundraiser?",
    "What are the club officer requirements?",
])
def test_policy_questions_do_not_route(msg):
    assert r(msg) is None, msg


@pytest.mark.parametrize("text,expected", [
    ("this friday", (date(2026, 11, 13), date(2026, 11, 13))),
    ("on friday", (date(2026, 11, 13), date(2026, 11, 13))),
    ("tuesday", (date(2026, 11, 10), date(2026, 11, 10))),       # today is Tuesday: today counts
    ("monday", (date(2026, 11, 16), date(2026, 11, 16))),
    ("next monday", (date(2026, 11, 16), date(2026, 11, 16))),
    ("next friday", (date(2026, 11, 20), date(2026, 11, 20))),
    ("right now", (date(2026, 11, 10), date(2026, 11, 10))),
])
def test_weekday_dates(text, expected):
    assert parse_dates(text, TUE) == expected


# ---- matching behavior on events -------------------------------------------------------------------------------
def _ev(i, start, end, title, desc="d", loc=None, kind="Social"):
    loc_line = f"LOCATION:{loc}\n" if loc else ""
    return (f"BEGIN:VEVENT\nDTSTART:{start}\nDTEND:{end}\nUID:u{i}\nSUMMARY:{title}\n{loc_line}"
            f"URL:https://312.iit.edu/rsvp?id={i}\nDESCRIPTION:{desc}\n"
            f"CATEGORIES;X-CG-CATEGORY=event_type:{kind}\nEND:VEVENT\n")


def event_tools(*events, now=datetime(2026, 11, 10, 19, 0, tzinfo=CHI)):
    f = FakeFetcher()
    f.events_result = FetchResult(ok=True, url=EVENTS_URL, text="BEGIN:VCALENDAR\n" + "".join(events) + "END:VCALENDAR\n",
                                  retrieved_at="2026-11-11T01:00:00+00:00")
    return WebTools(WebToolsConfig(min_clubs=1, min_events=1), fetcher=f, now=lambda: now)


def titles(env):
    return [e["title"] for e in env["data"]["events"]]


def test_events_already_over_today_are_not_listed():
    t = event_tools(_ev(1, "20261110T150000Z", "20261110T160000Z", "Morning thing"),       # 9-10 AM CST: over
                    _ev(2, "20261111T010000Z", "20261111T030000Z", "Evening thing"))        # 7-9 PM CST: running
    assert titles(t.run("search_events", {"date_from": "2026-11-10", "date_to": "2026-11-10"})) == ["Evening thing"]


def test_tonight_cutoff_hides_afternoon_events():
    t = event_tools(_ev(1, "20261110T200000Z", "20261110T210000Z", "Afternoon"),           # 2-3 PM
                    _ev(2, "20261111T010000Z", "20261111T030000Z", "Evening"), now=datetime(2026, 11, 10, 9, 0, tzinfo=CHI))
    env = t.run("search_events", {"date_from": "2026-11-10", "date_to": "2026-11-10", "time_from": "17:00"})
    assert titles(env) == ["Evening"]


def test_past_days_still_searchable_when_asked_for():
    t = event_tools(_ev(1, "20260812T150000Z", "20260812T160000Z", "August thing"))
    assert titles(t.run("search_events", {"date_from": "2026-08-01", "date_to": "2026-08-31"})) == ["August thing"]


def test_short_token_ai_does_not_match_aid_or_air():
    t = event_tools(_ev(1, "20261112T150000Z", "20261112T160000Z", "Graduate School Financial Aid 101"),
                    _ev(2, "20261112T170000Z", "20261112T180000Z", "AI in Education Panel"),
                    _ev(3, "20261112T190000Z", "20261112T200000Z", "Air Quality Workshop"))
    assert titles(t.run("search_events", {"query": "AI", "date_to": "2026-11-20"})) == ["AI in Education Panel"]


def test_online_detected_from_text_when_location_hidden():
    t = event_tools(_ev(1, "20261112T150000Z", "20261112T160000Z", "Resume Webinar", loc="Sign in to download the location"),
                    _ev(2, "20261112T170000Z", "20261112T180000Z", "Pizza Night", loc="Sign in to download the location"),
                    _ev(3, "20261112T190000Z", "20261112T200000Z", "Remote Talk", desc="Join us on Zoom", loc="Online Event"))
    env = t.run("search_events", {"location_type": "online", "date_to": "2026-11-20"})
    assert set(titles(env)) == {"Resume Webinar", "Remote Talk"}
    assert "best-effort" in env["data"]["note"]


def test_type_with_no_matches_falls_back_to_text():
    t = event_tools(_ev(1, "20261114T150000Z", "20261114T160000Z", "3v3 Basketball Tournament",
                        desc="Come play sports with friends", kind="Fundraiser"))
    env = t.run("search_events", {"event_type": "Athletic/Sports", "date_to": "2026-11-20"})
    assert titles(env) == ["3v3 Basketball Tournament"] and "mention it instead" in env["data"]["note"]


def test_loose_text_fallback_needs_two_words():
    t = event_tools(_ev(1, "20261112T150000Z", "20261112T160000Z", "Robotics Meeting", desc="gears"))
    assert titles(t.run("search_events", {"query": "robotics quidditch", "date_to": "2026-11-20"})) == []


# ---- location questions & the sign-in message ----------------------------------------------------------------
from web_tools.render import render_for_user


@pytest.mark.parametrize("msg", ["where is event 385770?", "what is the location of event 385770?", "address for event 385770"])
def test_where_questions_carry_location_intent(msg):
    call = r(msg)
    assert call.name == "get_event_details" and call.intent == "location"
    assert r("what is event 385770?").intent is None


def test_where_question_for_search_has_intent():
    assert r("where are the events tomorrow?").intent == "location"


HIDDEN = _ev(1, "20261112T150000Z", "20261112T160000Z", "Pizza Night", loc="Sign in to download the location")
PUBLIC = _ev(2, "20261112T170000Z", "20261112T180000Z", "Open Lab", loc="MTCC 221, 3201 South State Street, Chicago")
TBD = _ev(3, "20261112T190000Z", "20261112T200000Z", "Mystery Meetup", loc="TBD")


def answer(ref, event, intent):
    env = event_tools(event).run("get_event_details", {"event_id_or_url": ref})
    env["intent"] = intent
    return render_for_user(env)


def test_hidden_location_answer_says_to_sign_in():
    text = answer("1", HIDDEN, "location")
    assert text.startswith("The location for **Pizza Night** isn't shown publicly. You need to sign in with your Illinois Tech account")
    assert "https://312.iit.edu/rsvp?id=1" in text.split("\n")[0]


def test_public_and_tbd_location_answers():
    assert answer("2", PUBLIC, "location").startswith("**Open Lab** is at MTCC 221")
    assert "listed as TBD" in answer("3", TBD, "location").split("\n")[0]


def test_detail_without_location_intent_still_explains_sign_in():
    assert "sign in with your Illinois Tech account" in answer("1", HIDDEN, None)


def test_list_keeps_sign_in_note_even_when_truncated():
    many = [_ev(i, "20261112T150000Z", "20261112T160000Z", f"Event {i}", loc="Sign in to download the location")
            for i in range(1, 10)]
    env = event_tools(*many).run("search_events", {"limit": 3, "date_to": "2026-11-20"})
    text = render_for_user(env)
    assert "Showing 3 of 9" in text and "sign in with your Illinois Tech account to see them" in text


def test_list_without_hidden_locations_has_no_sign_in_note():
    env = event_tools(PUBLIC).run("search_events", {"date_to": "2026-11-20"})
    assert "sign in" not in render_for_user(env).lower()
