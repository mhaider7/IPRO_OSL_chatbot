"""RagService: retrieval + Ollama generation. No hardcoded topic routing."""
import json
import logging
from typing import Generator

import chromadb

from api.schemas import ChatRequest, ChatResponse, Conversation, EscalationResponse
from config.loader import Config
from escalation.email_builder import build_email_draft
from llm.ollama_client import (
    citation_sources, generate_answer, load_model, model_loaded,
    stream_answer, warm_up_in_background,
)
from retrieval.chroma_setup import retrieve
from web_tools.render import render_for_prompt, render_for_user
from web_tools.router import ToolCall, route as route_live_lookup
from web_tools.tools.registry import WebTools, get_default_tools

logger = logging.getLogger("hawk")


class RagService:
    def __init__(self, config: Config, collection: chromadb.api.models.Collection.Collection,
                 web_tools: WebTools | None = None):
        self.config = config
        self.collection = collection
        self._web_tools = web_tools

    # --- live 312.iit.edu lookups (clubs; events soon) -------------------------------------
    def _tools(self) -> WebTools | None:
        if self._web_tools is None and self.config.web_tools.get("enabled", True):
            self._web_tools = get_default_tools(self.config.web_tools)
        return self._web_tools

    def _live_call(self, latest: str) -> ToolCall | None:
        """Which live tool (if any) this message needs. Never raises, and builds no fetcher
        unless the message actually matches a lookup."""
        try:
            known = self._web_tools.known_club_names() if self._web_tools else None
            call = route_live_lookup(latest, known)
            if call is None or self._tools() is None:
                return None
            return call
        except Exception:
            logger.exception("live-lookup routing failed")
            return None

    def _direct(self) -> bool:
        tools = self._web_tools
        return bool(tools and tools.cfg.answer_mode == "direct")

    def _run_live(self, call: ToolCall) -> tuple[str | None, list[str]]:
        """Run the tool and render it (for the prompt, or as the final answer in direct mode).
        A failure never breaks the chat."""
        try:
            tools = self._tools()
            env = tools.run(call.name, call.args)
            err = env.get("error") or {}
            if call.fallback and err.get("code") == "no_results":
                env = tools.run(call.fallback.name, call.fallback.args)
                if env.get("ok") and isinstance(env.get("data"), dict):
                    env["data"]["no_exact_match"] = call.args.get("slug")  # these are only near-matches
            sources = [f"(live data from {env['source_url']})"] if env.get("ok") and env.get("source_url") else []
            env["intent"] = call.intent
            render = render_for_user if self._direct() else render_for_prompt
            return render(env), sources
        except Exception:
            logger.exception("live lookup failed")
            return None, []

    @staticmethod
    def _parse(request: ChatRequest) -> tuple[str, list[dict]]:
        users = [m.text for m in request.messages if m.role == "user"]
        history = [{"role": m.role, "text": m.text} for m in request.messages[:-1]]
        return users[-1], history

    def _retrieval_args(self, request: ChatRequest) -> tuple[str, list[dict], list[dict]]:
        latest, history = self._parse(request)
        return latest, retrieve(self.collection, latest), history

    @staticmethod
    def _chunks_of_text(text: str):
        """Split a finished answer into line-sized pieces so the widget still renders progressively."""
        for line in text.splitlines(keepends=True):
            yield line

    def chat(self, request: ChatRequest) -> ChatResponse:
        # Empty retrieval still goes to the LLM so greetings / "what do you cover?"
        # can use the system prompt (Persona Exception). Context then shows
        # "[No matching OSL material was found.]".
        latest, history = self._parse(request)
        call = self._live_call(latest)
        live, live_sources = self._run_live(call) if call else (None, [])
        if call and live and self._direct():
            return ChatResponse(reply=live, sources=live_sources)
        chunks = [] if live else retrieve(self.collection, latest)   # live data replaces handbook noise
        extra = {"live_context": live} if live else {}
        answer = generate_answer(
            question=latest,
            context_chunks=chunks,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            history=history,
            **extra,
        )
        return ChatResponse(reply=answer, sources=citation_sources(chunks) + live_sources)

    def chat_stream(self, request: ChatRequest) -> Generator[str, None, None]:
        """NDJSON lines: {stage} while working, then {token}s, then one {done}.

        Stages are only sent when they really start: "searching" (handbook search),
        "searching_clubs"/"searching_events" (live 312 lookup, instead of the handbook
        search), "loading" (only if Ollama reports the model isn't in memory; it is loaded
        before moving on), "writing" (the model reads the material and writes; ends at the
        first token). Clients that don't know `stage` lines can ignore them.

        Live 312 lookups (see web_tools): in `direct` answer mode the rendered result is
        streamed as the answer without calling the model; in `llm` mode it is given to the
        model as extra material. Either way the handbook is not searched for that turn.

        Empty retrieval still calls the model so greetings and persona questions are
        handled by the system prompt instead of a fixed low-confidence reply.

        Handbook cites are sent only on `done.sources` (from chunk metadata), not
        inlined into the reply text.
        """
        latest, history = self._parse(request)
        call = self._live_call(latest)
        live, live_sources = None, []
        if call:
            yield json.dumps({"stage": call.stage}) + "\n"
            live, live_sources = self._run_live(call)
            if live and self._direct():
                for piece in self._chunks_of_text(live):
                    yield json.dumps({"token": piece}) + "\n"
                yield json.dumps({
                    "done": True, "escalate": False, "escalation_reason": None,
                    "offer_options": True, "sources": live_sources,
                }) + "\n"
                return
        else:
            yield json.dumps({"stage": "searching"}) + "\n"
        chunks = [] if live else retrieve(self.collection, latest)
        if model_loaded(self.config.model) is False:   # None = Ollama unreachable: say nothing
            yield json.dumps({"stage": "loading"}) + "\n"
            load_model(self.config.model)
        yield json.dumps({"stage": "writing"}) + "\n"
        extra = {"live_context": live} if live else {}
        for token in stream_answer(
            question=latest,
            context_chunks=chunks,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            history=history,
            **extra,
        ):
            yield json.dumps({"token": token}) + "\n"
        yield json.dumps({
            "done": True,
            "escalate": False,
            "escalation_reason": None,
            "offer_options": True,
            "sources": citation_sources(chunks) + live_sources,
        }) + "\n"

    def warm_up(self) -> None:
        """Load the model in the background (or restart its keep_alive timer); returns at once."""
        warm_up_in_background(self.config.model)

    def escalate(self, request: Conversation) -> EscalationResponse:
        return build_email_draft(request, self.config)
