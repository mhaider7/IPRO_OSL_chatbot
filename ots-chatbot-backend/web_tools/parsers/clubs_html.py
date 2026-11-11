"""Parse https://312.iit.edu/club_signup?view=all into Club records (pure function, no I/O)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

GENERIC_CONTACT_RE = re.compile(r"^(email|e-mail|contact|message|send)\b", re.I)
PLACEHOLDER_RE = re.compile(r"\(\s*define your .*? under group settings\s*\)", re.I)


class LayoutChanged(Exception):
    """The page no longer looks like the structure this parser was written against."""


@dataclass(frozen=True)
class Club:
    club_id: str
    slug: str | None                # 312 page slug; None when the listing links only an external site
    name: str
    type: str                       # e.g. "Student Organization", "Residence Hall"
    categories: tuple[str, ...]     # e.g. ("Arts and Media",)
    website_url: str
    mission: str
    membership_benefits: str
    contact_name: str | None
    flags: tuple[str, ...]          # "not_registered", "restricted_to_some_users"

    @property
    def type_line(self) -> str:
        return self.type + (" - " + ", ".join(self.categories) if self.categories else "")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _section_text(el: Tag | None, label: str) -> str:
    """Text of a Mission / Membership Benefits <p>, minus its bold label and CMS placeholders."""
    if el is None:
        return ""
    text = _clean(el.get_text(" ", strip=True))
    if text.lower().startswith(label.lower()):
        text = text[len(label):].strip()
    return "" if PLACEHOLDER_RE.search(text) else text


def split_type_line(line: str) -> tuple[str, tuple[str, ...]]:
    line = _clean(line)
    head, sep, tail = line.partition(" - ")
    cats = tuple(c.strip() for c in tail.split(",") if c.strip()) if sep else ()
    return head.strip(), cats


def _slug_from_url(url: str) -> str | None:
    """312 slug, ONLY for 312.iit.edu URLs (an external site's first path segment is not a slug)."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "312.iit.edu":
        return None
    segs = [s for s in parts.path.split("/") if s]
    return segs[0] if segs else None


def _merge(a: Club, b: Club) -> Club:
    """The listing repeats some clubs; keep the first, filling gaps and uniting flags."""
    return Club(
        club_id=a.club_id, slug=a.slug or b.slug, name=a.name, type=a.type or b.type,
        categories=tuple(dict.fromkeys(a.categories + b.categories)), website_url=a.website_url,
        mission=a.mission or b.mission, membership_benefits=a.membership_benefits or b.membership_benefits,
        contact_name=a.contact_name or b.contact_name, flags=tuple(dict.fromkeys(a.flags + b.flags)),
    )


def parse_clubs_listing(html: str, min_clubs: int = 1, max_bad_fraction: float = 0.1) -> list[Club]:
    soup = BeautifulSoup(html, "lxml")
    checkboxes = soup.select("input[id^=cb_club_]")
    clubs: dict[str, Club] = {}
    bad = 0
    for box in checkboxes:
        club_id = box["id"].removeprefix("cb_club_")
        li = box.find_parent("li")
        link = li.select_one("h2 a[href]") if li else None
        name = _clean(link.get_text(" ", strip=True)) if link else ""
        if li is None or not name:
            bad += 1
            continue
        url = link["href"].strip()
        type_el = li.select_one("p.grey-element")
        ctype, cats = split_type_line(type_el.get_text(" ", strip=True)) if type_el else ("", ())

        contact = None
        for p in li.find_all("p"):
            if _clean(p.get_text(" ", strip=True)).startswith("Contact:"):
                a = p.find("a")
                contact = _clean(a.get_text(" ", strip=True)) if a else None
                if contact and GENERIC_CONTACT_RE.match(contact):
                    contact = None   # "Email group officers" is a button label, not a person
                break

        flags: list[str] = []
        if any("not registered" in b.get_text().lower() for b in li.select(".badge")):
            flags.append("not_registered")
        restriction = li.select_one(f"#email_restriction_{club_id}")
        if restriction and "restricted" in restriction.get_text().lower():
            flags.append("restricted_to_some_users")

        club = Club(
            club_id=club_id, slug=_slug_from_url(url), name=name, type=ctype, categories=cats,
            website_url=url,
            mission=_section_text(li.select_one(f"#club_{club_id}"), "Mission"),
            membership_benefits=_section_text(li.select_one(f"#club_whatwedo_{club_id}"), "Membership Benefits"),
            contact_name=contact or None, flags=tuple(flags),
        )
        prev = clubs.get(club_id)
        clubs[club_id] = club if prev is None else _merge(prev, club)  # same club listed twice

    total = len(checkboxes)
    clubs_list = list(clubs.values())
    if len(clubs_list) < min_clubs or (total and bad / total > max_bad_fraction):
        raise LayoutChanged(
            f"clubs listing: parsed {len(clubs_list)} clubs from {total} entries ({bad} unusable); "
            "expected cb_club_* inputs inside li.list-group-item with an h2 link")
    return clubs_list
