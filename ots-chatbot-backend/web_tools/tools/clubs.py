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
