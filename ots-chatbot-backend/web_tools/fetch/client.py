"""WebFetcher: the ONLY code in the project that opens network connections for web tools.

Every hop of every request passes RobotsGuard.check() before a socket is opened. Redirects are
followed manually (max N) so each hop is re-checked; a redirect to a login page is reported as
`auth_required` and never followed.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from web_tools.config import CONTACT_PLACEHOLDER, WebToolsConfig
from web_tools.fetch.cache import CacheEntry, ResponseCache
from web_tools.fetch.ratelimit import HostRateLimiter
from web_tools.fetch.robots import (
    FetchPolicyError,
    RobotsGuard,
    RobotsResult,
    RobotsUnavailable,
)

logger = logging.getLogger("hawk")

LOGIN_MARKERS = ("/webapp/auth/login", "/auth/login", "/login")


def is_login_url(url: str) -> bool:
    path = urlsplit(url).path.lower().rstrip("/")
    return any(path == m or path.startswith(m + "/") or path.endswith(m) for m in LOGIN_MARKERS)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


@dataclass
class FetchResult:
    ok: bool
    url: str
    text: str | None = None
    final_url: str | None = None
    retrieved_at: str | None = None
    last_modified: str | None = None
    stale: bool = False
    stale_reason: str | None = None
    auth_required: bool = False
    from_cache: bool = False
    error_code: str | None = None
    error_message: str | None = None


class _FetchError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


class _AuthRequired(Exception):
    def __init__(self, location: str):
        self.location = location


class WebFetcher:
    def __init__(
        self,
        config: WebToolsConfig | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        limiter: HostRateLimiter | None = None,
        guard: RobotsGuard | None = None,
        cache: ResponseCache | None = None,
    ):
        self.cfg = config or WebToolsConfig()
        if self.cfg.contact == CONTACT_PLACEHOLDER:
            logger.warning("HAWK_FETCH_CONTACT is not set; User-Agent carries a placeholder contact")
        self._clock = clock
        self._client = client or httpx.Client(
            follow_redirects=False,
            headers={"User-Agent": self.cfg.user_agent, "Accept": "*/*"},
            timeout=httpx.Timeout(self.cfg.read_timeout_s, connect=self.cfg.connect_timeout_s),
        )
        self._limiter = limiter or HostRateLimiter(self.cfg.min_interval_s)
        self.guard = guard or RobotsGuard(self._fetch_robots, ttl=self.cfg.robots_ttl_s, clock=clock)
        self.cache = cache or ResponseCache(self.cfg.cache_dir, clock=clock)
        self._flight_lock = threading.Lock()
        self._flights: dict[str, threading.Lock] = {}

    # -- public ---------------------------------------------------------------
    def fetch(self, url: str, ttl: float, read_timeout: float | None = None) -> FetchResult:
        """Return fresh-or-cached content for `url`.

        Raises FetchPolicyError subclass RobotsDisallowed if the URL (or any redirect hop) is
        disallowed; this is deliberately loud and never served from cache.
        """
        try:
            self.guard.check(url)
        except RobotsUnavailable:
            pass  # handled below via stale cache / error result
        entry = self.cache.get(url)
        if entry is not None and self.cache.is_fresh(entry, ttl):
            return self._ok(entry, from_cache=True)

        with self._flight(url):
            entry = self.cache.get(url)  # another thread may have just refreshed it
            if entry is not None and self.cache.is_fresh(entry, ttl):
                return self._ok(entry, from_cache=True)
            try:
                return self._fetch_live(url, entry, read_timeout or self.cfg.read_timeout_s)
            except _AuthRequired as exc:
                return self._fallback(url, entry, "auth_required", f"redirected to {exc.location}", auth=True)
            except RobotsUnavailable as exc:
                return self._fallback(url, entry, "robots_unavailable", str(exc))
            except _FetchError as exc:
                return self._fallback(url, entry, exc.code, exc.message)
            # RobotsDisallowed / other FetchPolicyError propagate on purpose.

    # -- internals ------------------------------------------------------------
    def _flight(self, url: str) -> threading.Lock:
        with self._flight_lock:
            return self._flights.setdefault(url, threading.Lock())

    def _ok(self, entry: CacheEntry, *, from_cache: bool, stale: bool = False,
            reason: str | None = None, auth: bool = False) -> FetchResult:
        return FetchResult(
            ok=True, url=entry.url, text=_decode(entry), final_url=entry.final_url or entry.url,
            retrieved_at=_iso(entry.retrieved_at), last_modified=entry.last_modified,
            stale=stale, stale_reason=reason, from_cache=from_cache, auth_required=auth,
        )

    def _fallback(self, url: str, entry: CacheEntry | None, code: str, message: str,
                  auth: bool = False) -> FetchResult:
        logger.warning("web fetch failed for %s: %s (%s); %s", url, code, message,
                       "serving stale cache" if entry else "no cache")
        if entry is not None:
            return self._ok(entry, from_cache=True, stale=True, reason=code, auth=auth)
        return FetchResult(ok=False, url=url, auth_required=auth, error_code=code, error_message=message)

    def _fetch_live(self, url: str, entry: CacheEntry | None, read_timeout: float) -> FetchResult:
        headers: dict[str, str] = {}
        if entry is not None:
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified
        current = url
        for _ in range(self.cfg.max_redirects + 1):
            self.guard.check(current)  # every hop, before any I/O
            status, hdrs, body = self._get(current, headers, read_timeout)
            if status in (301, 302, 303, 307, 308) and hdrs.get("location"):
                nxt = urljoin(current, hdrs["location"])
                if is_login_url(nxt):
                    raise _AuthRequired(nxt)
                current = nxt
                continue
            break
        else:
            raise _FetchError("http_error", f"more than {self.cfg.max_redirects} redirects")

        if status == 304 and entry is not None:
            self.cache.touch(entry)
            return self._ok(entry, from_cache=True)
        if status == 401:
            raise _AuthRequired(current)
        if status != 200:
            raise _FetchError("http_error", f"HTTP {status} from {current}")
        new = CacheEntry(
            url=url, body=body, retrieved_at=self._clock(), etag=hdrs.get("etag"),
            last_modified=hdrs.get("last-modified"), content_type=hdrs.get("content-type"),
            final_url=current,
        )
        self.cache.put(new)
        return self._ok(new, from_cache=False)

    def _get(self, url: str, headers: dict[str, str], read_timeout: float
             ) -> tuple[int, dict[str, str], bytes]:
        host = (urlsplit(url).hostname or "").lower()
        if self._limiter.blocked_for(host) > self.cfg.max_wait_s:
            raise _FetchError("rate_limited", f"backing off {host} (server asked us to slow down)")
        self._limiter.wait(host)
        timeout = httpx.Timeout(read_timeout, connect=self.cfg.connect_timeout_s)
        try:
            req_headers = {"User-Agent": self.cfg.user_agent, "Accept": "*/*", **headers}
            with self._client.stream("GET", url, headers=req_headers, timeout=timeout) as resp:
                logger.info("web_tools GET %s -> %s", url, resp.status_code)
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                if resp.status_code in (429, 503):
                    self._limiter.back_off(host, _retry_after(hdrs.get("retry-after")))
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > self.cfg.max_body_bytes:
                        raise _FetchError("http_error", f"response exceeds {self.cfg.max_body_bytes} bytes")
                    chunks.append(chunk)
                return resp.status_code, hdrs, b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise _FetchError("timeout", f"timed out fetching {url}: {exc.__class__.__name__}") from exc
        except httpx.HTTPError as exc:
            raise _FetchError("http_error", f"{exc.__class__.__name__} fetching {url}") from exc

    def _fetch_robots(self, url: str) -> RobotsResult:
        """Used only by RobotsGuard. Single request, no redirects (3xx => fail closed)."""
        try:
            self.guard.check(url)  # allowlist + baseline; /robots.txt skips live rules
            status, _hdrs, body = self._get(url, {}, self.cfg.read_timeout_s)
        except (_FetchError, FetchPolicyError) as exc:
            logger.warning("robots.txt fetch failed: %s", exc)
            return RobotsResult(None)
        return RobotsResult(status, body.decode("utf-8", errors="replace"))


def _retry_after(value: str | None) -> float:
    try:
        return min(max(float(value), 1.0), 300.0) if value else 30.0
    except ValueError:
        return 30.0


def _decode(entry: CacheEntry) -> str:
    charset = "utf-8"
    if entry.content_type and "charset=" in entry.content_type.lower():
        charset = entry.content_type.lower().split("charset=")[1].split(";")[0].strip()
    try:
        return entry.body.decode(charset, errors="replace")
    except LookupError:
        return entry.body.decode("utf-8", errors="replace")
