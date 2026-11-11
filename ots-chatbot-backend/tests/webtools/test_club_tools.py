from pathlib import Path

import pytest

from web_tools.config import WebToolsConfig
from web_tools.fetch.client import FetchResult
from web_tools.fetch.robots import RobotsDisallowed
from web_tools.render import render_for_prompt
from web_tools.tools.registry import WebTools

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"
HTML = (FIX / "clubs_listing_sample.html").read_text()
URL = "https://312.iit.edu/club_signup?view=all"
EVENTS_URL = "https://312.iit.edu/ical/iit/ical_iit.ics"


class FakeFetcher:
    def __init__(self, text=HTML, **kw):
        self.result = FetchResult(ok=True, url=URL, text=text, retrieved_at="2026-11-10T20:00:00+00:00", **kw)
        self.events_result = FetchResult(ok=False, url=EVENTS_URL, error_code="http_error", error_message="n/a")
        self.calls = 0   # counts club-listing fetches only

    def fetch(self, url, ttl, read_timeout=None):
        if url == EVENTS_URL:
            if isinstance(self.events_result, Exception):
                raise self.events_result
            return self.events_result
        assert url == URL
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def tools(fetcher=None):
    return WebTools(WebToolsConfig(min_clubs=1), fetcher=fetcher or FakeFetcher())


def test_search_by_interest_ranks_name_first():
    env = tools().run("search_clubs", {"query": "robotics"})
    names = [c["name"] for c in env["data"]["clubs"]]
    assert env["ok"] and env["source_url"] == URL and env["retrieved_at"]
    assert set(names) == {"Robotics", "AI and Robotics"} and names[0] == "Robotics"
    assert env["stale"] is False and env["truncated"] is False


def test_stopwords_and_stems():
    env = tools().run("search_clubs", {"query": "Are there any robotic clubs?"})
    assert env["data"]["total_matches"] == 2


def test_filters_by_type_and_category():
    t = tools()
    halls = t.run("search_clubs", {"type": "Residence Hall"})["data"]["clubs"]
    assert [c["name"] for c in halls] == ["Carman 2nd & 3rd Floor"]
    arts = t.run("search_clubs", {"category": "arts and media"})["data"]["clubs"]
    assert [c["name"] for c in arts] == ["33rd Street Productions"]
    both = t.run("search_clubs", {"query": "robotics", "category": "Community Service"})["data"]["clubs"]
    assert [c["name"] for c in both] == ["Robotics"]


def test_no_results_is_ok_with_hint():
    env = tools().run("search_clubs", {"query": "quidditch"})
    assert env["ok"] and env["data"]["clubs"] == [] and "hint" in env["data"]


def test_limit_and_empty_query_returns_alphabetical_with_total():
    env = tools().run("search_clubs", {"limit": 2})
    d = env["data"]
    assert d["returned"] == 2 and d["total_matches"] == 6
    assert [c["name"] for c in d["clubs"]] == sorted(c["name"] for c in d["clubs"])


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 99}, {"nope": 1}, {"query": "x" * 500}])
def test_bad_args(bad):
    env = tools().run("search_clubs", bad)
    assert not env["ok"] and env["error"]["code"] == "bad_args"


def test_unknown_tool():
    assert tools().run("drop_tables", {})["error"]["code"] == "bad_args"


def test_include_archived_is_noted_not_fatal():
    env = tools().run("search_clubs", {"query": "robotics", "include_archived": True})
    assert env["ok"] and "active groups only" in env["data"]["note"]


@pytest.mark.parametrize("ident", ["33rd", "33RD", "35460", "33rd street productions"])
def test_details_by_slug_id_or_name(ident):
    env = tools().run("get_club_details", {"slug": ident})
    assert env["ok"] and env["data"]["name"] == "33rd Street Productions"
    assert env["source_url"] == "https://312.iit.edu/33rd/"
    assert len(env["data"]["mission"]) > 100


def test_details_for_external_site_club_links_website():
    env = tools().run("get_club_details", {"slug": "35443"})
    assert env["ok"] and env["data"]["slug"] is None
    assert env["data"]["page_url"] == "https://illinoistechrobotics.org/"


def test_details_unknown_returns_close_matches():
    env = tools().run("get_club_details", {"slug": "robotic hamster"})
    assert not env["ok"] and env["error"]["code"] == "no_results"
    assert env["data"]["close_matches"]


def test_caches_parse_between_calls():
    f = FakeFetcher()
    t = tools(f)
    t.run("search_clubs", {"query": "a"}); t.run("search_clubs", {"query": "b"})
    assert URL in t._memo and f.calls == 2  # fetcher (cache) asked each time, parse reused


def test_stale_flag_propagates():
    f = FakeFetcher(stale=True, stale_reason="timeout")
    env = tools(f).run("search_clubs", {"query": "robotics"})
    assert env["ok"] and env["stale"] and env["stale_reason"] == "timeout"
    assert "live refresh just failed" in render_for_prompt(env)


def test_fetch_failure_becomes_error_envelope():
    f = FakeFetcher()
    f.result = FetchResult(ok=False, url=URL, error_code="timeout", error_message="timed out")
    env = tools(f).run("search_clubs", {"query": "robotics"})
    assert not env["ok"] and env["error"]["code"] == "timeout"
    assert "unavailable" in render_for_prompt(env)


def test_auth_required_with_cached_data():
    f = FakeFetcher(stale=True, auth_required=True, stale_reason="auth_required")
    env = tools(f).run("search_clubs", {"query": "robotics"})
    assert env["ok"] and env["auth_required"] and env["data"]["clubs"]


def test_robots_disallowed_surfaces_as_error_and_never_raises():
    f = FakeFetcher()
    f.result = RobotsDisallowed(URL, "/x")
    env = tools(f).run("search_clubs", {})
    assert not env["ok"] and env["error"]["code"] == "robots_disallowed"


def test_unexpected_exception_is_contained():
    f = FakeFetcher()
    f.result = RuntimeError("boom")
    env = tools(f).run("search_clubs", {})
    assert not env["ok"] and env["error"]["code"] == "internal_error"


def test_layout_drift_serves_last_good_parse_as_stale():
    f = FakeFetcher()
    t = tools(f)
    # memo needs >=50 clubs to count as "good"; build a synthetic big listing
    rows = "".join(
        f'<li class="list-group-item"><input id="cb_club_{i}"><h2><a href="https://312.iit.edu/c{i}/">Club {i}</a></h2></li>'
        for i in range(60))
    f.result = FetchResult(ok=True, url=URL, text=f"<ul>{rows}</ul>", retrieved_at="t1")
    assert t.run("search_clubs", {"limit": 1})["data"]["total_matches"] == 60
    f.result = FetchResult(ok=True, url=URL, text="<html>[eventName] redesigned</html>", retrieved_at="t2")
    env = t.run("search_clubs", {"limit": 1})
    assert env["ok"] and env["stale"] and env["stale_reason"] == "layout_changed"
    assert env["error"]["code"] == "layout_changed" and env["data"]["total_matches"] == 60


def test_layout_drift_without_memo_is_an_error():
    f = FakeFetcher(text="<html>nothing here</html>")
    env = tools(f).run("search_clubs", {})
    assert not env["ok"] and env["error"]["code"] == "layout_changed"


def test_render_is_size_capped_and_uses_plain_markdown():
    rows = "".join(
        f'<li class="list-group-item"><input id="cb_club_{i}"><h2><a href="https://312.iit.edu/c{i}/">Club {i}</a></h2>'
        f'<p class="grey-element">Student Organization - Academic</p><p id="club_{i}"><strong>Mission</strong>{"word " * 300}</p></li>'
        for i in range(60))
    t = tools(FakeFetcher(text=f"<ul>{rows}</ul>"))
    env = t.run("search_clubs", {"limit": 25})
    text = render_for_prompt(env)
    assert len(text) <= 6000 and env["truncated"] is True
    assert "https://312.iit.edu/c0/" in text and "|" not in text.split("\n")[1]
    assert "more matches not shown" in text


def test_external_site_link_is_labelled_website():
    text = render_for_prompt(tools().run("search_clubs", {"query": "robotics", "limit": 5}))
    assert "[website](https://illinoistechrobotics.org/)" in text
    assert "[312 page](https://312.iit.edu/AAR/)" in text


@pytest.mark.parametrize("ident", ["Boxing", "boxing club", "the Boxing Club"])
def test_name_lookup_ignores_club_suffix(ident):
    from web_tools.parsers.clubs_html import parse_clubs_listing
    from web_tools.tools.clubs import find_club
    html = ('<ul><li class="list-group-item"><input id="cb_club_1"><h2><a href="https://312.iit.edu/box/">Boxing Club</a></h2></li>'
            '<li class="list-group-item"><input id="cb_club_2"><h2><a href="https://312.iit.edu/x/">Chess Society</a></h2></li></ul>')
    assert find_club(parse_clubs_listing(html), ident).slug == "box"


def test_type_and_category_aliases_find_greek_faith_and_service():
    html = "".join(
        f'<li class="list-group-item"><input id="cb_club_{i}"><h2><a href="https://312.iit.edu/c{i}/">{n}</a></h2>'
        f'<p class="grey-element">{t}</p></li>'
        for i, (n, t) in enumerate([
            ("Alpha Sigma Phi", "Fraternity/Sorority"), ("Delta Sigma Theta", "Fraternity/Sorority"),
            ("Newman Center", "Student Organization - Faith and Spirituality"),
            ("MEDLIFE", "Student Organization - Community Service"),
            ("Soccer Club", "Student Organization - Sports and Recreation"),
            ("Photography Club", "Student Organization - Arts and Media")]))
    t = tools(FakeFetcher(text=f"<ul>{html}</ul>"))
    def names(q): return {c["name"] for c in t.run("search_clubs", {"query": q})["data"]["clubs"]}
    assert names("are there any fraternities or sororities?") == {"Alpha Sigma Phi", "Delta Sigma Theta"}
    assert names("religious groups") == {"Newman Center"}
    assert names("volunteering clubs") == {"MEDLIFE"}
    assert names("sports clubs") == {"Soccer Club"}
    assert names("art clubs") == {"Photography Club"}


def test_weak_mission_mentions_dropped_when_name_matches():
    html = ('<ul><li class="list-group-item"><input id="cb_club_1"><h2><a href="https://312.iit.edu/r/">Robotics</a></h2></li>'
            '<li class="list-group-item"><input id="cb_club_2"><h2><a href="https://312.iit.edu/o/">Office of Student Life</a></h2>'
            '<p id="club_2"><strong>Mission</strong>we love robotics a little</p></li></ul>')
    env = tools(FakeFetcher(text=html)).run("search_clubs", {"query": "robotics"})
    assert [c["name"] for c in env["data"]["clubs"]] == ["Robotics"]
