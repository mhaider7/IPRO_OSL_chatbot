import pytest

from web_tools.router import route


@pytest.mark.parametrize("msg", [
    "What clubs are there for robotics?",
    "Any clubs about music?",
    "I'm interested in joining a dance club",
    "recommend a club for gaming",
    "list student organizations",
])
def test_discovery_routes_to_search_clubs(msg):
    call = route(msg)
    assert call and call.name == "search_clubs" and call.args["query"] == msg
    assert call.stage == "searching_clubs"


@pytest.mark.parametrize("msg", [
    "Tell me about the Boxing Club",
    "tell me more about the Robotics Club?",
    "What is the Chess Club?",
])
def test_details_routes_with_search_fallback(msg):
    call = route(msg)
    assert call and call.name == "get_club_details"
    assert call.fallback and call.fallback.name == "search_clubs"
    assert not call.args["slug"].lower().endswith("club")


def test_name_resolved_when_known_even_without_club_word():
    assert route("tell me about Alpha Sigma Alpha", {"alpha sigma alpha"}).name == "get_club_details"
    assert route("tell me about Alpha Sigma Alpha") is None


@pytest.mark.parametrize("msg", [
    "How do I start a club?",
    "What is the club re-registration deadline?",
    "How do I get funding for my organization?",
    "Who is my club's advisor?",
    "How do I reserve a room for my org meeting?",
    "What are the requirements to be a recognized student organization?",
    "Hello!",
    "How do I drop a class?",
    "",
])
def test_policy_and_unrelated_questions_do_not_route(msg):
    assert route(msg) is None


# Policy questions that mention an event or a club. Before the policy check, almost all of these were sent to
# 312 (the word "event" alone was enough) and the student got an events or club list instead of an answer.
@pytest.mark.parametrize("msg", [
    # found in the 2026-11-15 measurement
    "What does my club need to do to stay in good standing?", "What's the limit on expense claims?",
    "Can my club sell food at an event?", "What fees get added to a big event?",
    "Do we need public safety for our event with 80 people?", "How much are linens for an event?",
    "What counts as a large event?", "What are the steps for club travel?",
    "Can I sign a contract with a DJ for our event?", "How do we book a performer for an event?",
    "Where can our club store equipment?", "Can we bring high schoolers to our club event?",
    "Can we serve alcohol at our event?",
    "Our robotics club wants to build something with power tools, what do we need?",
    # held-out set, written before the fix
    "Do we have to pay for security at our event?", "Can our event have a DJ?",
    "How many guests can come to our fraternity event?", "Is there a fee to use the MTCC ballroom for an event?",
    "Our club wants to host a movie night, what do we need to do?", "Can non-students attend our club's events?",
    "What do I need to do to bring a speaker to campus for an event?", "Can we have a bonfire at our event?",
    "Is food allowed at events in the auditorium?", "Can my club give away prizes at an event?",
    "Who pays for the janitor at our event?", "Does our event need a waiver?",
    "How many events does a club have to hold each semester?", "Can my club store food in the storage room?",
    "What does our club treasurer do?", "Can our club use Discord?",
    "What happens if our club doesn't hold any events this semester?",
    "Can our org paint the MTCC windows for our event?", "Can we sell tickets for our club event?",
    "Is there a limit on how much our club can spend on an event?", "What's the rule on minors at club events?",
    "Are clubs allowed to charge dues?", "What is a club advisor?",
    # reported by the user 2026-11-15 ("can I" in the middle of the question)
    "What foods can I serve at an event?", "Which rooms can we use for an event?",
])
def test_policy_questions_mentioning_events_or_clubs_do_not_route(msg):
    assert route(msg) is None, msg


# Lookups phrased like policy questions ("can I", "what should I", "my friend ...") must still reach 312.
@pytest.mark.parametrize("msg,tool", [
    ("Can you show me events this weekend?", "search_events"), ("What should I do this weekend?", "search_events"),
    ("What can I do on campus this Friday?", "search_events"), ("Any events where I do not have to pay?", "search_events"),
    ("Are there any events I can go to tonight?", "search_events"), ("Where can I find events this week?", "search_events"),
    ("What events can I go to this week?", "search_events"), ("Does SWE have any events this week?", "search_events"),
    ("What events are there this Thursday?", "search_events"), ("Any cultural events coming up?", "search_events"),
    ("Is anything happening on campus this weekend?", "search_events"), ("Show me upcoming workshops", "search_events"),
    ("When is the next hackathon?", "search_events"), ("What's on this week?", "search_events"),
    ("Do you have any clubs for chess?", "search_clubs"), ("What clubs should I join?", "search_clubs"),
    ("Can I join the chess club?", "search_clubs"), ("I need to find a club for dance", "search_clubs"),
    ("My friend wants to join a dance club, any ideas?", "search_clubs"), ("Is the chess club still active?", "search_clubs"),
    ("Is there a robotics team?", "search_clubs"), ("What student orgs are there for music?", "search_clubs"),
    ("How do I join the Robotics Club?", "get_club_details"), ("Who leads the Robotics Club?", "get_club_details"),
])
def test_lookups_phrased_like_questions_still_route(msg, tool):
    call = route(msg)
    assert call is not None and call.name == tool, f"{msg!r}: {call.name if call else None}"


def test_overlong_messages_ignored():
    assert route("clubs " * 200) is None


# ---- events -------------------------------------------------------------------------------------
from datetime import date

from web_tools.router import parse_dates

TUE = date(2026, 11, 10)   # a Tuesday


def ev(msg, today=TUE):
    return route(msg, today=today)


def test_events_basic_default_window():
    call = ev("What events are happening on campus?")
    assert call.name == "search_events" and call.args == {"limit": 6} and call.stage == "searching_events"


def test_free_food_this_week():
    call = ev("Is there any free food this week?")
    assert call.name == "search_events"
    assert call.args == {"limit": 6, "date_from": "2026-11-10", "date_to": "2026-11-15", "tags": ["Free Food"]}


def test_tonight_and_tomorrow():
    assert ev("any events tonight?").args["date_from"] == "2026-11-10"
    c = ev("what's happening tomorrow")
    assert c.args["date_from"] == c.args["date_to"] == "2026-11-11"


def test_type_online_and_topic_query():
    call = ev("Any virtual career events this month?")
    assert call.args["event_type"] == "Career Preparation" and call.args["location_type"] == "online"
    assert call.args["date_to"] == "2026-11-30"
    assert "query" not in call.args
    call = ev("What events does the Robotics Club have?")
    assert call.name == "search_events" and "Robotics" in call.args["query"]


def test_event_lookup_by_id_or_url():
    assert ev("tell me about event 385553").args == {"event_id_or_url": "385553"}
    assert ev("what is https://312.iit.edu/rsvp?id=385553").name == "get_event_details"


@pytest.mark.parametrize("msg", [
    "our event costs $1500 total and we already spent $400, how much more can we request from Finance Board",
    "the event will cost 2000 dollars, how do I get funding",
])
def test_money_amount_is_not_an_event_id(msg):
    call = ev(msg)
    assert call is None or call.name != "get_event_details"


@pytest.mark.parametrize("msg", [
    "How do I host an event?", "How do I register an event on 312?", "What are the event planning rules?",
    "How do I get approval for a campus event?", "Who pays event charges?", "How do I advertise my event?",
])
def test_event_policy_questions_do_not_route(msg):
    assert ev(msg) is None


def test_club_questions_still_go_to_clubs():
    assert ev("any clubs about music?").name == "search_clubs"
    assert ev("tell me about the Boxing Club").name == "get_club_details"


@pytest.mark.parametrize("text,expected", [
    ("this weekend", (date(2026, 11, 14), date(2026, 11, 15))),
    ("next week", (date(2026, 11, 16), date(2026, 11, 22))),
    ("in december", (date(2026, 12, 1), date(2026, 12, 31))),
    ("in january", (date(2027, 1, 1), date(2027, 1, 31))),
    ("on November 20th", (date(2026, 11, 20), date(2026, 11, 20))),
    ("on march 3", (date(2027, 3, 3), date(2027, 3, 3))),
    ("in november", (date(2026, 11, 10), date(2026, 11, 30))),
    ("next month", (date(2026, 12, 1), date(2026, 12, 31))),
    ("whenever", None),
])
def test_parse_dates(text, expected):
    assert parse_dates(text, TUE) == expected


def test_weekend_when_already_weekend():
    assert parse_dates("this weekend", date(2026, 11, 14)) == (date(2026, 11, 14), date(2026, 11, 15))
    assert parse_dates("this weekend", date(2026, 11, 15)) == (date(2026, 11, 15), date(2026, 11, 15))


# ---- multi-turn follow-ups ----------------------------------------------------------------------
def u(t): return {"role": "user", "text": t}
def b(t): return {"role": "bot", "text": t}


EVENT_LIST = (
    "Here's what's on 312 from **Tue Nov 10** to **Sun Nov 15** (Central time, as of now):\n\n"
    "- **Pizza Night** — Tue Nov 10, 6:00 PM–7:00 PM Central · Chess Club · Free Food. [event page](https://312.iit.edu/rsvp?id=111)\n"
    "  Come eat.\n"
    "- **Robotics Demo** — Wed Nov 11, 5:00 PM Central · Robotics Club. [event page](https://312.iit.edu/rsvp?id=222)\n"
    "- **Career Fair Prep** — Thu Nov 12, 4:00 PM Central · Career Services. [event page](https://312.iit.edu/rsvp?id=333)\n"
    "\nShowing 3 of 9. See all at https://312.iit.edu/events"
)
CLUB_LIST = (
    "Here are clubs on 312 (as of now):\n\n"
    "- **Robotics Club** — Student Organization (Engineering). [312 page](https://312.iit.edu/robotics/)\n"
    "- **Drone Team** — Student Organization. [312 page](https://312.iit.edu/drone/)\n"
)
EV_HIST = [u("Is there any free food this week?"), b(EVENT_LIST)]
CLUB_HIST = [u("any clubs about robotics?"), b(CLUB_LIST)]


def fu(msg, hist, today=TUE, known=None):
    return route(msg, known, today, hist)


@pytest.mark.parametrize("msg,idx_id", [
    ("tell me about the second one", "222"), ("what about the first one?", "111"),
    ("when is the last one?", "333"), ("more on #3", "333"), ("tell me more about the third event", "333"),
])
def test_ordinal_reference_resolves_event(msg, idx_id):
    call = fu(msg, EV_HIST)
    assert call.name == "get_event_details" and call.args == {"event_id_or_url": idx_id}


def test_ordinal_reference_resolves_club_and_location_intent():
    call = fu("tell me about the second one", CLUB_HIST)
    assert call.name == "get_club_details" and call.args["slug"] == "Drone Team"
    assert call.fallback.name == "search_clubs"
    assert fu("where is the first one?", EV_HIST).intent == "location"


def test_ordinal_out_of_range_or_not_a_reference_is_ignored():
    assert fu("tell me about the sixth one", EV_HIST) is None
    assert fu("any events for first year students?", EV_HIST).args.get("query")   # normal event search, not item 1
    assert fu("tell me about the second one", [u("hi"), b("Hello!")]) is None


def test_refine_date():
    call = fu("what about tomorrow?", EV_HIST)
    assert call.name == "search_events"
    assert call.args == {"limit": 6, "date_from": "2026-11-11", "date_to": "2026-11-11", "tags": ["Free Food"]}


def test_refine_bare_date_and_online():
    assert fu("on Friday?", EV_HIST).args["date_from"] == "2026-11-13"
    call = fu("and online?", EV_HIST)
    assert call.args["location_type"] == "online" and call.args["tags"] == ["Free Food"]


def test_refine_swaps_tag_and_topic():
    assert fu("how about career events?", EV_HIST).args["event_type"] == "Career Preparation"
    assert fu("what about robotics?", EV_HIST).args["query"] == "robotics"


def test_refine_clubs_new_interest():
    call = fu("how about music?", CLUB_HIST)
    assert call.name == "search_clubs" and call.args["query"] == "music"


def test_more_pages_by_what_was_shown():
    call = fu("more", EV_HIST)
    assert call.name == "search_events" and call.args["offset"] == 3 and call.args["tags"] == ["Free Food"]
    again = fu("show more", EV_HIST + [u("more"), b(EVENT_LIST)])      # a second "more" keeps paging
    assert again.args["offset"] == 6
    assert fu("anything else?", CLUB_HIST).args == {"query": "any clubs about robotics?", "limit": 5, "offset": 2}


def test_more_without_a_live_answer_is_ignored():
    assert fu("more", [u("hi"), b("Hello!")]) is None


def test_club_pronoun_events():
    hist = [u("tell me about the Boxing Club"), b("- **Boxing Club** — x. [312 page](https://312.iit.edu/box/)")]
    call = fu("what events do they have?", hist)
    assert call.name == "search_events" and call.args["query"] == "Boxing"


def test_followup_policy_question_still_goes_to_handbook():
    assert fu("what's the deadline for that?", EV_HIST) is None
    assert fu("how do I register it?", CLUB_HIST) is None


def test_standalone_questions_unaffected_by_history():
    assert fu("any clubs about music?", EV_HIST).name == "search_clubs"
    assert fu("any events tonight?", CLUB_HIST).name == "search_events"
    assert fu("Hello!", EV_HIST) is None


def test_refine_looks_back_past_a_handbook_turn():
    hist = CLUB_HIST + [u("How do I start a club?"), b("1. Complete the registration form.")]
    call = fu("what about dance?", hist)
    assert call.name == "search_clubs" and call.args["query"] == "dance"
    assert fu("how about tomorrow?", hist).name == "search_events"   # clubs have no dates: events on the topic
    assert fu("more", hist) is None                          # paging only applies to the immediately previous answer


def test_short_answer_to_a_bot_question_is_not_a_club_search():
    ask = [u("How do I get money?"), b("Would you like to know about fundraising or Finance Board?")]
    assert fu("for my club's trip", ask) is None
    assert fu("any clubs about hiking?", ask).name == "search_clubs"          # a real discovery cue still routes
    # "my club's ..." is about the student's own club (a handbook question), with or without a bot question
    assert fu("for my club's trip", [u("hi"), b("Hello!")]) is None


def test_date_after_club_search_means_events_on_that_topic():
    call = fu("how about tomorrow?", CLUB_HIST)
    assert call.name == "search_events"
    assert call.args == {"limit": 6, "date_from": "2026-11-11", "date_to": "2026-11-11", "query": "robotic"}
    assert fu("on Friday?", CLUB_HIST).args["date_from"] == "2026-11-13"
    assert fu("what about this weekend?", CLUB_HIST).args["date_from"] == "2026-11-14"
    assert fu("and online?", CLUB_HIST).args["location_type"] == "online"
    assert fu("how about free food?", CLUB_HIST).args["tags"] == ["Free Food"]
    assert fu("where is that tomorrow?", CLUB_HIST).intent == "location"


def test_new_topic_with_a_date_uses_the_new_topic():
    call = fu("how about music tomorrow?", CLUB_HIST)
    assert call.name == "search_events" and call.args["query"] == "music" and call.args["date_from"] == "2026-11-11"


def test_date_after_club_search_without_a_topic_leaves_query_out():
    hist = [u("show me clubs"), b("- **Robotics** — x. [312 page](https://312.iit.edu/robotics/)")]
    assert "query" not in fu("how about tomorrow?", hist).args


def test_policy_cue_still_beats_date_after_clubs():
    assert fu("what's the deadline for tomorrow?", CLUB_HIST) is None
