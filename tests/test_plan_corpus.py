"""Parsers against the REAL responses captured by scripts/probe_plan.py (tests/fixtures, sanitized)."""

import json
from pathlib import Path

from plan_bana.corpus import (
    haversine_km,
    parse_event_details,
    parse_restaurant_details,
    parse_restaurant_search,
    parse_shows,
    parse_slots,
    parse_suggestions,
)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def test_live_search_events_suggestions():
    sugg = parse_suggestions(load("scenes_search_events_comedy")["structured"])
    assert sugg[0] == {"eventId": "100116693", "name": "Comedy In Thane", "location": "Majiwada, Mumbai"}
    assert len(sugg) > 10


def test_live_event_details_has_venue_coords():
    e = parse_event_details(load("scenes_get_event_details")["structured"])
    assert (e.event_id, e.venue_id, e.venue_name) == ("100116693", "1385", "Backspace Thane")
    assert abs(e.venue_lat - 19.211753) < 1e-6 and abs(e.venue_lng - 72.985027) < 1e-6


def test_live_shows_have_real_end_time_and_per_person_price():
    shows = parse_shows(load("scenes_list_event_shows")["structured"])
    s = shows[0]
    assert s.show_id == "100418702" and s.event_id == "100116693" and s.venue_id == "1385"
    assert s.end - s.start == 90 * 60 and not s.end_estimated
    assert s.ticket_price == 249  # "For One" 249 vs "For Two" 499/2 → 250
    assert s.available


def test_live_restaurant_search_text_lines():
    rows = parse_restaurant_search(load("dineout_search_restaurants")["text"])
    assert rows[0] == {"restaurantId": "708583", "name": "1441 Pizzeria", "cuisines": ["American", "Italian"],
                       "rating": 4.2, "cost_for_two": 1000, "area": "Majiwada"}
    assert [r["restaurantId"] for r in rows] == ["708583", "1220690", "864731", "39735", "1275084"]


def test_live_restaurant_details_distance_and_cost():
    d = parse_restaurant_details(load("dineout_get_restaurant_details")["structured"])
    assert d["distance_km"] == 2.0 and d["cost_for_two"] == 1500 and d["cuisines"] == ["Italian", "Middle Eastern"]


def test_live_slots_free_item_and_times():
    info = parse_slots(load("dineout_get_available_slots")["text"], "2026-10-03")
    assert (info.free_slot_id, info.free_item) == (1, "864731-735129")
    assert info.times[1791045000] == "10:00 PM" and info.times[1791007200] == "11:30 AM"
    assert len(info.times) >= 40


def test_slots_are_listed_per_date_and_parsed_only_for_that_date():
    import datetime as dt

    from plan_bana.model import IST
    info = parse_slots(load("dineout_get_available_slots")["text"], "2026-10-05")
    assert info.free_item == "864731-735129" and info.times
    assert {dt.datetime.fromtimestamp(t, IST).date().isoformat() for t in info.times} == {"2026-10-05"}


def test_empty_and_garbage_inputs():
    assert parse_suggestions(None) == [] and parse_event_details({}) is None
    assert parse_shows({"eventShowListing": {}}) == [] and parse_restaurant_search("") == []
    assert parse_restaurant_details(None) is None and parse_slots("", "2026-10-03").free_item is None


def test_metre_distance():
    d = parse_restaurant_details({"restaurant": {"id": "1", "address": "850 m • Somewhere", "cuisines": []}})
    assert d["distance_km"] == 0.85


def test_haversine_mulund_to_backspace():
    assert 4 < haversine_km(19.1726, 72.9565, 19.211753, 72.985027) < 6
