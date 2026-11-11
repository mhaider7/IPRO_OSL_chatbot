import httpx
import pytest

from web_tools.config import WebToolsConfig
from web_tools.fetch.cache import ResponseCache
from web_tools.fetch.client import WebFetcher, is_login_url
from web_tools.fetch.ratelimit import HostRateLimiter
from web_tools.fetch.robots import RobotsDisallowed

FEED = "https://312.iit.edu/ical/iit/ical_iit.ics"
CDN = "https://static-prod-us-east-1.campusgroups.com/ical/iit/ical_iit.ics"
CDN_LOCATION = "https://static-prod-us-east-1.campusgroups.com:443/ical/iit/ical_iit.ics"  # as sent by the real site
ROBOTS = "User-agent: *\nDisallow: /upload/\nDisallow: /student_docs/\nDisallow: /downloads/\n" \
         "Disallow: /mobile_ws/v17/\nDisallow: /mobile_ws/v18/\nDisallow: /*ajax_widget_new\n"


class Net:
    """Scriptable fake network; records every request URL."""
    def __init__(self):
        self.requests = []
        self.routes = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((url, dict(request.headers)))
        key = url.split("?")[0] if url.split("?")[0] in self.routes else url
        r = self.routes.get(key)
        if callable(r):
            return r(request)
        if r is None:
            return httpx.Response(404)
        return r

    def urls(self):
        return [u for u, _ in self.requests]


class Clock:
    def __init__(self): self.t = 1_000_000.0
    def __call__(self): return self.t


@pytest.fixture
def env(tmp_path):
    net, clock = Net(), Clock()
    net.routes["https://312.iit.edu/robots.txt"] = httpx.Response(200, text=ROBOTS)
    net.routes["https://static-prod-us-east-1.campusgroups.com/robots.txt"] = httpx.Response(403, text="denied")
    cfg = WebToolsConfig(cache_dir=tmp_path / "cache", contact="test@example.com", max_body_bytes=1000)
    client = httpx.Client(transport=httpx.MockTransport(net.handler), follow_redirects=False)
    limiter = HostRateLimiter(2.0, clock=lambda: clock.t, sleep=lambda s: setattr(clock, "t", clock.t + s))
    f = WebFetcher(cfg, client=client, clock=clock, limiter=limiter,
                   cache=ResponseCache(tmp_path / "cache", clock=clock))
    return f, net, clock


def feed_redirect(net, body="BEGIN:VCALENDAR", **hdrs):
    net.routes[FEED] = httpx.Response(302, headers={"location": CDN_LOCATION})
    net.routes[CDN] = httpx.Response(200, text=body, headers={"content-type": "text/calendar; charset=utf-8", **hdrs})


def test_follows_allowed_cross_host_redirect_and_caches(env):
    f, net, clock = env
    feed_redirect(net, etag='"abc"', **{"last-modified": "Tue, 10 Nov 2026 17:52:58 GMT"})
    r = f.fetch(FEED, ttl=1800)
    assert r.ok and r.text == "BEGIN:VCALENDAR" and not r.stale and not r.from_cache
    assert r.last_modified.startswith("Tue, 10 Nov")
    n = len(net.requests)
    r2 = f.fetch(FEED, ttl=1800)
    assert r2.from_cache and len(net.requests) == n


def test_user_agent_is_honest(env):
    f, net, _ = env
    feed_redirect(net)
    f.fetch(FEED, ttl=10)
    ua = next(h for u, h in net.requests if u.endswith("ical_iit.ics"))["user-agent"]
    assert ua.startswith("HawkBot/") and "contact: test@example.com" in ua


def test_conditional_get_304_refreshes_ttl(env):
    f, net, clock = env
    feed_redirect(net, etag='"abc"')
    f.fetch(FEED, ttl=100)
    clock.t += 500
    net.routes[CDN] = httpx.Response(304)
    r = f.fetch(FEED, ttl=100)
    assert r.ok and r.from_cache and not r.stale and r.text == "BEGIN:VCALENDAR"
    sent = [h for u, h in net.requests if u == CDN][-1]
    assert sent.get("if-none-match") == '"abc"'
    n = len(net.requests)
    f.fetch(FEED, ttl=100)  # fresh again after touch
    assert len(net.requests) == n


def test_stale_if_error(env):
    f, net, clock = env
    feed_redirect(net)
    first = f.fetch(FEED, ttl=10)
    clock.t += 100
    net.routes[CDN] = httpx.Response(500)
    r = f.fetch(FEED, ttl=10)
    assert r.ok and r.stale and r.stale_reason == "http_error" and r.text == "BEGIN:VCALENDAR"
    assert r.retrieved_at == first.retrieved_at  # original timestamp preserved


def test_timeout_without_cache_returns_typed_error(env):
    f, net, _ = env
    def boom(request): raise httpx.ReadTimeout("slow", request=request)
    net.routes[FEED] = boom
    r = f.fetch(FEED, ttl=10)
    assert not r.ok and r.error_code == "timeout"


def test_disk_cache_survives_new_fetcher(env, tmp_path):
    f, net, clock = env
    feed_redirect(net)
    f.fetch(FEED, ttl=10)
    clock.t += 100
    net.routes[CDN] = httpx.Response(500)
    f2 = WebFetcher(f.cfg, client=f._client, clock=clock, limiter=f._limiter,
                    cache=ResponseCache(tmp_path / "cache", clock=clock))
    r = f2.fetch(FEED, ttl=10)
    assert r.ok and r.stale and r.text == "BEGIN:VCALENDAR"


def test_oversize_body_aborted(env):
    f, net, _ = env
    feed_redirect(net, body="x" * 5000)
    r = f.fetch(FEED, ttl=10)
    assert not r.ok and r.error_code == "http_error" and "exceeds" in r.error_message


@pytest.mark.parametrize("location", [
    "https://312.iit.edu/webapp/auth/login?redirect=x", "/login?embed=1",
])
def test_login_redirect_is_auth_required_and_not_followed(env, location):
    f, net, _ = env
    page = "https://312.iit.edu/calendar_sync"
    net.routes[page] = httpx.Response(302, headers={"location": location})
    r = f.fetch(page, ttl=10)
    assert not r.ok and r.auth_required and r.error_code == "auth_required"
    assert all("login" not in u for u in net.urls())


def test_auth_required_still_returns_cached_public_data(env):
    f, net, clock = env
    page = "https://312.iit.edu/club_signup?view=all"
    net.routes[page] = httpx.Response(200, text="<html>clubs</html>")
    f.fetch(page, ttl=10)
    clock.t += 100
    net.routes[page] = httpx.Response(302, headers={"location": "/webapp/auth/login"})
    r = f.fetch(page, ttl=10)
    assert r.ok and r.auth_required and r.stale and r.text == "<html>clubs</html>"


# ---- the robots guarantees, end to end through the HTTP layer -------------------------------

@pytest.mark.parametrize("path", [
    "/upload/x.png", "/student_docs/a.pdf", "/downloads/z", "/mobile_ws/v17/e", "/mobile_ws/v18/e",
    "/events/ajax_widget_new", "/x/y/ajax_widget_new?a=1",
])
def test_disallowed_url_never_reaches_network(env, path):
    f, net, _ = env
    with pytest.raises(RobotsDisallowed):
        f.fetch("https://312.iit.edu" + path, ttl=10)
    assert not any(path.split("?")[0] in u for u in net.urls())


def test_disallowed_url_not_served_from_cache(env):
    f, net, _ = env
    url = "https://312.iit.edu/upload/x.png"
    from web_tools.fetch.cache import CacheEntry
    f.cache.put(CacheEntry(url=url, body=b"cached", retrieved_at=f._clock()))
    with pytest.raises(RobotsDisallowed):
        f.fetch(url, ttl=10_000)


def test_redirect_to_disallowed_path_is_refused_mid_chain(env):
    f, net, _ = env
    net.routes["https://312.iit.edu/events"] = httpx.Response(
        302, headers={"location": "https://312.iit.edu/x/ajax_widget_new?y=1"})
    with pytest.raises(RobotsDisallowed):
        f.fetch("https://312.iit.edu/events", ttl=10)
    assert not any("ajax_widget_new" in u for u in net.urls())


@pytest.mark.parametrize("target", [
    "https://evil.example.com/ical/iit/ical_iit.ics",
    "https://static-prod-us-east-1.campusgroups.com/other/file",
    "http://312.iit.edu/events",
])
def test_redirect_to_non_allowlisted_target_is_refused(env, target):
    f, net, _ = env
    net.routes[FEED] = httpx.Response(302, headers={"location": target})
    with pytest.raises(RobotsDisallowed):
        f.fetch(FEED, ttl=10)
    assert target not in net.urls()


def test_redirect_loop_bounded(env):
    f, net, _ = env
    net.routes["https://312.iit.edu/a"] = httpx.Response(302, headers={"location": "/a"})
    r = f.fetch("https://312.iit.edu/a", ttl=10)
    assert not r.ok and "redirects" in r.error_message
    assert len([u for u in net.urls() if u.endswith("/a")]) == f.cfg.max_redirects + 1


def test_robots_5xx_fails_closed_without_fetching_page(env):
    f, net, _ = env
    net.routes["https://312.iit.edu/robots.txt"] = httpx.Response(503)
    net.routes["https://312.iit.edu/events"] = httpx.Response(200, text="page")
    r = f.fetch("https://312.iit.edu/events", ttl=10)
    assert not r.ok and r.error_code == "robots_unavailable"
    assert "https://312.iit.edu/events" not in net.urls()


def test_live_robots_extra_rule_enforced(env):
    f, net, _ = env
    net.routes["https://312.iit.edu/robots.txt"] = httpx.Response(200, text=ROBOTS + "Disallow: /secret/\n")
    with pytest.raises(RobotsDisallowed):
        f.fetch("https://312.iit.edu/secret/page", ttl=10)


# ---- rate limiting / backoff / single flight ------------------------------------------------

def test_per_host_spacing(env):
    f, net, clock = env
    for p in ("a", "b", "c"):
        net.routes[f"https://312.iit.edu/{p}"] = httpx.Response(200, text=p)
    t0 = clock.t
    for p in ("a", "b", "c"):
        f.fetch(f"https://312.iit.edu/{p}", ttl=10)
    # robots.txt + 3 pages = 4 requests to one host => at least 3 intervals of 2s
    assert clock.t - t0 >= 6.0


def test_retry_after_backoff_serves_stale_and_skips_network(env):
    f, net, clock = env
    page = "https://312.iit.edu/club_signup?view=all"
    net.routes[page] = httpx.Response(200, text="ok")
    f.fetch(page, ttl=10)
    clock.t += 100
    net.routes[page] = httpx.Response(429, headers={"retry-after": "120"})
    assert f.fetch(page, ttl=10).stale
    n = len(net.requests)
    r = f.fetch(page, ttl=10)
    assert r.stale and r.stale_reason == "rate_limited" and len(net.requests) == n


def test_single_flight_concurrent_fetches(env):
    import threading
    f, net, _ = env
    feed_redirect(net)
    results = []
    ts = [threading.Thread(target=lambda: results.append(f.fetch(FEED, ttl=100))) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results) == 5 and all(r.ok for r in results)
    assert sum(1 for u in net.urls() if u == CDN) == 1


def test_is_login_url():
    assert is_login_url("https://312.iit.edu/webapp/auth/login?x=1")
    assert is_login_url("https://312.iit.edu/login?embed=1&redirect=ics_helper")
    assert not is_login_url("https://312.iit.edu/events")
