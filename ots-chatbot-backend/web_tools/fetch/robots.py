"""robots.txt enforcement for every outbound request.

Design rules:
- A hard-coded BASELINE of Disallow rules ALWAYS applies. Live robots.txt content can only
  add restrictions, never lift the baseline, and a failed robots fetch cannot weaken it.
- Matching follows RFC 9309 (prefix match, `*` wildcard, `$` anchor, longest rule wins) on
  path + query, case-insensitively, and on several normalized forms of the URL
  (percent-decoded, dot-segments resolved) so encoding tricks cannot slip past a rule.
  When any form is disallowed, the URL is disallowed (fail closed).
- Only allowlisted hosts/paths can be requested at all.
"""
from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote, urlsplit

# Mirrors https://312.iit.edu/robots.txt (verified 2026-11-10). Always enforced.
BASELINE_RULES: tuple[str, ...] = (
    "/upload/",
    "/student_docs/",
    "/downloads/",
    "/mobile_ws/v17/",
    "/mobile_ws/v18/",
    "/*ajax_widget_new",
)

# host -> allowed path prefixes (None = any path on that host).
DEFAULT_ALLOWLIST: dict[str, tuple[str, ...] | None] = {
    "312.iit.edu": None,
    # The public iCal feeds 302 to this CDN host; nothing else on it is in scope.
    "static-prod-us-east-1.campusgroups.com": ("/ical/",),
}

AGENT_TOKEN = "hawkbot"


class FetchPolicyError(Exception):
    """Base class: a request was refused before any network I/O."""


class RobotsDisallowed(FetchPolicyError):
    def __init__(self, url: str, rule: str, reason: str = "disallowed by robots.txt"):
        self.url, self.rule = url, rule
        super().__init__(f"Refusing to fetch {url}: {reason} (rule: {rule})")


class RobotsUnavailable(FetchPolicyError):
    """robots.txt could not be retrieved (5xx/timeout) so we fail closed for that host."""


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str


def parse_robots(text: str, agent: str = AGENT_TOKEN) -> list[Rule]:
    """Return the rules of the group that best matches `agent` (falling back to `*`)."""
    groups: list[tuple[list[str], list[Rule]]] = []
    agents: list[str] = []
    rules: list[Rule] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent:
                if agents:
                    groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if value:  # empty Disallow = allow everything: no rule
                rules.append(Rule(key == "allow", value))
        else:
            last_was_agent = False
    if agents:
        groups.append((agents, rules))

    agent = agent.lower()
    specific = [r for names, r in groups if any(n != "*" and n in agent for n in names)]
    if specific:
        return [x for r in specific for x in r]
    return [x for names, r in groups if "*" in names for x in r]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(rx + (r"\Z" if anchored else ""), re.IGNORECASE)


def rule_matches(pattern: str, target: str) -> bool:
    """RFC 9309 pattern match (prefix semantics) against `path[?query]`."""
    return _compile(pattern).match(target) is not None


def _targets(url: str) -> set[str]:
    """All path[?query] spellings of a URL that a rule might apply to."""
    parts = urlsplit(url)
    forms: set[str] = set()
    raw_paths = {parts.path or "/"}
    # Percent-decode up to twice (double-encoding), and resolve dot segments.
    for p in list(raw_paths):
        d1 = unquote(p)
        raw_paths.update({d1, unquote(d1)})
    for p in list(raw_paths):
        norm = posixpath.normpath(p) if p else "/"
        if p.endswith("/") and not norm.endswith("/"):
            norm += "/"
        raw_paths.add(re.sub(r"/{2,}", "/", norm))
    queries = {parts.query, unquote(parts.query), unquote(unquote(parts.query))}
    for p in raw_paths:
        for q in queries:
            forms.add(p + (f"?{q}" if q else ""))
    return forms


def _best_rule(rules: list[Rule], target: str) -> Rule | None:
    best: Rule | None = None
    for r in rules:
        if rule_matches(r.pattern, target):
            if (
                best is None
                or len(r.pattern) > len(best.pattern)
                or (len(r.pattern) == len(best.pattern) and r.allow and not best.allow)
            ):
                best = r
    return best


@dataclass
class RobotsResult:
    """Outcome of fetching a host's robots.txt, supplied by the HTTP client."""
    status: int | None  # None = network error / timeout
    text: str = ""


@dataclass
class _HostRules:
    rules: list[Rule] | None  # None = fetch failed and we hold no earlier rules
    fetched_at: float
    ok: bool


class RobotsGuard:
    def __init__(
        self,
        fetch_robots: Callable[[str], RobotsResult],
        allowlist: dict[str, tuple[str, ...] | None] | None = None,
        baseline: tuple[str, ...] = BASELINE_RULES,
        ttl: float = 3600.0,
        failure_retry: float = 60.0,
        clock: Callable[[], float] = time.time,
    ):
        self._fetch_robots = fetch_robots
        self.allowlist = dict(DEFAULT_ALLOWLIST if allowlist is None else allowlist)
        self._baseline = tuple(baseline)
        self._ttl = ttl
        self._failure_retry = failure_retry
        self._clock = clock
        self._hosts: dict[str, _HostRules] = {}

    def check(self, url: str) -> None:
        """Raise FetchPolicyError unless `url` may be requested. Call before EVERY hop."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https":
            raise RobotsDisallowed(url, "scheme", "only https is allowed")
        if parts.port not in (None, 443):
            raise RobotsDisallowed(url, f"port:{parts.port}", "only the default https port is allowed")
        if host not in self.allowlist:
            raise RobotsDisallowed(url, f"host:{host}", "host is not on the allowlist")
        prefixes = self.allowlist[host]
        targets = _targets(url)
        if prefixes is not None and parts.path != "/robots.txt":
            # (robots.txt itself is always fetchable on an allowlisted host.)
            # Every spelling (decoded, dot-segments resolved) must stay inside the allowlist.
            for t in targets:
                if not t.split("?", 1)[0].startswith(prefixes):
                    raise RobotsDisallowed(
                        url, f"path:{t}", "path is outside the allowlist for this host")
        for target in targets:
            for pattern in self._baseline:
                if rule_matches(pattern, target):
                    raise RobotsDisallowed(url, pattern, "matches a built-in Disallow rule")

        if parts.path == "/robots.txt":
            return  # fetchable (allowlisted host); needed to learn the live rules
        rules = self._live_rules(host)
        for target in targets:
            best = _best_rule(rules, target)
            if best is not None and not best.allow:
                raise RobotsDisallowed(url, best.pattern)

    def _live_rules(self, host: str) -> list[Rule]:
        now = self._clock()
        cur = self._hosts.get(host)
        if cur is not None:
            age = now - cur.fetched_at
            if age < (self._ttl if cur.ok else self._failure_retry):
                if cur.rules is None:
                    raise RobotsUnavailable(f"robots.txt for {host} is unavailable; failing closed")
                return cur.rules
        res = self._fetch_robots(f"https://{host}/robots.txt")
        if res.status is not None and 200 <= res.status < 300:
            new = _HostRules(parse_robots(res.text), now, True)
        elif res.status is not None and 400 <= res.status < 500:
            new = _HostRules([], now, True)  # RFC 9309: 4xx = no restrictions published
        else:
            # 5xx / network error: reuse older rules if we have them, otherwise fail closed.
            new = _HostRules(cur.rules if cur is not None else None, now, False)
        self._hosts[host] = new
        if new.rules is None:
            raise RobotsUnavailable(f"robots.txt for {host} is unavailable (status={res.status})")
        return new.rules
