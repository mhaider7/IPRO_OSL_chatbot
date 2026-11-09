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


def _labelled(chunk: dict) -> str:
    """Prefix a chunk with its source and page so the model can cite it."""
    source = Path(chunk.get("source", "")).stem.replace("_", " ")
    start, end = chunk.get("page_start"), chunk.get("page_end")
    if start is None:
        where = source
    else:
        pages = f"p. {start}" if start == end else f"pp. {start}-{end}"
        where = f"{source}, {pages}"
    return f"[Source: {where}]\n{chunk['text']}" if where else chunk["text"]


def _messages(question: str, context_chunks: list[dict], system_prompt: str, history: list[dict] | None) -> list[dict]:
    context = "\n\n".join(_labelled(c) for c in context_chunks) or "[No matching OSL material was found.]"
    turns = [{"role": ROLE_MAP[t["role"]], "content": t["text"]} for t in (history or [])]
    return [
        {"role": "system", "content": f"{system_prompt}\n\nDepartment material:\n{context}"},
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
            "options": {"num_ctx": NUM_CTX},
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
) -> str:
    try:
        response = _client.post(OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history),
            "stream": False,
            "options": {"num_ctx": NUM_CTX},
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
) -> Generator[str, None, None]:
    try:
        with _client.stream("POST", OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history),
            "stream": True,
            "options": {"num_ctx": NUM_CTX},
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
