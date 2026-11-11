"""Turn tool envelopes into a compact, size-capped markdown block for the LLM prompt.

Output uses plain bullets and absolute https links only (the widget's markdown subset).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MAX_CHARS = 6000
CHI = ZoneInfo("America/Chicago")


def central(iso: str | None) -> str:
    """'2026-11-11T05:08:56+00:00' -> 'Tue Nov 10, 11:08 PM Central' (what a student can read)."""
    if not iso:
        return "an unknown time"
    try:
        d = datetime.fromisoformat(iso).astimezone(CHI)
    except ValueError:
        return iso
    return f"{d:%a %b} {d.day}, {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} Central"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _http_date(value: str) -> str | None:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


def _club_line(c: dict, mission_n: int) -> str:
    cats = f" ({', '.join(c['categories'])})" if c.get("categories") else ""
    flags = ""
    if "not_registered" in c.get("flags", []):
        flags += " [not yet registered]"
    if "restricted_to_some_users" in c.get("flags", []):
        flags += " [restricted to some Illinois Tech users]"
    mission = f" Mission: {_clip(c['mission'], mission_n)}" if c.get("mission") else ""
    contact = f" Contact: {c['contact_name']}." if c.get("contact_name") else ""
    label = "312 page" if c["page_url"].startswith("https://312.iit.edu/") else "website"
    return f"- **{c['name']}** — {c.get('type', '')}{cats}{flags}.{mission}{contact} [{label}]({c['page_url']})"


def _event_line(e: dict, desc_n: int) -> str:
    bits = [e["when"]]
    if e.get("host_club"):
        bits.append(f"hosted by {e['host_club']}")
    if e.get("event_type"):
        bits.append(e["event_type"])
    if e.get("tags"):
        bits.append("tags: " + ", ".join(e["tags"]))
    if e.get("location"):
        bits.append(f"location: {e['location']}")
    elif e.get("location_hidden"):
        bits.append("location shown only to signed-in users")
    elif e.get("location_type") == "tbd":
        bits.append("location TBD")
    desc = f" {_clip(e['description_snippet'], desc_n)}" if desc_n and e.get("description_snippet") else ""
    return f"- **{e['title']}** — {'; '.join(bits)}.{desc} [event page]({e['url']})"


def _fit(lines_for, items, head: str, max_chars: int, steps: tuple[int, ...]) -> tuple[str, int]:
    """Render with progressively shorter snippets, then drop trailing items, to fit max_chars."""
    for n in steps:
        lines = [lines_for(i, n) for i in items]
        if len(head) + len("\n".join(lines)) <= max_chars:
            return "\n".join(lines), len(lines)
    lines = [lines_for(i, steps[-1]) for i in items]
    shown = len(lines)
    while shown > 1 and len(head) + len("\n".join(lines[:shown])) > max_chars:
        shown -= 1
    return "\n".join(lines[:shown]), shown


def render_for_prompt(env: dict, max_chars: int = MAX_CHARS) -> str:
    head = f"Facts from 312.iit.edu, current as of {central(env.get('retrieved_at'))}."
    if env.get("stale"):
        head += f" NOTE: the live refresh just failed ({env.get('stale_reason')}), so this may be out of date; tell the student."
    if env.get("auth_required"):
        head += " NOTE: some content needs an Illinois Tech sign-in."
    if not env["ok"]:
        err = env.get("error") or {}
        body = f"Lookup failed ({err.get('code')}). Tell the student the live lookup is unavailable and point them to https://312.iit.edu."
        return f"{head}\n{body}"

    data = env["data"] or {}
    lines: list[str] = []
    if "events" in data:
        w = data.get("window", {})
        head += f"\nEvents from {w.get('from')} to {w.get('to')} (Central time)."
        if env.get("feed_last_modified"):
            head += f" Calendar feed last updated {central(_http_date(env['feed_last_modified']))}."
        if not data["events"]:
            return f"{head}\nNo matching events found. {data.get('hint', '')}".strip()
        body, shown = _fit(_event_line, data["events"], head, max_chars, (200, 100, 0))
        extra = data.get("total_matches", shown) - shown
        if extra > 0:
            body += f"\n(+{extra} more events not shown; browse all at https://312.iit.edu/events)"
            env["truncated"] = True
        return f"{head}\n{body}"
    if "event_id" in data:
        d = data
        body = _event_line({**d, "description_snippet": ""}, 0)
        if d.get("description"):
            body += f"\n  Description: {d['description']}"
        body += "\n  Not available publicly: " + ", ".join(d.get("unavailable_fields", [])) + \
                " (see the event page)."
        return f"{head}\n{body}"
    if "clubs" in data:
        clubs = data["clubs"]
        if data.get("no_exact_match") and clubs:
            head += (f"\nNo club named '{data['no_exact_match']}' was found. "
                     "Say so, then offer these as the closest matches only:")
        if not clubs:
            return f"{head}\nNo matching clubs found. {data.get('hint', '')}".strip()
        for mission_n in (400, 200, 80):
            lines = [_club_line(c, mission_n) for c in clubs]
            text = "\n".join(lines)
            if len(head) + len(text) <= max_chars:
                break
        shown = len(lines)
        while shown > 1 and len(head) + len("\n".join(lines[:shown])) > max_chars:
            shown -= 1
        body = "\n".join(lines[:shown])
        extra = data.get("total_matches", shown) - shown
        if extra > 0:
            body += f"\n(+{extra} more matches not shown; browse all at https://312.iit.edu/club_signup?view=all)"
            env["truncated"] = True
    else:  # single club detail
        d = data
        body = _club_line(d, 1500)
        if d.get("membership_benefits"):
            body += f"\n  Membership benefits: {d['membership_benefits']}"
        if d.get("website_url") and d["website_url"] != d.get("page_url"):
            body += f"\n  Website: {d['website_url']}"
        ups = d.get("upcoming_events")
        if ups:
            body += "\n  Upcoming events:\n" + "\n".join("  " + _event_line(e, 0) for e in ups)
        elif ups == []:
            body += "\n  No upcoming events are listed for this club."
    return f"{head}\n{body}"


# ---- direct, student-facing rendering (no LLM in the loop) ---------------------------------------
BROWSE = {"events": "https://312.iit.edu/events", "clubs": "https://312.iit.edu/club_signup?view=all"}


def _plain(text: str, n: int) -> str:
    return _clip(" ".join(text.split()), n)


def _user_club(c: dict, mission_n: int = 160) -> str:
    cats = f" ({', '.join(c['categories'])})" if c.get("categories") else ""
    flags = ""
    if "not_registered" in c.get("flags", []):
        flags += " · not yet registered"
    if "restricted_to_some_users" in c.get("flags", []):
        flags += " · open to some Illinois Tech users only"
    label = "312 page" if c["page_url"].startswith("https://312.iit.edu/") else "website"
    line = f"- **{c['name']}** — {c.get('type', '')}{cats}{flags}. [{label}]({c['page_url']})"
    if c.get("mission"):
        line += f"\n  {_plain(c['mission'], mission_n)}"
    if c.get("contact_name"):
        line += f"\n  Contact: {c['contact_name']}"
    return line


def _user_event(e: dict, desc_n: int = 120) -> str:
    bits = [e["when"]]
    if e.get("host_club"):
        bits.append(e["host_club"])
    if e.get("event_type"):
        bits.append(e["event_type"])
    if e.get("tags"):
        bits.append(", ".join(e["tags"]))
    if e.get("location"):
        bits.append(e["location"].split(", 3201 South State")[0])
    elif e.get("location_hidden"):
        bits.append("location visible after signing in")
    elif e.get("location_type") == "tbd":
        bits.append("location TBD")
    line = f"- **{e['title']}** — {' · '.join(bits)}. [event page]({e['url']})"
    if desc_n and e.get("description_snippet"):
        line += f"\n  {_plain(e['description_snippet'], desc_n)}"
    return line


def _span(w: dict) -> str:
    from datetime import date as _d
    f, t = _d.fromisoformat(w["from"]), _d.fromisoformat(w["to"])
    fmt = lambda d: f"{d:%a %b} {d.day}"
    return f"on **{fmt(f)}**" if f == t else f"from **{fmt(f)}** to **{fmt(t)}**"


SIGN_IN_NOTE = ("Locations marked \"visible after signing in\" are only shown to signed-in users: open the "
                "event page and sign in with your Illinois Tech account to see them.")


def _location_answer(d: dict) -> str:
    """Direct answer to 'where is it?' for one event."""
    t = d["title"]
    if d.get("location"):
        return f"**{t}** is at {d['location']}."
    if d.get("location_hidden"):
        return (f"The location for **{t}** isn't shown publicly. You need to sign in with your Illinois Tech "
                f"account on the event page to see it: {d['url']}")
    if d.get("location_type") == "tbd":
        return f"The location for **{t}** is listed as TBD. Check the event page for updates: {d['url']}"
    return f"No location is listed for **{t}**. See the event page: {d['url']}"


def render_for_user(env: dict, max_chars: int = MAX_CHARS) -> str:
    """Markdown answer built straight from tool data. Plain bullets and https links only."""
    asof = central(env.get("retrieved_at"))
    notes = []
    if env.get("stale"):
        notes.append("Note: I couldn't refresh from 312 just now, so this may be out of date.")
    if env.get("auth_required"):
        notes.append("Note: some details need an Illinois Tech sign-in on 312.")
    data = env.get("data") or {}

    if not env["ok"]:
        err = env.get("error") or {}
        if err.get("code") == "no_results" and data.get("close_matches"):
            lines = "\n".join(_user_club(c, 0) for c in data["close_matches"])
            return f"I couldn't find an exact match. These are the closest clubs on 312:\n\n{lines}"
        if err.get("code") == "no_results" and env.get("source_url"):
            return f"{err.get('message')}\n\n[Open the event page]({env['source_url']})"
        if env.get("auth_required"):
            return "That needs an Illinois Tech sign-in on 312: https://312.iit.edu"
        kind = "events" if "ical" in (env.get("source_url") or "") else "clubs"
        return ("I couldn't reach the 312 platform just now, so I can't look that up. "
                f"You can browse directly at {BROWSE[kind]}")

    if "events" in data:
        head = f"Here's what's on 312 {_span(data['window'])} (Central time, as of {asof}):"
        if not data["events"]:
            body = "I didn't find any matching events. Try a different day or topic, or browse https://312.iit.edu/events"
            return "\n\n".join([head, body] + notes)
        body, shown = _fit(lambda e, n: _user_event(e, n), data["events"], head, max_chars, (120, 60, 0))
        more = data["total_matches"] - shown
        if more > 0:
            env["truncated"] = True
        tail = [f"Showing {shown} of {data['total_matches']}. See all at {BROWSE['events']}"] if more > 0 else []
        if any(e.get("location_hidden") for e in data["events"][:shown]):
            tail.append(SIGN_IN_NOTE)
        return "\n\n".join([head, body] + tail + notes)

    if "event_id" in data:
        d = data
        out = [_location_answer(d)] if env.get("intent") == "location" else []
        out.append(_user_event({**d, "description_snippet": ""}, 0))
        if d.get("description"):
            out.append(_plain(d["description"], 900))
        if d.get("location_hidden") and env.get("intent") != "location":
            out.append(SIGN_IN_NOTE)
        out.append("Not available publicly: " + ", ".join(x.replace("_", " ") for x in d.get("unavailable_fields", []))
                   + ". The event page has more.")
        return "\n\n".join(out + notes)

    if "clubs" in data:
        head = f"Here are clubs on 312 (as of {asof}):"
        if data.get("no_exact_match"):
            head = (f"I couldn't find a club named '{data['no_exact_match']}'. "
                    f"These are the closest matches (as of {asof}):")
        if not data["clubs"]:
            msg = (f"I couldn't find a club named '{data['no_exact_match']}' on 312. Check the spelling, or browse "
                   if data.get("no_exact_match") else
                   "I didn't find any matching clubs. Try a broader interest word, or browse ")
            return "\n\n".join([msg + BROWSE["clubs"]] + notes)
        body, shown = _fit(lambda c, n: _user_club(c, n), data["clubs"], head, max_chars, (160, 80, 0))
        more = data["total_matches"] - shown
        tail = f"Showing {shown} of {data['total_matches']}. Browse all at {BROWSE['clubs']}" if more > 0 else ""
        if more > 0:
            env["truncated"] = True
        return "\n\n".join(x for x in [head, body, tail] + notes if x)

    # single club
    d = data
    out = [_user_club(d, 700)]
    if not d.get("contact_name"):
        out.append("No contact person is listed for this club on 312; its page may list officers.")
    if d.get("membership_benefits"):
        out.append("Membership benefits: " + _plain(d["membership_benefits"], 400))
    if d.get("website_url") and d["website_url"] != d.get("page_url"):
        out.append(f"Website: {d['website_url']}")
    ups = d.get("upcoming_events")
    if ups:
        out.append("Upcoming events:\n" + "\n".join(_user_event(e, 0) for e in ups[:4]))
    elif ups == []:
        out.append("No upcoming events are listed for this club.")
    return "\n\n".join(out + [f"(as of {asof})"] + notes)
