"""Plain data shared by extraction, planning, checks, rounds and copy. JSON round-trippable."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")  # India has no DST; no tzdata needed (eng E9)

# Named time windows, minutes after IST midnight (CEO extraction rules).
WINDOWS = {
    "subah": (8 * 60, 12 * 60),
    "dopahar": (12 * 60, 16 * 60),
    "shaam": (17 * 60, 20 * 60),
    "raat": (19 * 60, 23 * 60 + 30),
}

TERMINAL = frozenset({"BOOKED", "TEST_ONLY", "FAILED", "NEEDS_REVIEW", "CANCELLED", "EXPIRED"})
LOCKED_STATES = frozenset({"RSVP", "BOOK_PENDING", "BOOKING"})


def now_ist(ts: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, IST)


@dataclass
class Person:
    user_id: int
    name: str
    veg: bool | None = None
    budget: int | None = None
    available: dict[str, bool] = field(default_factory=dict)  # ISO date -> can come
    not_coming: bool = False
    source: str = "chat"  # "chat" (said something) | "tap" (only tapped a button, design DR4)


@dataclass
class Constraints:
    date: str | None = None  # ISO date
    window: tuple[int, int] = WINDOWS["raat"]
    window_label: str = "raat"
    people: dict[int, Person] = field(default_factory=dict)
    headcount: int = 0
    stated_headcount: int | None = None
    budget: int | None = None  # group per-head limit = min stated budget
    genres: list[str] = field(default_factory=list)  # "comedy" | "music" | ...; empty = dinner only
    gaps: list[str] = field(default_factory=list)  # ordered: date, time, budget, genre, headcount
    assumed: list[str] = field(default_factory=list)  # human-readable defaults for the 🤖 line
    tie_dates: list[str] = field(default_factory=list)
    extra: str = ""  # organizer's Badlo correction text, fed back into extraction

    def coming(self) -> list[Person]:
        return [p for p in self.people.values() if not p.not_coming]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["people"] = {str(k): asdict(v) for k, v in self.people.items()}
        d["window"] = list(self.window)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Constraints:
        d = dict(d)
        d["people"] = {int(k): Person(**v) for k, v in (d.get("people") or {}).items()}
        d["window"] = tuple(d.get("window") or WINDOWS["raat"])
        return cls(**d)


@dataclass
class EventFacts:
    event_id: str
    name: str
    venue_id: str
    venue_name: str
    venue_lat: float | None
    venue_lng: float | None
    show_id: str
    start: float  # unix seconds
    end: float
    end_estimated: bool
    ticket_price: int | None  # cheapest per-person ticket, ₹
    area: str = ""
    group_distance_km: float | None = None


@dataclass
class RestaurantFacts:
    restaurant_id: str
    name: str
    cuisines: list[str]
    cost_for_two: int | None
    distance_km: float | None  # from the point it was searched at
    searched_at_venue: bool
    area: str = ""
    rating: float | None = None


@dataclass
class PlanOption:
    idx: int
    title: str
    restaurant: RestaurantFacts
    reservation_time: int  # unix seconds
    slot_id: int
    item_id: str
    event: EventFacts | None = None
    per_head: int | None = None
    ticket_unknown: bool = False
    fit: list[tuple[str, str, str]] = field(default_factory=list)  # (name, field, "ok"|"bad"|"unknown")

    @property
    def kind(self) -> str:
        return "event" if self.event else "dinner"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> PlanOption:
        d = dict(d)
        d["restaurant"] = RestaurantFacts(**d["restaurant"])
        d["event"] = EventFacts(**d["event"]) if d.get("event") else None
        d["fit"] = [tuple(x) for x in d.get("fit") or []]
        return cls(**d)
