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
# Club searches need a real discovery cue. Plain "what" only counts right before a plural group noun
# ("what clubs", "what student organizations"): "what are the steps for club travel?" is a policy question.
CLUB_DISCOVERY = re.compile(
    r"\b(find|any|recommend\w*|suggest\w*|looking for|interested in|list|show|"
    r"are there|is there|do you have|have any|join|sign up|clubs? (?:for|about|on)|clubs? that)\b"
    r"|\b(?:what|which)\W+(?:\w+\W+){0,2}?(?:clubs|orgs|organi[sz]ations|groups|teams|societies|fraternities|sororities)\b",
    re.I)
# Question openers that make a short club message a question about something else ("what is a club advisor?",
# "where can our club store things?") rather than a search. "is/are" stay out: "is the chess club still active?"
QUESTION_START = re.compile(r"^\W*(what|how|why|when|where|who|can|could|do|does|should|will|would|may)\b", re.I)

# ---- "How do things work here?" questions belong to the handbook ------------------------------------------
# Policy questions often mention an event or a club ("can we serve alcohol at our event?"), which the lookup
# patterns below would otherwise grab. Checked before any lookup pattern.
# STRONG: the shape of a rules/cost/process question; always the handbook.
POLICY_STRONG = re.compile(
    r"\bhow\s+(?:much|long|far|often|soon|early)\b"
    r"|\bhow\s+many\b(?!\s+(?:events|clubs|organi[sz]ations|groups)\s+(?:are|is)\s+(?:there|happening|going\s+on))"
    r"|\bwhat\s+(?:fees|charges|costs?|counts|happens|qualifies)\b"
    r"|\bwhat(?:'s|\s+is|\s+are)\s+the\s+(?:limits?|rules?|polic(?:y|ies)|process|procedure|steps|deadlines?|"
    r"requirements?|costs?|fees?|prices?|max(?:imum)?|min(?:imum)?)\b"
    r"|\bwho\s+(?:pays|is\s+responsible|approves|signs|covers)\b"
    r"|\b(?:allowed|permitted|required|prohibited|banned)\b"
    r"|\bis\s+it\s+(?:ok(?:ay)?|possible|legal|fine)\b"
    r"|\b(?:fees?|costs?|prices?|limits?)\b",
    re.I)
# "Things to do" questions are event lookups even when phrased "what can I do ...".
THINGS_TO_DO = re.compile(r"\bwhat\s+(?:can|should|could)\s+(?:i|we)\s+do\b", re.I)
# SOFT: about the student's own club/event, or "can we / how do I ..."; the handbook unless the message
# clearly asks for a list of events (LISTING). Verbs like join/find/contact stay lookups ("how do I join ...").
_LOOKUP_VERBS = r"(?:join|find|see|sign\s+up|get\s+involved|contact|meet|attend|look)"
POLICY_SOFT = re.compile(
    r"\b(?:our|my)\s+(?:\w+\s+){0,2}?(?:club|org|organi[sz]ation|event|team|fraternity|sorority|group|chapter|society)(?:'?s)?\b"
    r"|^\W*(?:our|my)\b(?!.*\b(?:join|find)\b)"
    r"|^\W*(?:can|could|may|should|must|do|does|did|will|would|are|is)\s+(?:we|i|our|my|students?|non-?students|"
    r"minors|guests|members|officers|treasurers?|presidents?|advisors?)\b(?!\s+" + _LOOKUP_VERBS + r")"
    # "can I / should we ..." anywhere: "what foods can I serve at an event?"
    r"|\b(?:can|could|may|should|must)\s+(?:i|we)\b(?!\s+" + _LOOKUP_VERBS + r")"
    r"|\bhow\s+(?:do|can|should|would|could|does)\s+(?:i|we|our|my)\b(?!\s+" + _LOOKUP_VERBS + r")"
    r"|\bwhat\s+(?:do|does|should|must)\s+(?:i|we|our|my)\b"
    r"|\bwhere\s+(?:can|do|should|would)\s+(?:i|we|our|my)\b(?!\s+(?:find|see|join|look))",
    re.I)
LISTING = re.compile(
    r"\b(?:what|which|any|upcoming|show|list|find|see)\b[^?.!]{0,25}\bevents\b"
    r"|\bevents\s+(?:this|next|today|tonight|tomorrow|coming)\b|\bcoming up\b|\bupcoming\b", re.I)


def is_policy_question(text: str) -> bool:
    """True when the message asks how something works (rules, costs, process) rather than for a lookup."""
    listing = LISTING.search(text) or THINGS_TO_DO.search(text)
    return bool(POLICY_STRONG.search(text) or (POLICY_SOFT.search(text) and not listing))
DETAILS = re.compile(
    r"(?:tell me (?:more )?about|info(?:rmation)? (?:on|about)|details (?:on|about|for)|more about|"
    r"what(?:'s| is| does)|who (?:runs|leads)|contact (?:info )?(?:for|of)|how (?:do|can) i (?:join|contact))\s+"
    r"(?:the\s+)?(?P<name>[^?!.]+?)\s*[?.!]*$", re.I)
TRAILING_NOUN = re.compile(r"\s+(?:club|team|organi[sz]ation|society|group)s?$", re.I)


EVENT_NOUN = re.compile(r"\b(events?|happening|going on|things to do|activities|what'?s on|what is on)\b", re.I)
# "$" is kept out of the gap and a money word can't follow, so "our event costs $1500" is not event #1500.
EVENT_ID = re.compile(r"(?:rsvp\?id=|\bevent\b[^0-9$\n]{0,15}#?)(\d{4,7})\b(?!\s*(?:dollars?|usd|bucks)\b)", re.I)
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


AFTER_TIME = re.compile(r"\b(?:after|from|starting(?:\s+at)?|post)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def parse_time_after(text: str) -> str | None:
    """'after 5pm' -> '17:00'; None if the message names no 'after <clock time>'."""
    m = AFTER_TIME.search(text)
    if not m:
        return "17:00" if re.search(r"\b(tonight|after work|this evening|in the evening)\b", text, re.I) else None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 7:
        h += 12     # "after 5" means evening
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


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
        # "what's on" only right after "what": "what's the limit on ..." is not an events question
        or re.search(r"\b(anything|something|what)\b.{0,20}\b(to do|going on|happening)\b|\bwhat(?:'s|\s+is)\s+on\b",
                     text, re.I)
        or THINGS_TO_DO.search(text)
    )
    if not wants_events or CLUB_ONLY.search(text) and not EVENT_NOUN.search(text):
        return None
    args: dict = {"limit": EVENT_LIMIT}   # small: a 3B model can't digest long lists
    if dates:
        args["date_from"], args["date_to"] = dates[0].isoformat(), dates[1].isoformat()
    if parse_time_after(text):
        args["time_from"] = parse_time_after(text)
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


def _apply_event_filters(args: dict, text: str, dates, tags: list[str], types: list[str], online: bool) -> bool:
    """Write the date/tag/type/online filters a message supplies into search_events args; True if any."""
    changed = False
    if dates:
        args["date_from"], args["date_to"] = dates[0].isoformat(), dates[1].isoformat()
        args.pop("time_from", None)
        changed = True
    if parse_time_after(text):
        args["time_from"] = parse_time_after(text)
    if tags:
        args["tags"], changed = tags, True
    if types:
        args["event_type"], changed = types[0], True
    if online:
        args["location_type"], changed = "online", True
    return changed


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
        changed = _apply_event_filters(args, text, dates, tags, types, online)
        from web_tools.tools.events import event_tokens
        leftover = _strip_filters(rest)
        if event_tokens(leftover):
            args["query"], changed = leftover.strip(" ?!."), True
        return ToolCall("search_events", args) if changed else None
    if prior.name == "search_clubs":
        from web_tools.tools.clubs import tokens as club_tokens
        topic = _strip_filters(rest).strip(" ?!.")          # 'how about tomorrow?' is not a club topic
        if dates or tags or types or online:
            # Clubs have no dates: "how about tomorrow?" after a club search means events on that topic.
            args = {"limit": EVENT_LIMIT}
            _apply_event_filters(args, text, dates, tags, types, online)
            words = club_tokens(topic) or club_tokens(prior.args.get("query", ""))
            if words:
                args["query"] = " ".join(words)
            return ToolCall("search_events", args, intent="location" if WHERE.search(text) else None)
        if opener and club_tokens(topic):
            return ToolCall("search_clubs", {"query": topic, "limit": CLUB_LIMIT})
    return None


def rule_call(message: str, history: list[dict] | None = None, known_club_names: set[str] | None = None,
              today: date | None = None, include_more: bool = True) -> ToolCall | None:
    """The lookups that stay rules even with the search picker (api/routing.py): an event ID or rsvp link, a
    position in the list Hawk just showed ("the second one", "#3"), and "more" (the next page of the same search).
    They need the exact ID, link or offset from Hawk's last reply, which code reads reliably. "more" re-reads the
    previous message with the regex router, so it is only right when the regex router made that search: with the
    search picker on, the service resolves "more" itself (include_more=False)."""
    text = message.strip()
    if not text or len(text) > 400:
        return None
    today = today or datetime.now(CHI).date()
    history = history or []
    m = EVENT_ID.search(text)
    if m:
        return ToolCall("get_event_details", {"event_id_or_url": m.group(1)},
                        intent="location" if WHERE.search(text) else None)
    if not history or history[-1]["role"] != "bot" or len(text.split()) > FOLLOWUP_MAX_WORDS:
        return None
    items = list_items(history[-1]["text"])
    idx = ordinal_index(text)
    if idx is not None and -len(items) <= idx < len(items):
        return _item_call(items[idx], text)
    if include_more and MORE.match(text):
        prior = _prior(history, known_club_names, today)
        if prior and prior.name in ("search_events", "search_clubs"):
            args = dict(prior.args)
            args["offset"] = args.get("offset", 0) + (len(items) or args.get("limit", EVENT_LIMIT))
            return ToolCall(prior.name, args)
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
    if is_policy_question(text):
        return None
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
        # A name, not a clause: "what does my club need to do ..." must not look up a club called "my club need ..."
        looks_like_name = (len(name.split()) <= 6
                           and not re.match(r"(?:my|our|your|a|an)\b", name, re.I))
        if name and name.lower() not in generic and looks_like_name:
            known = bool(known_club_names and name.lower() in known_club_names)
            if known or CLUB_NOUN.search(text):
                return ToolCall("get_club_details", {"slug": name},
                                fallback=ToolCall("search_clubs", {"query": name, "limit": CLUB_LIMIT}))

    # A short reply to a question the bot just asked ("for my club's trip") is an answer, not a club search.
    answering = bool(history) and history[-1]["role"] == "bot" and history[-1]["text"].rstrip().endswith("?")
    # Short messages without a question word ("art clubs", "clubs for international students") are searches.
    short_search = len(text.split()) <= 10 and not answering and not QUESTION_START.match(text)
    if CLUB_NOUN.search(text) and (CLUB_DISCOVERY.search(text) or short_search):
        return ToolCall("search_clubs", {"query": text, "limit": CLUB_LIMIT})
    return None
