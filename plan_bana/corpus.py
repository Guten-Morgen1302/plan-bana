"""Structured record of everything Swiggy told us during one planning run (eng R3-2).

Checks read facts from here, keyed by the ids the model proposed, never from model-written text.
Parsers match the live formats captured in tests/fixtures (probe of 2026-09-30):
  search_events        structured {"suggestions": [{eventId, eventName, location}]}
  get_event_details    structured {eventId, eventName, venueId, venueName, area, venueLat, venueLng, ...}
  list_event_shows     structured {"eventShowListing": {"dateGroupedEventShow": [{"eventShow": [...]}]}}
  search_restaurants   text only: "1. Name — Cuisines | 4.2★ | ₹1000 for two | Area (ID: 708583)"
  get_restaurant_details  structured {"restaurant": {cuisines, costForTwo, address "2 km • …"}}
  get_available_slots  text: '2026-10-03 [FREE]: slotId=1, itemId="864731-735129"' and
                       "  Dinner: 05:00 PM→1791027000, 05:15 PM→1791027900, …" under "Slots for 2026-10-03:"
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any

SHOW_DEFAULT_MIN = 90  # design: unknown duration → start + 90 min, labelled "~"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_to_unix(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _rupees(s: Any) -> int | None:
    m = re.search(r"₹\s*([\d,]+)", str(s or ""))
    return int(m[1].replace(",", "")) if m else None


# ---------- Scenes ----------

def parse_suggestions(structured: Any) -> list[dict[str, str]]:
    items = (structured or {}).get("suggestions") or [] if isinstance(structured, dict) else []
    return [{"eventId": str(s["eventId"]), "name": str(s.get("eventName", "")), "location": str(s.get("location", ""))}
            for s in items if isinstance(s, dict) and s.get("eventId")]


@dataclass
class EventInfo:
    event_id: str
    name: str
    venue_id: str
    venue_name: str
    area: str
    venue_lat: float | None
    venue_lng: float | None


def parse_event_details(structured: Any) -> EventInfo | None:
    s = structured if isinstance(structured, dict) else {}
    if not s.get("eventId") or not s.get("venueId"):
        return None
    return EventInfo(str(s["eventId"]), str(s.get("eventName", "")), str(s["venueId"]), str(s.get("venueName", "")),
                     str(s.get("area", "")), _float(s.get("venueLat")), _float(s.get("venueLng")))


@dataclass
class ShowInfo:
    show_id: str
    event_id: str
    venue_id: str
    start: float
    end: float
    end_estimated: bool
    ticket_price: int | None
    available: bool


def parse_shows(structured: Any) -> list[ShowInfo]:
    listing = (structured or {}).get("eventShowListing") or {} if isinstance(structured, dict) else {}
    out = []
    for day in listing.get("dateGroupedEventShow") or []:
        for sh in day.get("eventShow") or []:
            tr = sh.get("timeRange") or {}
            start = _iso_to_unix(tr.get("startTime")) or _float(sh.get("show_time_unix"))
            if start is None:
                continue
            end = _iso_to_unix(tr.get("endTime"))
            estimated = end is None or end <= start
            if estimated:
                end = start + SHOW_DEFAULT_MIN * 60
            prices = []
            for t in sh.get("tickets") or []:
                units = (((t.get("price") or {}).get("discountedPrice") or (t.get("price") or {}).get("price") or {})
                         .get("units"))
                per = 2 if "two" in str(t.get("name", "")).lower() else 1
                if units is not None and (t.get("availableInventory") or 0) > 0:
                    prices.append(math.ceil(int(units) / per))
            available = bool(sh.get("maxAvailableInventory", 1)) and "ON_SALE" in str(sh.get("status", "ON_SALE"))
            out.append(ShowInfo(str(sh.get("id")), str(sh.get("eventId")), str((sh.get("venue") or {}).get("venueId", "")),
                                start, end, estimated, min(prices) if prices else None, available))
    return out


# ---------- Dineout ----------

_SEARCH_LINE = re.compile(r"^\s*\d+\.\s+(?P<name>.+?)\s+—\s+(?P<rest>.+?)\s+\(ID:\s*(?P<id>\d+)\)\s*$")


def parse_restaurant_search(text: str) -> list[dict[str, Any]]:
    out = []
    for line in (text or "").splitlines():
        m = _SEARCH_LINE.match(line)
        if not m:
            continue
        parts = [p.strip() for p in m["rest"].split(" | ")]
        rec: dict[str, Any] = {"restaurantId": m["id"], "name": m["name"].strip(), "cuisines": [], "rating": None,
                               "cost_for_two": None, "area": ""}
        for p in parts:
            if p.endswith("★"):
                rec["rating"] = _float(p[:-1])
            elif "for two" in p:
                rec["cost_for_two"] = _rupees(p)
            elif not rec["cuisines"]:
                rec["cuisines"] = [c.strip() for c in p.split(",") if c.strip()]
            else:
                rec["area"] = p
        out.append(rec)
    return out


_DIST = re.compile(r"^\s*([\d.]+)\s*(km|m)\s*•")


def parse_restaurant_details(structured: Any) -> dict[str, Any] | None:
    r = (structured or {}).get("restaurant") if isinstance(structured, dict) else None
    if not isinstance(r, dict):
        return None
    m = _DIST.match(str(r.get("address") or ""))
    dist = None
    if m:
        dist = float(m[1]) / (1000 if m[2] == "m" else 1)
    return {"restaurantId": str(r.get("restaurantId") or r.get("id")), "name": str(r.get("name", "")),
            "cuisines": [str(c) for c in r.get("cuisines") or []], "cost_for_two": _rupees(r.get("costForTwo")),
            "distance_km": dist, "area": str(r.get("area") or r.get("locality") or "").strip(),
            "rating": _float(r.get("avgRating"))}


@dataclass
class SlotInfo:
    date: str
    free_item: str | None
    free_slot_id: int | None
    times: dict[int, str] = field(default_factory=dict)  # reservationTime -> "10:00 PM"


def parse_slots(text: str, date: str) -> SlotInfo:
    info = SlotInfo(date, None, None)
    m = re.search(rf'^\s*{re.escape(date)} \[FREE\]: slotId=(\d+), itemId="([^"]+)"', text or "", re.MULTILINE)
    if m:
        info.free_slot_id, info.free_item = int(m[1]), m[2]
    sec = re.search(rf"^Slots for {re.escape(date)}:\n((?:[ \t]+.+\n?)+)", text or "", re.MULTILINE)
    if sec:
        for label, unix in re.findall(r"(\d{1,2}:\d{2} [AP]M)\s*→\s*(\d{9,11})", sec[1]):
            info.times[int(unix)] = label
    return info


# ---------- the record ----------

@dataclass
class Corpus:
    group_lat: float
    group_lng: float
    date: str
    suggestions: dict[str, dict[str, str]] = field(default_factory=dict)
    events: dict[str, EventInfo] = field(default_factory=dict)
    shows: dict[str, ShowInfo] = field(default_factory=dict)
    restaurants: dict[str, dict[str, Any]] = field(default_factory=dict)  # + "search_point": "pin"|venue_id
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    slots: dict[str, SlotInfo] = field(default_factory=dict)
    calls: int = 0

    def venue_coords(self, venue_id: str) -> tuple[float, float] | None:
        for e in self.events.values():
            if e.venue_id == venue_id and e.venue_lat is not None and e.venue_lng is not None:
                return e.venue_lat, e.venue_lng
        return None
