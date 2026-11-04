"""Local Ollama integration — model and system prompt come from the config."""
import json
from typing import Generator

import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
NUM_CTX = 8192

ROLE_MAP = {"user": "user", "bot": "assistant"}


def _messages(question: str, context_chunks: list[str], system_prompt: str, history: list[dict] | None) -> list[dict]:
    context = "\n\n".join(context_chunks) or "[No matching OSL material was found.]"
    turns = [{"role": ROLE_MAP[t["role"]], "content": t["text"]} for t in (history or [])]
    return [
        {"role": "system", "content": f"{system_prompt}\n\nDepartment material:\n{context}"},
        *turns,
        {"role": "user", "content": question},
    ]


def generate_answer(
    question: str,
    context_chunks: list[str],
    system_prompt: str,
    model: str,
    history: list[dict] | None = None,
) -> str:
    try:
        response = httpx.post(OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history),
            "stream": False,
            "options": {"num_ctx": NUM_CTX},
        }, timeout=120)
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
    context_chunks: list[str],
    system_prompt: str,
    model: str,
    history: list[dict] | None = None,
) -> Generator[str, None, None]:
    try:
        with httpx.stream("POST", OLLAMA_URL, json={
            "model": model,
            "messages": _messages(question, context_chunks, system_prompt, history),
            "stream": True,
            "options": {"num_ctx": NUM_CTX},
        }, timeout=120) as response:
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
