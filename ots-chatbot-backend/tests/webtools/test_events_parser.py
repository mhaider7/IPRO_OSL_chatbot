from datetime import datetime, timezone
from pathlib import Path

import pytest

from web_tools.parsers.clubs_html import LayoutChanged
from web_tools.parsers.events_ics import parse_ics, unescape

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"


def read(name):
    with open(FIX / name, encoding="utf-8", newline="") as f:   # keep CRLF, like the real feed
        return f.read()


@pytest.fixture(scope="module")
def events():
    text = read("ical_sample.ics")
    return {e.event_id: e for e in parse_ics(text)}


def test_all_fixture_events_parse_crlf(events):
    assert len(events) == 12


def test_core_fields(events):
    e = events["382770"]
    assert e.title == "Illinois Tech Bible Club: Good Word Gathering"
    assert e.start == datetime(2026, 11, 10, 18, 45, tzinfo=timezone.utc)
    assert e.end == datetime(2026, 11, 10, 19, 45, tzinfo=timezone.utc)
    assert e.url == "https://312.iit.edu/rsvp?id=382770"
    assert e.club_name == "Illinois Tech Bible Club" and e.club_slug == "IITUBF"
    assert e.event_type == "Spiritual" and e.tags == ("Free Food",)
    assert e.location.startswith("Rettaliata Engineering Center Room 034")
    assert e.location_type == "in_person"


def test_description_drops_trailing_event_details_link(events):
    d = events["382770"].description
    assert d.startswith("Come join the Illinois Tech Bible Club") and "Event Details" not in d
    assert "\\n" not in d


@pytest.mark.parametrize("event_id,kind", [
    ("382672", "hidden"), ("385553", "tbd"), ("382720", "online"), ("382770", "in_person"),
])
def test_location_types(events, event_id, kind):
    assert events[event_id].location_type == kind


def test_tags_multi_and_empty(events):
    assert any(len(e.tags) >= 2 for e in events.values())
    assert any(e.tags == () for e in events.values())


def test_unicode_title_survives(events):
    assert any("🎮" in e.title for e in events.values())


def test_unescape():
    assert unescape(r"a\nb\, c\; d\\e") == "a\nb, c; d\\e"


def _vevent(**kw):
    f = {"DTSTART": "20261110T184500Z", "UID": "u1", "SUMMARY": "S", "URL": "https://312.iit.edu/rsvp?id=9"}
    f.update(kw)
    return "BEGIN:VEVENT\n" + "\n".join(f"{k}:{v}" for k, v in f.items() if v is not None) + "\nEND:VEVENT\n"


def _cal(*events):
    return "BEGIN:VCALENDAR\nVERSION:2.0\n" + "".join(events) + "END:VCALENDAR\n"


def test_folded_lines_are_unfolded():
    text = _cal(_vevent(DESCRIPTION="hello\n  world"))
    assert parse_ics(text)[0].description == "hello world"


def test_organizer_with_colon_in_quoted_name():
    ev = 'BEGIN:VEVENT\nORGANIZER;CN="Club: The Sequel":mailto:x@y.z\n'
    text = _cal(ev + _vevent().removeprefix("BEGIN:VEVENT\n"))
    assert parse_ics(text)[0].club_name == "Club: The Sequel"


def test_malformed_events_skipped_not_fatal():
    good = [_vevent(UID=f"u{i}", URL=f"https://312.iit.edu/rsvp?id={i}") for i in range(1, 5)]
    bad = _vevent(DTSTART="garbage") + _vevent(URL="https://312.iit.edu/rsvp")  # no id
    assert len(parse_ics(_cal(*good, bad))) == 4


def test_too_many_malformed_is_layout_change():
    bad = "".join(_vevent(DTSTART="x", UID=f"u{i}") for i in range(6))
    with pytest.raises(LayoutChanged):
        parse_ics(_cal(bad, _vevent()))


@pytest.mark.parametrize("text", ["", "<html><body>Please sign in</body></html>", "BEGIN:VCALENDAR\nEND:VCALENDAR\n"])
def test_non_feed_raises(text):
    with pytest.raises(LayoutChanged):
        parse_ics(text)


def test_club_feed_fixture_parses():
    evs = parse_ics(read("ical_club.ics"))
    assert evs and evs[0].club_name == "Chicago-Kent Clinic"
