import pytest

from api.conversation import (ACK_REPLY, DECLINE_REPLY, FIXED_REPLIES, PROMPT_REPLY, THANKS_REPLY, is_followup,
                              retrieval_query, short_reply, what_hawk_asked)
from llm.ollama_client import MAX_HISTORY_TURNS, NUM_CTX, _messages, fit_history


def u(t): return {"role": "user", "text": t}
def b(t): return {"role": "bot", "text": t}


@pytest.mark.parametrize("text", [
    "what's the deadline for that?", "and what about funding?", "How do I do it?", "Why?", "what are their requirements",
])
def test_followups(text):
    assert is_followup(text)


@pytest.mark.parametrize("text", [
    "How do I register a student organization?", "Is there any free food this week?",
    "What events are happening this week?", "What is Finance Board?",
    "I want to know about the process of getting my organization recognized by the university and what steps are needed for that",
])
def test_standalone_questions_are_not_followups(text):
    assert not is_followup(text)


def test_standalone_query_unchanged():
    assert retrieval_query("How do I register a club?", [u("hi"), b("Hello!")]) == "How do I register a club?"
    assert retrieval_query("what about that?", []) == "what about that?"


def test_followup_query_carries_previous_question_and_answer():
    hist = [u("How do I re-register my org?"), b("Re-registration is done each fall through the portal. Submit the form.")]
    q = retrieval_query("what's the deadline for that?", hist)
    assert "re-register" in q and "deadline" in q and q.startswith("Re-registration is done each fall through the portal.")


def test_live_answer_sentence_not_added():
    hist = [u("free food this week?"), b("Here's what's on 312 this week (as of today):\n- **Pizza** ...")]
    q = retrieval_query("what about that one?", hist)
    assert q == "free food this week? what about that one?"


# ---- short replies ("yes", "tell me more", "thanks") ---------------------------------------------------
OFFER_Q = b("Students can't sign contracts. Do you want to know how far in advance the contract has to be submitted?")
OFFER_NO_Q = b("Posters need OSL approval. Let me know if you'd like the list of what a poster must include.")
REAL_Q = b("It depends on the size of the event. Will more than 50 people attend?")
PLAIN = b("Expense claims for catering cannot exceed $300.")
GENERIC = b("Yes, but the code of conduct still applies. If you have any other questions, feel free to ask!")
HANDBACK = b("I'm Hawk, the OSL assistant. I can help with clubs and events. Is there something specific you'd like help with?")
HANDBACK_MORE = b("To start a club, complete the registration form on 312. Is there anything specific you'd like to know more about?")


@pytest.mark.parametrize("reply,bot,kind", [
    # agreeing to an offer, with or without "?"
    ("yes", OFFER_Q, "answer"), ("ok", OFFER_Q, "answer"), ("yeah sure", OFFER_Q, "answer"),
    ("please do", OFFER_Q, "answer"), ("Yes, please!", OFFER_Q, "answer"), ("sure!", OFFER_NO_Q, "answer"),
    ("tell me more", OFFER_Q, "answer"),
    # answering a real question: "no" is information here, not declining
    ("no", REAL_Q, "answer"), ("yes", REAL_Q, "answer"),
    # asking for more with nothing offered
    ("tell me more", PLAIN, "more"), ("how?", PLAIN, "more"), ("go on", PLAIN, "more"), ("yes please", PLAIN, "more"),
    # acknowledgements and thanks: nothing to look up
    ("got it", PLAIN, "ack"), ("cool", PLAIN, "ack"), ("yep", PLAIN, "ack"), ("yep", GENERIC, "ack"),
    ("thank you!", PLAIN, "thanks"), ("ok thanks", PLAIN, "thanks"),
    # declining an offer
    ("no thanks", OFFER_Q, "decline"), ("I'm good", OFFER_NO_Q, "decline"), ("nope", PLAIN, "decline"),
    # Hawk handed the conversation back (user's transcript 2026-11-15): a bare "yeah" has nothing to agree to
    ("yeah", HANDBACK, "prompt"), ("yep", b("Glad that helps! Is there anything else I can help you with?"), "prompt"),
    ("Yes, please.", HANDBACK_MORE, "more"), ("tell me more", HANDBACK_MORE, "more"), ("thanks", HANDBACK, "thanks"),
])
def test_short_reply_kinds(reply, bot, kind):
    assert short_reply(reply, [u("q"), bot]) == kind


@pytest.mark.parametrize("reply,hist", [
    ("yes", []),                                                       # nothing to reply to
    ("how do I submit a budget?", [u("hi"), b("Hello!")]),             # a real question
    ("yes, and what about catering over $300?", [u("q"), OFFER_Q]),   # a short reply plus a new question
    ("yes", [u("q"), b("a"), u("yes")]),                               # last message isn't Hawk's
])
def test_not_short_replies(reply, hist):
    assert short_reply(reply, hist) is None


def test_offer_detection_skips_generic_closers():
    assert what_hawk_asked(OFFER_NO_Q["text"]) == ("Let me know if you'd like the list of what a poster must include.", True)
    assert what_hawk_asked(REAL_Q["text"]) == ("Will more than 50 people attend?", False)
    assert what_hawk_asked(GENERIC["text"]) == (None, False)
    assert what_hawk_asked(PLAIN["text"]) == (None, False)


def test_yes_searches_for_what_hawk_offered():
    q = retrieval_query("yes", [u("Can we hire a DJ for our event?"), OFFER_Q])
    assert q == ("Do you want to know how far in advance the contract has to be submitted? "
                 "Can we hire a DJ for our event?")


def test_tell_me_more_searches_the_previous_topic():
    q = retrieval_query("tell me more", [u("Can our club charge dues?"), b("Yes, organizations may charge dues. Ask OSL.")])
    assert q == "Yes, organizations may charge dues. Can our club charge dues?"


def test_handback_question_is_not_searched():
    hist = [u("Yep"), HANDBACK]
    assert what_hawk_asked(HANDBACK["text"]) == (None, False)
    assert retrieval_query("yeah", hist) == "yeah"          # nothing searched: the fixed prompt reply is sent instead


def test_yes_please_after_handback_searches_the_last_topic():
    q = retrieval_query("Yes, please.", [u("How do I start a club?"), HANDBACK_MORE])
    assert q == "To start a club, complete the registration form on 312. How do I start a club?"


def test_fixed_replies():
    assert FIXED_REPLIES == {"ack": ACK_REPLY, "thanks": THANKS_REPLY, "decline": DECLINE_REPLY, "prompt": PROMPT_REPLY}


def test_fit_history_keeps_recent_turns_within_budget():
    hist = [u("q1 " + "x" * 500), b("a1 " + "y" * 500), u("q2"), b("a2"), u("q3"), b("a3")]
    out = fit_history(hist, 5)
    assert [t["text"] for t in out] == ["q3", "a3"]
    assert out[0]["role"] == "user"


def test_fit_history_caps_turn_count_and_starts_with_user():
    hist = [u(f"q{i}") if i % 2 == 0 else b(f"a{i}") for i in range(40)]
    out = fit_history(hist, 10_000)
    assert len(out) <= MAX_HISTORY_TURNS and out[0]["role"] == "user"


def test_fit_history_shrinks_old_live_lists_but_keeps_latest():
    big = "Here's what's on 312:\n" + "- **E** — x. [event page](https://312.iit.edu/rsvp?id=1)\n" * 30
    hist = [u("events?"), b(big), u("more"), b(big)]
    out = fit_history(hist, 100_000)
    assert "omitted" in out[1]["text"] and len(out[1]["text"]) < 300 and "Here's what" not in out[1]["text"]
    assert out[3]["text"] == big


def test_messages_never_exceed_context_window():
    system = "S" * 3500
    hist = [u("q" * 3000), b("a" * 3000)] * 10
    msgs = _messages("latest question", [{"text": "c" * 2000}] * 3, system, hist)
    assert msgs[0]["role"] == "system" and msgs[-1]["content"] == "latest question"
    total = sum(len(m["content"]) for m in msgs)
    assert total < NUM_CTX * 4


def test_followup_query_names_the_item_from_the_last_live_answer():
    hist = [u("tell me about the second one"),
            b("- **AI and Robotics** — Mumbai Student Organization. [312 page](https://312.iit.edu/AAR/)\n\n(as of now)")]
    q = retrieval_query("how do I join it?", hist)
    assert q.startswith("AI and Robotics") and "how do I join it?" in q
