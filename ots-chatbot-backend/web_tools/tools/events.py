"""search_events / get_event_details over the parsed iCal feed. All filtering is ours."""
from __future__ import annotations

import base64
import binascii
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from web_tools.parsers.events_ics import Event
from web_tools.tools.clubs import ALIASES, STOPWORDS, _stem, _word_prefix, tokens as club_tokens

EVENTS_URL = "https://312.iit.edu/ical/iit/ical_iit.ics"
CHI = ZoneInfo("America/Chicago")
DEFAULT_WINDOW_DAYS = 14
UNAVAILABLE = ["price", "attendee_count", "registration_status"]   # not in the public feed

EVENT_STOPWORDS = STOPWORDS | {
    "event", "events", "happening", "happens", "going", "upcoming", "coming", "up", "week", "weekend",
    "today", "tonight", "tomorrow", "next", "month", "soon", "activities", "activity", "things", "thing",
    "when", "campus", "anything", "something", "else", "stuff", "right", "now", "currently", "by", "from",
    "at", "put", "run", "hosted", "hosting", "host", "organized", "sponsored", "available", "offered",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "weekday", "later",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "around", "near", "tonite", "whats", "going",
}


def event_tokens(query: str) -> list[str]:
    """Drop event-ish filler words BEFORE stemming ('happening' would otherwise stem past the list)."""
    query = query.replace("\u2019", "'")
    words = [w[:-2] if w.lower().endswith("'s") else w
             for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+#&'-]*", query)]
    return club_tokens(" ".join(w for w in words if w.lower() not in EVENT_STOPWORDS))


def local(dt: datetime) -> datetime:
    return dt.astimezone(CHI)


def friendly_when(e: Event) -> str:
    s = local(e.start)
    def clock(d): return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    out = f"{s:%a %b} {s.day}, {clock(s)}"
    if e.end:
        en = local(e.end)
        out += f"–{clock(en)}" if en.date() == s.date() else f" – {en:%a %b} {en.day}, {clock(en)}"
    return out + " Central"


def _cut(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def club_page(e: Event) -> str | None:
    return f"https://312.iit.edu/{e.club_slug}/" if e.club_slug else None


LONG_RUNNING_DAYS = 3


def is_ongoing(e: Event, window_start: date | None = None) -> bool:
    """Multi-day listings (e.g. a semester-long closet) rather than something you attend 'tomorrow'."""
    long = e.end is not None and (e.end - e.start) > timedelta(days=LONG_RUNNING_DAYS)
    began_earlier = window_start is not None and local(e.start).date() < window_start
    return long or (began_earlier and e.end is not None and local(e.end).date() > local(e.start).date())


def event_summary(e: Event, desc_n: int = 200, window_start: date | None = None) -> dict:
    ongoing = is_ongoing(e, window_start)
    return {
        "event_id": e.event_id, "title": e.title,
        "start_iso": local(e.start).isoformat(), "end_iso": local(e.end).isoformat() if e.end else None,
        "ongoing": ongoing,
        "when": (f"ongoing through {local(e.end):%a %b} {local(e.end).day}" if ongoing and e.end else friendly_when(e)), "host_club": e.club_name, "host_slug": e.club_slug,
        "event_type": e.event_type, "tags": list(e.tags),
        "location": None if e.location_type in ("hidden", "tbd") else e.location,
        "location_hidden": e.location_type == "hidden", "location_type": e.location_type,
        "description_snippet": _cut(e.description, desc_n), "url": e.url,
    }


def resolve_window(date_from: date | None, date_to: date | None, today: date) -> tuple[date, date]:
    start = date_from or today
    end = date_to or (start + timedelta(days=DEFAULT_WINDOW_DAYS))
    return start, end


ONLINE_HINT = re.compile(r"\b(virtual|online|zoom|webinar|teams meeting|remote)\b", re.I)


def looks_online(e: Event) -> bool:
    """Most locations are hidden in the public feed, so also trust the title/description."""
    return e.location_type == "online" or bool(ONLINE_HINT.search(e.title + " " + e.description[:400]))


def _matches_text(e: Event, toks: list[str], require_all: bool) -> bool:
    hay = " ".join([e.title, e.description, e.club_name, e.event_type, " ".join(e.tags)]).lower()
    hits = []
    for t in toks:
        variants = [t] + [_stem(a) for a in ALIASES.get(t, [])]
        hits.append(any(_word_prefix(v, hay) for v in variants))
    if require_all:
        return all(hits)
    return sum(hits) >= max(2, (len(hits) + 1) // 2)    # loose fallback: at least half the words


def _pick(values: list[str], wanted: str, key=lambda x: x) -> list[str]:
    w = wanted.casefold()
    exact = [v for v in values if key(v).casefold() == w]
    return exact or [v for v in values if w in key(v).casefold()]


def search(events: list[Event], *, query=None, date_from=None, date_to=None, category=None,
           event_type=None, tags=None, location_type=None, today: date,
           now: datetime | None = None, time_from: time | None = None) -> tuple[list[Event], str]:
    start, end = resolve_window(date_from, date_to, today)
    pool = [e for e in events if start <= local(e.end or e.start).date() and local(e.start).date() <= end]
    if now is not None and start <= today:
        # Today's events that are already over are not "what's on": drop them.
        cutoff = now
        if time_from is not None:
            cutoff = max(now, datetime.combine(today, time_from, tzinfo=CHI))
        pool = [e for e in pool
                if not (local(e.end or e.start).date() == today and local(e.end or e.start) < cutoff)]

    for wanted in (event_type, category):   # the feed has no separate "category"; both mean event_type
        if wanted:
            types = _pick(sorted({e.event_type for e in pool}), wanted)
            pool = [e for e in pool if e.event_type in types]
    if tags:
        wanted_tags = [t.casefold() for t in tags]
        pool = [e for e in pool if any(w in (x.casefold() for x in e.tags) for w in wanted_tags)
                or any(w in x.casefold() for w in wanted_tags for x in e.tags)]
    if location_type == "online":
        pool = [e for e in pool if looks_online(e)]
    elif location_type:
        pool = [e for e in pool if e.location_type == location_type]

    mode = "all"
    toks = event_tokens(query) if query else []
    if toks:
        strict = [e for e in pool if _matches_text(e, toks, True)]
        if strict:
            pool = strict
        else:
            pool, mode = [e for e in pool if _matches_text(e, toks, False)], "any"
    # Things you can attend in the window first (by start time); long-running listings after.
    return sorted(pool, key=lambda e: (is_ongoing(e, start), e.start, e.title.lower())), mode


def parse_event_ref(ref: str) -> str | None:
    """Event id from '385553', an rsvp URL, a /w/_/_/events/<base64> URL, or 'iit:Event:385553'."""
    ref = ref.strip()
    if ref.isdigit():
        return ref
    m = re.search(r"Event:(\d+)", ref)
    if m:
        return m.group(1)
    parts = urlsplit(ref)
    ident = (parse_qs(parts.query).get("id") or [""])[0]
    if ident.isdigit():
        return ident
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if last:
        try:
            decoded = base64.b64decode(last + "=" * (-len(last) % 4), validate=True).decode()
            m = re.search(r"Event:(\d+)", decoded)
            return m.group(1) if m else None
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
    return None


def event_detail(e: Event) -> dict:
    d = event_summary(e, desc_n=1800)
    d["description"] = _cut(e.description, 1800)
    d.pop("description_snippet")
    d["club_page_url"] = club_page(e)
    d["unavailable_fields"] = list(UNAVAILABLE) + (["location"] if e.location_type == "hidden" else [])
    return d
