"""Code checks every proposed plan must pass (CEO D6 checks 1–5 + probe check 0). Pure functions.

    candidate {title, eventId?, showId?, restaurantId, reservationTime}
      0 event venue within 30 km of the group pin
      1 ids came from this run: restaurant has FREE slots fetched, reservationTime is one of its slot
        times, show belongs to the event (R3-3)
      2 table (and show) on the plan date, inside the time window
      3 event plans: restaurant ≤ 5 km from the venue when known; table ≥ show end + 20 min (known
        distance) or + 45 min (unknown)
      4 budget: ticket + cost_for_two/2 must not exceed the group limit when known (R3-13)
      5 veg: fails only if every cuisine is meat-only (veg-friendly = veg options exist)
Each check returns reasons in the bot's own Hinglish so they can go straight into the 0-plan message.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from plan_bana.corpus import Corpus, haversine_km
from plan_bana.model import IST, Constraints, EventFacts, PlanOption, RestaurantFacts

MAX_EVENT_KM = 30.0
MAX_VENUE_TO_TABLE_KM = 5.0
GAP_KNOWN_MIN = 20
GAP_UNKNOWN_MIN = 45
MEAT_ONLY = frozenset({"seafood", "kebabs", "barbecue", "bbq", "steak", "meat", "mughlai kebabs"})


def _minutes(ts: float) -> int:
    t = dt.datetime.fromtimestamp(ts, IST)
    return t.hour * 60 + t.minute


def _date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, IST).date().isoformat()


def veg_friendly(cuisines: list[str]) -> bool:
    cs = [c.strip().lower() for c in cuisines if c.strip()]
    return not cs or not all(c in MEAT_ONLY for c in cs)


def check_plan(cand: dict[str, Any], corpus: Corpus, c: Constraints, idx: int) -> tuple[PlanOption | None, list[str]]:
    reasons: list[str] = []
    rid = str(cand.get("restaurantId") or "")
    try:
        rtime = int(cand.get("reservationTime"))
    except (TypeError, ValueError):
        return None, ["table ka time galat"]
    rest, slots, det = corpus.restaurants.get(rid), corpus.slots.get(rid), corpus.details.get(rid)

    # 1: ids from this run
    if rest is None or slots is None:
        return None, ["restaurant ke slots nahi mile"]
    if not slots.free_item or not slots.free_slot_id:
        return None, [f"{rest['name']}: FREE table nahi hai"]
    if rtime not in slots.times:
        return None, [f"{rest['name']}: woh time slot nahi hai"]

    event: EventFacts | None = None
    eid, sid = str(cand.get("eventId") or ""), str(cand.get("showId") or "")
    if eid or sid:
        ev, show = corpus.events.get(eid), corpus.shows.get(sid)
        if ev is None or show is None or show.event_id != eid or (show.venue_id and show.venue_id != ev.venue_id):
            return None, ["show ki details match nahi hui"]
        if not show.available:
            return None, [f"{ev.name}: seats khatam"]
        gdist = None
        if ev.venue_lat is not None and ev.venue_lng is not None:
            gdist = round(haversine_km(corpus.group_lat, corpus.group_lng, ev.venue_lat, ev.venue_lng), 1)
            if gdist > MAX_EVENT_KM:  # 0: show near the group
                reasons.append(f"{ev.name} bahut door hai ({gdist:g} km)")
        event = EventFacts(ev.event_id, ev.name, ev.venue_id, ev.venue_name, ev.venue_lat, ev.venue_lng, show.show_id,
                           show.start, show.end, show.end_estimated, show.ticket_price, ev.area, gdist)

    # 2: date + window
    lo, hi = c.window
    if c.date and _date(rtime) != c.date:
        reasons.append("table galat din ki hai")
    if not lo <= _minutes(rtime) <= hi:
        reasons.append("table time window se bahar hai")
    if event:
        if c.date and _date(event.start) != c.date:
            reasons.append("show galat din ka hai")
        if not lo <= _minutes(event.start) <= hi:
            reasons.append("show time window se bahar hai")

    # 3: distance + gap after the show
    searched_at_venue = bool(event and rest.get("search_point") == event.venue_id)
    dist = det.get("distance_km") if det else None
    if event:
        known = searched_at_venue and dist is not None
        if known and dist > MAX_VENUE_TO_TABLE_KM:
            reasons.append(f"{rest['name']} show se {dist:g} km door hai")
        gap = GAP_KNOWN_MIN if known else GAP_UNKNOWN_MIN
        if rtime < event.end + gap * 60:
            reasons.append("show aur table ke beech time kam hai")

    # 4: budget
    cost2 = (det or {}).get("cost_for_two") or rest.get("cost_for_two")
    ticket_unknown = bool(event and event.ticket_price is None)
    per_head = None
    if cost2 is not None:
        per_head = round(cost2 / 2) + ((event.ticket_price or 0) if event else 0)
    if c.budget and per_head is not None and per_head > c.budget:
        reasons.append(f"budget se mehenga (~₹{per_head}/head)")

    # 5: veg
    cuisines = (det or {}).get("cuisines") or rest.get("cuisines") or []
    vegf = veg_friendly(cuisines)
    if any(p.veg for p in c.coming()) and not vegf:
        reasons.append(f"{rest['name']} mein veg option nahi")

    if reasons:
        return None, reasons

    fit: list[tuple[str, str, str]] = []
    for p in c.coming():
        if p.veg:
            fit.append((p.name, "veg", "ok" if vegf else "bad"))
        if p.budget:
            if per_head is None or ticket_unknown:
                fit.append((p.name, "budget", "unknown"))
            else:
                fit.append((p.name, "budget", "ok" if per_head <= p.budget else "bad"))
        if p.available and c.date:
            ok = p.available.get(c.date)
            fit.append((p.name, "date", "unknown" if ok is None else "ok" if ok else "bad"))

    rf = RestaurantFacts(rid, str((det or {}).get("name") or rest["name"]), list(cuisines), cost2,
                         dist if searched_at_venue else None, searched_at_venue,
                         str((det or {}).get("area") or rest.get("area") or ""), rest.get("rating"))
    title = str(cand.get("title") or (f"{event.name} + {rf.name}" if event else rf.name)).strip()
    return PlanOption(idx, title, rf, rtime, int(slots.free_slot_id), str(slots.free_item), event, per_head,
                      ticket_unknown, fit), []
