"""Club candidate step: the name a student (or the model) wrote -> real 312 clubs (web_tools/tools/clubs.py).

Abbreviations ("ML"), the same name written differently ("formula one club"), small typos ("hawkapela"), and
several clubs sharing initials ("ISA") -> ask which one. Only clubs on the list are ever returned.
"""
from web_tools.fetch.client import FetchResult
from web_tools.parsers.clubs_html import Club
from web_tools.render import render_for_prompt, render_for_user
from web_tools.tools.clubs import candidates, display_ident, initials, name_key
from web_tools.tools.registry import WebTools


def club(name, slug=None, type="Student Organization", categories=()):
    return Club(club_id=str(abs(hash(name)) % 10**6), slug=slug, name=name, type=type, categories=tuple(categories),
                website_url=f"https://312.iit.edu/{slug or 'x'}/", mission="", membership_benefits="",
                contact_name=None, flags=())


CLUBS = [
    club("Machine Learning", "machinelearning"),
    club("Mathematics Club", "math"),
    club("Indian Student Association", "iitisa"),
    club("Iranian Student Association", "irsa"),
    club("American Chemical Society", "ACS"),
    club("American Constitution Society", "ckacs", type="Chicago-Kent Student Organization"),
    club("Hawkapella", "apella"),
    club("Formula 1 Club", "formula1"),
    club("Mock Trial Club", "mtc"),
    club("Cultural and Sports Committee (CASCO)", "CASCO", type="Mumbai Student Organization"),
    club("Esports", "esports", categories=("Sports and Recreation",)),
    club("Soccer Club", "soccer", categories=("Sports and Recreation",)),
    club("Art Club", "artclub"),
    club("Society of Women Engineers", "swe"),
]


def names(match):
    return [c.name for c in match.clubs]


def test_initials_are_built_from_every_name():
    assert "ML" in initials("Machine Learning")
    assert {"MTC", "MT"} <= initials("Mock Trial Club")          # with and without the trailing "Club"
    assert "SWE" in initials("Society of Women Engineers")       # "of" skipped
    assert "CASCO" in initials("Cultural and Sports Committee (CASCO)")
    assert "F1" in initials("Formula 1 Club")


def test_abbreviation_finds_the_club():
    for asked in ("ML", "ml", "the ML club's 312 page", "ML club website"):
        m = candidates(CLUBS, asked)
        assert m.kind == "initials" and names(m) == ["Machine Learning"], asked
    assert names(candidates(CLUBS, "MT")) == ["Mock Trial Club"]
    assert names(candidates(CLUBS, "casco")) == ["Cultural and Sports Committee (CASCO)"]


def test_shared_initials_return_every_club_to_ask_which():
    m = candidates(CLUBS, "ISA")
    assert not m.clear and set(names(m)) == {"Indian Student Association", "Iranian Student Association"}


def test_a_slug_that_is_also_another_clubs_initials_returns_both():
    m = candidates(CLUBS, "ACS")
    assert names(m) == ["American Chemical Society", "American Constitution Society"]   # the slug's club first


def test_exact_names_and_slugs_still_win():
    assert names(candidates(CLUBS, "Machine Learning")) == ["Machine Learning"]
    assert names(candidates(CLUBS, "swe")) == ["Society of Women Engineers"]
    assert candidates(CLUBS, "Hawkapella").kind == "exact"


def test_the_same_name_written_differently():
    m = candidates(CLUBS, "formula one club")
    assert m.kind == "name" and names(m) == ["Formula 1 Club"]


def test_small_typos():
    assert names(candidates(CLUBS, "hawkapela")) == ["Hawkapella"]
    assert names(candidates(CLUBS, "soccr club")) == ["Soccer Club"]
    assert names(candidates(CLUBS, "Society of Women Engineer")) == ["Society of Women Engineers"]


def test_real_words_are_not_typos_or_initials():
    assert candidates(CLUBS, "sports").kind == "none"         # not "Esports": the word search has a better answer
    assert candidates(CLUBS, "art").kind != "initials"        # a word in a club name, not initials
    assert candidates(CLUBS, "chess").kind == "none"          # nothing on the list -> nothing invented
    assert candidates(CLUBS, "").kind == "none"


def test_short_words_get_no_typo_matching():
    assert candidates(CLUBS, "mth").kind == "none"


def test_a_sentence_is_tried_again_with_its_key_words():
    # the regex router sends the whole sentence (user's quality-7 test, 2026-11-17)
    m = candidates(CLUBS, "Is there a hawkapela club?")
    assert names(m) == ["Hawkapella"] and m.asked == "hawkapela"
    assert names(candidates(CLUBS, 'Maybe it was "hawkapela"')) == ["Hawkapella"]
    assert names(candidates(CLUBS, "is there an ACS chapter?")) == ["American Chemical Society",
                                                                    "American Constitution Society"]
    assert candidates(CLUBS, "Are there any clubs for singing?").kind == "none"   # a topic, left to the word search


def test_names_spelled_the_way_they_sound():
    m = candidates(CLUBS, "Is there a hawkuhpela club?")   # 3 letters off: too far for a typo
    assert m.kind == "sound" and names(m) == ["Hawkapella"]
    assert names(candidates(CLUBS, "fotografy club")) == []   # no Photography Club in this small list
    assert candidates(CLUBS, "singing").kind == "none"


def test_sound_alike_needs_a_long_enough_name():
    assert candidates(CLUBS, "sokker").kind == "none"         # 'skr' would match too much


def test_display_and_key():
    assert name_key("The ML club's 312 page") == "ml"
    assert name_key("Formula One Club") == "formula 1"
    assert display_ident("the ML club's website", "initials") == "ML"
    assert display_ident("soccr club", "typo") == "soccr club"


# ---- through the tools and the renderers ---------------------------------------------------------------------
def tools_with(clubs):
    t = WebTools(fetcher=type("F", (), {"fetch": lambda self, url, ttl, read_timeout=None: FetchResult(
        ok=False, url=url, error_code="http_error", error_message="n/a")})())
    fr = FetchResult(ok=True, url="https://312.iit.edu/club_signup?view=all", retrieved_at="2026-11-16T16:20:00+00:00")
    t._load_clubs = lambda: (clubs, fr, None)
    return t


def test_details_for_an_abbreviation_say_which_club_was_meant():
    env = tools_with(CLUBS).run("get_club_details", {"slug": "ML"})
    assert env["ok"] and env["data"]["name"] == "Machine Learning" and env["data"]["matched_by"] == "initials"
    assert render_for_user(env).startswith("'ML' matches **Machine Learning** on 312:")
    assert "The student wrote 'ML'" in render_for_prompt(env)


def test_details_for_a_typo_say_it_was_the_closest_spelling():
    env = tools_with(CLUBS).run("get_club_details", {"slug": "hawkapela"})
    assert env["data"]["matched_by"] == "typo"
    assert "closest club on 312 is **Hawkapella**" in render_for_user(env)


def test_club_search_with_a_sentence_finds_a_misspelled_club():
    data = tools_with(CLUBS).run("search_clubs", {"query": "Is there a hawkuhpela club?"})["data"]
    assert [c["name"] for c in data["clubs"]] == ["Hawkapella"]


def test_details_quote_the_key_words_not_the_whole_sentence():
    env = tools_with(CLUBS).run("get_club_details", {"slug": "is there a hawkuhpela club"})
    assert render_for_user(env).startswith("I couldn't find 'hawkuhpela' exactly; the closest club on 312 is "
                                           "**Hawkapella**:")


def test_details_for_shared_initials_ask_did_you_mean():
    env = tools_with(CLUBS).run("get_club_details", {"slug": "the ISA"})
    assert not env["ok"] and env["error"]["code"] == "ambiguous"
    text = render_for_user(env)
    assert text.startswith("'ISA' could be more than one club on 312. Did you mean")
    assert "**Indian Student Association**" in text and "**Iranian Student Association**" in text
    assert "ask which one" in render_for_prompt(env)


def test_details_with_no_candidate_keep_the_close_matches():
    env = tools_with(CLUBS).run("get_club_details", {"slug": "chess"})
    assert env["error"]["code"] == "no_results" and "close_matches" in env["data"]


def test_club_search_puts_an_abbreviation_match_first():
    data = tools_with(CLUBS).run("search_clubs", {"query": "ML"})["data"]
    assert data["clubs"][0]["name"] == "Machine Learning"
    data = tools_with(CLUBS).run("search_clubs", {"query": "hawkapela"})["data"]   # typo, word search finds nothing
    assert [c["name"] for c in data["clubs"]] == ["Hawkapella"]
