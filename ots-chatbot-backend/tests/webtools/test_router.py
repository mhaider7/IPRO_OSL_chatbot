import pytest

from web_tools.router import route


@pytest.mark.parametrize("msg", [
    "What clubs are there for robotics?",
    "Any clubs about music?",
    "I'm interested in joining a dance club",
    "recommend a club for gaming",
    "list student organizations",
])
def test_discovery_routes_to_search_clubs(msg):
    call = route(msg)
    assert call and call.name == "search_clubs" and call.args["query"] == msg
    assert call.stage == "searching_clubs"


@pytest.mark.parametrize("msg", [
    "Tell me about the Boxing Club",
    "tell me more about the Robotics Club?",
    "What is the Chess Club?",
])
def test_details_routes_with_search_fallback(msg):
    call = route(msg)
    assert call and call.name == "get_club_details"
    assert call.fallback and call.fallback.name == "search_clubs"
    assert not call.args["slug"].lower().endswith("club")


def test_name_resolved_when_known_even_without_club_word():
    assert route("tell me about Alpha Sigma Alpha", {"alpha sigma alpha"}).name == "get_club_details"
    assert route("tell me about Alpha Sigma Alpha") is None


@pytest.mark.parametrize("msg", [
    "How do I start a club?",
    "What is the club re-registration deadline?",
    "How do I get funding for my organization?",
    "Who is my club's advisor?",
    "How do I reserve a room for my org meeting?",
    "What are the requirements to be a recognized student organization?",
    "Hello!",
    "How do I drop a class?",
    "",
])
def test_policy_and_unrelated_questions_do_not_route(msg):
    assert route(msg) is None


def test_overlong_messages_ignored():
    assert route("clubs " * 200) is None


# ---- events -------------------------------------------------------------------------------------
from datetime import date

from web_tools.router import parse_dates

TUE = date(2026, 11, 10)   # a Tuesday


def ev(msg, today=TUE):
    return route(msg, today=today)


def test_events_basic_default_window():
    call = ev("What events are happening on campus?")
    assert call.name == "search_events" and call.args == {"limit": 6} and call.stage == "searching_events"


def test_free_food_this_week():
    call = ev("Is there any free food this week?")
    assert call.name == "search_events"
    assert call.args == {"limit": 6, "date_from": "2026-11-10", "date_to": "2026-11-15", "tags": ["Free Food"]}


def test_tonight_and_tomorrow():
    assert ev("any events tonight?").args["date_from"] == "2026-11-10"
    c = ev("what's happening tomorrow")
    assert c.args["date_from"] == c.args["date_to"] == "2026-11-11"


def test_type_online_and_topic_query():
    call = ev("Any virtual career events this month?")
    assert call.args["event_type"] == "Career Preparation" and call.args["location_type"] == "online"
    assert call.args["date_to"] == "2026-11-30"
    assert "query" not in call.args
    call = ev("What events does the Robotics Club have?")
    assert call.name == "search_events" and "Robotics" in call.args["query"]


def test_event_lookup_by_id_or_url():
    assert ev("tell me about event 385553").args == {"event_id_or_url": "385553"}
    assert ev("what is https://312.iit.edu/rsvp?id=385553").name == "get_event_details"


@pytest.mark.parametrize("msg", [
    "How do I host an event?", "How do I register an event on 312?", "What are the event planning rules?",
    "How do I get approval for a campus event?", "Who pays event charges?", "How do I advertise my event?",
])
def test_event_policy_questions_do_not_route(msg):
    assert ev(msg) is None


def test_club_questions_still_go_to_clubs():
    assert ev("any clubs about music?").name == "search_clubs"
    assert ev("tell me about the Boxing Club").name == "get_club_details"


@pytest.mark.parametrize("text,expected", [
    ("this weekend", (date(2026, 11, 14), date(2026, 11, 15))),
    ("next week", (date(2026, 11, 16), date(2026, 11, 22))),
    ("in december", (date(2026, 12, 1), date(2026, 12, 31))),
    ("in january", (date(2027, 1, 1), date(2027, 1, 31))),
    ("on November 20th", (date(2026, 11, 20), date(2026, 11, 20))),
    ("on march 3", (date(2027, 3, 3), date(2027, 3, 3))),
    ("in november", (date(2026, 11, 10), date(2026, 11, 30))),
    ("next month", (date(2026, 12, 1), date(2026, 12, 31))),
    ("whenever", None),
])
def test_parse_dates(text, expected):
    assert parse_dates(text, TUE) == expected


def test_weekend_when_already_weekend():
    assert parse_dates("this weekend", date(2026, 11, 14)) == (date(2026, 11, 14), date(2026, 11, 15))
    assert parse_dates("this weekend", date(2026, 11, 15)) == (date(2026, 11, 15), date(2026, 11, 15))
