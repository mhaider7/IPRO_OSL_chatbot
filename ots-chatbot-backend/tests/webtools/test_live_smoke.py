"""Live layout-drift check against 312.iit.edu. Excluded from `make test`; run with `make smoke`.

Makes about 5 polite requests (robots x2, clubs listing, iCal feed through its redirect). Every
assertion message says WHICH invariant drifted so a failure points at the parser to fix.
"""
import re
from urllib.parse import urlsplit

import pytest

from web_tools.config import WebToolsConfig
from web_tools.fetch.robots import BASELINE_RULES, parse_robots
from web_tools.parsers.events_ics import parse_ics, unfold
from web_tools.tools.registry import WebTools

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    return WebTools(WebToolsConfig(cache_dir=tmp_path_factory.mktemp("live-cache")))


def test_robots_txt_still_has_our_baseline_rules(live):
    r = live.fetcher._fetch_robots("https://312.iit.edu/robots.txt")
    assert r.status == 200, f"robots.txt returned {r.status}"
    published = {rule.pattern for rule in parse_robots(r.text) if not rule.allow}
    missing = set(BASELINE_RULES) - published
    assert not missing, f"baseline rules no longer published (update BASELINE_RULES?): {missing}"
    extra = published - set(BASELINE_RULES)
    assert not extra, f"robots.txt gained NEW rules (guard applies them live; review): {extra}"
    assert not re.search(r"(?im)^\s*(crawl-delay|sitemap)\s*:", r.text), "robots.txt now has Crawl-delay/Sitemap"


def test_clubs_listing_layout(live):
    env = live.run("search_clubs", {"limit": 25})
    assert env["ok"], f"clubs lookup failed: {env['error']}"
    assert not env["stale"], f"clubs served stale: {env['stale_reason']}"
    assert env["data"]["total_matches"] >= 200, f"only {env['data']['total_matches']} clubs parsed (layout change?)"
    clubs = env["data"]["clubs"]
    assert all(c["name"] and c["club_id"] and c["page_url"] for c in clubs)
    typed = sum(1 for c in clubs if c["type"])
    assert typed / len(clubs) >= 0.9, "type line no longer parsed (p.grey-element changed?)"


def test_events_feed_layout(live):
    fr = live.fetcher.fetch("https://312.iit.edu/ical/iit/ical_iit.ics", ttl=1800, read_timeout=40)
    assert fr.ok and not fr.stale, f"feed unavailable: {fr.error_code} {fr.error_message}"
    assert urlsplit(fr.final_url).hostname == "static-prod-us-east-1.campusgroups.com", \
        f"feed no longer redirects to the allowlisted CDN host: {fr.final_url}"
    blocks = len(re.findall(r"BEGIN:VEVENT", unfold(fr.text)))
    events = parse_ics(fr.text)
    assert len(events) >= 100, f"only {len(events)} events"
    assert len(events) / blocks >= 0.95, f"parse rate {len(events)}/{blocks} below 95%"
    assert all(e.title and e.url.startswith("https://312.iit.edu/rsvp?id=") and e.event_type for e in events)
    assert any(e.location_type == "in_person" for e in events), \
        "no event has a public location at all (feed may now redact everything)"


def test_end_to_end_tools(live):
    ev = live.run("search_events", {"date_from": "2020-01-01", "date_to": "2030-01-01", "limit": 3})
    assert ev["ok"] and ev["data"]["events"], f"search_events: {ev['error']}"
    one = live.run("get_event_details", {"event_id_or_url": ev["data"]["events"][0]["event_id"]})
    assert one["ok"] and one["data"]["description"] is not None
