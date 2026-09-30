import datetime as dt

import pytest

from plan_bana.extract import (
    ExtractError,
    Line,
    Statement,
    apply_answer,
    chat_prompt,
    extract_statements,
    reduce,
    validate,
)
from plan_bana.llm import ModelTurn, ToolCall
from plan_bana.model import IST, WINDOWS

NOW = dt.datetime(2026, 9, 30, 18, 0, tzinfo=IST).timestamp()  # Wednesday
TODAY = dt.date(2026, 9, 30)
SAT, SUN = "2026-10-03", "2026-10-04"
ORG = (1, "Harsh")

LINES = [
    Line(10, 1, "Harsh", "sat raat free ho sab?", NOW - 600),
    Line(11, 2, "Rohan", "haan sat chalega, budget 800 max", NOW - 500),
    Line(12, 3, "Priya", "veg hai main, sat ok", NOW - 400),
    Line(13, 4, "Aman", "comedy?", NOW - 300),
]


def st(mid, uid, field, value, dts=0):
    return Statement(mid, uid, field, value, NOW - 1000 + mid + dts)


# ---------- validate (unknown ids/fields/values dropped) ----------

def test_validate_drops_invented_message_ids_and_bad_values():
    raw = {"statements": [
        {"message_id": 11, "field": "budget", "value": "800"},
        {"message_id": 99, "field": "budget", "value": "500"},        # not in buffer
        {"message_id": 12, "field": "salary", "value": "1"},          # unknown field
        {"message_id": 12, "field": "veg", "value": "maybe"},         # unparseable
        {"message_id": 13, "field": "date_ok", "value": "2020-01-01"},  # past
        {"message_id": 12, "field": "veg", "value": "true"},
    ]}
    out = validate(raw, LINES, TODAY)
    assert [(s.message_id, s.user_id, s.field, s.value) for s in out] == [
        (11, 2, "budget", 800), (12, 3, "veg", True)]


def test_validate_rejects_non_list():
    with pytest.raises(ExtractError):
        validate({"statements": "none"}, LINES, TODAY)


def test_prompt_shows_ids_names_and_ist_time():
    assert chat_prompt(LINES[:1]) == "[10] Harsh (Wed 17:50): sat raat free ho sab?"


# ---------- reduce (CEO D6 rules) ----------

def test_full_chat_reduces_to_samjha():
    c = reduce([
        st(10, 1, "date_ok", SAT), st(10, 1, "time_window", "raat"),
        st(11, 2, "date_ok", SAT), st(11, 2, "budget", 800),
        st(12, 3, "veg", True), st(12, 3, "date_ok", SAT),
        st(13, 4, "genre", "comedy"), st(13, 4, "headcount", 4),
    ], LINES, ORG, NOW)
    assert c.date == SAT and c.window == WINDOWS["raat"] and c.budget == 800
    assert c.genres == ["comedy"] and c.headcount == 4 and c.gaps == []
    assert c.people[3].veg is True and {p.name for p in c.coming()} == {"Harsh", "Rohan", "Priya", "Aman"}


def test_latest_statement_per_person_wins():
    c = reduce([st(11, 2, "budget", 1500), st(12, 2, "budget", 600), st(10, 1, "date_ok", SAT),
                st(10, 1, "time_window", "raat"), st(10, 1, "genre", "music"), st(10, 1, "headcount", 3)],
               LINES, ORG, NOW)
    assert c.budget == 600


def test_group_budget_is_minimum_of_people_coming():
    c = reduce([st(11, 2, "budget", 800), st(12, 3, "budget", 500), st(13, 3, "not_coming", True),
                st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.budget == 800  # Priya dropped out, her ₹500 no longer binds


def test_date_tie_becomes_gap_with_tied_dates():
    c = reduce([st(11, 2, "date_ok", SAT), st(12, 3, "date_ok", SUN)], LINES, ORG, NOW)
    assert c.gaps[0] == "date" and c.tie_dates == [SAT, SUN] and c.date is None


def test_no_date_asks_date_and_defaults_the_rest():
    c = reduce([st(11, 2, "budget", 800)], LINES, ORG, NOW)
    assert c.gaps == ["date", "time", "genre"]
    assert c.window == WINDOWS["raat"] and c.genres == ["comedy", "music"]
    assert any("raat" in a for a in c.assumed) and any("comedy ya music" in a for a in c.assumed)
    assert not any("din" in a for a in c.assumed)  # the asked gap is not shown as an assumption


def test_window_intersection_and_empty_intersection():
    c = reduce([st(10, 1, "time_window", "raat"), st(11, 2, "time_window", (20 * 60, 23 * 60)),
                st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.window == (20 * 60, 23 * 60)
    c = reduce([st(10, 1, "time_window", "dopahar"), st(11, 2, "time_window", "raat"),
                st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.gaps[0] == "time"


def test_genre_tie_searches_both_and_none_means_dinner():
    c = reduce([st(10, 1, "genre", "comedy"), st(11, 2, "genre", "music"), st(10, 1, "date_ok", SAT)],
               LINES, ORG, NOW)
    assert c.genres == ["comedy", "music"]
    c = reduce([st(10, 1, "genre", "none"), st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.genres == []


def test_headcount_larger_of_stated_and_people():
    c = reduce([st(10, 1, "headcount", 5), st(11, 2, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.headcount == 5 and c.stated_headcount == 5 and len(c.coming()) == 2


def test_not_coming_excluded_from_headcount_and_dates():
    c = reduce([st(11, 2, "date_ok", SUN), st(12, 3, "date_ok", SAT), st(13, 4, "date_ok", SAT),
                st(14, 4, "not_coming", True), st(10, 1, "date_ok", SUN)], LINES, ORG, NOW)
    assert c.date == SUN and 4 not in {p.user_id for p in c.coming()}


def test_names_come_from_buffer_not_model():
    c = reduce([st(11, 2, "budget", 800), st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    assert c.people[2].name == "Rohan"


def test_organizer_always_included():
    c = reduce([], [], ORG, NOW)
    assert list(c.people) == [1] and "date" in c.gaps and "headcount" in c.gaps


# ---------- gap answers ----------

def test_answer_fills_gap_and_stops_further_questions():
    c = reduce([st(11, 2, "budget", 800)], LINES, ORG, NOW)
    apply_answer(c, "date", SAT, TODAY)
    assert c.date == SAT and c.gaps == []


def test_answer_budget_zero_means_no_limit():
    c = reduce([st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    apply_answer(c, "budget", "0", TODAY)
    assert c.budget is None


def test_answer_rejects_past_date():
    c = reduce([], LINES, ORG, NOW)
    with pytest.raises(ValueError):
        apply_answer(c, "date", "2020-01-01", TODAY)


def test_constraints_json_roundtrip():
    from plan_bana.model import Constraints
    c = reduce([st(12, 3, "veg", True), st(10, 1, "date_ok", SAT)], LINES, ORG, NOW)
    back = Constraints.from_json(c.to_json())
    assert back == c


# ---------- model call: retry once, then fail visibly (CEO D4) ----------

class Scripted:
    def __init__(self, turns):
        self.turns = turns

    def start(self, *a):
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


async def test_extract_retries_once_then_succeeds():
    turns = [ModelTurn([], "I cannot help"),
             ModelTurn([ToolCall("report_statements", {"statements": [
                 {"message_id": 11, "field": "budget", "value": "800"}]})])]
    out = await extract_statements(lambda: Scripted(turns), LINES, NOW)
    assert [(s.field, s.value) for s in out] == [("budget", 800)]


async def test_extract_gives_up_after_two_failures():
    turns = [RuntimeError("503"), ModelTurn([ToolCall("report_statements", {"statements": "x"})])]
    with pytest.raises(ExtractError):
        await extract_statements(lambda: Scripted(turns), LINES, NOW)
