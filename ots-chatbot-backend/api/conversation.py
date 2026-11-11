"""Multi-turn helpers: pure functions, no I/O.

The widget sends the whole conversation on every request, but retrieval used to see only the
latest message. A follow-up like "what's the deadline for that?" embeds to nothing useful, so
for messages that lean on earlier turns we build the retrieval query from those turns too.
"""
from __future__ import annotations

import re

# Messages that start by continuing the previous topic ("and ...", "what about ...").
OPENER = re.compile(r"^\s*(?:ok(?:ay)?[, ]+)?(?:and|also|but|so|then|what about|how about|what else|why|more)\b", re.I)
# Pronouns that only make sense with an antecedent. ("there"/"one"/"this" are left out on
# purpose: "is there...", "this week" appear in complete questions.)
PRONOUN = re.compile(r"\b(?:it|its|it's|that|those|them|they|their|these)\b", re.I)
LIVE_ITEM = re.compile(r"^- \*\*(.+?)\*\* — ", re.M)   # a bulleted 312 result in a previous bot answer
FOLLOWUP_MAX_WORDS = 12
SHORT_QUERY_CHARS = 80


def is_followup(text: str) -> bool:
    """True when the message probably can't be understood without the turns before it."""
    if OPENER.match(text):
        return True
    return len(text.split()) <= FOLLOWUP_MAX_WORDS and bool(PRONOUN.search(text))


def _last(history: list[dict], role: str) -> str:
    return next((m["text"] for m in reversed(history) if m["role"] == role), "")


def retrieval_query(latest: str, history: list[dict] | None) -> str:
    """The text to embed for handbook retrieval.

    Standalone questions are returned unchanged. For follow-ups, the previous student message
    is prepended, plus the names of any 312 results in the last answer; for a short query about a
    prose answer, that answer's first sentence too.
    """
    if not history or not is_followup(latest):
        return latest
    prev = _last(history, "user")
    if not prev:
        return latest
    bot = _last(history, "bot")
    names = LIVE_ITEM.findall(bot)[:2]        # "tell me about the second one" names nothing by itself
    query = f"{' '.join(names)} {prev} {latest}" if names else f"{prev} {latest}"
    if not names and len(query) < SHORT_QUERY_CHARS:     # prose answer: its opening sentence names the topic
        bot = bot.strip()
        first = re.split(r"(?<=[.!?])\s|\n", bot, maxsplit=1)[0] if bot else ""
        if first and "312" not in first:
            query = f"{first[:150]} {query}"
    return query
