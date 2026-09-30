"""Constraints → up to 3 checked plans. Gemini picks; Swiggy supplies facts; code checks (CEO D6, eng R3-1..5).

    attempt 1 (event plans, ≤12 Swiggy calls) ─► propose_plans ─► checks
        < 3 passed ─► retry with the failure reasons (≤8 calls) ─► checks
    still < 3 ─► dinner-only attempt near the group pin (≤10 calls, always reserved) ─► checks
    hard deadline 90 s: whatever passed so far is shown (partial)

The model gets read tools only, never booking. It proposes ids + reservationTime; slotId/itemId, prices,
distances and times on the card all come from the Corpus (Swiggy's own replies). Tool calls from one model
turn run concurrently; every model call runs in a worker thread (eng E2).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from maa.llm import Model
from plan_bana.checks import GAP_KNOWN_MIN, GAP_UNKNOWN_MIN, check_plan
from plan_bana.corpus import (
    Corpus,
    haversine_km,
    parse_event_details,
    parse_restaurant_details,
    parse_restaurant_search,
    parse_shows,
    parse_slots,
    parse_suggestions,
)
from plan_bana.model import IST, Constraints, PlanOption

MAX_PLANS = 3
CALL_CAPS = {"event": 12, "retry": 8, "dinner": 10}
DEADLINE_S = 90.0
DINNER_RESERVE_S = 25.0  # the event attempt may not eat the dinner fallback's time (R3-5); live dinner ≈ 15 s
MAX_STEPS = 10
SLOT_LIST_MAX = 16


class SwiggyAuthError(Exception):
    """Swiggy token expired or was rejected (CEO D4: organizer is told to log in again)."""


class Reader(Protocol):
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


def is_auth_failure(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("401", "unauthorized", "unauthorised", "invalid token", "token expired",
                                "access token"))


SYSTEM_EVENTS = """You plan a night out for a friend group in India using Swiggy Scenes (shows) and Swiggy Dineout (tables).
The group is at {area} (lat {lat}, lng {lng}). Plan date: {day} ({date}). Time window: {window}.

Make up to {need} DIFFERENT plans, each = one show + a dinner table AFTER the show ends. Work in exactly these
rounds, putting all calls of a round in ONE turn (they run in parallel). You have {calls} lookups in total.
1. search_events ONCE per genre ({genres}). Do not try other words. Results are nationwide.
2. get_event on the 2-3 results whose location is closest to the group (same city). It returns distance and
   the plan date's shows. Keep shows that start inside the window and end at least 1 hour before it closes.
3. search_restaurants near_eventId=<that event> for each kept show, with the venue's AREA name as the query
   (e.g. "Majiwada"), so results are walking distance from the show.
4. get_slots on 1-2 restaurants per show whose cost fits the budget (must be ≤ 5 km from the venue).
5. propose_plans. reservationTime = a listed time at least 20 min after the show ends (45 min if the
   distance is unknown), inside the window. Copy eventId, showId, restaurantId, reservationTime exactly.
Budget per head = ticket + half the cost for two, must stay within {budget}. {veg}If no show fits, propose
nothing (empty list) and we will plan dinner instead. Titles: short fun Hinglish, max 4 words ("Comedy + Pizza")."""

SYSTEM_DINNER = """You plan a dinner for a friend group in India using Swiggy Dineout (tables).
The group is at {area} (lat {lat}, lng {lng}). Plan date: {day} ({date}). Time window: {window}.
Make up to {need} DIFFERENT dinner plans (no shows). search_restaurants with 1-2 cuisine/vibe words near the
group, get_slots on the best 2-3, then propose_plans once with restaurantId + reservationTime copied exactly.
Budget per head (half the cost for two) must stay within {budget}. {veg}Avoid these already-chosen places: {avoid}.
Titles: short, fun Hinglish (max 4 words)."""

RETRY = ("{n_ok} plan(s) passed. These failed our checks: {fails}. Propose ONLY replacements for the failed "
         "ones (do not repeat the passed ones), fixing the problems. You have {calls} more lookups.")


def _tool(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}


T_SEARCH_EVENTS = _tool("search_events", "Search Swiggy Scenes events by genre or artist (nationwide results).",
                        {"query": {"type": "string"}}, ["query"])
T_GET_EVENT = _tool("get_event", "Venue, distance from the group and the plan date's shows for one event.",
                    {"eventId": {"type": "string"}}, ["eventId"])
T_SEARCH_REST = _tool(
    "search_restaurants", "Search Dineout restaurants by cuisine/vibe. Set near_eventId to search near a show venue.",
    {"query": {"type": "string"}, "near_eventId": {"type": "string"}}, ["query"])
T_GET_SLOTS = _tool("get_slots", "FREE table times on the plan date + distance/cost/cuisines for one restaurant.",
                    {"restaurantId": {"type": "string"}}, ["restaurantId"])
T_PROPOSE = _tool("propose_plans", "Finish: the plans for the group to vote on.", {
    "plans": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "eventId": {"type": "string"}, "showId": {"type": "string"},
        "restaurantId": {"type": "string"}, "reservationTime": {"type": "integer"}},
        "required": ["title", "restaurantId", "reservationTime"]}}}, ["plans"])

TOOLS = {"event": [T_SEARCH_EVENTS, T_GET_EVENT, T_SEARCH_REST, T_GET_SLOTS, T_PROPOSE],
         "dinner": [T_SEARCH_REST, T_GET_SLOTS, T_PROPOSE]}
TOOL_COST = {"search_events": 1, "get_event": 2, "search_restaurants": 1, "get_slots": 2}


@dataclass
class PlanRun:
    options: list[PlanOption] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    partial: bool = False
    calls: int = 0


def _clock_label(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%I:%M %p").lstrip("0")


def _day_bounds(date: str) -> tuple[str, str]:
    d = dt.date.fromisoformat(date)
    start = dt.datetime.combine(d, dt.time(0, 0), IST)
    return start.isoformat(), (start + dt.timedelta(days=1)).isoformat()


class Planner:
    def __init__(self, make_model: Callable[[], Model], scenes: Reader, dineout: Reader, c: Constraints,
                 lat: float, lng: float, area: str,
                 log: Callable[[str, dict[str, Any]], None] = lambda k, d: None,
                 progress: Callable[[str], Awaitable[None]] | None = None,
                 deadline_s: float = DEADLINE_S, clock: Callable[[], float] = time.monotonic):
        assert c.date, "plan date must be decided before planning"
        self.make_model, self.scenes, self.dineout, self.c = make_model, scenes, dineout, c
        self.lat, self.lng, self.area = lat, lng, area
        self.log, self.progress, self.clock = log, progress, clock
        self.corpus = Corpus(lat, lng, c.date)
        self.search_coords: dict[str, tuple[float, float]] = {}
        self.deadline = clock() + deadline_s
        self.stage_deadline = self.deadline
        self._stage = ""
        self._budget = 0

    # ---------- public ----------

    async def run(self) -> PlanRun:
        out = PlanRun()
        if self.c.genres:
            self.stage_deadline = self.deadline - DINNER_RESERVE_S
            await self._set_stage("events")
            try:
                await self._attempt("event", out)
            except TimeoutError:
                self.log("event_timeout", {"kept": len(out.options)})
        if len(out.options) < MAX_PLANS:
            self.stage_deadline = self.deadline
            await self._set_stage("tables")
            try:
                await self._attempt("dinner", out)
            except TimeoutError:
                out.partial = True
                self.log("deadline", {"kept": len(out.options)})
        out.calls = self.corpus.calls
        for i, o in enumerate(out.options, 1):
            o.idx = i
        return out

    # ---------- attempts ----------

    def _remaining(self) -> float:
        left = self.stage_deadline - self.clock()
        if left <= 0:
            raise TimeoutError
        return left

    async def _model(self, fn: Callable[..., Any], *args: Any) -> Any:
        left = self._remaining()  # raise before creating the coroutine, so nothing is left un-awaited
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), left)

    def _prompt(self, mode: str, need: int, avoid: list[str]) -> str:
        c = self.c
        veg = "Someone is vegetarian: pick places with veg options. " if any(p.veg for p in c.coming()) else ""
        start, end = c.window
        fmt = {"area": self.area, "date": c.date, "day": dt.date.fromisoformat(c.date).strftime("%A"),
               "window": f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} IST",
               "budget": f"₹{c.budget}" if c.budget else "no limit", "veg": veg,
               "genres": " and ".join(c.genres) or "any", "need": need, "avoid": ", ".join(avoid) or "none",
               "lat": round(self.lat, 4), "lng": round(self.lng, 4), "calls": CALL_CAPS[mode]}
        return (SYSTEM_EVENTS if mode == "event" else SYSTEM_DINNER).format(**fmt)

    async def _attempt(self, mode: str, out: PlanRun) -> None:
        need = MAX_PLANS - len(out.options)
        avoid = [o.restaurant.name for o in out.options]
        model = self.make_model()
        self._budget = CALL_CAPS[mode]
        user = f"Group: {self.c.headcount} people. Make {need} plan(s)."
        turn = await self._model(model.start, self._prompt(mode, need, avoid), user, TOOLS[mode])
        retried = mode != "event"
        for _ in range(MAX_STEPS):
            if not turn.calls:
                self.log("model_stopped", {"mode": mode, "text": turn.text[:200]})
                return
            finish = next((call for call in turn.calls if call.name == "propose_plans"), None)
            if finish is not None:
                fails = await self._evaluate(finish.args.get("plans") or [], out)
                if retried or len(out.options) >= MAX_PLANS or not fails:
                    return
                retried = True
                self._budget = CALL_CAPS["retry"]
                msg = RETRY.format(n_ok=len(out.options), fails="; ".join(fails[:4]), calls=self._budget)
                # Gemini wants one response per function call in the turn, in order.
                replies = [(call.name, {"error": msg} if call is finish else {"error": "skipped: plans were proposed"})
                           for call in turn.calls]
                turn = await self._model(model.send_tool_results, replies)
                continue
            left = self._remaining()
            results = await asyncio.wait_for(
                asyncio.gather(*(self._dispatch(call.name, dict(call.args)) for call in turn.calls)), left)
            turn = await self._model(model.send_tool_results, [(call.name, r) for call, r in zip(turn.calls, results,
                                                                                                    strict=True)])

    async def _evaluate(self, cands: list[Any], out: PlanRun) -> list[str]:
        await self._set_stage("checks")
        fails = []
        seen = {(o.restaurant.restaurant_id, o.event.show_id if o.event else "") for o in out.options}
        for cand in cands if isinstance(cands, list) else []:
            if len(out.options) >= MAX_PLANS or not isinstance(cand, dict):
                break
            opt, reasons = check_plan(cand, self.corpus, self.c, len(out.options) + 1)
            key = (str(cand.get("restaurantId")), str(cand.get("showId") or ""))
            if opt is not None and key in seen:
                opt, reasons = None, ["same plan dobara"]
            self.log("check", {"cand": cand, "ok": opt is not None, "reasons": reasons})
            if opt is None:
                fails.append(f"{cand.get('title', '?')}: {', '.join(reasons)}")
                out.reasons.extend(reasons)
            else:
                seen.add(key)
                out.options.append(opt)
        return fails

    async def _set_stage(self, stage: str) -> None:
        if stage != self._stage and self.progress is not None:
            self._stage = stage
            try:
                await self.progress(stage)
            except Exception as e:  # noqa: BLE001 (a failed progress edit must never stop planning)
                self.log("progress_failed", {"error": f"{type(e).__name__}: {e}"})

    # ---------- tools ----------

    async def _swiggy(self, reader: Reader, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.corpus.calls += 1
        try:
            res = await reader.call(tool, args)
        except Exception as e:
            if is_auth_failure(str(e)):
                raise SwiggyAuthError(str(e)) from e
            self.log("tool_error", {"tool": tool, "args": args, "error": f"{type(e).__name__}: {e}"[:300]})
            raise
        if res.get("is_error") and is_auth_failure(res.get("text", "")):
            raise SwiggyAuthError(res.get("text", "")[:300])
        self.log("tool", {"tool": tool, "args": args, "is_error": res.get("is_error"),
                          "text": (res.get("text") or "")[:2000]})
        return res

    async def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        cost = TOOL_COST.get(name)
        if cost is None:
            return {"error": f"tool {name} is not available"}
        if self._budget < cost:
            return {"error": "lookup limit reached; call propose_plans now with what you have"}
        self._budget -= cost
        try:
            return await getattr(self, f"_t_{name}")(args)
        except SwiggyAuthError:
            raise
        except Exception as e:  # noqa: BLE001 (one failed lookup goes back to the model as an error)
            return {"error": f"{type(e).__name__}: {str(e)[:200]}"}

    async def _t_search_events(self, args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query", ""))[:60]
        res = await self._swiggy(self.scenes, "search_events", {"query": q, "latitude": self.lat, "longitude": self.lng})
        if res.get("is_error"):
            return {"error": res.get("text", "")[:300]}
        sugg = parse_suggestions(res.get("structured"))
        for s in sugg:
            self.corpus.suggestions[s["eventId"]] = s
        return {"events": sugg[:20]} if sugg else {"events": [], "note": "no events for this word"}

    async def _t_get_event(self, args: dict[str, Any]) -> dict[str, Any]:
        eid = str(args.get("eventId", ""))
        loc = {"latitude": self.lat, "longitude": self.lng}
        det = await self._swiggy(self.scenes, "get_event_details", {"eventId": eid, **loc})
        info = parse_event_details(det.get("structured"))
        if det.get("is_error") or info is None:
            return {"error": (det.get("text") or "event not found")[:300]}
        self.corpus.events[eid] = info
        start, end = _day_bounds(self.c.date)
        sh = await self._swiggy(self.scenes, "list_event_shows", {
            "eventId": eid, "venueId": info.venue_id, "showStartTime": start, "showEndTime": end, **loc})
        # Scenes ignores the show window and returns only the event's next show date (probe 2026-09-30),
        # so filter to the plan date here and tell the model when the next show is.
        all_shows = parse_shows(sh.get("structured"))
        shows = [s for s in all_shows if dt.datetime.fromtimestamp(s.start, IST).date().isoformat() == self.c.date]
        for s in shows:
            self.corpus.shows[s.show_id] = s
        dist = (round(haversine_km(self.lat, self.lng, info.venue_lat, info.venue_lng), 1)
                if info.venue_lat is not None and info.venue_lng is not None else None)
        return {"event": info.name, "venue": info.venue_name, "area": info.area, "distance_from_group_km": dist,
                "next_show": (None if shows or not all_shows else
                              dt.datetime.fromtimestamp(all_shows[0].start, IST).strftime("%a %d %b %I:%M %p")),
                "shows_on_plan_date": [
                    {"showId": s.show_id, "starts": _clock_label(s.start),
                     "ends": ("~" if s.end_estimated else "") + _clock_label(s.end),
                     "ticket_from": s.ticket_price, "seats_left": s.available} for s in shows]}

    async def _t_search_restaurants(self, args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query", ""))[:60] or "restaurant"
        near = str(args.get("near_eventId") or "")
        point, (lat, lng) = "pin", (self.lat, self.lng)
        ev = self.corpus.events.get(near)
        if ev is not None and ev.venue_lat is not None and ev.venue_lng is not None:
            point, (lat, lng) = ev.venue_id, (ev.venue_lat, ev.venue_lng)
        res = await self._swiggy(self.dineout, "search_restaurants_dineout",
                                 {"query": q, "latitude": lat, "longitude": lng, "limit": 8})
        if res.get("is_error"):
            return {"error": res.get("text", "")[:300]}
        found = parse_restaurant_search(res.get("text", ""))
        for r in found:
            prev = self.corpus.restaurants.get(r["restaurantId"])
            if prev is None or (prev["search_point"] == "pin" and point != "pin"):
                self.corpus.restaurants[r["restaurantId"]] = {**r, "search_point": point}
                self.search_coords[r["restaurantId"]] = (lat, lng)
        return {"restaurants": [{k: r[k] for k in ("restaurantId", "name", "cuisines", "cost_for_two", "rating", "area")}
                                for r in found]}

    async def _t_get_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        rid = str(args.get("restaurantId", ""))
        if rid not in self.corpus.restaurants:
            return {"error": "use a restaurantId from search_restaurants"}
        lat, lng = self.search_coords[rid]
        loc = {"latitude": lat, "longitude": lng}
        det, sl = await asyncio.gather(
            self._swiggy(self.dineout, "get_restaurant_details", {"restaurantId": rid, **loc}),
            self._swiggy(self.dineout, "get_available_slots", {"restaurantId": rid, "date": self.c.date, **loc}))
        d = parse_restaurant_details(det.get("structured"))
        if d is not None:
            self.corpus.details[rid] = d
        if sl.get("is_error"):
            return {"error": sl.get("text", "")[:300]}
        info = parse_slots(sl.get("text", ""), self.c.date)
        self.corpus.slots[rid] = info
        lo, hi = self.c.window
        times = []
        for unix, label in sorted(info.times.items()):
            t = dt.datetime.fromtimestamp(unix, IST)
            if lo <= t.hour * 60 + t.minute <= hi:
                times.append({"time": label, "reservationTime": unix})
        near = self.corpus.restaurants[rid]["search_point"] != "pin"
        return {"name": self.corpus.restaurants[rid]["name"], "free_table": bool(info.free_item),
                "distance_km" + ("_from_show_venue" if near else "_from_group"): (d or {}).get("distance_km"),
                "cost_for_two": (d or {}).get("cost_for_two"), "cuisines": (d or {}).get("cuisines"),
                "times_in_window": times[:SLOT_LIST_MAX] if info.free_item else []}


# ---------------------------------------------------------------- booking re-check (CEO/eng: Book step)


@dataclass
class Recheck:
    status: str  # "ok" | "show_gone" | "slot_gone"
    reservation_time: int | None = None
    slot_id: int | None = None
    item_id: str | None = None


def table_coords(opt: PlanOption, lat: float, lng: float) -> tuple[float, float]:
    e = opt.event
    if opt.restaurant.searched_at_venue and e and e.venue_lat is not None and e.venue_lng is not None:
        return e.venue_lat, e.venue_lng
    return lat, lng


async def recheck(opt: PlanOption, scenes: Reader, dineout: Reader, c: Constraints, lat: float, lng: float,
                  table_only: bool) -> Recheck:
    """Right before booking: the show is still on sale and the same FREE slot still exists (R2-9, R1-7)."""
    date = dt.datetime.fromtimestamp(opt.reservation_time, IST).date().isoformat()
    e = opt.event
    if e and not table_only:
        start, end = _day_bounds(date)
        sh = await scenes.call("list_event_shows", {"eventId": e.event_id, "venueId": e.venue_id,
                                                    "showStartTime": start, "showEndTime": end,
                                                    "latitude": lat, "longitude": lng})
        if sh.get("is_error") and is_auth_failure(sh.get("text", "")):
            raise SwiggyAuthError(sh.get("text", ""))
        live = {s.show_id: s for s in parse_shows(sh.get("structured"))}
        if e.show_id not in live or not live[e.show_id].available:
            return Recheck("show_gone")
    tlat, tlng = table_coords(opt, lat, lng)
    sl = await dineout.call("get_available_slots", {"restaurantId": opt.restaurant.restaurant_id, "date": date,
                                                    "latitude": tlat, "longitude": tlng})
    if sl.get("is_error") and is_auth_failure(sl.get("text", "")):
        raise SwiggyAuthError(sl.get("text", ""))
    info = parse_slots(sl.get("text", ""), date)
    if info.free_item == opt.item_id and opt.reservation_time in info.times:
        return Recheck("ok", opt.reservation_time, info.free_slot_id, info.free_item)
    if not info.free_item:
        return Recheck("slot_gone")
    lo, hi = c.window
    earliest = 0.0
    if e and not table_only:
        gap = GAP_KNOWN_MIN if opt.restaurant.distance_km is not None else GAP_UNKNOWN_MIN
        earliest = e.end + gap * 60
    ok = [t for t in info.times if t >= earliest
          and lo <= dt.datetime.fromtimestamp(t, IST).hour * 60 + dt.datetime.fromtimestamp(t, IST).minute <= hi]
    if not ok:
        return Recheck("slot_gone")
    alt = min(ok, key=lambda t: abs(t - opt.reservation_time))
    return Recheck("slot_gone", alt, info.free_slot_id, info.free_item)
