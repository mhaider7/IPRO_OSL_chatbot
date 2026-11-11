"""WebTools: wires the fetcher, parsers and tools together. One shared instance per process."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from pydantic import ValidationError

from web_tools.config import WebToolsConfig
from web_tools.fetch.client import FetchResult, WebFetcher
from web_tools.fetch.robots import FetchPolicyError, RobotsDisallowed
from web_tools.parsers.clubs_html import LayoutChanged, parse_clubs_listing
from web_tools.parsers.events_ics import Event, parse_ics
from web_tools.tools import clubs as clubs_mod
from web_tools.tools import events as events_mod
from web_tools.tools.envelope import envelope, error_envelope
from web_tools.tools.schemas import (
    TOOL_SPECS, GetClubDetailsArgs, GetEventDetailsArgs, SearchClubsArgs, SearchEventsArgs,
)

logger = logging.getLogger("hawk")
Loaded = tuple[list | None, FetchResult, "tuple[str, str] | None"]


class WebTools:
    def __init__(self, cfg: WebToolsConfig | None = None, fetcher: WebFetcher | None = None,
                 now: Callable[[], datetime] | None = None):
        self.cfg = cfg or WebToolsConfig()
        self.fetcher = fetcher or WebFetcher(self.cfg)
        self._now = now or (lambda: datetime.now(events_mod.CHI))
        self._lock = threading.Lock()
        self._memo: dict[str, tuple[tuple, list]] = {}   # url -> (cache key, last good parse)

    specs = TOOL_SPECS

    # -- shared loader ------------------------------------------------------------------------
    def _load(self, url: str, ttl: float, parse: Callable[[str], list], read_timeout: float | None = None) -> Loaded:
        """Fetch + parse with memoization. On layout drift, serves the last good parse as stale
        (never replacing it with a suspicious parse)."""
        fr = self.fetcher.fetch(url, ttl=ttl, read_timeout=read_timeout)
        if not fr.ok:
            return None, fr, (fr.error_code or "http_error", fr.error_message or "fetch failed")
        key = (fr.retrieved_at, len(fr.text or ""))
        with self._lock:
            memo = self._memo.get(url)
        if memo and memo[0] == key:
            return memo[1], fr, None
        try:
            parsed = parse(fr.text or "")
        except LayoutChanged as exc:
            logger.error("layout changed for %s: %s", url, exc)
            if memo:
                fr.stale, fr.stale_reason = True, "layout_changed"
                return memo[1], fr, ("layout_changed", str(exc))
            return None, fr, ("layout_changed", str(exc))
        with self._lock:
            self._memo[url] = (key, parsed)
        return parsed, fr, None

    def _load_clubs(self) -> Loaded:
        return self._load(clubs_mod.CLUBS_URL, self.cfg.clubs_ttl_s,
                          lambda t: parse_clubs_listing(t, min_clubs=self.cfg.min_clubs))

    def _load_events(self) -> Loaded:
        return self._load(events_mod.EVENTS_URL, self.cfg.events_ttl_s,
                          lambda t: parse_ics(t, min_events=self.cfg.min_events),
                          read_timeout=self.cfg.feed_read_timeout_s)

    def known_club_names(self) -> set[str]:
        """Lower-cased names from the last parsed listing (empty before the first lookup; no I/O)."""
        with self._lock:
            memo = self._memo.get(clubs_mod.CLUBS_URL)
        return {c.name.lower() for c in memo[1]} if memo else set()

    # -- club tools ---------------------------------------------------------------------------
    def search_clubs(self, args: SearchClubsArgs) -> dict:
        clubs, fr, err = self._load_clubs()
        if clubs is None:
            return error_envelope(err[0], err[1], clubs_mod.CLUBS_URL, fr)
        hits = clubs_mod.filter_clubs(clubs, args.query, args.category, args.type)
        data = {
            "total_matches": len(hits), "returned": min(len(hits), args.limit),
            "clubs": [clubs_mod.club_summary(c) for c in hits[: args.limit]],
        }
        if not hits:
            data["hint"] = "No clubs matched. Try a broader interest word, or drop the category/type filter."
        if args.include_archived:
            data["note"] = "The public listing contains active groups only; archived groups are not searchable."
        return envelope(ok=True, data=data, source_url=clubs_mod.CLUBS_URL, fetch=fr,
                        error=err, extra={"layout_warning": True} if err else None)

    def get_club_details(self, args: GetClubDetailsArgs) -> dict:
        clubs, fr, err = self._load_clubs()
        if clubs is None:
            return error_envelope(err[0], err[1], clubs_mod.CLUBS_URL, fr)
        club = clubs_mod.find_club(clubs, args.slug)
        if club is None:
            near = [clubs_mod.club_summary(c, 150, 0) for c in clubs_mod.close_matches(clubs, args.slug)]
            return envelope(ok=False, data={"close_matches": near}, source_url=clubs_mod.CLUBS_URL, fetch=fr,
                            error=("no_results", f"No club with slug, id or name '{args.slug}'."))
        detail = clubs_mod.club_summary(club, mission_n=1500, benefits_n=1000)
        detail["website_url"] = club.website_url
        detail["type_line"] = club.type_line
        detail["upcoming_events"] = self._club_events(club.slug)
        return envelope(ok=True, data=detail, source_url=clubs_mod.page_url(club), fetch=fr, error=err)

    def _club_events(self, slug: str | None, limit: int = 5) -> list[dict] | None:
        """Next events hosted by this club, from the cached site feed. None = unavailable."""
        if not slug:
            return None
        try:
            evs, _fr, _err = self._load_events()
        except RobotsDisallowed:
            raise   # a refused URL is never swallowed
        except Exception:
            logger.exception("club upcoming-events lookup failed")
            return None
        if evs is None:
            return None
        today = self._now().date()
        mine = [e for e in evs if (e.club_slug or "").casefold() == slug.casefold()
                and events_mod.local(e.end or e.start).date() >= today]
        mine.sort(key=lambda e: e.start)
        return [events_mod.event_summary(e, 120) for e in mine[:limit]]

    # -- event tools --------------------------------------------------------------------------
    def search_events(self, args: SearchEventsArgs) -> dict:
        evs, fr, err = self._load_events()
        if evs is None:
            return error_envelope(err[0], err[1], events_mod.EVENTS_URL, fr)
        today = self._now().date()
        start, end = events_mod.resolve_window(args.date_from, args.date_to, today)
        kw = dict(date_from=args.date_from, date_to=args.date_to, tags=args.tags,
                  location_type=args.location_type, today=today, now=self._now(), time_from=args.time_from)
        hits, mode = events_mod.search(evs, query=args.query, category=args.category,
                                       event_type=args.event_type, **kw)
        note = None
        wanted_type = args.event_type or args.category
        if not hits and wanted_type:
            # The feed's type labels are coarse ("sports" events are often tagged Social): retry as text.
            word = wanted_type.split("/")[-1]            # "Athletic/Sports" -> "Sports"
            q = f"{args.query} {word}" if args.query else word
            hits, mode = events_mod.search(evs, query=q, **kw)
            note = f"No events are labelled '{wanted_type}'; showing events that mention it instead." if hits else None
        data = {
            "window": {"from": start.isoformat(), "to": end.isoformat(), "timezone": "America/Chicago"},
            "total_matches": len(hits), "returned": min(len(hits), args.limit), "text_match": mode,
            "events": [events_mod.event_summary(e, window_start=start) for e in hits[: args.limit]],
        }
        if note:
            data["note"] = note
        if not hits:
            data["hint"] = "No events matched. Try a wider date range or fewer filters."
        if args.location_type in ("online", "in_person"):
            data["note"] = (data.get("note", "") + " Most event locations are hidden in the public feed, so "
                            "online/in-person filtering is best-effort.").strip()
        return envelope(ok=True, data=data, source_url=events_mod.EVENTS_URL, fetch=fr, error=err,
                        extra={"feed_last_modified": fr.last_modified})

    def get_event_details(self, args: GetEventDetailsArgs) -> dict:
        event_id = events_mod.parse_event_ref(args.event_id_or_url)
        if event_id is None:
            return error_envelope("bad_args", "Could not find an event id in that value.", events_mod.EVENTS_URL)
        evs, fr, err = self._load_events()
        if evs is None:
            return error_envelope(err[0], err[1], events_mod.EVENTS_URL, fr)
        found = next((e for e in evs if e.event_id == event_id), None)
        link = f"https://312.iit.edu/rsvp?id={event_id}"
        if found is None:
            return envelope(ok=False, source_url=link, fetch=fr, extra={"feed_last_modified": fr.last_modified},
                            error=("no_results", f"Event {event_id} is not in the public feed (it may be past, "
                                                 "private, or removed). Try the event page directly."))
        return envelope(ok=True, data=events_mod.event_detail(found), source_url=found.url, fetch=fr, error=err,
                        extra={"feed_last_modified": fr.last_modified})

    # -- dispatcher ---------------------------------------------------------------------------
    def run(self, name: str, arguments: dict | None) -> dict:
        """Validate args, run the tool, and ALWAYS return an envelope (never raises)."""
        tools = {
            "search_clubs": (self.search_clubs, SearchClubsArgs),
            "get_club_details": (self.get_club_details, GetClubDetailsArgs),
            "search_events": (self.search_events, SearchEventsArgs),
            "get_event_details": (self.get_event_details, GetEventDetailsArgs),
        }
        if name not in tools:
            return error_envelope("bad_args", f"unknown tool '{name}'", "")
        fn, model = tools[name]
        try:
            args = model.model_validate(arguments or {})
        except ValidationError as exc:
            first = exc.errors()[0]
            return error_envelope("bad_args", f"{first['msg']} ({'.'.join(map(str, first['loc']))})", "")
        try:
            return fn(args)
        except RobotsDisallowed as exc:
            logger.error("ROBOTS GUARD refused a fetch: %s", exc)
            return error_envelope("robots_disallowed", str(exc), "")
        except FetchPolicyError as exc:
            return error_envelope("robots_unavailable", str(exc), "")
        except Exception as exc:  # a tool must never break the chat stream
            logger.exception("web tool %s crashed", name)
            return error_envelope("internal_error", exc.__class__.__name__, "")


_default: WebTools | None = None
_default_lock = threading.Lock()


def get_default_tools(raw_config: dict | None = None) -> WebTools:
    global _default
    with _default_lock:
        if _default is None:
            _default = WebTools(WebToolsConfig.from_dict(raw_config))
        return _default
