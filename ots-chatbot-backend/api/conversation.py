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


# ---- short replies ("yes", "tell me more", "thanks") -----------------------------------------------------
# A whole message made only of these phrases is a short reply to Hawk's last message. Searching the handbook
# for the word "yes" finds nothing, so these get special handling (see short_reply / retrieval_query).
_PHRASES = {
    "yes": ["yes", "yeah", "yep", "yup", "yea", "ya", "sure", "ok", "okay", "k", "alright", "all right", "definitely",
            "absolutely", "of course", "sounds good", "that works", "go ahead", "please do", "do it", "i would",
            "i'd like that", "id like that", "that would help", "that would be great", "that'd be great", "please"],
    "ack": ["got it", "gotcha", "cool", "great", "nice", "perfect", "awesome", "makes sense", "understood", "good",
            "good to know", "i see", "noted"],
    "thanks": ["thanks", "thank you", "thx", "ty", "thank you so much", "thanks a lot", "appreciate it",
               "much appreciated", "so much"],
    "more": ["tell me more", "more", "go on", "continue", "keep going", "more details", "more info", "details",
             "explain", "elaborate", "explain more", "how", "why"],
    "no": ["no", "nope", "nah", "not now", "i'm good", "im good", "that's all", "thats all", "that's it",
           "thats it", "all good", "never mind", "nevermind"],
}
_PHRASE_LIST = sorted(((p, cat) for cat, ps in _PHRASES.items() for p in ps), key=lambda x: -len(x[0]))
SHORT_REPLY_MAX_WORDS = 6

# An offer from Hawk: "Would you like ...?", "Let me know if you'd like ...", "I can also explain ...".
OFFER = re.compile(r"\b(would you like|do you want|want me to|shall i|should i (?:explain|list|go over)|"
                   r"let me know if|if you'?d like|if you would like|if you want|i can (?:also )?(?:explain|list|go over|"
                   r"walk you through|tell you|help you with))\b", re.I)
# Closing lines that offer nothing specific: a "yes" to these isn't asking for anything.
GENERIC_OFFER = re.compile(r"\b(anything else|any other|other questions|any (?:more |further )?questions|"
                           r"further (?:assistance|help|questions)|more questions|feel free to ask|"
                           r"(?:something|anything) specific|what'?s on your mind|how can i help|"
                           r"what (?:can i help|would you like to know|do you want to know))\b", re.I)

ACK_REPLY = "Glad that helps! Is there anything else I can help you with about student organizations or OSL?"
THANKS_REPLY = "You're welcome! Let me know if you have any other questions about student organizations or OSL."
DECLINE_REPLY = "No problem! Let me know if there's anything else I can help with."
PROMPT_REPLY = "Sure! What would you like to know about student organizations or OSL?"
FIXED_REPLIES = {"ack": ACK_REPLY, "thanks": THANKS_REPLY, "decline": DECLINE_REPLY, "prompt": PROMPT_REPLY}


def _reply_categories(text: str) -> set[str] | None:
    """The phrase categories a message is made of, or None if it contains anything else."""
    words = re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'")).split()
    if not words or len(words) > SHORT_REPLY_MAX_WORDS:
        return None
    rest, cats = " ".join(words), set()
    while rest:
        for phrase, cat in _PHRASE_LIST:
            if rest == phrase or rest.startswith(phrase + " "):
                cats.add(cat)
                rest = rest[len(phrase):].strip()
                break
        else:
            return None
    return cats


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def what_hawk_asked(bot_text: str) -> tuple[str | None, bool]:
    """(the sentence a short reply answers, whether it is an offer). Generic closers ("any other questions?")
    don't count. An offer can lack a "?" ("Let me know if you'd like the list.")."""
    sentences = [s for s in _sentences(bot_text) if not GENERIC_OFFER.search(s)]
    for s in reversed(sentences):
        if OFFER.search(s):
            return s, True
    if sentences and sentences[-1].endswith("?"):
        return sentences[-1], False
    return None, False


def _hands_back(bot_text: str) -> bool:
    """Hawk's last message ends by handing the conversation back ("Is there something specific you'd like
    help with?", "What's on your mind?"): a question, but not one a "yes" can answer."""
    sentences = _sentences(bot_text)
    return bool(sentences) and sentences[-1].endswith("?") and bool(GENERIC_OFFER.search(sentences[-1]))


def short_reply(latest: str, history: list[dict] | None) -> str | None:
    """What a short reply means, given Hawk's last message:
    "answer" = agrees to Hawk's offer or answers its question (search what Hawk asked),
    "more"   = wants more on the same topic, no offer to agree to (search the previous topic),
    "ack" / "thanks" / "decline" / "prompt" = nothing to look up (fixed reply; "prompt" = a bare "yeah" to
               "is there something specific you'd like help with?", so ask what they'd like to know),
    None     = not a short reply (or no earlier conversation): handle normally."""
    cats = _reply_categories(latest)
    if not cats or not history or history[-1]["role"] != "bot":
        return None
    asked, is_offer = what_hawk_asked(history[-1]["text"])
    if cats <= {"yes"} and not asked and _hands_back(history[-1]["text"]) and not re.search(r"\bplease\b", latest, re.I):
        return "prompt"
    if "no" in cats:
        return "answer" if asked and not is_offer else "decline"   # "no" to "Will 50+ people attend?" is information
    if "more" in cats:
        return "answer" if asked else "more"
    if "yes" in cats:
        if asked:
            return "answer"
        return "more" if re.search(r"\bplease\b", latest, re.I) else ("thanks" if "thanks" in cats else "ack")
    return "thanks" if "thanks" in cats else "ack"


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
    kind = short_reply(latest, history)
    if kind in ("answer", "more"):
        # The short reply itself ("yes") says nothing: search for what it refers to instead.
        prev = _last(history, "user")
        if kind == "answer":
            asked, _ = what_hawk_asked(history[-1]["text"])
            return f"{asked} {prev}".strip()
        sentences = _sentences(history[-1]["text"])
        return f"{sentences[0] if sentences else ''} {prev}".strip()
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
