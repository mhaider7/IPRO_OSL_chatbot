"""Validated tool arguments (pydantic) and the JSON-schema specs for each tool."""
from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchClubsArgs(_Args):
    query: str | None = Field(None, max_length=200, description="Free-text interest or name, e.g. 'robotics'.")
    category: str | None = Field(None, max_length=100, description="Category from the listing, e.g. 'Arts and Media'.")
    type: str | None = Field(None, max_length=100, description="Group type, e.g. 'Student Organization', 'Residence Hall'.")
    include_archived: bool = Field(False, description="Accepted for forward compatibility; the public listing only contains active groups.")
    limit: int = Field(10, ge=1, le=25)
    offset: int = Field(0, ge=0, le=500, description="Skip this many matches (for 'show more').")


class GetClubDetailsArgs(_Args):
    slug: str = Field(..., min_length=1, max_length=200, description="312 slug (e.g. 'box'), numeric club id, or exact club name.")


class SearchEventsArgs(_Args):
    query: str | None = Field(None, max_length=200, description="Free text matched against title, description, host club, type and tags.")
    date_from: date | None = Field(None, description="ISO date (YYYY-MM-DD), Central time. Default: today.")
    date_to: date | None = Field(None, description="ISO date, inclusive. Default: date_from + 14 days.")
    category: str | None = Field(None, max_length=100, description="Alias of event_type (the public feed has no separate category).")
    event_type: str | None = Field(None, max_length=100, description="e.g. 'Social', 'Meeting', 'Career Preparation', 'Athletic/Sports'.")
    tags: list[str] | None = Field(None, max_length=10, description="Match ANY of these tags, e.g. ['Free Food'].")
    location_type: Literal["in_person", "online", "tbd", "hidden"] | None = None
    time_from: time | None = Field(None, description="HH:MM Central. On today's date, ignore events that ended before this time (e.g. 17:00 for 'tonight').")
    limit: int = Field(10, ge=1, le=25)
    offset: int = Field(0, ge=0, le=500, description="Skip this many matches (for 'show more').")

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_str(cls, v):
        return [v] if isinstance(v, str) else v

    @model_validator(mode="after")
    def _order(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class GetEventDetailsArgs(_Args):
    event_id_or_url: str = Field(..., min_length=1, max_length=500,
                                 description="Event id, https://312.iit.edu/rsvp?id=N, or the /w/_/_/events/<id> URL.")


TOOL_SPECS: list[dict] = [
    {
        "name": "search_clubs",
        "description": "Search Illinois Tech clubs/groups listed on 312.iit.edu by interest, category or type.",
        "input_schema": SearchClubsArgs.model_json_schema(),
    },
    {
        "name": "get_club_details",
        "description": "Full listing details (mission, benefits, flags, links) for one 312 club.",
        "input_schema": GetClubDetailsArgs.model_json_schema(),
    },
    {
        "name": "search_events",
        "description": "Search upcoming Illinois Tech events from the public 312.iit.edu calendar feed.",
        "input_schema": SearchEventsArgs.model_json_schema(),
    },
    {
        "name": "get_event_details",
        "description": "Full public details for one 312 event (description, host, time, location if public).",
        "input_schema": GetEventDetailsArgs.model_json_schema(),
    },
]
