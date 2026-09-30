"""Plan Bana: turn a friend group's "/plan saturday 4 log comedy + dinner" into 3 real, bookable plans.

    request ─► Gemini (read tools only) ─► Scenes: search_events / get_event / list_shows
                                         └► Dineout: search_restaurants / get_slots
            ─► propose_plans (3 plans) ─► code validates every id against Swiggy's own replies

The model never books. It only proposes; booking happens later in code (book_table, FREE deals only,
behind the DRY_RUN lock). Location is injected by code, never chosen by the model.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from maa.llm import AgentOutputError, Model

MAX_STEPS = 12
MAX_TOOL_RESULT_CHARS = 5000
MAX_CALLS = 14

SYSTEM_PROMPT = """You plan outings for a group of friends in India using Swiggy Scenes (events/shows) and
Swiggy Dineout (restaurant tables). Today is {today} ({weekday}). The group is near {area}.

Goal: propose exactly 3 DIFFERENT plans the group can vote on. Each plan = an optional event + a restaurant
table. Good mixes: comedy show then dinner nearby; live music then dinner; or just a great dinner place.

Process (be quick, do calls in parallel where you can):
1. search_events with 1-2 genre words from the request (e.g. "comedy", "music", "open mic"). Pick events on
   the requested day(s). Call get_event for a chosen event to get its venueId, then list_shows(eventId, venueId)
   for the exact show time. Skip events if the group only wants food.
2. search_restaurants with a cuisine or vibe word (e.g. "italian", "rooftop", "north indian").
3. get_slots(restaurantId, date) for each chosen restaurant on the plan date. Pick a FREE deal only
   ([FREE] line) and a time that fits: after the show ends (assume shows last ~1.5 h) or at the requested time.
   Copy slotId, itemId and reservationTime EXACTLY from get_slots output.
4. Call propose_plans once with all 3 plans. Never invent ids, times or prices; copy them from tool output.
If the request is missing something essential (which day, how many people), call ask_group with ONE short
Hinglish question instead. Keep all text short and in casual Hinglish."""

TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "search_events", "description": "Search Scenes events/shows near the group by genre or artist.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_event", "description": "Event details incl. venueId, venue, area, next date.",
     "parameters": {"type": "object", "properties": {"eventId": {"type": "string"}}, "required": ["eventId"]}},
    {"name": "list_shows", "description": "Show times + ticket types for an event at a venue.",
     "parameters": {"type": "object", "properties": {"eventId": {"type": "string"}, "venueId": {"type": "string"}},
                    "required": ["eventId", "venueId"]}},
    {"name": "search_restaurants", "description": "Search Dineout restaurants near the group (cuisine/vibe/name).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_slots", "description": "Table slots for a restaurant for 7 days from date (YYYY-MM-DD).",
     "parameters": {"type": "object", "properties": {"restaurantId": {"type": "string"}, "date": {"type": "string"}},
                    "required": ["restaurantId", "date"]}},
    {
        "name": "propose_plans",
        "description": "Finish: 3 plans for the group to vote on.",
        "parameters": {
            "type": "object",
            "properties": {
                "headcount": {"type": "integer", "minimum": 1, "maximum": 20},
                "plans": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short catchy Hinglish title"},
                            "event": {
                                "type": "object",
                                "description": "Omit for dinner-only plans",
                                "properties": {
                                    "eventId": {"type": "string"}, "name": {"type": "string"},
                                    "venue": {"type": "string"}, "when": {"type": "string"},
                                    "price": {"type": "string", "description": "Ticket price if known"},
                                },
                                "required": ["eventId", "name", "venue", "when"],
                            },
                            "restaurant": {
                                "type": "object",
                                "properties": {
                                    "restaurantId": {"type": "string"}, "name": {"type": "string"},
                                    "area": {"type": "string"}, "rating": {"type": "string"},
                                    "cost_for_two": {"type": "string"},
                                },
                                "required": ["restaurantId", "name"],
                            },
                            "table": {
                                "type": "object",
                                "properties": {
                                    "date": {"type": "string"}, "time": {"type": "string"},
                                    "slotId": {"type": "integer"}, "itemId": {"type": "string"},
                                    "reservationTime": {"type": "integer"},
                                },
                                "required": ["date", "time", "slotId", "itemId", "reservationTime"],
                            },
                            "why": {"type": "string", "description": "One line: why this plan is fun"},
                        },
                        "required": ["title", "restaurant", "table"],
                    },
                },
            },
            "required": ["headcount", "plans"],
        },
    },
    {"name": "ask_group", "description": "Finish: ask the group one short Hinglish question.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]

FINISH = {"propose_plans", "ask_group"}


class Reader(Protocol):
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Plan:
    title: str
    restaurant_id: str
    restaurant_name: str
    table_date: str
    table_time: str
    slot_id: int
    item_id: str
    reservation_time: int
    area: str = ""
    rating: str = ""
    cost_for_two: str = ""
    event_id: str | None = None
    event_name: str | None = None
    event_venue: str | None = None
    event_when: str | None = None
    event_price: str | None = None
    why: str = ""


@dataclass
class PlanResult:
    kind: str  # "plans" | "question"
    plans: list[Plan] = field(default_factory=list)
    headcount: int = 0
    question: str = ""
    tool_log: list[str] = field(default_factory=list)


def _free_items(slots_text: str) -> set[str]:
    """itemIds that appear on a [FREE] booking-params line (never book a paid deal)."""
    return set(re.findall(r'\[FREE\]:\s*slotId=\d+,\s*itemId="([^"]+)"', slots_text))


def validate_plans(args: dict[str, Any], corpus: str, free_items: set[str]) -> tuple[list[Plan], int]:
    """Every id must have come from Swiggy's own tool output (no invented ids)."""
    raw_plans = args.get("plans") or []
    if not 1 <= len(raw_plans) <= 3:
        raise AgentOutputError(f"expected 1-3 plans, got {len(raw_plans)}")
    headcount = args.get("headcount")
    if not isinstance(headcount, int | float) or not 1 <= int(headcount) <= 20:
        raise AgentOutputError(f"invalid headcount {headcount!r}")
    plans = []
    for p in raw_plans:
        r, t, e = p.get("restaurant") or {}, p.get("table") or {}, p.get("event") or None
        rid, item = str(r.get("restaurantId") or ""), str(t.get("itemId") or "")
        rtime = t.get("reservationTime")
        if not rid or rid not in corpus:
            raise AgentOutputError(f"restaurantId {rid!r} not from Swiggy results")
        if item not in free_items or not item.startswith(f"{rid}-"):
            raise AgentOutputError(f"itemId {item!r} is not a FREE deal of restaurant {rid}")
        if not isinstance(rtime, int | float) or str(int(rtime)) not in corpus:
            raise AgentOutputError(f"reservationTime {rtime!r} not from Swiggy slots")
        if e and str(e.get("eventId") or "") not in corpus:
            raise AgentOutputError(f"eventId {e.get('eventId')!r} not from Swiggy results")
        plans.append(Plan(
            title=str(p.get("title") or r.get("name")), restaurant_id=rid, restaurant_name=str(r.get("name")),
            area=str(r.get("area") or ""), rating=str(r.get("rating") or ""), cost_for_two=str(r.get("cost_for_two") or ""),
            table_date=str(t.get("date")), table_time=str(t.get("time")), slot_id=int(t.get("slotId") or 0),
            item_id=item, reservation_time=int(rtime),
            event_id=str(e["eventId"]) if e else None, event_name=e.get("name") if e else None,
            event_venue=e.get("venue") if e else None, event_when=e.get("when") if e else None,
            event_price=e.get("price") if e else None, why=str(p.get("why") or ""),
        ))
    return plans, int(headcount)


async def plan_outing(
    model: Model, scenes: Reader, dineout: Reader, lat: float, lng: float, area: str, request: str,
    today: dt.date | None = None,
) -> PlanResult:
    today = today or dt.date.today()
    system = SYSTEM_PROMPT.format(today=today.isoformat(), weekday=today.strftime("%A"), area=area)
    log: list[str] = []
    corpus_parts: list[str] = []
    free_items: set[str] = set()
    calls = 0
    loc = {"latitude": lat, "longitude": lng}

    async def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls > MAX_CALLS:
            return {"error": "call limit reached; propose_plans with what you have"}
        if name == "search_events":
            res = await scenes.call("search_events", {"query": str(args.get("query", ""))[:80], **loc})
        elif name == "get_event":
            res = await scenes.call("get_event_details", {"eventId": str(args.get("eventId", "")), **loc})
        elif name == "list_shows":
            res = await scenes.call("list_event_shows", {"eventId": str(args.get("eventId", "")),
                                                         "venueId": str(args.get("venueId", "")), **loc})
        elif name == "search_restaurants":
            res = await dineout.call("search_restaurants_dineout", {"query": str(args.get("query", ""))[:80], **loc})
        elif name == "get_slots":
            res = await dineout.call("get_available_slots", {"restaurantId": str(args.get("restaurantId", "")),
                                                             "date": str(args.get("date", "")), **loc})
        else:
            return {"error": f"tool {name} is not available"}
        text = res.get("text", "")
        corpus_parts.append(text)
        if name == "get_slots":
            free_items.update(_free_items(text))
        if res.get("is_error"):
            return {"error": text[:500]}
        return {"result": text[:MAX_TOOL_RESULT_CHARS]}

    turn = model.start(system, f"Group request: {request}", TOOL_SPECS)
    for _ in range(MAX_STEPS):
        if not turn.calls:
            raise AgentOutputError(f"planner stopped without a tool call: {turn.text[:200]!r}")
        results: list[tuple[str, dict[str, Any]]] = []
        for call in turn.calls:
            log.append(call.name)
            if call.name == "ask_group":
                q = str(call.args.get("question") or "").strip()
                if not q:
                    raise AgentOutputError("ask_group without a question")
                return PlanResult(kind="question", question=q, tool_log=log)
            if call.name == "propose_plans":
                try:
                    plans, headcount = validate_plans(call.args, "\n".join(corpus_parts), free_items)
                except AgentOutputError as e:
                    results.append((call.name, {"error": f"{e}. Fix it using exact values from tool output."}))
                    continue
                return PlanResult(kind="plans", plans=plans, headcount=headcount, tool_log=log)
            results.append((call.name, await dispatch(call.name, dict(call.args))))
        turn = model.send_tool_results(results)
    raise AgentOutputError(f"no plans after {MAX_STEPS} steps")


def plans_message(plans: list[Plan], headcount: int, request: str) -> str:
    lines = [f"🗓️ Plan Bana: “{request}” ({headcount} log)", ""]
    for n, p in enumerate(plans, 1):
        lines.append(f"{n}️⃣ {p.title}")
        if p.event_name:
            price = f" · {p.event_price}" if p.event_price else ""
            lines.append(f"   🎤 {p.event_name} @ {p.event_venue} · {p.event_when}{price}")
        meta = " · ".join(x for x in (p.area, f"{p.rating}★" if p.rating else "", p.cost_for_two) if x)
        lines.append(f"   🍽️ {p.restaurant_name}{' (' + meta + ')' if meta else ''}")
        lines.append(f"   🪑 Table: {p.table_date} {p.table_time} (free reservation)")
        if p.why:
            lines.append(f"   {p.why}")
        lines.append("")
    lines.append("Vote karo 👇 (organiser locks the plan)")
    return "\n".join(lines)


def plans_to_json(plans: list[Plan]) -> str:
    return json.dumps([p.__dict__ for p in plans], ensure_ascii=False)


def plans_from_json(raw: str) -> list[Plan]:
    return [Plan(**p) for p in json.loads(raw)]
