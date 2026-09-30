"""In-memory Swiggy Scenes + Dineout that answer in the LIVE formats (see tests/fixtures), eng E8.

Build events/restaurants in IST, get two Readers (scenes, dineout) that record every call and can inject
errors per tool: fail["get_available_slots"] = "auth" | "error" | SomeException(...).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from plan_bana.model import IST


def ist(y, mo, d, h, mi=0) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi, tzinfo=IST)


@dataclass
class FakeShow:
    show_id: str
    start: dt.datetime
    end: dt.datetime | None
    price: int | None = 249
    seats: int = 20


@dataclass
class FakeEvent:
    event_id: str
    name: str
    venue_id: str
    venue_name: str
    lat: float
    lng: float
    area: str = "Majiwada"
    location: str = "Majiwada, Mumbai"
    shows: list[FakeShow] = field(default_factory=list)


@dataclass
class FakeRestaurant:
    rid: str
    name: str
    cuisines: list[str]
    cost_for_two: int
    area: str = "Mulund West"
    rating: float = 4.3
    distance: str = "1.2 km"
    free_ticket: str | None = "735129"
    times: list[dt.datetime] = field(default_factory=list)


class FakePlanSwiggy:
    def __init__(self, events: list[FakeEvent], restaurants: list[FakeRestaurant],
                 search_hits: dict[str, list[str]] | None = None, rest_hits: dict[str, list[str]] | None = None):
        self.events = {e.event_id: e for e in events}
        self.restaurants = {r.rid: r for r in restaurants}
        self.search_hits = search_hits or {}
        self.rest_hits = rest_hits or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, Any] = {}
        self.booked: list[dict[str, Any]] = []
        self.scenes = _Reader(self)
        self.dineout = _Reader(self)

    async def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        f = self.fail.get(name)
        if isinstance(f, BaseException):
            raise f
        if f == "auth":
            return _res("401 Unauthorized: access token expired", is_error=True)
        if f == "error":
            return _res("Something went wrong", is_error=True)
        return getattr(self, f"_{name}")(args)

    # ---- Scenes ----
    def _search_events(self, a):
        ids = self.search_hits.get(a["query"].lower(), list(self.events))
        sugg = [{"eventId": i, "eventName": self.events[i].name, "location": self.events[i].location} for i in ids]
        return _res(f"Found {len(sugg)} event(s)", {"suggestions": sugg})

    def _get_event_details(self, a):
        e = self.events.get(a["eventId"])
        if e is None:
            return _res("Event not found", is_error=True)
        return _res(f"Event: {e.name}", {"eventId": e.event_id, "eventName": e.name, "venueId": e.venue_id,
                                         "venueName": e.venue_name, "area": e.area, "formattedAddress": e.venue_name,
                                         "venueLat": str(e.lat), "venueLng": str(e.lng), "upcomingDate": "", "price": ""})

    def _list_event_shows(self, a):
        e = self.events[a["eventId"]]
        lo = dt.datetime.fromisoformat(a["showStartTime"]) if a.get("showStartTime") else None
        hi = dt.datetime.fromisoformat(a["showEndTime"]) if a.get("showEndTime") else None
        shows = [s for s in e.shows if (lo is None or s.start >= lo) and (hi is None or s.start < hi)]
        groups = []
        for s in shows:
            utc = s.start.astimezone(dt.UTC)
            end = (s.end or s.start).astimezone(dt.UTC)
            tickets = [] if s.price is None else [
                {"id": "1", "name": "Entry Pass For One", "availableInventory": s.seats,
                 "price": {"price": {"units": str(s.price)}, "discountedPrice": {"units": str(s.price)}}},
                {"id": "2", "name": "Entry Pass For Two", "availableInventory": s.seats,
                 "price": {"price": {"units": str(s.price * 2 - 1)}, "discountedPrice": {"units": str(s.price * 2 - 1)}}}]
            groups.append({"date": {}, "eventShow": [{
                "id": s.show_id, "timeRange": {"startTime": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                               "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
                "eventId": e.event_id, "venue": {"venueId": e.venue_id}, "maxAvailableInventory": s.seats,
                "tickets": tickets, "status": "ENTITIES_DISCOVERY_STATE_ON_SALE" if s.seats else "SOLD_OUT",
                "show_time_unix": int(s.start.timestamp())}]})
        return _res(f"{len(shows)} shows", {"eventShowListing": {"dateGroupedEventShow": groups}})

    # ---- Dineout ----
    def _search_restaurants_dineout(self, a):
        ids = self.rest_hits.get(a["query"].lower(), list(self.restaurants))
        lines = [f"Found {len(ids)} restaurant(s) matching \"{a['query']}\""]
        for n, i in enumerate(ids, 1):
            r = self.restaurants[i]
            lines.append(f"{n}. {r.name} — {', '.join(r.cuisines)} | {r.rating}★ | ₹{r.cost_for_two} for two | "
                         f"{r.area} (ID: {r.rid})")
        return _res("\n".join(lines), {})

    def _get_restaurant_details(self, a):
        r = self.restaurants[a["restaurantId"]]
        return _res(f"Restaurant: {r.name}", {"restaurantId": r.rid, "restaurant": {
            "id": r.rid, "restaurantId": r.rid, "name": r.name, "cuisines": r.cuisines, "area": f" {r.area}",
            "address": f"{r.distance} • Somewhere, {r.area}", "avgRating": r.rating,
            "costForTwo": f"₹{r.cost_for_two} for two"}})

    def _get_available_slots(self, a):
        r = self.restaurants[a["restaurantId"]]
        date = a["date"]
        lines = ["Found slots. Availability per date:", "", "Booking params per date:"]
        if r.free_ticket:
            lines.append(f'  {date} [FREE]: slotId=1, itemId="{r.rid}-{r.free_ticket}"')
        lines.append(f'  {date} [PAID ₹25]: slotId=1, itemId="{r.rid}-999999" — "Flat 10% off"')
        day = [t for t in r.times if t.date().isoformat() == date]
        lines += ["", f"Slots for {date}:"]
        if day:
            lines.append("  Dinner: " + ", ".join(f"{t.strftime('%I:%M %p')}→{int(t.timestamp())}" for t in day))
        return _res("\n".join(lines), {"restaurantId": r.rid, "date": date})


class _Reader:
    def __init__(self, fake: FakePlanSwiggy):
        self.fake = fake

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.fake.handle(name, arguments or {})

    async def place_order(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from plan_bana.swiggy import OrderBlocked, orders_enabled
        if not orders_enabled():
            raise OrderBlocked("DRY_RUN is on")
        self.fake.booked.append(arguments)
        f = self.fake.fail.get(name)
        if isinstance(f, BaseException):
            raise f
        if f == "error":
            return _res("Slot no longer available", is_error=True)
        return _res("Booking confirmed. Order ID: DO123", {"orderId": "DO123"})


def _res(text: str, structured: Any = None, is_error: bool = False) -> dict[str, Any]:
    return {"is_error": is_error, "structured": structured, "text": text, "parsed": None}


def evening(date=(2026, 10, 3), start_h=17, end_h=23) -> list[dt.datetime]:
    base = ist(*date, start_h)
    return [base + dt.timedelta(minutes=15 * i) for i in range((end_h - start_h) * 4 + 1)]


def standard_world() -> FakePlanSwiggy:
    """Saturday 3 Oct 2026: two comedy shows near Thane, three restaurants."""
    comedy = FakeEvent("100116693", "Comedy In Thane", "1385", "Backspace Thane", 19.2117, 72.9850,
                       shows=[FakeShow("s1", ist(2026, 10, 3, 20, 30), ist(2026, 10, 3, 22, 0), 249)])
    open_mic = FakeEvent("100122867", "Open Mic Night", "2001", "The Habitat Thane", 19.2000, 72.9700,
                         shows=[FakeShow("s2", ist(2026, 10, 3, 19, 30), None, 199)])
    far = FakeEvent("100114197", "Pune Comedy", "3001", "Vintage Club Pune", 18.5913, 73.7389, area="Hinjawadi",
                    location="Hinjawadi, Pune", shows=[FakeShow("s3", ist(2026, 10, 3, 20, 0), ist(2026, 10, 3, 21, 30))])
    rests = [
        FakeRestaurant("708583", "1441 Pizzeria", ["American", "Italian"], 1000, "Majiwada", times=evening()),
        FakeRestaurant("864731", "Mad-Doh Sourdough", ["Italian", "Middle Eastern"], 1500, "Mulund West",
                       distance="2 km", times=evening()),
        FakeRestaurant("555001", "Kebab Korner", ["Kebabs", "Barbecue"], 800, "Thane West", times=evening()),
    ]
    return FakePlanSwiggy([comedy, open_mic, far], rests)
