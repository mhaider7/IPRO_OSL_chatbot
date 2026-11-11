"""Deterministic intent routing: decide whether a chat message needs a live 312 lookup.

Pure functions, no I/O. Handbook/policy questions ("how do I start a club?", "club
re-registration deadline") must NOT trigger live lookups, so any policy-ish cue wins.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CLUB_NOUN = re.compile(r"\b(clubs?|organi[sz]ations?|orgs?|societ(?:y|ies)|fraternit\w+|soror\w+|teams?|groups?)\b", re.I)
HANDBOOK_CUES = re.compile(
    r"\b(start|starting|create|creating|form|forming|register|registering|registration|re-?regist\w*|"
    r"recogni[sz]\w*|constitution|reimburs\w*|fund(?:s|ing|ed)?|budget\w*|finance\w*|officers?|advisors?|"
    r"transition\w*|renew\w*|dues|polic(?:y|ies)|reserv\w+|charter\w*|eligib\w+|requirements?|deadline\w*|"
    r"form\b|forms\b|rules?|bylaws?|probation|suspend\w*|hazing|"
    r"submit\w*|approv\w+|advertis\w+|promot\w+|publici[sz]\w+|add(?:ing)? an? event|charges?|chartwells)\b", re.I)
# "host/plan/organize an event" is a handbook question when the student is the one doing it;
# "what events is ACM hosting?" is a lookup.
HANDBOOK_SELF = re.compile(
    r"\b(how|where|can i|can we|could i|could we|do i|do we|need to|want to|have to|steps?|process|rules?|"
    r"allowed|permission|policy)\b.{0,40}\b(host|hosting|plan|planning|organi[sz]e|organi[sz]ing|sponsor\w*|co-?sponsor\w*)\b",
    re.I)
DISCOVERY = re.compile(
    r"\b(find|any|recommend\w*|suggest\w*|looking for|interested in|into|what|which|list|show|"
    r"are there|is there|do you have|have any|join|sign up|clubs? (?:for|about|on)|clubs? that)\b", re.I)
DETAILS = re.compile(
    r"(?:tell me (?:more )?about|info(?:rmation)? (?:on|about)|details (?:on|about|for)|more about|"
    r"what(?:'s| is| does)|who (?:runs|leads)|contact (?:info )?(?:for|of)|how (?:do|can) i (?:join|contact))\s+"
    r"(?:the\s+)?(?P<name>[^?!.]+?)\s*[?.!]*$", re.I)
TRAILING_NOUN = re.compile(r"\s+(?:club|team|organi[sz]ation|society|group)s?$", re.I)


EVENT_NOUN = re.compile(r"\b(events?|happening|going on|things to do|activities|what'?s on|what is on)\b", re.I)
EVENT_ID = re.compile(r"(?:rsvp\?id=|\bevent\b[^0-9\n]{0,15}#?)(\d{4,7})\b", re.I)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTH_RE = r"(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b"
TAG_PHRASES = [
    (re.compile(r"\b(free (?:food|pizza|lunch|dinner|snacks?)|food|pizza|snacks?|lunch|dinner)\b", re.I), "Free Food"),
    (re.compile(r"\bwelcome week\b", re.I), "Welcome Week"),
    (re.compile(r"\bnew students?\b", re.I), "New Students"),
    (re.compile(r"\bprospective students?\b", re.I), "Prospective Students Welcome"),
    (re.compile(r"\bhonors(?: college)?\b", re.I), "Honors College Experience"),
]
TYPE_PHRASES = [
    (re.compile(r"\bcareer\b", re.I), "Career Preparation"), (re.compile(r"\bworkshops?\b", re.I), "Workshop"),
    (re.compile(r"\bsocials?\b", re.I), "Social"), (re.compile(r"\b(sports?|athletic\w*)\b", re.I), "Athletic/Sports"),
    (re.compile(r"\bacademic\b", re.I), "Academic"), (re.compile(r"\bmeetings?\b", re.I), "Meeting"),
    (re.compile(r"\bfundrais\w+\b", re.I), "Fundraiser"), (re.compile(r"\bcultural?\b", re.I), "Cultural"),
    (re.compile(r"\b(spiritual|religio\w+|worship)\b", re.I), "Spiritual"),
    (re.compile(r"\btabling\b", re.I), "Information Tabling"),
]
EVENT_KINDS = re.compile(
    r"\b(workshops?|meetings?|fairs?|hackathons?|fundraisers?|tabling|panels?|conferences?|networking|info(?:rmation)? sessions?|"
    r"competitions?|tournaments?|concerts?|game nights?|parties|party|socials?|lectures?|seminars?|webinars?|"
    r"career fairs?|open mic|mixers?|bake sales?|performances?|screenings?|volunteer(?:ing)? (?:day|event))\b", re.I)
TIME_CUE = re.compile(r"\b(coming up|upcoming|soon|later|anything to do|something to do|right now|currently|"
                      r"this (?:week|weekend|month|semester)|next (?:week|month)|tonight|today|tomorrow)\b", re.I)
WEEKDAYS = {n.lower(): i for i, n in enumerate(calendar.day_name)}
WEEKDAY_RE = r"\b(?:(this|next|on)\s+)?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b"
CLUB_ONLY = re.compile(r"\b(clubs?|organi[sz]ations?|orgs?|fraternit\w+|soror\w+)\b(?!.{0,30}\b(events?|hosting|have)\b)", re.I)
WHERE = re.compile(r"\b(where|location|address|what room|which room|which building)\b", re.I)
ONLINE = re.compile(r"\b(online|virtual|zoom|remote)\b", re.I)
CHI = ZoneInfo("America/Chicago")
EVENT_LIMIT = 6
CLUB_LIMIT = 5


def parse_dates(text: str, today: date) -> tuple[date, date] | None:
    """Relative/absolute date phrases -> (from, to), or None to use the tool's default window."""
    t = text.lower()
    if re.search(r"\b(today|tonight)\b", t):
        return today, today
    if "tomorrow" in t:
        return today + timedelta(days=1), today + timedelta(days=1)
    if re.search(r"\b(right now|currently|at the moment)\b", t):
        return today, today
    m = re.search(WEEKDAY_RE, t)
    if m and not re.search(MONTH_RE + r"\s+\d", t):
        idx = WEEKDAYS[m.group(2)]
        if m.group(1) == "next":                      # "next Friday" = the Friday of next week
            d = today + timedelta(days=7 - today.weekday() + idx)
        else:                                         # "Friday"/"this Friday" = the coming one (today counts)
            d = today + timedelta(days=(idx - today.weekday()) % 7)
        return d, d
    sunday = today + timedelta(days=6 - today.weekday())
    if re.search(r"\bthis weekend\b|\bweekend\b", t):
        sat = today + timedelta(days=(5 - today.weekday()) % 7) if today.weekday() < 5 else today
        return sat, sunday
    if "next week" in t:
        mon = today + timedelta(days=7 - today.weekday())
        return mon, mon + timedelta(days=6)
    if "this week" in t:
        return today, sunday
    m = re.search(MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        try:
            d = date(today.year, MONTHS[m.group(1)], int(m.group(2)))
            return (d if d >= today else d.replace(year=today.year + 1),) * 2
        except ValueError:
            pass
    if "this month" in t or "next month" in t:
        y, mo = today.year, today.month + (1 if "next month" in t else 0)
        if mo == 13:
            y, mo = y + 1, 1
        first = today if "this month" in t else date(y, mo, 1)
        return first, date(y, mo, calendar.monthrange(y, mo)[1])
    m = re.search(r"\b(?:in|during|for)\s+" + MONTH_RE, t)
    if m:
        mo = MONTHS[m.group(1)]
        y = today.year if mo >= today.month else today.year + 1
        first = today if (mo == today.month and y == today.year) else date(y, mo, 1)
        return first, date(y, mo, calendar.monthrange(y, mo)[1])
    return None


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    fallback: "ToolCall | None" = None   # used if the primary returns no_results
    intent: str | None = None            # "location" when the student asked WHERE something is

    @property
    def stage(self) -> str:
        return "searching_clubs" if "club" in self.name else "searching_events"


def _strip_filters(text: str) -> str:
    """Leave only the words that aren't already expressed as filters (they become the free-text query)."""
    leftover = text
    for rx, _ in TAG_PHRASES + TYPE_PHRASES:
        leftover = rx.sub(" ", leftover)
    leftover = ONLINE.sub(" ", leftover)
    leftover = re.sub(r"\b(today|tonight|tomorrow|this weekend|weekend|next week|this week|this month|next month|"
                      r"right now|currently)\b", " ", leftover, flags=re.I)
    leftover = re.sub(WEEKDAY_RE, " ", leftover, flags=re.I)
    return re.sub(MONTH_RE + r"(\s+\d{1,2}(?:st|nd|rd|th)?)?", " ", leftover, flags=re.I)


def _event_call(text: str, today: date) -> ToolCall | None:
    tags = [tag for rx, tag in TAG_PHRASES if rx.search(text)]
    types = [ty for rx, ty in TYPE_PHRASES if rx.search(text)]
    dates = parse_dates(text, today)
    kinds = EVENT_KINDS.search(text)
    short = len(text.split()) <= 8
    asks = DISCOVERY.search(text) or re.search(r"\b(is there|are there|any)\b", text, re.I)
    wants_events = (
        EVENT_NOUN.search(text)
        or (tags and (asks or dates))
        or (kinds and (dates or TIME_CUE.search(text) or asks or short))
        or (types and dates and short)
        or re.search(r"\b(anything|something|what)\b.{0,20}\b(to do|going on|happening|on)\b", text, re.I)
    )
    if not wants_events or CLUB_ONLY.search(text) and not EVENT_NOUN.search(text):
        return None
    args: dict = {"limit": EVENT_LIMIT}   # small: a 3B model can't digest long lists
    if dates:
        args["date_from"], args["date_to"] = dates[0].isoformat(), dates[1].isoformat()
    if re.search(r"\btonight\b", text, re.I):
        args["time_from"] = "17:00"
    if tags:
        args["tags"] = tags
    if types:
        args["event_type"] = types[0]
    if ONLINE.search(text):
        args["location_type"] = "online"
    leftover = _strip_filters(text)
    from web_tools.tools.events import event_tokens   # local import: keeps router import-light
    if event_tokens(leftover):
        args["query"] = leftover.strip()
    return ToolCall("search_events", args, intent="location" if WHERE.search(text) else None)


# ---- follow-ups (multi-turn) ---------------------------------------------------------------------
# A follow-up is resolved from the previous exchange only: the last bot message (its list of results)
# and the student message before it (re-routed to recover the tool call that produced that list).
OPENER = re.compile(r"^\s*(?:ok(?:ay)?[, ]+)?(?:and|also|but|so|then|what about|how about|what else)\b[ ,]*", re.I)
MORE = re.compile(
    r"^\W*(?:are there |is there |any |show me |show |give me |see )?(?:any )?"
    r"(?:more|others|other ones|anything else|what else|something else|the rest|next)"
    r"(?: (?:results?|ones?|clubs?|events?|options?))?\W*$", re.I)
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
            "fifth": 4, "5th": 4, "sixth": 5, "6th": 5, "last": -1}
ORDINAL_RE = re.compile(r"\b(" + "|".join(ORDINALS) + r")\b(?=\s+(?:one|event|club|org|organi[sz]ation|result|item|"
                        r"option|group)\b|\W*$)", re.I)
NUMBER_RE = re.compile(r"(?:#|\bnumber\s+|\bno\.?\s*|\boption\s+|\bitem\s+|\bresult\s+)(\d)\b", re.I)
ITEM_RE = re.compile(r"^- \*\*(?P<name>.+?)\*\* — .*\]\((?P<url>https?://[^)\s]+)\)\s*$", re.M)
PRON_REF = re.compile(r"\b(they|their|them|it|its|that club|this club|the club)\b", re.I)
FOLLOWUP_MAX_WORDS = 14


@dataclass
class Item:
    name: str
    url: str

    @property
    def event_id(self) -> str | None:
        m = re.search(r"[?&]id=(\d+)", self.url)
        return m.group(1) if m and "312.iit.edu" in self.url else None


def list_items(bot_text: str) -> list[Item]:
    """The bulleted results ('- **Name** ... [link](url)') in a previous bot message, in order."""
    return [Item(m["name"].strip(), m["url"]) for m in ITEM_RE.finditer(bot_text or "")]


def ordinal_index(text: str) -> int | None:
    """0-based position from 'the second one', '#3', 'the last one'; None if the message has none."""
    m = ORDINAL_RE.search(text)
    if m:
        return ORDINALS[m.group(1).lower()]
    m = NUMBER_RE.search(text)
    return int(m.group(1)) - 1 if m and int(m.group(1)) >= 1 else None


def _prior(history: list[dict], known: set[str] | None, today: date, max_back: int = 1) -> ToolCall | None:
    """The tool call behind the last bot message (None if it wasn't a live lookup). With max_back > 1,
    keeps looking through earlier exchanges (e.g. a handbook answer in between) for the last live one."""
    end = len(history)
    for _ in range(max_back):
        if end < 2 or history[end - 1]["role"] != "bot" or history[end - 2]["role"] != "user":
            return None
        call = route(history[end - 2]["text"], known, today, history[:end - 2])
        if call:
            return call
        end -= 2
    return None


def _item_call(item: Item, text: str) -> ToolCall:
    if item.event_id:
        return ToolCall("get_event_details", {"event_id_or_url": item.event_id},
                        intent="location" if WHERE.search(text) else None)
    return ToolCall("get_club_details", {"slug": item.name},
                    fallback=ToolCall("search_clubs", {"query": item.name, "limit": CLUB_LIMIT}))


def _reference_call(text: str, history: list[dict], known: set[str] | None, today: date) -> ToolCall | None:
    """Follow-ups that point at the last answer: 'the second one', 'more', 'what events do they have?'."""
    if len(text.split()) > FOLLOWUP_MAX_WORDS:
        return None
    items = list_items(history[-1]["text"]) if history[-1]["role"] == "bot" else []
    idx = ordinal_index(text)
    if idx is not None and -len(items) <= idx < len(items):
        return _item_call(items[idx], text)
    if MORE.match(text):
        prior = _prior(history, known, today)
        if prior and prior.name in ("search_events", "search_clubs"):
            args = dict(prior.args)
            # Skip what was actually shown (the renderer may have dropped some to fit), not just `limit`.
            args["offset"] = args.get("offset", 0) + (len(items) or args.get("limit", EVENT_LIMIT))
            return ToolCall(prior.name, args)
    if PRON_REF.search(text) and EVENT_NOUN.search(text):
        prior = _prior(history, known, today)
        if prior and prior.name == "get_club_details":
            return ToolCall("search_events", {"limit": EVENT_LIMIT, "query": prior.args["slug"]},
                            intent="location" if WHERE.search(text) else None)
    return None


def _refine_call(text: str, history: list[dict], known: set[str] | None, today: date) -> ToolCall | None:
    """Follow-ups that change the last search: 'what about tomorrow?', 'and online?', 'how about music?'.
    Only tried after normal routing found nothing, so full questions are never hijacked."""
    if len(text.split()) > FOLLOWUP_MAX_WORDS or CLUB_NOUN.search(text):
        return None
    opener = OPENER.match(text)
    dates = parse_dates(text, today)
    tags = [tag for rx, tag in TAG_PHRASES if rx.search(text)]
    types = [ty for rx, ty in TYPE_PHRASES if rx.search(text)]
    online = bool(ONLINE.search(text))
    if not opener and not ((dates or tags or types or online) and len(text.split()) <= 4):
        return None
    prior = _prior(history, known, today, max_back=3)
    if prior is None:
        return None
    rest = text[opener.end():] if opener else text

    # a named item from the last list: 'tell me more about the robotics one' / 'the Hackathon Kickoff'
    items = list_items(history[-1]["text"])
    lowered = text.lower()
    named = [i for i in items if len(i.name) >= 3 and i.name.lower() in lowered]
    if len(named) == 1:
        return _item_call(named[0], text)

    if prior.name == "search_events":
        args = {k: v for k, v in prior.args.items() if k != "offset"}
        changed = False
        if dates:
            args["date_from"], args["date_to"] = dates[0].isoformat(), dates[1].isoformat()
            args.pop("time_from", None)
            changed = True
        if re.search(r"\btonight\b", text, re.I):
            args["time_from"] = "17:00"
        if tags:
            args["tags"], changed = tags, True
        if types:
            args["event_type"], changed = types[0], True
        if online:
            args["location_type"], changed = "online", True
        from web_tools.tools.events import event_tokens
        leftover = _strip_filters(rest)
        if event_tokens(leftover):
            args["query"], changed = leftover.strip(" ?!."), True
        return ToolCall("search_events", args) if changed else None
    if prior.name == "search_clubs" and opener:
        from web_tools.tools.clubs import tokens as club_tokens
        topic = _strip_filters(rest).strip(" ?!.")          # 'how about tomorrow?' is not a club topic
        if club_tokens(topic):
            return ToolCall("search_clubs", {"query": topic, "limit": CLUB_LIMIT})
    return None


def route(message: str, known_club_names: set[str] | None = None, today: date | None = None,
          history: list[dict] | None = None) -> ToolCall | None:
    """`history` = the messages before `message` ({"role": "user"|"bot", "text": ...}); it lets short
    follow-ups ('what about tomorrow?', 'tell me about the second one', 'more') resolve."""
    text = message.strip()
    if not text or len(text) > 400 or HANDBOOK_CUES.search(text) or HANDBOOK_SELF.search(text):
        return None
    today = today or datetime.now(CHI).date()
    history = history or []

    m = EVENT_ID.search(text)
    if m:
        return ToolCall("get_event_details", {"event_id_or_url": m.group(1)},
                        intent="location" if WHERE.search(text) else None)
    if history:
        call = _reference_call(text, history, known_club_names, today)
        if call:
            return call
    call = _event_call(text, today)
    if call:
        return call
    if history:
        call = _refine_call(text, history, known_club_names, today)
        if call:
            return call

    m = DETAILS.search(text)
    if m:
        name = TRAILING_NOUN.sub("", m.group("name").strip(" '\""))
        generic = {"club", "clubs", "the", "it", "that", "this", "iit", "illinois tech"}
        if name and name.lower() not in generic:
            known = bool(known_club_names and name.lower() in known_club_names)
            if known or CLUB_NOUN.search(text):
                return ToolCall("get_club_details", {"slug": name},
                                fallback=ToolCall("search_clubs", {"query": name, "limit": CLUB_LIMIT}))

    # A short reply to a question the bot just asked ("for my club's trip") is an answer, not a club search.
    answering = bool(history) and history[-1]["role"] == "bot" and history[-1]["text"].rstrip().endswith("?")
    if CLUB_NOUN.search(text) and (DISCOVERY.search(text) or (len(text.split()) <= 10 and not answering)):
        return ToolCall("search_clubs", {"query": text, "limit": CLUB_LIMIT})
    return None
