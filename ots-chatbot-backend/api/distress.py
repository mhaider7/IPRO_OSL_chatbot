"""Distress check: runs first, before the 312 router, the fixed short replies and the off-topic check.

Why it exists: a crisis message isn't about student organizations, so the off-topic check refused most of them
("I want to kill myself" -> "I can only help with student organizations…"), the router sent others to a 312
search ("I'm going to hurt someone at the event tonight" -> "I didn't find any matching events"), and the model
wrote whatever it wrote (CHATBOT_BEHAVIOR.md 4.15). A student in crisis gets a fixed, checked text instead: the
988 Suicide & Crisis Lifeline (call, text or chat) and 911 for immediate danger. Never model-written.

How a message is judged (`DistressCheck.check`), in this order:
  1. The message itself sounds like distress (phrase patterns, or close to an example sentence) -> support reply
  2. Hawk's last reply was the support reply or the follow-up:
       a clear OSL question (off-topic margin clearly on-topic) -> normal path (None)
       anything else ("ok", "thanks", "I'm fine", pushback, an off-topic request) -> the short follow-up
  3. Otherwise -> None (the normal checks run)

Tuned for recall: missing a crisis is worse than an extra support reply (the opposite of the off-topic check).
Distress + an OSL question in the same message gets the support reply only (decided 2026-11-16); the reply ends
by saying Hawk is still there for OSL questions. OSL questions about mental-health topics ("a suicide prevention
walk") must not trigger: those phrases are removed before matching.

The example sentences are compared with the same MiniLM embeddings the handbook search and the off-topic check
use. MiniLM is English-only, so is this check (a known limit). If anything here fails, the message is not
treated as distress and the error is logged; the student's message itself is never logged.
Measured on eval/distress (development set, 2026-11-16; CHATBOT_BEHAVIOR.md 4.15-4.16).
"""
import logging
import re
import threading
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("hawk")

# --- the fixed texts (wording decided with the user 2026-11-16; facts checked on 988lifeline.org) -------------
SUPPORT_OPENING = "I'm really sorry you're going through this, and I'm glad you said something."
SUPPORT_TEXT = (SUPPORT_OPENING + " You don't have to handle it alone. You can call or text 988, the Suicide & "
                "Crisis Lifeline, or chat at 988lifeline.org, any time, day or night. It's free and confidential, "
                "and it's there if you're worried about someone else, too. If you or someone else is in immediate "
                "danger, call 911. I'm still here whenever you have a question about student organizations or OSL.")
FOLLOWUP_OPENING = "I'm here. If things feel heavy, please call or text 988"
FOLLOWUP_TEXT = (FOLLOWUP_OPENING + ", or chat at 988lifeline.org; talking to someone can really help. If anyone "
                 "is in immediate danger, call 911. Whenever you're ready, I can help with anything about student "
                 "organizations or OSL.")

# --- thresholds -------------------------------------------------------------------------------------------------
NEAR_DISTRESS = 0.50   # cosine distance to the closest example sentence below this -> distress. Dev 2026-11-16:
                       # distress messages 0.15-0.83 (median 0.46), closest normal message 0.54. The patterns do
                       # most of the work; the examples catch wordings the patterns don't know.
CLEARLY_ON = -0.10     # after a support reply: off-topic margin below this = a clear OSL question (same as guardrail)

# --- phrase patterns (lower-case text, curly apostrophes straightened) ------------------------------------------
_ME = r"(i|i'm|im|i am|i'll|ill|i will|i've|ive|i'd|i feel|i'm feeling|feeling|i've been|ive been|i keep)"
_ADV = r"((so|really|very|pretty|completely|kind of|kinda|just|all|too|always|honestly|super) )*"
PATTERNS = [
    # suicide and self-harm, stated directly
    r"\bsuicid(e|al)\b",
    r"\bself[- ]?harm",
    r"\b(kill|killing|hurt|hurting|harm|harming|cut|cutting|burn|burning|hang|hanging|starve|starving) ?my ?self\b",
    r"\b(kill|killing|hurt|hurting|harm|harming|cut|cutting|burn|burning) (him|her|them)sel(f|ves)\b",
    r"\b(end|ending|take|taking) (my|my own) life\b",
    r"\bend(ing)? it all\b",
    rf"\b{_ME}\b[^.?!]{{0,30}}\b(end|ending) it\b",
    r"\b(want|wants|wanted|wanna|going|gonna|ready|plan|planning|deserve) (to )?die\b",
    r"\bwish (i|i'd|i had) (was|were|been|never been) (dead|born)\b",
    r"\bbetter off (dead|without me)\b",
    r"\b(don't|dont|do not|no longer) want to (live|be alive|exist|be here|wake up)\b",
    r"\b(reason|point) (to|of|in) (live|living|being alive)\b",
    r"\bnot worth living\b|\b(isn't|isnt|is not) worth (living|it anymore)\b",
    r"\btired of (being alive|living|life)\b",
    r"\b(overdose|overdosing|od on)\b",
    r"\b(all|a bunch of|enough) (my |of my |sleeping )?pills\b|\bsleeping pills\b",
    r"\b(jump|jumping) off\b|\bon the (roof|ledge|edge) of\b",
    r"\b(not|never|don't|dont) wake up\b",
    r"\b(goodbye|suicide) (note|letter|message)\b",
    r"\b(not|won't|wont) (be )?(here|around) (tomorrow|much longer|anymore)\b|\bbe (here|around) much longer\b",
    # hopeless / burden / disappearing
    r"\b(notice|care|miss me)\b[^.?!]{0,15}\bif i (was|were|wasn't|wasnt|weren't) (gone|here|around)\b",
    r"\bif i (wasn't|wasnt|weren't|was not) (here|around|alive)\b",
    r"\bburden (to|on|for) (everyone|everybody|my|people|them)\b|\blike a burden\b",
    r"\b(disappear|disappearing|vanish)\b[^.?!]{0,15}\b(for good|forever)\b|\bwant to (disappear|vanish)\b",
    r"\b(can't|cant|cannot) (do this|go on|keep going|take (it|this)|handle (it|this|life))( any ?more| like this)\b",
    r"\b(can't|cant|cannot) go on\b",
    r"\bdone with (everything|life|it all)\b|\bgive up on (everything|life)\b",
    r"\b(want|need) (everything|it all|it|the pain) to stop\b",
    r"\bno way out\b|\bnothing matters( any ?more)?\b",
    # added after the first held-out run (2026-11-16; misses in CHATBOT_BEHAVIOR.md 4.18)
    r"\b(been|keep|kept|started|still|i'm|im) (cutting|burning)\b(?! (costs?|back|down|out|the|our|my|a|an|it|"
    r"corners|class|classes|ties|paper|wood|fabric|hair|budget|expenses))",
    r"\b(cutting|burning) (myself )?again\b",
    r"\bstruggling (so much|a lot|really bad|badly|so bad|right now|lately|every day)\b(?! (with|to|on|in))",
    r"\b(i'm|im|i am) (really |so |just )?struggling[.!]*$",
    r"\b(don't|dont|do not) care (any ?more|about anything|about (my )?life|if i (die|live|wake up))\b",
    r"\b(haven't|havent|have not) (eaten|slept) in (days|a week|weeks)\b",
    r"\b(won't|wont|will not) matter (anyway|soon|any ?more|after (tonight|tomorrow))\b",
    r"\b(bought|got|have|tied|found) a (rope|noose)\b(?! (for|to (use|climb)|ladder|course))|\bnoose\b",
    # a few words between "I'm" and a strong feeling ("I'm new and really lonely"), but not about other people
    # ("I'm starting a club for lonely students", "I'm hosting a panel about depressed students")
    r"\b(i'm|im|i am|i feel|feeling)\b(?:(?!\b(for|about|help|helping|support|supporting|with|to)\b)[^.?!]){0,25}"
    r"\b(lonely|depressed|hopeless|miserable|worthless|suicidal)\b"
    r"(?! (and |or )?(students?|people|freshmen|members|peers|kids|folks|international|transfer|new|first|ones))",
    r"^(what's|whats|what is) the point( of (anything|it all|life|living|trying))?[.?!]*$",
    # low mood (support reply too, decided 2026-11-16)
    rf"\b{_ME} {_ADV}(depressed|hopeless|worthless|empty|lonely|isolated|alone|miserable|numb|broken|"
    rf"sad|down|not ok|not okay|like (a failure|nothing|giving up))\b",
    r"\b(i'm|im|i am) not (ok|okay)\b",
    r"\b(been|keep|can't stop|cant stop) crying\b|\bcrying (every|all the) (day|night|time)\b",
    # hurting someone else (911)
    rf"\b{_ME}\b[^.?!]{{0,25}}\b(hurt|kill|shoot|stab|attack)\b (someone|somebody|people|everyone|everybody|"
    r"them|him|her|the people|my [a-z]+|a [a-z]+)\b",
]
_PATTERNS = [re.compile(p) for p in PATTERNS]
# OSL topics that contain a distress word; removed before matching so they get the normal answer.
TOPIC_PHRASES = re.compile(r"\b(suicide|self[- ]?harm|depression|mental health)[- ](prevention|awareness|"
                           r"walk|charity|fundraiser|event|week|month|training|panel|speaker|talk|workshop)\b")

# --- example sentences (none shared with eval/distress/cases.py) ------------------------------------------------
EXAMPLES = [
    "I don't want to wake up tomorrow.",
    "I think about ending things a lot.",
    "Everyone would be better off if I disappeared.",
    "I feel like there's no reason to keep going.",
    "I'm thinking about taking my own life.",
    "I don't see a future for myself.",
    "I hurt myself when things get bad.",
    "I can't take the pain anymore.",
    "I'm planning to end everything.",
    "My life is pointless.",
    "No one would care if I died.",
    "I feel so hopeless about everything.",
    "I've been feeling really depressed and alone.",
    "I'm having thoughts of suicide.",
    "I don't want to exist anymore.",
    "I'm scared I might do something to myself.",
    "My friend says they want to kill themselves.",
    "I'm worried my friend is going to hurt themself.",
    "I feel numb and empty inside.",
    "Nothing will ever get better for me.",
    "I just want to go to sleep forever.",
    "I feel like nobody cares about me.",
    "I'm going to hurt somebody.",
    "I have nobody to talk to and I feel awful.",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'").lower()).strip()


def matches_pattern(text: str) -> str | None:
    """The first distress pattern the message matches, or None."""
    t = TOPIC_PHRASES.sub(" ", normalize(text))
    for p in _PATTERNS:
        if p.search(t):
            return p.pattern
    return None


def is_support(text: str) -> bool:
    """Hawk's last reply was the support reply or the follow-up."""
    return text.startswith(SUPPORT_OPENING) or text.startswith(FOLLOWUP_OPENING)


@dataclass
class DistressVerdict:
    reply: str | None   # the fixed text to send, or None = carry on with the normal checks
    reason: str         # which rule decided (for logs and tests; never the message)


class DistressCheck:
    """`embed(texts) -> vectors` (the handbook's embedding function, or None = patterns only),
    `on_margin(text) -> float` (the off-topic check's margin, or None). Tests pass fakes."""

    def __init__(self, embed=None, on_margin=None):
        self._embed, self._on_margin = embed, on_margin
        self._examples = None
        self._lock = threading.Lock()

    def _unit(self, texts):
        v = np.array(self._embed(list(texts)), dtype=float)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def example_distance(self, text: str) -> float:
        """Cosine distance to the closest example sentence (1.0 without embeddings)."""
        if self._embed is None:
            return 1.0
        with self._lock:   # embedded once, on first use
            if self._examples is None:
                self._examples = self._unit(EXAMPLES)
        v = self._unit([text])[0]
        return float((1 - self._examples @ v).min())

    def is_distress(self, text: str) -> str | None:
        """Why the message counts as distress ("pattern" / "example"), or None."""
        if matches_pattern(text):
            return "pattern"
        if TOPIC_PHRASES.search(normalize(text)):   # an OSL question about a mental-health topic
            return None
        if self.example_distance(text) < NEAR_DISTRESS:
            return "example"
        return None

    def check(self, latest: str, history: list[dict] | None) -> DistressVerdict:
        """Never raises: if the check fails, the message goes on to the normal checks (and the error is logged)."""
        try:
            return self._check(latest, history or [])
        except Exception:
            logger.exception("distress check failed; continuing with the normal checks")
            return DistressVerdict(None, "check-failed")

    def _check(self, latest: str, history: list[dict]) -> DistressVerdict:
        why = self.is_distress(latest)
        if why:
            return DistressVerdict(SUPPORT_TEXT, why)
        last_bot = next((t["text"] for t in reversed(history) if t["role"] == "bot"), None)
        if last_bot is not None and is_support(last_bot):
            if self._clear_osl_question(latest):
                return DistressVerdict(None, "osl-question-after-support")
            return DistressVerdict(FOLLOWUP_TEXT, "after-support")
        return DistressVerdict(None, "no-distress")

    def _clear_osl_question(self, text: str) -> bool:
        """After a support reply: is this clearly an OSL question? If that can't be told, no (-> the follow-up,
        never the off-topic refusal)."""
        if self._on_margin is None:
            return False
        try:
            return self._on_margin(text) < CLEARLY_ON
        except Exception:
            logger.exception("off-topic margin failed after a support reply; sending the follow-up")
            return False
