"""Common result envelope shared by every tool."""
from __future__ import annotations

from typing import Any

from web_tools.fetch.client import FetchResult


def envelope(*, ok: bool, data: Any = None, source_url: str, fetch: FetchResult | None = None,
             stale: bool = False, stale_reason: str | None = None, error: tuple[str, str] | None = None,
             auth_required: bool = False, extra: dict | None = None) -> dict:
    out = {
        "ok": ok,
        "data": data,
        "source_url": source_url,
        "retrieved_at": fetch.retrieved_at if fetch else None,
        "stale": stale or bool(fetch and fetch.stale),
        "stale_reason": stale_reason or (fetch.stale_reason if fetch else None),
        "auth_required": auth_required or bool(fetch and fetch.auth_required),
        "truncated": False,
        "error": {"code": error[0], "message": error[1]} if error else None,
    }
    if extra:
        out.update(extra)
    return out


def error_envelope(code: str, message: str, source_url: str, fetch: FetchResult | None = None) -> dict:
    return envelope(ok=False, source_url=source_url, fetch=fetch, error=(code, message))
