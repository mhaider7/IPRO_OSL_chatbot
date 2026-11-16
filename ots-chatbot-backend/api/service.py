"""RagService: retrieval + Ollama generation.

Order for each message: distress check (api.distress; the support reply is a fixed text, and it runs before
everything else so a crisis message is never routed to 312, given a fixed short reply, or refused) -> 312 router
decision (rules only, nothing fetched yet) -> fixed replies for "thanks" / "got it" (api.conversation) ->
off-topic check (api.guardrail; a refusal is a fixed text) -> 312 lookup or handbook search -> the answer model.
"""
import json
import logging
from typing import Generator

import chromadb

from api.conversation import FIXED_REPLIES, retrieval_query, short_reply
from api.distress import DistressCheck
from api.guardrail import Guardrail, Verdict, refusal_text
from api.schemas import ChatRequest, ChatResponse, Conversation, EscalationResponse
from config.loader import Config
from escalation.email_builder import build_email_draft
from llm.ollama_client import (
    citation_sources, generate_answer, load_model, model_loaded,
    stream_answer, warm_up_in_background,
)
from retrieval.chroma_setup import EMBEDDING_FUNCTION, retrieve
from web_tools.render import render_for_prompt, render_for_user
from web_tools.router import ToolCall, route as route_live_lookup
from web_tools.tools.registry import WebTools, get_default_tools

logger = logging.getLogger("hawk")


class RagService:
    def __init__(self, config: Config, collection: chromadb.api.models.Collection.Collection,
                 web_tools: WebTools | None = None, guardrail: Guardrail | None = None,
                 distress: DistressCheck | None = None):
        self.config = config
        self.collection = collection
        self._web_tools = web_tools
        # The off-topic check needs the handbook index; without one (only in tests) there is no check.
        if guardrail is None and collection is not None:
            guardrail = Guardrail.for_collection(collection, EMBEDDING_FUNCTION, config.model)
        self._guardrail = guardrail
        # The distress check always runs. Without a handbook index (only in tests) it has its patterns only.
        if distress is None:
            distress = DistressCheck(
                EMBEDDING_FUNCTION if collection is not None else None,
                (lambda text: guardrail.scores(text)["margin"]) if guardrail is not None else None,
            )
        self._distress = distress

    # --- distress check (api/distress.py) ---------------------------------------------------------
    def _support_reply(self, latest: str, history: list[dict]) -> str | None:
        verdict = self._distress.check(latest, history)
        if verdict.reply:
            logger.info("distress check: fixed reply (%s)", verdict.reason)   # never the message itself
        return verdict.reply

    # --- off-topic check (api/guardrail.py) ------------------------------------------------------
    def _verdict(self, latest: str, history: list[dict], call: ToolCall | None) -> Verdict:
        if self._guardrail is None:
            return Verdict("allow", "no-check")
        verdict = self._guardrail.check(latest, history, call)
        logger.info("off-topic check: %s (%s)", verdict.decision, verdict.reason)
        return verdict

    def _refusal(self) -> str:
        return refusal_text(self.config.contact_email, self.config.contact_phone)

    # --- live 312.iit.edu lookups (clubs; events soon) -------------------------------------
    def _tools(self) -> WebTools | None:
        if self._web_tools is None and self.config.web_tools.get("enabled", True):
            self._web_tools = get_default_tools(self.config.web_tools)
        return self._web_tools

    def _live_call(self, latest: str, history: list[dict] | None = None) -> ToolCall | None:
        """Which live tool (if any) this message needs. Never raises, and builds no fetcher
        unless the message actually matches a lookup. `history` lets follow-ups resolve."""
        try:
            known = self._web_tools.known_club_names() if self._web_tools else None
            call = route_live_lookup(latest, known, history=history)
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
    def _fixed_reply(latest: str, history: list[dict]) -> str | None:
        """A fixed reply for plain acknowledgements, thanks and "no thanks" (see api.conversation.short_reply)."""
        return FIXED_REPLIES.get(short_reply(latest, history) or "")

    @staticmethod
    def _parse(request: ChatRequest) -> tuple[str, list[dict]]:
        users = [m.text for m in request.messages if m.role == "user"]
        history = [{"role": m.role, "text": m.text} for m in request.messages[:-1]]
        return users[-1], history

    def _retrieval_args(self, request: ChatRequest) -> tuple[str, list[dict], list[dict]]:
        latest, history = self._parse(request)
        return latest, retrieve(self.collection, retrieval_query(latest, history)), history

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
        support = self._support_reply(latest, history)
        if support:   # before the router, the fixed replies and the off-topic check
            return ChatResponse(reply=support, sources=[])
        call = self._live_call(latest, history)
        fixed = None if call else self._fixed_reply(latest, history)
        if fixed:
            return ChatResponse(reply=fixed, sources=[])
        if self._verdict(latest, history, call).decision == "refuse":   # before any lookup, search or model
            return ChatResponse(reply=self._refusal(), sources=[])
        live, live_sources = self._run_live(call) if call else (None, [])
        if call and live and self._direct():
            return ChatResponse(reply=live, sources=live_sources)
        chunks = [] if live else retrieve(self.collection, retrieval_query(latest, history))   # live data replaces handbook noise
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
        fixed = self._support_reply(latest, history)   # distress: fixed text, before everything else
        call = None if fixed else self._live_call(latest, history)
        live, live_sources = None, []
        if not fixed and not call:
            fixed = self._fixed_reply(latest, history)
        if not fixed and self._verdict(latest, history, call).decision == "refuse":
            fixed = self._refusal()   # off-topic: fixed text, no lookup, no search, no model, no sources
        if fixed:   # support reply / "got it" / "thanks" / "no thanks" / refusal: nothing for the model to invent
            yield json.dumps({"token": fixed}) + "\n"
            yield json.dumps({
                "done": True, "escalate": False, "escalation_reason": None,
                "offer_options": True, "sources": [],
            }) + "\n"
            return
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
        chunks = [] if live else retrieve(self.collection, retrieval_query(latest, history))
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
