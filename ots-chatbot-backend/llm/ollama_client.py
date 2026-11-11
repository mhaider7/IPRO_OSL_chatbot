"""Local Ollama integration — model and system prompt come from the config."""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Generator

import httpx

logger = logging.getLogger("hawk")

# 127.0.0.1, not "localhost": on Windows "localhost" resolves to IPv6 ::1 first, Ollama
# only listens on IPv4, and Windows waits ~2 s before falling back (measured on every
# message). One shared client for all requests: creating one costs ~0.3 s (TLS setup).
OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_URL = f"{OLLAMA_BASE}/api/chat"
NUM_CTX = 8192
# temperature 0.1 + fixed seed: same prompt should yield the same answer (default
# sampling was causing different handbook details on repeated questions).
GENERATE_OPTIONS = {"num_ctx": NUM_CTX, "temperature": 0.1, "seed": 42}
# How long Ollama keeps the model in memory after the last request ("60m", "2h", "-1" =
# until Ollama restarts). Ollama's default, 5 minutes, meant a ~5 s reload after short breaks.
# Plain numbers (seconds, or -1) must be sent as JSON numbers: Ollama rejects "-1" as text.
def _keep_alive(raw: str) -> str | int:
    raw = raw.strip()
    return int(raw) if raw.lstrip("-").isdigit() else raw


KEEP_ALIVE = _keep_alive(os.getenv("HAWK_OLLAMA_KEEP_ALIVE", "60m"))

_client = httpx.Client(timeout=120)
_warming = threading.Lock()

ROLE_MAP = {"user": "user", "bot": "assistant"}


def _source_name(chunk: dict) -> str:
    return Path(chunk.get("source", "")).stem.replace("_", " ") or "handbook"


def _page_label(chunk: dict) -> str | None:
    start, end = chunk.get("page_start"), chunk.get("page_end")
    if start is None:
        return None
    return f"p. {start}" if start == end else f"pp. {start}-{end}"


def _labelled(chunk: dict) -> str:
    """Prefix a chunk with its source and page so the model can cite it."""
    source = _source_name(chunk)
    pages = _page_label(chunk)
    where = f"{source}, {pages}" if pages else source
    return f"[Source: {where}]\n{chunk['text']}" if where else chunk["text"]


def citation_sources(chunks: list[dict]) -> list[str]:
    """Deduped handbook cites for the UI/API, e.g. '(student org handbook, p. 28)'."""
    seen: list[str] = []
    for chunk in chunks:
        name = _source_name(chunk)
        pages = _page_label(chunk)
        cite = f"({name}, {pages})" if pages else f"({name})"
        if cite not in seen:
            seen.append(cite)
    return seen


CHARS_PER_TOKEN = 4          # rough; enough for budgeting
REPLY_HEADROOM_CHARS = 6000  # ~1.5k tokens left free for the answer
HISTORY_SHARE = 0.4          # history may use at most this share of the context window
MAX_HISTORY_TURNS = 8
OLD_LIVE_REPLY_CHARS = 200
LIVE_LINK = "https://312.iit.edu/"


def fit_history(history: list[dict] | None, budget_chars: int) -> list[dict]:
    """The most recent turns that fit `budget_chars` (and at most MAX_HISTORY_TURNS).

    Ollama silently drops the *start* of an over-long prompt, which would be the system prompt.
    Older live-lookup answers (long result lists) are cut to their first line: the latest bot turn
    stays intact so "the second one" can still be resolved from it. The kept turns start with a
    student message.
    """
    turns = list(history or [])[-MAX_HISTORY_TURNS:]
    last_bot = max((i for i, t in enumerate(turns) if t["role"] == "bot"), default=-1)
    slim = []
    for i, t in enumerate(turns):
        text = t["text"]
        if t["role"] == "bot" and i != last_bot and LIVE_LINK in text and len(text) > OLD_LIVE_REPLY_CHARS:
            text = "[An earlier list of 312.iit.edu results was shown here and has been omitted.]"
        slim.append({**t, "text": text})
    while slim and (sum(len(t["text"]) for t in slim) > budget_chars or slim[0]["role"] != "user"):
        slim.pop(0)
    return slim


def _messages(question: str, context_chunks: list[dict], system_prompt: str, history: list[dict] | None,
              live_context: str | None = None) -> list[dict]:
    context = "\n\n".join(_labelled(c) for c in context_chunks) or "[No matching OSL material was found.]"
    if live_context:   # live 312.iit.edu lookup, already rendered and size-capped
        context += f"\n\n{live_context}"
    system = f"{system_prompt}\n\nDepartment material:\n{context}"
    window = NUM_CTX * CHARS_PER_TOKEN
    budget = max(2000, min(int(window * HISTORY_SHARE), window - len(system) - len(question) - REPLY_HEADROOM_CHARS))
    turns = [{"role": ROLE_MAP[t["role"]], "content": t["text"]} for t in fit_history(history, budget)]
    return [
        {"role": "system", "content": system},
        *turns,
        {"role": "user", "content": question},
    ]


def model_loaded(model: str) -> bool | None:
    """Whether Ollama has the model in memory right now (None = couldn't ask Ollama)."""
    try:
        response = _client.get(f"{OLLAMA_BASE}/api/ps", timeout=5)
        response.raise_for_status()
        names = {m.get("name") for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return None
    return model in names or (":" not in model and f"{model}:latest" in names)


def load_model(model: str) -> bool:
    """Load the model into memory (or just restart its keep_alive timer if it's already
    loaded) without generating anything. Blocks until it's ready, ~5 s from cold.
    Uses the same num_ctx as real requests; a different one would make Ollama reload."""
    try:
        response = _client.post(f"{OLLAMA_BASE}/api/generate", json={
            "model": model,
            "options": GENERATE_OPTIONS,
            "keep_alive": KEEP_ALIVE,
        })
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def warm_up_in_background(model: str) -> None:
    """Start load_model in a background thread; skip if a warm-up is already running."""
    def run():
        try:
            ok = load_model(model)
            logger.info("model warm-up %s", "done" if ok else "failed (is Ollama running?)")
        finally:
            _warming.release()

    if _warming.acquire(blocking=False):
        threading.Thread(target=run, name="hawk-warm-up", daemon=True).start()


def generate_answer(
    question: str,
    context_chunks: list[dict],
    system_prompt: str,
    model: str,
    history: list[dict] | None = None,
    live_context: str | None = None,
) -> str:
    try:
        response = _client.post(OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history, live_context),
            "stream": False,
            "options": GENERATE_OPTIONS,
            "keep_alive": KEEP_ALIVE,
        })
        response.raise_for_status()
        return response.json()["message"]["content"].strip()
    except httpx.ConnectError:
        return "Ollama is not running. Start it with: ollama serve"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"Model '{model}' not found in Ollama. Run: ollama pull {model}"
        raise


def stream_answer(
    question: str,
    context_chunks: list[dict],
    system_prompt: str,
    model: str,
    history: list[dict] | None = None,
    live_context: str | None = None,
) -> Generator[str, None, None]:
    try:
        with _client.stream("POST", OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history, live_context),
            "stream": True,
            "options": GENERATE_OPTIONS,
            "keep_alive": KEEP_ALIVE,
        }) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if not data.get("done"):
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield token
    except httpx.ConnectError:
        yield "Ollama is not running. Start it with: ollama serve"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            yield f"Model '{model}' not found. Run: ollama pull {model}"
        else:
            raise
