"""Settings for the live-web tools (overridable under `web_tools:` in config.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

CONTACT_PLACEHOLDER = "CONTACT_PLACEHOLDER"


@dataclass
class WebToolsConfig:
    enabled: bool = True
    contact: str = field(default_factory=lambda: os.getenv("HAWK_FETCH_CONTACT", CONTACT_PLACEHOLDER))
    min_interval_s: float = 2.0          # per-host spacing between requests
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 20.0
    feed_read_timeout_s: float = 40.0
    max_body_bytes: int = 8 * 1024 * 1024
    max_redirects: int = 3
    max_wait_s: float = 10.0             # never sleep longer than this inside a chat request
    events_ttl_s: float = 30 * 60        # plan: events 15-60 min
    clubs_ttl_s: float = 12 * 3600       # plan: clubs 12-24 h
    robots_ttl_s: float = 3600.0
    answer_mode: str = "direct"          # direct = stream tool results as-is; llm = give them to the model to phrase
    min_events: int = 20                 # same idea for the iCal feed
    min_clubs: int = 50                  # fewer parsed clubs than this = layout drift, not a real listing
    cache_dir: Path = Path(__file__).resolve().parent.parent / ".cache" / "web_tools"

    @property
    def user_agent(self) -> str:
        return f"HawkBot/0.1 (IIT OSL campus chatbot; contact: {self.contact})"

    def __post_init__(self):
        if self.answer_mode not in ("direct", "llm"):
            raise ValueError("web_tools.answer_mode must be 'direct' or 'llm'")

    @classmethod
    def from_dict(cls, raw: dict | None) -> "WebToolsConfig":
        raw = dict(raw or {})
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown web_tools settings: {sorted(unknown)}")
        if "cache_dir" in raw:
            raw["cache_dir"] = Path(raw["cache_dir"])
        return cls(**raw)
