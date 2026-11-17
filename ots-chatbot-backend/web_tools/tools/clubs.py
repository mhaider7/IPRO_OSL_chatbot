"""search_clubs / get_club_details: filtering is ours (the site has no search)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from web_tools.parsers.clubs_html import Club, LayoutChanged, parse_clubs_listing

CLUBS_URL = "https://312.iit.edu/club_signup?view=all"

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "in", "on", "at", "to", "about", "with", "any", "some",
    "is", "are", "there", "do", "does", "you", "have", "has", "i", "me", "my", "we", "us", "can", "what",
    "which", "who", "where", "how", "find", "show", "list", "recommend", "looking", "interested",
    "interest", "join", "get", "want", "like", "that", "this", "club", "clubs", "group", "groups",
    "organization", "organizations", "org", "orgs", "student", "students", "campus", "iit", "illinois",
    "tech", "please", "tell", "give", "need", "ones", "type", "kind", "kinds", "related", "related",
}

# Small synonym map so common interests hit the listing's own vocabulary.
ALIASES = {
    "esports": ["e-sports", "gaming", "video game"],
    "gaming": ["esports", "video game"],
    "game": ["gaming", "esports", "games"],
    "coding": ["programming", "software", "computer science", "developers"],
    "programming": ["coding", "software", "computer science", "developers"],
    "music": ["band", "orchestra", "choir", "choral", "a cappella", "singing", "string"],
    "dance": ["dancing"],
    "sports": ["sports and recreation", "recreation", "athletic", "athletics", "fitness"],
    "sport": ["sports and recreation", "recreation", "athletic", "athletics", "fitness"],
    "athletic": ["sports and recreation", "athletics"],
    "volunteering": ["community service", "volunteer"],
    "volunteer": ["community service", "volunteering"],
    "faith": ["faith and spirituality", "spiritual", "religious", "religion"],
    "religious": ["faith and spirituality", "spirituality", "faith", "religion"],
    "religion": ["faith and spirituality", "spirituality", "faith", "religious"],
    "spiritual": ["faith and spirituality", "faith", "religious"],
    "art": ["arts and media", "arts", "media"],
    "theater": ["theatre", "drama", "productions"],
    "fraternity": ["fraternity/sorority", "greek", "sorority"],
    "sorority": ["fraternity/sorority", "greek", "fraternity"],
    "greek": ["fraternity/sorority", "fraternity", "sorority"],
    "premed": ["medical", "health", "pre-health"],
    "pre-med": ["medical", "health", "pre-health"],
    "outdoors": ["outdoor", "hiking", "nature"],
    "outdoor": ["outdoors", "hiking", "nature"],
    "hiking": ["outdoor", "outdoors", "nature"],
    "cultural": ["culture", "heritage"],
    "business": ["professional organization", "entrepreneurship", "management", "marketing", "finance"],
    "engineering": ["engineers", "engineer"],
    "international": ["global", "abroad"],
}


def tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#&'-]*", query.lower().replace("\u2019", "'"))
    words = [w[:-2] if w.endswith("'s") else w for w in words]          # what's -> what
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 5:
        return w[:-3] + "y"                                             # fraternities -> fraternity
    for suf in ("ing", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


ALIASES = {**ALIASES, **{_stem(k): v for k, v in ALIASES.items()}}   # lookups use stemmed tokens


def _word_prefix(stem: str, text: str) -> bool:
    end = r"\b" if len(stem) <= 3 else ""      # 'ai' must not match 'aid'/'air'
    return re.search(r"\b" + re.escape(stem) + end, text) is not None


def score(club: Club, toks: list[str]) -> tuple[int, int]:
    """(distinct tokens matched, weighted score). Name > categories/type > mission > benefits."""
    name, cats = club.name.lower(), (club.type + " " + " ".join(club.categories)).lower()
    mission, ben = club.mission.lower(), club.membership_benefits.lower()
    matched = total = 0
    for t in toks:
        variants = [t] + [_stem(a) for a in ALIASES.get(t, [])]
        w = 0
        for v in variants:
            w = max(w, 5 if _word_prefix(v, name) else 0, 3 if _word_prefix(v, cats) else 0,
                    2 if _word_prefix(v, mission) else 0, 1 if _word_prefix(v, ben) else 0)
        if w:
            matched += 1
            total += w
            # A club *named* for the interest beats one that merely mentions it.
            if any(name.startswith(v) for v in variants):
                total += 2
            if _stem(name) == t:
                total += 3
    return matched, total


def _cut(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def page_url(club: Club) -> str:
    return f"https://312.iit.edu/{club.slug}/" if club.slug else club.website_url


def club_summary(c: Club, mission_n: int = 400, benefits_n: int = 300) -> dict:
    return {
        "slug": c.slug, "club_id": c.club_id, "name": c.name, "type": c.type,
        "categories": list(c.categories), "page_url": page_url(c),
        "mission": _cut(c.mission, mission_n), "membership_benefits": _cut(c.membership_benefits, benefits_n),
        "contact_name": c.contact_name, "flags": list(c.flags),
    }


def filter_clubs(clubs: list[Club], query=None, category=None, type=None) -> list[Club]:
    pool = clubs
    if type:
        t = type.casefold()
        exact = [c for c in pool if c.type.casefold() == t]
        pool = exact or [c for c in pool if t in c.type.casefold()]
    if category:
        cat = category.casefold()
        pool = [c for c in pool if any(cat in x.casefold() for x in c.categories) or cat in c.type.casefold()]
    toks = tokens(query) if query else []
    if toks:
        scored = []
        for c in pool:
            matched, total = score(c, toks)
            if matched:
                scored.append((matched, total, c))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2].name.lower()))
        if scored:
            best = (scored[0][0], scored[0][1])
            # Drop weak matches (e.g. a single passing mention) when strong ones exist.
            scored = [x for x in scored if x[0] == best[0] and x[1] >= max(2, best[1] * 0.4)]
        return [c for _, _, c in scored]
    return sorted(pool, key=lambda c: c.name.lower())


_NAME_SUFFIX = re.compile(r"\s+(club|team|organi[sz]ation|society|association|group)s?$", re.I)


def _norm_name(name: str) -> str:
    name = re.sub(r"^the\s+", "", _NAME_SUFFIX.sub("", name.strip()), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def find_club(clubs: list[Club], ident: str) -> Club | None:
    key = ident.strip().casefold()
    for c in clubs:
        if key in {(c.slug or "").casefold(), c.club_id, c.name.casefold()}:
            return c
    norm = _norm_name(ident)
    if norm:   # 'Boxing' or 'the Boxing Club' -> 'Boxing Club'; only when unambiguous
        hits = [c for c in clubs if _norm_name(c.name) == norm]
        if len(hits) == 1:
            return hits[0]
    return None


def close_matches(clubs: list[Club], ident: str, n: int = 3) -> list[Club]:
    return filter_clubs(clubs, query=ident)[:n]


# ---- club candidates: the name a student (or the model) wrote -> real 312 clubs ---------------------------------
# Students write "ML", "the SWE chapter", "hawkapela" or "formula one club". Exact-name lookup misses all of these,
# and the meaning-based (MiniLM) search doesn't help with abbreviations ("ML" is as close to Mathematics Club as to
# Machine Learning). So: exact name / slug / id -> the same name written differently -> initials built from every
# club name -> a small typo. Only clubs on the 312 list are ever returned. Several equally good matches are all
# returned, so the student can be asked which one ("ISA" = Indian or Iranian Student Association).
NOISE = {"club", "clubs", "team", "teams", "chapter", "chapters", "org", "orgs", "organization", "organizations",
         "group", "groups", "page", "pages", "website", "site", "link", "312", "the", "iit", "illinois", "tech",
         "info", "official", "at", "on", "campus", "here"}
_INITIALS_SKIP = {"of", "and", "the", "for", "at", "in", "a", "an", "to", "on"}
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10"}
MAX_CANDIDATES = 6


@dataclass
class ClubMatch:
    kind: str            # "exact" | "name" | "initials" | "typo" | "sound" | "none"
    clubs: list          # best first; one = a clear match, several = ask which one
    asked: str | None = None   # the key words that matched, when only part of a sentence did

    @property
    def clear(self) -> bool:
        return len(self.clubs) == 1


def name_key(text: str) -> str:
    """'the ML club's 312 page' -> 'ml'; 'Formula One Club' -> 'formula 1'. Same function for names and input."""
    t = (text or "").lower().replace("’", "'").replace("'s", "")
    words = re.findall(r"[a-z0-9]+", t)
    return " ".join(_NUMBER_WORDS.get(w, w) for w in words if w not in NOISE)


def initials(name: str) -> set[str]:
    """Abbreviations a club is likely known by: first letters of its words (with and without a trailing
    'Club'/'Team'), plus any abbreviation written in brackets in its name."""
    keys = {m.upper() for m in re.findall(r"\(([A-Za-z][A-Za-z0-9&]{1,7})\)", name)}
    words = [w for w in re.findall(r"[A-Za-z0-9]+", re.sub(r"\(.*?\)", " ", name)) if w.lower() not in _INITIALS_SKIP]
    for ws in (words, words[:-1] if words and words[-1].lower() in ("club", "team") else []):
        if len(ws) >= 2:
            keys.add("".join(w[0] if not w.isdigit() else w for w in ws).upper())
    return keys


def display_ident(ident: str, kind: str) -> str:
    """How to quote what the student asked for: 'ML' for "the ML club's 312 page" (matched by initials), else the
    text as written (or the key words that matched)."""
    key = name_key(ident)
    return key.upper() if kind == "initials" and re.fullmatch(r"[a-z0-9]{2,6}", key) else ident.strip()


def _distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (limit + 1) once it can't be <= limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _typo_limit(key: str) -> int:
    return 0 if len(key) < 5 else 1 if len(key) < 9 else 2


def skeleton(key: str) -> str:
    """How a name sounds, roughly: the first letter, then the consonants without h/w/y, doubled letters collapsed.
    'hawkuhpela' and 'hawkapella' -> 'hkpl'. Catches names spelled the way they sound."""
    s = re.sub(r"[^a-z0-9]", "", key)
    if not s:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s")):   # same sounds
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s[0] + re.sub(r"[aeiouyhw]", "", s[1:]))


# Words around a name in a sentence ("is there a ... club", "maybe it was ..."): dropped before the second try.
FILLER = STOPWORDS | {"maybe", "it", "was", "its", "called", "named", "think", "im", "mean", "meant", "about",
                      "know", "whats", "still", "around", "active", "exist", "exists", "here", "chapter"}


def core_words(ident: str) -> str:
    """'Is there a hawkuhpela club?' -> 'hawkuhpela': the words left after the sentence around a name."""
    return " ".join(w for w in name_key(ident).split() if w not in FILLER)


def candidates(clubs: list[Club], ident: str) -> ClubMatch:
    """Real 312 clubs for a name, abbreviation or misspelling (see the comment above). If the whole text matches
    nothing, it's tried once more with only its key words, so a sentence ("Is there a hawkuhpela club?") works too."""
    match = _candidates(clubs, ident, ident)
    if match.kind == "none":
        core = core_words(ident)
        if core and core != name_key(ident):
            match = _candidates(clubs, core, ident)
            match.asked = core
    return match


def _typed_upper(key: str, original: str) -> bool:
    """The student wrote this word in capitals ("ML", "ACS"): an abbreviation, even if it's also a word."""
    return any(w.isupper() and len(w) > 1 and w.lower() == key for w in re.findall(r"[A-Za-z0-9]+", original))


def _candidates(clubs: list[Club], ident: str, original: str) -> ClubMatch:
    key = name_key(ident)
    abbrev = key.upper() if re.fullmatch(r"[a-z0-9]{2,6}", key) else None
    exact = find_club(clubs, ident)
    if exact and not abbrev:
        return ClubMatch("exact", [exact])
    if not key:
        return ClubMatch("exact", [exact]) if exact else ClubMatch("none", [])
    same = [c for c in clubs if name_key(c.name) == key or name_key(c.slug or "") == key]
    if exact or same:
        found = [exact] if exact else []
        found += [c for c in same if c not in found]
        if abbrev:   # a slug like "ACS" can also be another club's initials: show both
            found += [c for c in clubs if abbrev in initials(c.name) and c not in found]
        return ClubMatch("exact" if exact else "name", found[:MAX_CANDIDATES])
    if abbrev:
        # A lower-case real word ("art", "chess") that appears in a club name is a word, not initials.
        is_word = not _typed_upper(key, original) and any(re.search(rf"\b{re.escape(key)}\b", c.name.lower())
                                                           for c in clubs)
        hits = [] if is_word else [c for c in clubs if abbrev in initials(c.name)]
        if hits:
            return ClubMatch("initials", hits[:MAX_CANDIDATES])
    words = None   # the word search, computed only if a typo or sound-alike needs checking against it

    def word_search_agrees(hits):
        # A real word is not a misspelling when the word search has a better answer ("sports" is not "Esports").
        nonlocal words
        words = filter_clubs(clubs, query=ident) if words is None else words
        return not words or words[0] in hits

    limit = _typo_limit(key)
    if limit:
        scored = []
        for c in clubs:
            keys = {name_key(c.name), name_key(c.slug or "")} - {""}
            d = min((_distance(key, k, limit) for k in keys), default=limit + 1)
            if d <= limit:
                scored.append((d, c))
        if scored:
            best = min(d for d, _ in scored)
            hits = [c for d, c in scored if d == best]
            if word_search_agrees(hits):
                return ClubMatch("typo", hits[:MAX_CANDIDATES])
    # Last resort: spelled the way it sounds. Only one clear club, long enough to mean something.
    sk = skeleton(key)
    if len(key.replace(" ", "")) >= 6 and len(sk) >= 4:
        hits = [c for c in clubs if sk in {skeleton(name_key(c.name)), skeleton(name_key(c.slug or ""))} - {""}]
        if len(hits) == 1 and word_search_agrees(hits):
            return ClubMatch("sound", hits)
    return ClubMatch("none", [])
