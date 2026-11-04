"""Build an email draft from the conversation. No sending — draft only."""
import re

from api.schemas import Conversation, EmailDraft, EscalationResponse
from config.loader import Config

CLARIFY_ISSUE = "What would you like help with? Please describe the issue in a sentence or two."
CLARIFY_DETAIL = "Can you give a bit more detail about the situation? (You can say 'skip' if you prefer.)"
QUESTIONS = {CLARIFY_ISSUE, CLARIFY_DETAIL}


def _redact(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email omitted]", text)
    text = re.sub(r"\b[Aa]\d{8}\b", "[student ID omitted]", text)
    return text


def build_email_draft(conversation: Conversation, config: Config) -> EscalationResponse:
    users = [m.text for m in conversation.messages if m.role == "user"]
    asked = any(m.role == "bot" and m.text in QUESTIONS for m in conversation.messages)

    if not asked and not users:
        return EscalationResponse(clarifying_question=CLARIFY_ISSUE)
    if not asked and len(users) == 1 and len(users[0].split()) < 8:
        return EscalationResponse(clarifying_question=CLARIFY_DETAIL)

    details = [_redact(t) for t in users if t.lower().strip() != "skip"]
    summary = "\n".join(f"- {t}" for t in details) or "[No details provided.]"

    return EscalationResponse(email=EmailDraft(
        to=config.email,
        subject=f"{config.short_name} Support Request",
        body=(
            f"Hello {config.name},\n\n"
            "I would appreciate help with the following:\n\n"
            + summary +
            "\n\nCould you advise me on the next steps?\n\nThank you."
        ),
    ))
