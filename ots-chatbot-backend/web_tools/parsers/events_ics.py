"""Parse the CampusGroups iCal feed into Event records (pure function, no I/O).

The feed is flat: UTC timestamps, no RRULE/VTIMEZONE, no line folding in practice (we still
unfold per RFC 5545). `ENCODING=QUOTED-PRINTABLE` on SUMMARY is cosmetic; values are plain text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from web_tools.parsers.clubs_html import LayoutChanged

REDACTED_LOCATION = "sign in to download the location"
DETAIL_TAIL_RE = re.compile(r"\s*---\s*Event Details:\s*\S+\s*$", re.S)


@dataclass(frozen=True)
class Event:
    event_id: str
    uid: str
    title: str
    start: datetime            # timezone-aware, UTC
    end: datetime | None
    location: str              # raw text from the feed ("" if absent)
    url: str                   # https://312.iit.edu/rsvp?id=N
    club_name: str
    club_slug: str | None      # 312 slug when known (from the CONTACT url, else club_acronym)
    event_type: str
    tags: tuple[str, ...]
    description: str           # unescaped, trailing "Event Details: <url>" removed
    last_modified: datetime | None

    @property
    def location_type(self) -> str:
        loc = self.location.strip().lower()
        if not loc or loc == "tbd":
            return "tbd"
        if loc == REDACTED_LOCATION:
            return "hidden"
        if "online" in loc or "zoom" in loc or loc.startswith("http"):
            return "online"
        return "in_person"


def unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)   # \n -> newline; \, \; \\ -> literal
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _split_line(line: str) -> tuple[str, dict[str, str], str] | None:
    """'NAME;P=v;P2="a:b":value' -> (NAME, {P: v}, value); colons inside quotes are ignored."""
    in_q, idx = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            idx = i
            break
    if idx < 0:
        return None
    head, value = line[:idx], line[idx + 1:]
    name, *params = re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', head)
    pd = {}
    for p in params:
        k, _, v = p.partition("=")
        pd[k.upper()] = v.strip('"')
    return name.upper(), pd, value


def _dt(value: str) -> datetime | None:
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def event_id_from_url(url: str) -> str | None:
    q = parse_qs(urlsplit(url).query)
    ident = (q.get("id") or [""])[0]
    return ident if ident.isdigit() else None


def _parse_event(block: str) -> Event | None:
    props: dict[str, tuple[dict[str, str], str]] = {}
    cats: dict[str, str] = {}
    for line in block.split("\n"):
        parsed = _split_line(line)
        if not parsed:
            continue
        name, params, value = parsed
        if name == "CATEGORIES":
            # e.g. `CATEGORIES;X-CG-CATEGORY=event_type:Social`
            cats[params.get("X-CG-CATEGORY", "")] = value
        else:
            props.setdefault(name, (params, value))
    return _build(props, cats)


def _build(props, cats) -> Event | None:
    def val(k): return props[k][1] if k in props else ""
    start, url, title, uid = _dt(val("DTSTART")), val("URL"), unescape(val("SUMMARY")).strip(), val("UID")
    event_id = event_id_from_url(url)
    if not (start and event_id and title and uid):
        return None
    contact = val("CONTACT")
    segs = [s for s in urlsplit(contact).path.split("/") if s]
    slug = segs[0] if len(segs) >= 2 and urlsplit(contact).hostname == "312.iit.edu" else None
    slug = slug or cats.get("club_acronym") or None
    desc = DETAIL_TAIL_RE.sub("", unescape(val("DESCRIPTION"))).strip()
    tags = tuple(t.strip() for t in cats.get("event_tags", "").split(",") if t.strip())
    organizer = props.get("ORGANIZER", ({}, ""))[0].get("CN", "")
    return Event(
        event_id=event_id, uid=uid, title=title, start=start, end=_dt(val("DTEND")),
        location=unescape(val("LOCATION")).strip(), url=f"https://312.iit.edu/rsvp?id={event_id}",
        club_name=organizer, club_slug=slug, event_type=cats.get("event_type", "").strip(),
        tags=tags, description=desc, last_modified=_dt(val("LAST-MODIFIED")),
    )


def parse_ics(text: str, min_parse_rate: float = 0.5, min_events: int = 1) -> list[Event]:
    if "BEGIN:VCALENDAR" not in text[:2000]:
        raise LayoutChanged("iCal feed: no BEGIN:VCALENDAR header (got something else, e.g. HTML)")
    blocks = re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfold(text), re.S)
    events = [e for b in blocks if (e := _parse_event(b)) is not None]
    if len(events) < min_events or (blocks and len(events) / len(blocks) < min_parse_rate):
        raise LayoutChanged(
            f"iCal feed: parsed {len(events)} of {len(blocks)} VEVENTs; required fields "
            "(DTSTART, URL?id=, SUMMARY, UID) may have changed")
    return events
