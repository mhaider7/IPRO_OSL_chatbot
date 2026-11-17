"""Search picker: one model call decides which search runs for a message, and writes the search text.

Why: the regex router (web_tools/router.py) and the follow-up rules (api/conversation.py) decide from keywords
before any model sees the message, so "Machine learning?" after a club search, "Funding?" after a handbook answer,
or "No, I need food FOR an event" after a wrong events list can't be fixed by a better model. Here the model reads
the conversation and calls one of the tools below (or none); code then checks what it wrote.

What the model gets: a short routing-only prompt (Hawk's answer prompt makes models answer instead of searching,
CHATBOT_BEHAVIOR.md 4.22), a calendar of the next 21 days built here (models get weekday arithmetic wrong, 4.23),
the last few messages, and the tools: search_handbook(query) plus Hawk's real 312 tools (web_tools TOOL_SPECS).
What comes back is checked with Hawk's own argument classes: at most one handbook search, or one or two 312
lookups (a club and events, when the student asks for both). No tool = "none" (greeting, thanks, a reaction, an
unrelated request); whatever text the model wrote instead is thrown away, never shown.

If the call fails (Ollama error, over the time limit, nothing usable), the regex router and today's search text
decide instead (`source="fallback"`), so a failed call gives exactly the old behavior. The router's own choice is
always computed and logged next to the model's, so disagreements can be counted. The student's message is never
logged, only the tool names.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import ValidationError

from api.conversation import retrieval_query
from llm import ollama_client
from web_tools.router import CHI, CLUB_LIMIT, EVENT_LIMIT, WHERE, ToolCall, route
from web_tools.tools.schemas import (
    TOOL_SPECS, GetClubDetailsArgs, GetEventDetailsArgs, SearchClubsArgs, SearchEventsArgs,
)

logger = logging.getLogger("hawk")

ROUTING_PROMPT = """You are Hawk, the assistant of the Illinois Tech Office of Student Life (OSL). You help students with
student organizations: starting and running a club, officers, advisors, membership, events and event rules,
space reservations, the 312 platform, clubs and campus events, funding, the Finance Board, Buy It, purchases and
reimbursements, fundraising, travel, and OSL policies.

TOOLS
You have tools for looking things up. Today is {today} (Central time).
- search_handbook: the OSL student organization handbook (policies, procedures, rules, forms, contacts).
- search_clubs / get_club_details: the live list of clubs and groups on 312.iit.edu.
- search_events / get_event_details: the live calendar of campus events on 312.iit.edu.
Almost every message needs a tool call. Call no tool ONLY for: a greeting, thanks, a question about what you
can do, a reaction to your last reply that asks for nothing new (a complaint, a feeling, "ok"), or a request
unrelated to student organizations. Everything else gets a tool call, even a single word or a vague question.
Write the search text as a complete, standalone search: use the conversation to work out what a short follow-up
refers to ("what about X?", a bare word, "why?", "yes" to something you offered), and leave out words that are
not part of what to search for. Only if the student asks for two different things on 312 in one message (for
example a club and events), call two tools; otherwise call one.

CHOOSING THE TOOL
- If a tool could answer the question, call it now with your best reading of the question. Never ask the
  student a clarifying question first, and never reply "I can look that up" or "would you like me to search":
  call the tool instead. The student can narrow it down afterwards. If a detail is missing (which club, which
  dates, which rule), search anyway: questions about "my club", "our event" or "we" go to search_handbook, and
  event searches without dates use today to 14 days from today.
- A request unrelated to student organizations (for example a recipe, homework, or a recommendation for
  something off campus) gets no tool, even if it mentions a time, a place or an event. If a message mixes an
  OSL topic with something unrelated (cookies for a club event), search for the OSL part (food at club events).
- "Why?" or pushback about a rule asks for the rule's reason: search_handbook for that rule.
- Never answer a question about OSL rules, clubs or events from memory or from the conversation alone. Facts
  come only from a tool.
- If the student says you misunderstood, or asks again in other words, your earlier search was the wrong one:
  choose a different reading, usually a different tool.
- Rules, procedures, forms, money, deadlines, contacts and "how do we..." questions go to search_handbook,
  even when they mention an event, a weekend or a date.
- Whether a club or group exists, what it does, or how to join or contact it goes to search_clubs (use the name
  or abbreviation the student wrote) or get_club_details.
- What is happening on campus, and when, goes to search_events. A club's meetings are events too: search_events
  with the club's name. Always set both date_from and date_to (YYYY-MM-DD). For one day, use the same date for
  both. Use the calendar below: a day name means the next such day from today, and the named weeks and the
  weekend are listed with their dates. "Today", "tonight" and "right now" mean today only (date_from = date_to
  = today). If no time is mentioned, use today to 14 days from today.

CALENDAR
{calendar}"""
# Asked once more, appended to the prompt, when the model asked the student a question instead of calling a tool.
NUDGE = ("\n\nYour last reply asked the student a question instead of calling a tool. Do not ask: call the tool "
         "that fits your best reading of the message now. If you are unsure which tool fits, call search_handbook "
         "with the student's question written as a complete search. Call no tool only for a greeting, thanks, a "
         "question about what you can do, a reaction that asks for nothing new, or a request unrelated to student "
         "organizations.")

HANDBOOK_TOOL = {"type": "function", "function": {
    "name": "search_handbook", "description": "Search the OSL student organization handbook.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Standalone search text."}},
                   "required": ["query"]}}}
TOOLS = [HANDBOOK_TOOL] + [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                                            "parameters": s["input_schema"]}} for s in TOOL_SPECS]
ARG_CLASSES = {"search_clubs": SearchClubsArgs, "get_club_details": GetClubDetailsArgs,
               "search_events": SearchEventsArgs, "get_event_details": GetEventDetailsArgs}
DEFAULT_LIMITS = {"search_clubs": CLUB_LIMIT, "search_events": EVENT_LIMIT}

# temperature 0: the same conversation gets the same choice. Same num_ctx as the answers, or Ollama reloads.
# num_predict: two tool calls fit in well under 128 tokens; a text reply (thrown away anyway) is cut short, so a
# "no tool" turn doesn't spend a second writing an answer nobody sees.
PICK_OPTIONS = {"num_ctx": ollama_client.NUM_CTX, "temperature": 0, "seed": 42, "num_predict": 128}
HISTORY_MESSAGES = 6        # the last few messages are enough to resolve a follow-up
HISTORY_CHARS = 1500        # per message; a 312 list keeps its first results (names + links)
MAX_LOOKUPS = 2
DEFAULT_TIMEOUT_S = 5.0     # user's choice 2026-11-17: ~2x the slowest measured call (2.25 s), p99 1.46 s


@dataclass
class Pick:
    """What runs for this message. `calls` = 312 lookups (0-2); `handbook_query` = text for the handbook search
    (None = no handbook search). `source`: "model" | "fallback". `router` = the regex router's own choice."""
    calls: list[ToolCall] = field(default_factory=list)
    handbook_query: str | None = None
    source: str = "model"
    reason: str = ""
    secs: float = 0.0
    router: ToolCall | None = None

    @property
    def family(self) -> str:
        if self.calls:
            return "+".join(sorted({"clubs" if "club" in c.name else "events" for c in self.calls}))
        return "handbook" if self.handbook_query else "none"


def calendar_lines(today: date, days: int = 21) -> str:
    """The next 21 days with weekday names, plus the named windows students use, all computed here: models get
    weekday arithmetic wrong ("this weekend" -> Nov 14-16, "the week after next" -> two weeks)."""
    def span(a, b):
        return f"{a:%a %Y-%m-%d} to {b:%a %Y-%m-%d}"
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    weekend_start = max(today, monday + timedelta(days=5))   # on a Saturday or Sunday: the rest of this weekend
    week = timedelta(days=7)
    windows = [f"Today: {today:%a %Y-%m-%d}", f"Tomorrow: {today + timedelta(days=1):%a %Y-%m-%d}",
               f"This week (rest of it): {span(today, sunday)}", f"This weekend: {span(weekend_start, sunday)}",
               f"Next week: {span(monday + week, sunday + week)}",
               f"Next weekend: {span(monday + timedelta(days=12), sunday + week)}",
               f"The week after next: {span(monday + 2 * week, sunday + 2 * week)}"]
    days_list = [f"{(today + timedelta(days=i)):%a %Y-%m-%d}" + ("  (today)" if i == 0 else "") for i in range(days)]
    return "\n".join(windows + [""] + days_list)


def system_prompt(today: date) -> str:
    return (ROUTING_PROMPT.replace("{today}", f"{today:%A, %B} {today.day}, {today.year}")
            .replace("{calendar}", calendar_lines(today)))


def _messages(latest: str, history: list[dict], today: date) -> list[dict]:
    turns = [{"role": ollama_client.ROLE_MAP[t["role"]], "content": t["text"][:HISTORY_CHARS]}
             for t in history[-HISTORY_MESSAGES:]]
    return [{"role": "system", "content": system_prompt(today)}, *turns, {"role": "user", "content": latest}]


def ask_model(messages: list[dict], model: str, timeout: float) -> dict:
    """One Ollama chat call with the tools; returns the reply message ({"content", "tool_calls"})."""
    response = ollama_client._client.post(ollama_client.OLLAMA_URL, timeout=timeout, json={
        "model": model, "stream": False, "messages": messages, "tools": TOOLS, "options": PICK_OPTIONS,
        "keep_alive": ollama_client.KEEP_ALIVE, **ollama_client.think_fields(model),
    })
    response.raise_for_status()
    return response.json()["message"]


ANSWERED_CHARS = 150   # a text reply this long is an answer from memory, not a greeting or a one-line decline


def asks_back(content: str) -> bool:
    """The model wrote text instead of calling a tool, and the text asks the student something ("What date range
    are you interested in?") or answers from memory ("the policy is in place for food safety reasons..."). A
    greeting like "Hi! How can I help?" also counts; asking again costs one more call there."""
    return "?" in content or len(content.strip()) > ANSWERED_CHARS


def _args(raw) -> dict:
    if isinstance(raw, str):
        raw = json.loads(raw or "{}")
    return dict(raw or {})


def read_calls(message: dict, latest: str) -> tuple[list[ToolCall], str | None, str]:
    """The model's tool calls -> (312 lookups, handbook search text, note). Invalid ones are dropped."""
    calls, handbook, notes = [], None, []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        try:
            args = _args(fn.get("arguments"))
        except (ValueError, TypeError):
            notes.append(f"{name}: unreadable arguments")
            continue
        if name == "search_handbook":
            query = str(args.get("query") or "").strip()
            if query and handbook is None:
                handbook = query
            continue
        if name not in ARG_CLASSES:
            notes.append(f"unknown tool {name}")
            continue
        if name in DEFAULT_LIMITS:
            args.setdefault("limit", DEFAULT_LIMITS[name])
        try:
            ARG_CLASSES[name].model_validate(args)
        except ValidationError:
            notes.append(f"{name}: invalid arguments")
            continue
        call = ToolCall(name, args, intent="location" if WHERE.search(latest) else None)
        if name == "get_club_details":   # no exact club: show the word search's closest matches
            call.fallback = ToolCall("search_clubs", {"query": str(args["slug"]), "limit": CLUB_LIMIT})
        if all((c.name, c.args) != (call.name, call.args) for c in calls):
            calls.append(call)
    # Two lookups only as one club lookup + one event lookup ("an AI club or AI events"). Several of the same kind
    # is a fan-out (one get_event_details per event in the last list, seen for "any of those online?"): none of
    # them is what the student asked for, so that kind is dropped (nothing left -> the regex router decides).
    by_kind: dict[str, list[ToolCall]] = {}
    for c in calls:
        by_kind.setdefault(_kind(c.name), []).append(c)
    fanned = [k for k, cs in by_kind.items() if len(cs) > 1]
    if fanned:
        notes.append(f"several {'/'.join(fanned)} lookups: dropped")
    calls = [cs[0] for k, cs in by_kind.items() if len(cs) == 1][:MAX_LOOKUPS]
    if handbook and calls:   # both kinds: the handbook answer (written by the model) wins, the lookups are dropped
        notes.append("handbook + 312: handbook only")
        calls = []
    return calls, handbook, "; ".join(notes)


def _kind(tool: str) -> str:
    return "clubs" if "club" in tool else "events"


def fallback_pick(latest: str, history: list[dict], known: set[str] | None, today: date, reason: str,
                  router: ToolCall | None) -> Pick:
    """Today's rules: the regex router, and today's handbook search text when it found no lookup."""
    if router is not None:
        return Pick(calls=[router], source="fallback", reason=reason, router=router)
    return Pick(handbook_query=retrieval_query(latest, history), source="fallback", reason=reason, router=router)


def _describe(calls: list[ToolCall], handbook: str | None) -> str:
    return "+".join(c.name for c in calls) or ("search_handbook" if handbook else "none")


def pick(latest: str, history: list[dict] | None, model: str, known: set[str] | None = None,
         today: date | None = None, timeout: float = DEFAULT_TIMEOUT_S, ask=None) -> Pick:
    """Never raises. `ask(messages, model, timeout) -> message` is the model call (tests pass a fake)."""
    history = history or []
    today = today or datetime.now(CHI).date()
    try:
        router = route(latest, known, today, history)
    except Exception:
        logger.exception("regex router failed")
        router = None
    t0 = time.perf_counter()
    try:
        messages = _messages(latest, history, today)
        message = (ask or ask_model)(messages, model, timeout)
        if not message.get("tool_calls") and asks_back(message.get("content") or ""):
            # gemma asks "What date range?" instead of searching, despite the prompt: once more, told not to ask
            messages[0] = {**messages[0], "content": messages[0]["content"] + NUDGE}
            message = (ask or ask_model)(messages, model, max(0.1, timeout - (time.perf_counter() - t0)))
        secs = time.perf_counter() - t0
        calls, handbook, note = read_calls(message, latest)
        if not message.get("tool_calls"):
            result = Pick(handbook_query=None, source="model", reason="no tool", secs=secs, router=router)
        elif calls or handbook:
            result = Pick(calls=calls, handbook_query=handbook, source="model", reason=note, secs=secs, router=router)
        else:
            result = fallback_pick(latest, history, known, today, f"nothing usable ({note})", router)
            result.secs = secs
    except Exception as exc:   # Ollama down, over the time limit, a malformed reply
        result = fallback_pick(latest, history, known, today, f"call failed ({exc.__class__.__name__})", router)
        result.secs = time.perf_counter() - t0
    logger.info("search picker: %s [%s%s] %.2fs | regex router: %s", _describe(result.calls, result.handbook_query),
                result.source, f": {result.reason}" if result.reason else "", result.secs,
                router.name if router else "handbook")
    return result
