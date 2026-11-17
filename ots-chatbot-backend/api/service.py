"""RagService: retrieval + Ollama generation.

Order for each message (`_plan`):
  1. distress check (api.distress): a fixed support text, before everything else, so a crisis message is never
     routed to 312, given a fixed short reply, or refused;
  2. lookups that stay rules: an event ID or rsvp link, "the second one" / "#3" (web_tools.router.rule_call);
  3. fixed replies for "thanks" / "got it" / "no thanks" (api.conversation);
  4. a first message that is clearly off-topic by the embeddings alone -> the fixed refusal, before any model;
  5. otherwise the search picker (api.routing): one model call picks the search (handbook / clubs / events / none)
     and writes the search text; if it fails, the regex router and today's search text decide (fallback).
     "more" pages on through the search the picker made for the previous message;
  6. off-topic check (api.guardrail) on the student's own message; a refusal is a fixed text, unless a 312 lookup
     was chosen in direct mode (then the student sees only 312's own list) -- and if that lookup shows nothing,
     the refusal after all;
  7. the 312 lookup(s) (up to two) or the handbook search, then the answer model (in `direct` mode 312 results
     are shown as they are, with no model).
Steps 1-3 need no model, so they never wait for the model to load.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Generator

import chromadb

from api import routing
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
from web_tools.router import MORE, ToolCall, list_items, route as route_live_lookup, rule_call
from web_tools.tools.registry import WebTools, get_default_tools

logger = logging.getLogger("hawk")


@dataclass
class Plan:
    """What happens for one message: a fixed text, or 312 lookups, or a handbook search."""
    fixed: str | None = None
    calls: list[ToolCall] = field(default_factory=list)
    query: str = ""            # handbook search text (when there are no lookups)
    refuse_if_empty: bool = False   # allowed only because of the 312 lookup: if it shows nothing, refuse after all


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

    # --- live 312.iit.edu lookups ----------------------------------------------------------------
    def _tools(self) -> WebTools | None:
        if self._web_tools is None and self.config.web_tools.get("enabled", True):
            self._web_tools = get_default_tools(self.config.web_tools)
        return self._web_tools

    def _known_clubs(self) -> set[str] | None:
        return self._web_tools.known_club_names() if self._web_tools else None

    def _live_call(self, latest: str, history: list[dict] | None = None) -> ToolCall | None:
        """The regex router's lookup for this message (the search picker's fallback). Never raises, and builds
        no fetcher unless the message actually matches a lookup. `history` lets follow-ups resolve."""
        try:
            call = route_live_lookup(latest, self._known_clubs(), history=history)
            if call is None or self._tools() is None:
                return None
            return call
        except Exception:
            logger.exception("live-lookup routing failed")
            return None

    def _direct(self) -> bool:
        tools = self._web_tools
        return bool(tools and tools.cfg.answer_mode == "direct")

    @staticmethod
    def _searched(call: ToolCall) -> str:
        """What a lookup searched for, in the student's words ("AI", "Free Food"), for a "nothing found" reply."""
        a = call.args
        parts = [a.get("query"), *(a.get("tags") or []), a.get("event_type") or a.get("category"), a.get("type")]
        return " ".join(str(p) for p in parts if p).strip()

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
            env["searched"] = self._searched(call)
            render = render_for_user if self._direct() else render_for_prompt
            return render(env), sources
        except Exception:
            logger.exception("live lookup failed")
            return None, []

    def _run_lookups(self, calls: list[ToolCall]) -> tuple[str | None, list[str]]:
        """Up to two lookups (a club and events, when the student asked for both): their results one after the
        other, sources merged. None if none of them produced anything."""
        texts, sources = [], []
        for call in calls:
            live, live_sources = self._run_live(call)
            if live:
                texts.append(live)
                sources += [s for s in live_sources if s not in sources]
        return ("\n\n".join(texts) if texts else None), sources

    # --- choosing what runs ----------------------------------------------------------------------
    @staticmethod
    def _fixed_reply(latest: str, history: list[dict]) -> str | None:
        """A fixed reply for plain acknowledgements, thanks and "no thanks" (see api.conversation.short_reply)."""
        return FIXED_REPLIES.get(short_reply(latest, history) or "")

    def _picker_on(self) -> bool:
        return bool(self.config.search_picker.get("enabled", False))

    def _timeout(self) -> float:
        return float(self.config.search_picker.get("timeout_s", routing.DEFAULT_TIMEOUT_S))

    def _first(self, latest: str, history: list[dict]) -> tuple[str | None, ToolCall | None]:
        """Steps that need no model (so never wait for one): the support reply, then the kept lookup rules (an
        event ID or link, a list position), then the fixed short replies. Rules before the short replies, so
        "#2 thanks" after a list shows item 2. "more" is a rule here only when the search picker is off; with the
        picker on it is resolved in `_choose` (it needs the picker's earlier search)."""
        support = self._support_reply(latest, history)
        if support:
            return support, None
        try:
            rule = rule_call(latest, history, self._known_clubs(), include_more=not self._picker_on())
        except Exception:
            logger.exception("lookup rule failed")
            rule = None
        if rule is not None:
            return None, rule
        return self._fixed_reply(latest, history), None

    def _more_call(self, latest: str, history: list[dict], depth: int = 0) -> ToolCall | None:
        """"more" with the search picker on: the next page of the search that produced Hawk's last list. That
        search is found by asking the picker about the previous student message again (same conversation,
        temperature 0 -> the same search), then the offset moves past what was shown. Not when the last reply
        held two lists (which one?), or when the earlier turn had no single 312 search: then None (the picker
        decides about "more" itself)."""
        if not MORE.match(latest) or len(history) < 2 or history[-1]["role"] != "bot" or history[-2]["role"] != "user":
            return None
        bot = history[-1]["text"]
        if bot.count("Here are clubs on 312") + bot.count("Here's what's on 312") > 1:
            return None
        prev_text, prev_history = history[-2]["text"], history[:-2]
        prior = None
        if depth < 3 and MORE.match(prev_text):   # "more" again: page on from the previous "more"
            prior = self._more_call(prev_text, prev_history, depth + 1)
        if prior is None:
            prev = routing.pick(prev_text, prev_history, self.config.model, self._known_clubs(), timeout=self._timeout())
            if len(prev.calls) != 1 or prev.calls[0].name not in ("search_events", "search_clubs"):
                return None
            prior = prev.calls[0]
        args = dict(prior.args)
        args["offset"] = args.get("offset", 0) + (len(list_items(bot)) or args.get("limit", 6))
        return ToolCall(prior.name, args)

    def _choose(self, latest: str, history: list[dict]) -> routing.Pick:
        """Which search runs: the search picker, or (picker off / failed) the regex router."""
        known = self._known_clubs()
        if self._picker_on():
            more = self._more_call(latest, history)
            if more is not None:
                return routing.Pick(calls=[more], source="rule", reason="more")
            return routing.pick(latest, history, self.config.model, known, timeout=self._timeout())
        router = self._live_call(latest, history)
        return routing.fallback_pick(latest, history, known, None, "search picker off", router)

    def _plan(self, latest: str, history: list[dict], first=None) -> Plan:
        fixed, rule = first if first is not None else self._first(latest, history)
        if fixed:
            return Plan(fixed=fixed)
        shortcut = getattr(self._guardrail, "clearly_off_first", None)   # test fakes may not have it
        if rule is None and shortcut is not None and shortcut(latest, history):
            logger.info("off-topic check: refuse (clearly-off, before the search picker)")
            return Plan(fixed=self._refusal())   # obvious off-topic first messages never reach a model
        pick = routing.Pick(calls=[rule], source="rule") if rule else self._choose(latest, history)
        calls = pick.calls if self._tools() is not None else []   # 312 lookups switched off: handbook only
        # Only in direct mode does a 312 lookup show nothing but 312's own data; only then may it lift a refusal.
        verdict = self._verdict(latest, history, calls[0] if calls and self._direct() else None)
        if verdict.decision == "refuse":
            return Plan(fixed=self._refusal())   # before any lookup, search or model
        if calls:
            return Plan(calls=calls, refuse_if_empty=verdict.reason == "312-lookup")
        # "none" (a greeting, a reaction) still searches with today's text, so a wrong "none" is no worse than before
        return Plan(query=pick.handbook_query or retrieval_query(latest, history))

    def _load_before_picker(self, first) -> bool | None:
        """Load the model before the search picker runs, so a cold start isn't counted against its time limit.
        Only when the picker will run (no fixed text, no rule). Returns what Ollama said (True after loading)."""
        fixed, rule = first
        if fixed or rule is not None or not self._picker_on():
            return None
        loaded = model_loaded(self.config.model)
        if loaded is False:
            load_model(self.config.model)
            return True
        return loaded

    @staticmethod
    def _parse(request: ChatRequest) -> tuple[str, list[dict]]:
        users = [m.text for m in request.messages if m.role == "user"]
        history = [{"role": m.role, "text": m.text} for m in request.messages[:-1]]
        return users[-1], history

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
        first = self._first(latest, history)
        self._load_before_picker(first)
        plan = self._plan(latest, history, first)
        if plan.fixed:
            return ChatResponse(reply=plan.fixed, sources=[])
        live, live_sources = self._run_lookups(plan.calls) if plan.calls else (None, [])
        if plan.calls and not live and plan.refuse_if_empty:
            return ChatResponse(reply=self._refusal(), sources=[])   # the lookup was the only reason to allow it
        if plan.calls and live and self._direct():
            return ChatResponse(reply=live, sources=live_sources)
        query = plan.query or retrieval_query(latest, history)
        chunks = [] if live else retrieve(self.collection, query)   # live data replaces handbook noise
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

        Stages are only sent when they really start: "loading" (only if Ollama reports the model isn't in
        memory; it is loaded before the search picker runs, so a cold start isn't counted against its time
        limit), "searching" (handbook search), "searching_clubs"/"searching_events" (live 312 lookup, instead
        of the handbook search), "writing" (the model reads the material and writes; ends at the first token).
        Clients that don't know `stage` lines can ignore them.

        Live 312 lookups (see web_tools): in `direct` answer mode the rendered result is
        streamed as the answer without calling the model; in `llm` mode it is given to the
        model as extra material. Either way the handbook is not searched for that turn.

        Empty retrieval still calls the model so greetings and persona questions are
        handled by the system prompt instead of a fixed low-confidence reply.

        Handbook cites are sent only on `done.sources` (from chunk metadata), not
        inlined into the reply text.
        """
        latest, history = self._parse(request)
        loaded = None
        first = self._first(latest, history)   # distress / rules / short replies first: never wait for a model
        fixed, rule = first
        if not fixed and rule is None and self._picker_on():
            loaded = model_loaded(self.config.model)
            if loaded is False:   # None = Ollama unreachable: say nothing
                yield json.dumps({"stage": "loading"}) + "\n"
                load_model(self.config.model)
                loaded = True
        plan = self._plan(latest, history, first)
        if plan.fixed:   # support reply / "got it" / "thanks" / "no thanks" / refusal: nothing for the model to invent
            yield from self._fixed_lines(plan.fixed)
            return
        live, live_sources = None, []
        if plan.calls:
            texts = []
            for call in plan.calls:
                yield json.dumps({"stage": call.stage}) + "\n"
                text, sources = self._run_live(call)
                if text:
                    texts.append(text)
                    live_sources += [s for s in sources if s not in live_sources]
            live = "\n\n".join(texts) if texts else None
            if not live and plan.refuse_if_empty:   # the lookup was the only reason to allow it
                yield from self._fixed_lines(self._refusal())
                return
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
        query = plan.query or retrieval_query(latest, history)
        chunks = [] if live else retrieve(self.collection, query)
        if loaded is None and model_loaded(self.config.model) is False:   # None = Ollama unreachable: say nothing
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

    @staticmethod
    def _fixed_lines(text: str):
        yield json.dumps({"token": text}) + "\n"
        yield json.dumps({
            "done": True, "escalate": False, "escalation_reason": None,
            "offer_options": True, "sources": [],
        }) + "\n"

    def warm_up(self) -> None:
        """Load the model in the background (or restart its keep_alive timer); returns at once."""
        warm_up_in_background(self.config.model)

    def escalate(self, request: Conversation) -> EscalationResponse:
        return build_email_draft(request, self.config)
