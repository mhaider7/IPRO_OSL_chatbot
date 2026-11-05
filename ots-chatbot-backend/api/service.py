"""RagService: retrieval + Ollama generation. No hardcoded topic routing."""
import json
from typing import Generator

import chromadb

from api.schemas import ChatRequest, ChatResponse, Conversation, EscalationResponse
from config.loader import Config
from escalation.email_builder import build_email_draft
from llm.ollama_client import generate_answer, stream_answer
from retrieval.chroma_setup import retrieve


class RagService:
    def __init__(self, config: Config, collection: chromadb.api.models.Collection.Collection):
        self.config = config
        self.collection = collection

    def _retrieval_args(self, request: ChatRequest) -> tuple[str, list[dict], list[dict]]:
        users = [m.text for m in request.messages if m.role == "user"]
        latest = users[-1]
        history = [{"role": m.role, "text": m.text} for m in request.messages[:-1]]
        chunks = retrieve(self.collection, latest)
        return latest, chunks, history

    def chat(self, request: ChatRequest) -> ChatResponse:
        latest, chunks, history = self._retrieval_args(request)
        if not chunks:
            return ChatResponse(
                reply=(
                    f"I don't have information on that in the {self.config.name} materials. "
                    f"You can contact {self.config.name} directly or use the email option below."
                ),
                escalate=True,
                escalation_reason="low_confidence",
            )
        answer = generate_answer(
            question=latest,
            context_chunks=chunks,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            history=history,
        )
        return ChatResponse(reply=answer)

    def chat_stream(self, request: ChatRequest) -> Generator[str, None, None]:
        latest, chunks, history = self._retrieval_args(request)
        if not chunks:
            yield json.dumps({
                "done": True,
                "reply": (
                    f"I don't have information on that in the {self.config.name} materials. "
                    f"You can contact {self.config.name} directly or use the email option below."
                ),
                "escalate": True,
                "escalation_reason": "low_confidence",
                "offer_options": False,
            }) + "\n"
            return
        for token in stream_answer(
            question=latest,
            context_chunks=chunks,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            history=history,
        ):
            yield json.dumps({"token": token}) + "\n"
        yield json.dumps({
            "done": True,
            "escalate": False,
            "escalation_reason": None,
            "offer_options": True,
        }) + "\n"

    def escalate(self, request: Conversation) -> EscalationResponse:
        return build_email_draft(request, self.config)
