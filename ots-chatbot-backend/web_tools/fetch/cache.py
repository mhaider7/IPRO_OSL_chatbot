"""Disk-backed response cache with TTL, so restarts don't re-download multi-MB pages.

Entries are never deleted on expiry: an expired entry is still the best answer when a live
fetch fails (stale-if-error). Freshness is decided by the caller via `is_fresh`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("hawk")


@dataclass
class CacheEntry:
    url: str
    body: bytes
    retrieved_at: float          # epoch seconds of last successful (re)validation
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    final_url: str | None = None


class ResponseCache:
    def __init__(self, directory: Path | None, clock: Callable[[], float] = time.time):
        self._dir = directory
        self._clock = clock
        self._mem: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        if directory is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # cache is an optimisation; never fatal
                logger.warning("web cache disabled (cannot create %s): %s", directory, exc)
                self._dir = None

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:32]

    def get(self, url: str) -> CacheEntry | None:
        with self._lock:
            entry = self._mem.get(url)
        if entry is not None or self._dir is None:
            return entry
        try:
            k = self._key(url)
            meta = json.loads((self._dir / f"{k}.json").read_text())
            body = (self._dir / f"{k}.body").read_bytes()
            entry = CacheEntry(body=body, **meta)
        except (OSError, ValueError, TypeError):
            return None
        with self._lock:
            self._mem[url] = entry
        return entry

    def put(self, entry: CacheEntry) -> None:
        with self._lock:
            self._mem[entry.url] = entry
        if self._dir is None:
            return
        try:
            k = self._key(entry.url)
            meta = asdict(entry)
            meta.pop("body")
            (self._dir / f"{k}.body.tmp").write_bytes(entry.body)
            (self._dir / f"{k}.body.tmp").replace(self._dir / f"{k}.body")
            (self._dir / f"{k}.json.tmp").write_text(json.dumps(meta))
            (self._dir / f"{k}.json.tmp").replace(self._dir / f"{k}.json")
        except OSError as exc:
            logger.warning("could not persist web cache entry: %s", exc)

    def touch(self, entry: CacheEntry) -> None:
        """A 304 revalidated the entry: reset its freshness clock."""
        entry.retrieved_at = self._clock()
        self.put(entry)

    def is_fresh(self, entry: CacheEntry, ttl: float) -> bool:
        return (self._clock() - entry.retrieved_at) < ttl
