import pytest

from api.conversation import is_followup, retrieval_query
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
