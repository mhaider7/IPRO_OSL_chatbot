from datetime import date, datetime
from pathlib import Path

import pytest

from web_tools.config import WebToolsConfig
from web_tools.fetch.client import FetchResult
from web_tools.fetch.robots import RobotsDisallowed
from web_tools.render import render_for_prompt
from web_tools.tools.events import CHI, EVENTS_URL, parse_event_ref
from web_tools.tools.registry import WebTools

from .test_club_tools import FakeFetcher

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"
with open(FIX / "ical_sample.ics", encoding="utf-8", newline="") as _f:
    ICS = _f.read()
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=CHI)   # Tue Nov 10 2026, morning Central


def feed(text=ICS, **kw):
    return FetchResult(ok=True, url=EVENTS_URL, text=text, retrieved_at="2026-11-10T14:00:00+00:00",
                       last_modified="Tue, 10 Nov 2026 17:52:58 GMT", **kw)


def tools(result=None, clubs=None):
    f = clubs or FakeFetcher()
    f.events_result = result or feed()
    return WebTools(WebToolsConfig(min_clubs=1, min_events=1), fetcher=f, now=lambda: NOW)


def ids(env):
    return [e["event_id"] for e in env["data"]["events"]]


def test_default_window_is_today_plus_14_days_sorted():
    env = tools().run("search_events", {})
    assert env["ok"] and env["source_url"] == EVENTS_URL
    assert env["data"]["window"] == {"from": "2026-11-10", "to": "2026-11-24", "timezone": "America/Chicago"}
    starts = [e["start_iso"] for e in env["data"]["events"]]
    assert starts == sorted(starts) and starts
    assert "382672" not in ids(env)          # Aug event excluded
    assert env["data"]["events"][0]["start_iso"].endswith("-06:00")   # Central standard time
    assert env["feed_last_modified"].startswith("Tue, 10 Nov")


def test_past_window_includes_old_events():
    env = tools().run("search_events", {"date_from": "2026-08-01", "date_to": "2026-08-31", "limit": 25})
    assert "382672" in ids(env) and "382720" in ids(env)


def test_local_date_boundary_uses_central_time():
    # 385553 starts 2026-11-10 23:30Z = 17:30 CST on the 10th; 00:00Z events are 6 pm CST on Nov 9.
    env = tools().run("search_events", {"date_from": "2026-11-09", "date_to": "2026-11-09", "limit": 25})
    assert "383225" in ids(env) and "385553" not in ids(env)


def test_tags_filter_any():
    env = tools().run("search_events", {"tags": ["free food"], "limit": 25})
    assert env["data"]["events"] and all("Free Food" in e["tags"] for e in env["data"]["events"])
    assert tools().run("search_events", {"tags": "Free Food"})["ok"]    # bare string accepted


def test_event_type_and_category_alias():
    a = tools().run("search_events", {"event_type": "meeting", "limit": 25})
    b = tools().run("search_events", {"category": "Meeting", "limit": 25})
    assert ids(a) == ids(b) and ids(a)
    assert all(e["event_type"] == "Meeting" for e in a["data"]["events"])


@pytest.mark.parametrize("kind,expected_id", [("tbd", "385553"), ("hidden", "383225")])
def test_location_type_filter(kind, expected_id):
    env = tools().run("search_events", {"location_type": kind, "date_from": "2026-11-09", "limit": 25})
    assert expected_id in ids(env) and all(e["location_type"] == kind for e in env["data"]["events"])


def test_hidden_location_is_flagged_not_invented():
    env = tools().run("search_events", {"date_from": "2026-11-10", "date_to": "2026-11-10", "limit": 25})
    hidden = [e for e in env["data"]["events"] if e["location_hidden"]]
    assert hidden and all(e["location"] is None for e in hidden)


def test_text_query_all_tokens_then_any_fallback():
    t = tools()
    env = t.run("search_events", {"query": "bible study", "date_from": "2026-11-10", "date_to": "2026-11-30"})
    assert "382770" in ids(env) and env["data"]["text_match"] == "all"
    env = t.run("search_events", {"query": "bible gathering quidditch", "date_from": "2026-11-10", "date_to": "2026-11-30"})
    assert "382770" in ids(env) and env["data"]["text_match"] == "any"
    # one matching word out of two is NOT enough for the loose fallback
    env = t.run("search_events", {"query": "bible quidditch", "date_from": "2026-11-10", "date_to": "2026-11-30"})
    assert env["data"]["events"] == []


def test_query_stopwords_ignore_event_words():
    env = tools().run("search_events", {"query": "what events are happening this week", "limit": 25})
    assert env["data"]["total_matches"] > 3   # not filtered down by "events"/"happening"/"week"


def test_limit_and_total():
    env = tools().run("search_events", {"limit": 2, "date_from": "2026-11-10", "date_to": "2026-11-30"})
    assert env["data"]["returned"] == 2 and env["data"]["total_matches"] > 2


@pytest.mark.parametrize("bad", [
    {"date_from": "yesterday"}, {"date_from": "2026-11-10", "date_to": "2026-11-01"},
    {"limit": 100}, {"location_type": "mars"}, {"unknown": 1},
])
def test_bad_args(bad):
    assert tools().run("search_events", bad)["error"]["code"] == "bad_args"


def test_no_results_hint():
    env = tools().run("search_events", {"query": "quidditch"})
    assert env["ok"] and env["data"]["events"] == [] and env["data"]["hint"]


# ---- get_event_details ------------------------------------------------------------------------

@pytest.mark.parametrize("ref", [
    "382770", " 382770 ", "https://312.iit.edu/rsvp?id=382770", "https://312.iit.edu/IITUBF/rsvp_boot?id=382770#event_host",
    "https://312.iit.edu/w/_/_/events/aWl0OkV2ZW50OjM4Mjc3MA==", "iit:Event:382770",
])
def test_event_ref_forms(ref):
    assert parse_event_ref(ref) == "382770"
    env = tools().run("get_event_details", {"event_id_or_url": ref})
    assert env["ok"] and env["data"]["event_id"] == "382770"


@pytest.mark.parametrize("ref", ["hello", "https://312.iit.edu/events", "!!!notbase64"])
def test_event_ref_garbage(ref):
    assert tools().run("get_event_details", {"event_id_or_url": ref})["error"]["code"] == "bad_args"


def test_details_content_and_gaps():
    d = tools().run("get_event_details", {"event_id_or_url": "382770"})["data"]
    assert d["description"].startswith("Come join the Illinois Tech Bible Club") and "Event Details" not in d["description"]
    assert d["club_page_url"] == "https://312.iit.edu/IITUBF/"
    assert {"price", "attendee_count", "registration_status"} <= set(d["unavailable_fields"])
    hidden = tools().run("get_event_details", {"event_id_or_url": "383225"})["data"]
    assert "location" in hidden["unavailable_fields"] and hidden["location"] is None


def test_details_unknown_event():
    env = tools().run("get_event_details", {"event_id_or_url": "1"})
    assert not env["ok"] and env["error"]["code"] == "no_results" and env["source_url"].endswith("id=1")


# ---- error handling / freshness -----------------------------------------------------------------

def test_stale_feed_flag_and_render():
    env = tools(feed(stale=True, stale_reason="timeout")).run("search_events", {})
    assert env["ok"] and env["stale"] and "live refresh just failed" in render_for_prompt(env)


def test_feed_failure_and_auth():
    bad = FetchResult(ok=False, url=EVENTS_URL, error_code="timeout", error_message="slow")
    assert tools(bad).run("search_events", {})["error"]["code"] == "timeout"
    auth = feed(stale=True, auth_required=True, stale_reason="auth_required")
    env = tools(auth).run("search_events", {})
    assert env["ok"] and env["auth_required"]


def test_robots_refusal_is_reported():
    t = tools()
    t.fetcher.events_result = RobotsDisallowed(EVENTS_URL, "/x")
    assert t.run("search_events", {})["error"]["code"] == "robots_disallowed"


def test_html_instead_of_feed_is_layout_change():
    env = tools(feed("<html>Please sign in</html>")).run("search_events", {})
    assert not env["ok"] and env["error"]["code"] == "layout_changed"


# ---- club details gain upcoming events ------------------------------------------------------------

def test_club_details_include_upcoming_events_when_feed_available():
    d = tools().run("get_club_details", {"slug": "33rd"})["data"]
    assert d["upcoming_events"] == []                    # club has none in the fixture feed
    evs = tools()._club_events("IITUBF")
    assert [e["event_id"] for e in evs] == ["382770"]      # past Aug event excluded, slug match is case-insensitive


def test_club_details_survive_events_failure():
    env = tools(FetchResult(ok=False, url=EVENTS_URL, error_code="timeout", error_message="x")).run(
        "get_club_details", {"slug": "33rd"})
    assert env["ok"] and env["data"]["upcoming_events"] is None


# ---- rendering -------------------------------------------------------------------------------------

def test_render_events_block():
    env = tools().run("search_events", {"date_from": "2026-11-10", "date_to": "2026-11-12", "limit": 25})
    text = render_for_prompt(env)
    assert text.startswith("Facts from 312.iit.edu, current as of Tue Nov 10, 8:00 AM Central.")
    assert "Events from 2026-11-10 to 2026-11-12 (Central time)" in text
    assert "[event page](https://312.iit.edu/rsvp?id=382770)" in text
    assert "location shown only to signed-in users" in text and "location TBD" in text
    assert "12:45 PM–1:45 PM Central" in text
    assert len(text) <= 6000


def test_render_detail_and_size_cap():
    text = render_for_prompt(tools().run("get_event_details", {"event_id_or_url": "382770"}))
    assert "Not available publicly:" in text and "price" in text
    big = "".join(
        f"BEGIN:VEVENT\nDTSTART:20261111T150000Z\nUID:u{i}\nSUMMARY:Event {i}\nURL:https://312.iit.edu/rsvp?id={i + 1}\n"
        f"DESCRIPTION:{'word ' * 200}\nEND:VEVENT\n" for i in range(60))
    env = tools(feed("BEGIN:VCALENDAR\n" + big + "END:VCALENDAR\n")).run("search_events", {"limit": 25})
    out = render_for_prompt(env)
    assert len(out) <= 6000 and env["truncated"] is True and "more events not shown" in out


# ---- long-running listings must not crowd out things you can attend ---------------------------------

def _ev(i, start, end, title):
    return (f"BEGIN:VEVENT\nDTSTART:{start}\nDTEND:{end}\nUID:u{i}\nSUMMARY:{title}\n"
            f"URL:https://312.iit.edu/rsvp?id={i}\nDESCRIPTION:d\nEND:VEVENT\n")


def test_ongoing_listings_sort_after_events_in_the_window():
    cal = "BEGIN:VCALENDAR\n" + _ev(1, "20260902T180000Z", "20261215T200000Z", "Semester-long Closet") \
        + _ev(2, "20261112T150000Z", "20261112T160000Z", "Bagels") \
        + _ev(3, "20261030T050000Z", "20261120T180000Z", "Month-long Drive") \
        + _ev(4, "20261111T170000Z", "20261113T230000Z", "Two-day Photo Signup") \
        + "END:VCALENDAR\n"
    env = tools(feed(cal)).run("search_events", {"date_from": "2026-11-12", "date_to": "2026-11-12"})
    titles = [e["title"] for e in env["data"]["events"]]
    assert titles[0] == "Bagels" and set(titles[1:]) == {"Semester-long Closet", "Month-long Drive", "Two-day Photo Signup"}
    by = {e["title"]: e for e in env["data"]["events"]}
    assert by["Bagels"]["ongoing"] is False and by["Semester-long Closet"]["ongoing"] is True
    assert by["Semester-long Closet"]["when"] == "ongoing through Tue Dec 15"


def test_render_for_prompt_uses_central_times_not_utc():
    text = render_for_prompt(tools().run("search_events", {"date_from": "2026-11-10", "date_to": "2026-11-12"}))
    assert "+00:00" not in text and "GMT" not in text and "Calendar feed last updated Tue Nov 10, 11:52 AM Central" in text
