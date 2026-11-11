from pathlib import Path

import pytest

from web_tools.fetch.robots import (
    BASELINE_RULES, FetchPolicyError, RobotsDisallowed, RobotsGuard, RobotsResult,
    RobotsUnavailable, parse_robots, rule_matches,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"
ROBOTS_312 = (FIX / "robots_312.txt").read_text()
H = "https://312.iit.edu"


def make_guard(status=200, text=ROBOTS_312, clock=None, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return RobotsResult(status, text)
    kw = {"clock": clock} if clock else {}
    return RobotsGuard(fetch, **kw)


DISALLOWED = [
    "/upload/x.png", "/upload/iit/2026/a.jpeg", "/student_docs/a.pdf", "/downloads/file.zip",
    "/mobile_ws/v17/events", "/mobile_ws/v18/foo?x=1",
    "/ajax_widget_new", "/x/ajax_widget_new", "/a/b/ajax_widget_new?x=1",
    "/events?mode=ajax_widget_new",
    # case / encoding / traversal variants must not slip through
    "/UPLOAD/x.png", "/Mobile_WS/V17/a", "/%75pload/x.png", "/%2575pload/x.png",
    "/events/../upload/x.png", "//upload/x.png", "/x/ajax%5Fwidget_new",
    "/club/ajax_widget_new_list",
]
ALLOWED = [
    "/events", "/club_signup?view=all", "/club_signup?view=all&group_type=9999", "/box/",
    "/ical/iit/ical_iit.ics", "/rsvp?id=385553", "/robots.txt", "/ics_helper",
]


@pytest.mark.parametrize("path", DISALLOWED)
def test_baseline_blocks(path):
    with pytest.raises(RobotsDisallowed):
        make_guard().check(H + path)


@pytest.mark.parametrize("path", ALLOWED)
def test_allowed_paths_pass(path):
    make_guard().check(H + path)


def test_baseline_matches_published_robots_txt():
    published = {r.pattern for r in parse_robots(ROBOTS_312)}
    assert published == set(BASELINE_RULES)


def test_baseline_holds_when_live_robots_has_allow_override():
    guard = make_guard(text="User-agent: *\nAllow: /upload/\nAllow: /*ajax_widget_new\n")
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/upload/x.png")
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/a/ajax_widget_new")


def test_live_robots_can_only_add_restrictions():
    guard = make_guard(text="User-agent: *\nDisallow: /secret/\n")
    with pytest.raises(RobotsDisallowed) as e:
        guard.check(H + "/secret/page")
    assert "/secret/" in str(e.value)
    guard.check(H + "/events")


def test_live_allow_beats_shorter_disallow():
    guard = make_guard(text="User-agent: *\nDisallow: /a/\nAllow: /a/public/\n")
    guard.check(H + "/a/public/x")
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/a/private")


def test_specific_agent_group_wins():
    rules = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: HawkBot\nDisallow: /x/\n")
    assert [r.pattern for r in rules] == ["/x/"]


def test_robots_4xx_means_no_extra_rules_but_baseline_still_applies():
    guard = make_guard(status=403, text="<html>AccessDenied</html>")
    guard.check(H + "/events")
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/upload/x")


@pytest.mark.parametrize("status", [500, 503, None])
def test_robots_5xx_or_network_error_fails_closed(status):
    guard = make_guard(status=status, text="")
    with pytest.raises(RobotsUnavailable):
        guard.check(H + "/events")
    # baseline still reported as a policy refusal, not unavailable
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/upload/x")


def test_robots_failure_keeps_previous_rules():
    now = [0.0]
    state = {"status": 200}

    def fetch(url):
        return RobotsResult(state["status"], "User-agent: *\nDisallow: /secret/\n")
    guard = RobotsGuard(fetch, ttl=10, clock=lambda: now[0])
    guard.check(H + "/events")
    state["status"] = 500
    now[0] = 100
    guard.check(H + "/events")  # refetch fails; older rules reused
    with pytest.raises(RobotsDisallowed):
        guard.check(H + "/secret/x")


def test_live_robots_cached_for_ttl_then_refetched():
    now = [0.0]
    calls = []
    guard = make_guard(clock=lambda: now[0], calls=calls)
    guard.check(H + "/events"); guard.check(H + "/box/")
    assert len(calls) == 1
    now[0] = 3601
    guard.check(H + "/events")
    assert len(calls) == 2


@pytest.mark.parametrize("url", [
    "http://312.iit.edu/events",                       # not https
    "https://312.iit.edu:8443/events",                 # odd port
    "https://evil.example.com/events",                 # host not allowlisted
    "https://312.iit.edu.evil.com/events",
    "https://static-prod-us-east-1.campusgroups.com/robots.txtx",  # lookalike, outside /ical/
    "https://static-prod-us-east-1.campusgroups.com/upload/x",
    "https://static-prod-us-east-1.campusgroups.com/ical/../upload/x",
    "https://static-prod-us-east-1.campusgroups.com/ical/iit/%2e%2e/%2e%2e/other",
])
def test_allowlist_refusals(url):
    with pytest.raises(FetchPolicyError):
        make_guard().check(url)


def test_cdn_host_allows_only_ical_paths():
    make_guard().check("https://static-prod-us-east-1.campusgroups.com/ical/iit/ical_iit.ics")


def test_matcher_wildcard_and_anchor():
    assert rule_matches("/*ajax_widget_new", "/ajax_widget_new")
    assert rule_matches("/a/*/c$", "/a/b/c")
    assert not rule_matches("/a/*/c$", "/a/b/c/d")
    assert rule_matches("/upload/", "/upload/")
    assert not rule_matches("/upload/", "/uploads")


def test_robots_txt_fetchable_on_every_allowlisted_host():
    g = make_guard()
    g.check("https://static-prod-us-east-1.campusgroups.com/robots.txt")
    g.check("https://312.iit.edu/robots.txt")


def test_explicit_443_port_is_fine():
    make_guard().check("https://static-prod-us-east-1.campusgroups.com:443/ical/iit/ical_iit.ics")
