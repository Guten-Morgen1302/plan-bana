import asyncio
import datetime as dt

import pytest

from maa.llm import ModelTurn, ToolCall
from plan_bana.checks import check_plan, veg_friendly
from plan_bana.model import WINDOWS, Constraints, Person
from plan_bana.planner import Planner, SwiggyAuthError, recheck
from tests.fake_plan_swiggy import FakeRestaurant, evening, ist, standard_world

PIN = (19.1726, 72.9565)
SAT = "2026-10-03"


def cons(**kw):
    base = {"date": SAT, "window": WINDOWS["raat"], "people": {1: Person(1, "Harsh"), 2: Person(2, "Rohan")},
            "headcount": 2, "genres": ["comedy"]}
    base.update(kw)
    return Constraints(**base)


def T(h, m=0):
    return int(ist(2026, 10, 3, h, m).timestamp())


class Scripted:
    """One scripted conversation shared by every make_model() call (turns are consumed in order)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.sent = []

    def start(self, system, user, tools):
        self.sent.append(("start", [t["name"] for t in tools]))
        return self._next()

    def send_tool_results(self, results):
        self.sent.append(("results", results))
        return self._next()

    def _next(self):
        t = self.turns.pop(0) if self.turns else ModelTurn([], "done")
        if isinstance(t, Exception):
            raise t
        return t


def call(name, **args):
    return ToolCall(name, args)


def turns_event_plan(reservation):
    return [
        ModelTurn([call("search_events", query="comedy")]),
        ModelTurn([call("get_event", eventId="100116693")]),
        ModelTurn([call("search_restaurants", query="pizza", near_eventId="100116693")]),
        ModelTurn([call("get_slots", restaurantId="708583")]),
        ModelTurn([call("propose_plans", plans=[{"title": "Comedy + Pizza", "eventId": "100116693",
                                                 "showId": "s1", "restaurantId": "708583",
                                                 "reservationTime": reservation}])]),
    ]


def planner(world, script, c=None, **kw):
    return Planner(lambda: script, world.scenes, world.dineout, c or cons(), *PIN, "Mulund West, Mumbai", **kw)


async def fill_corpus(world, c=None):
    p = planner(world, Scripted([]), c)
    p._budget = 99
    await p._dispatch("search_events", {"query": "comedy"})
    await p._dispatch("get_event", {"eventId": "100116693"})
    await p._dispatch("get_event", {"eventId": "100114197"})
    await p._dispatch("search_restaurants", {"query": "pizza", "near_eventId": "100116693"})
    for rid in ("708583", "864731", "555001"):
        await p._dispatch("get_slots", {"restaurantId": rid})
    return p.corpus


# ---------------------------------------------------------------- checks (CEO D6 1–5 + 0)

async def test_good_event_plan_passes_with_facts_from_swiggy():
    corpus = await fill_corpus(standard_world())
    opt, reasons = check_plan({"title": "Comedy + Pizza", "eventId": "100116693", "showId": "s1",
                               "restaurantId": "708583", "reservationTime": T(22, 30)}, corpus, cons(), 1)
    assert reasons == [] and opt.item_id == "708583-735129" and opt.slot_id == 1
    assert opt.per_head == 500 + 249 and opt.restaurant.distance_km == 1.2 and opt.restaurant.searched_at_venue
    assert opt.event.end - opt.event.start == 90 * 60


async def test_invented_reservation_time_rejected():
    corpus = await fill_corpus(standard_world())
    opt, reasons = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                               "reservationTime": T(22, 31)}, corpus, cons(), 1)
    assert opt is None and "time slot nahi" in reasons[0]


async def test_show_must_belong_to_event_and_restaurant_must_be_fetched():
    corpus = await fill_corpus(standard_world())
    assert check_plan({"eventId": "100116693", "showId": "s2", "restaurantId": "708583",
                       "reservationTime": T(22, 30)}, corpus, cons(), 1)[0] is None
    assert check_plan({"restaurantId": "999", "reservationTime": T(22, 30)}, corpus, cons(), 1)[0] is None


async def test_table_too_soon_after_show():
    corpus = await fill_corpus(standard_world())
    _, reasons = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                             "reservationTime": T(22, 15)}, corpus, cons(), 1)
    assert "show aur table ke beech time kam hai" in reasons  # show ends 22:00, known distance → +20 min


async def test_unknown_distance_needs_45_minutes():
    corpus = await fill_corpus(standard_world())
    corpus.restaurants["708583"]["search_point"] = "pin"  # searched near the group, not the venue
    _, reasons = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                             "reservationTime": T(22, 30)}, corpus, cons(), 1)
    assert "show aur table ke beech time kam hai" in reasons
    opt, _ = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                         "reservationTime": T(22, 45)}, corpus, cons(), 1)
    assert opt is not None and opt.restaurant.distance_km is None


async def test_restaurant_far_from_venue_rejected():
    world = standard_world()
    world.restaurants["708583"].distance = "7.5 km"
    corpus = await fill_corpus(world)
    _, reasons = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                             "reservationTime": T(22, 30)}, corpus, cons(), 1)
    assert any("7.5 km door" in r for r in reasons)


async def test_event_far_from_group_rejected():
    world = standard_world()
    world.events["100114197"].shows[0].show_id = "s3"
    corpus = await fill_corpus(world)
    _, reasons = check_plan({"eventId": "100114197", "showId": "s3", "restaurantId": "864731",
                             "reservationTime": T(22, 30)}, corpus, cons(), 1)
    assert any("bahut door" in r for r in reasons)


async def test_window_and_date_checks():
    corpus = await fill_corpus(standard_world())
    _, reasons = check_plan({"restaurantId": "864731", "reservationTime": T(17, 30)}, corpus, cons(), 1)
    assert "table time window se bahar hai" in reasons


async def test_budget_uses_ticket_plus_half_cost():
    corpus = await fill_corpus(standard_world())
    _, reasons = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                             "reservationTime": T(22, 30)}, corpus, cons(budget=700), 1)
    assert any("budget se mehenga" in r for r in reasons)  # 249 + 500 = 749 > 700
    opt, _ = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                         "reservationTime": T(22, 30)}, corpus, cons(budget=800), 1)
    assert opt is not None


async def test_unknown_ticket_price_shows_question_mark_not_tick():
    world = standard_world()
    world.events["100116693"].shows[0].price = None
    corpus = await fill_corpus(world)
    c = cons(people={1: Person(1, "Harsh", budget=900)})
    opt, _ = check_plan({"eventId": "100116693", "showId": "s1", "restaurantId": "708583",
                         "reservationTime": T(22, 30)}, corpus, c, 1)
    assert opt.ticket_unknown and ("Harsh", "budget", "unknown") in opt.fit


async def test_veg_rule_and_per_person_fit():
    corpus = await fill_corpus(standard_world())
    c = cons(people={1: Person(1, "Harsh"), 2: Person(2, "Priya", veg=True, available={SAT: True}),
                     3: Person(3, "Aman", budget=400)})
    _, reasons = check_plan({"restaurantId": "555001", "reservationTime": T(21)}, corpus, c, 1)
    assert any("veg option nahi" in r for r in reasons)
    opt, _ = check_plan({"restaurantId": "864731", "reservationTime": T(21)}, corpus, c, 1)
    assert ("Priya", "veg", "ok") in opt.fit and ("Priya", "date", "ok") in opt.fit
    assert ("Aman", "budget", "bad") in opt.fit


def test_veg_friendly_heuristic():
    assert veg_friendly(["Italian"]) and veg_friendly([]) and veg_friendly(["Kebabs", "North Indian"])
    assert not veg_friendly(["Kebabs", "Barbecue"]) and not veg_friendly(["Seafood"])


# ---------------------------------------------------------------- planner loop

async def test_event_plan_then_dinner_fill():
    world = standard_world()
    turns = turns_event_plan(T(22, 30)) + [
        ModelTurn([call("search_restaurants", query="italian")]),
        ModelTurn([call("get_slots", restaurantId="864731"), call("get_slots", restaurantId="708583")]),
        ModelTurn([call("propose_plans", plans=[
            {"title": "Sourdough night", "restaurantId": "864731", "reservationTime": T(21)},
            {"title": "Pizza again", "restaurantId": "708583", "reservationTime": T(20)}])]),
    ]
    script = Scripted(turns)
    stages = []

    async def progress(stage):
        stages.append(stage)

    run = await planner(world, script, progress=progress).run()
    assert [o.title for o in run.options] == ["Comedy + Pizza", "Sourdough night", "Pizza again"]
    assert [o.idx for o in run.options] == [1, 2, 3] and not run.partial
    assert stages[0] == "events" and "tables" in stages and "checks" in stages
    assert [s[1] for s in script.sent if s[0] == "start"] == [
        ["search_events", "get_event", "search_restaurants", "get_slots", "propose_plans"],
        ["search_restaurants", "get_slots", "propose_plans"]]


async def test_failed_plan_triggers_one_retry_with_reasons():
    world = standard_world()
    turns = turns_event_plan(T(22, 15))  # too soon after the show
    turns += [ModelTurn([call("propose_plans", plans=[{"title": "Comedy + Pizza", "eventId": "100116693",
                                                      "showId": "s1", "restaurantId": "708583",
                                                      "reservationTime": T(22, 30)}])])]
    script = Scripted(turns)
    run = await planner(world, script, c=cons(genres=["comedy"])).run()
    retry = next(r for kind, r in script.sent if kind == "results" and r and r[0][0] == "propose_plans")
    assert "time kam" in retry[0][1]["error"]
    assert run.options and run.options[0].reservation_time == T(22, 30)


async def test_tool_calls_in_one_turn_run_concurrently():
    world = standard_world()
    started, release = [], asyncio.Event()
    orig = world.handle

    async def slow(name, args):
        if name == "get_restaurant_details":
            started.append(args["restaurantId"])
            await release.wait()
        return await orig(name, args)

    world.handle = slow
    p = planner(world, Scripted([]), c=cons(genres=[]))
    p._budget = 99
    await p._dispatch("search_restaurants", {"query": "x"})
    task = asyncio.gather(p._dispatch("get_slots", {"restaurantId": "708583"}),
                          p._dispatch("get_slots", {"restaurantId": "864731"}))
    for _ in range(20):
        await asyncio.sleep(0)
    assert sorted(started) == ["708583", "864731"]  # both in flight before either finished
    release.set()
    await task


async def test_call_budget_is_enforced():
    p = planner(standard_world(), Scripted([]))
    p._budget = 1
    assert "limit" in (await p._dispatch("get_event", {"eventId": "100116693"}))["error"]
    assert "events" in await p._dispatch("search_events", {"query": "comedy"})
    assert "limit" in (await p._dispatch("search_events", {"query": "comedy"}))["error"]


async def test_auth_failure_bubbles_up():
    world = standard_world()
    world.fail["search_events"] = "auth"
    with pytest.raises(SwiggyAuthError):
        await planner(world, Scripted(turns_event_plan(T(22, 30)))).run()


async def test_other_tool_errors_go_back_to_model():
    world = standard_world()
    world.fail["get_available_slots"] = "error"
    p = planner(world, Scripted([]))
    p._budget = 99
    await p._dispatch("search_restaurants", {"query": "x"})
    assert "error" in await p._dispatch("get_slots", {"restaurantId": "708583"})


async def test_deadline_returns_partial():
    world = standard_world()
    ticks = {"n": 0}

    def clock():  # time jumps past the deadline after a few model/tool rounds
        ticks["n"] += 1
        return 0.0 if ticks["n"] < 6 else 1000.0

    run = await planner(world, Scripted(turns_event_plan(T(22, 30))), deadline_s=90, clock=clock).run()
    assert run.partial and run.options == []


async def test_unknown_restaurant_for_slots_is_refused():
    p = planner(standard_world(), Scripted([]))
    p._budget = 99
    assert "search_restaurants" in (await p._dispatch("get_slots", {"restaurantId": "708583"}))["error"]


async def test_dinner_only_when_no_genres():
    world = standard_world()
    script = Scripted([
        ModelTurn([call("search_restaurants", query="italian")]),
        ModelTurn([call("get_slots", restaurantId="864731")]),
        ModelTurn([call("propose_plans", plans=[{"title": "Sourdough", "restaurantId": "864731",
                                                 "reservationTime": T(20, 30)}])]),
    ])
    run = await planner(world, script, c=cons(genres=[])).run()
    assert [o.kind for o in run.options] == ["dinner"]
    assert not any(n in ("search_events", "get_event_details") for n, _ in world.calls)


# ---------------------------------------------------------------- booking re-check

async def _one_option(world):
    run = await planner(world, Scripted(turns_event_plan(T(22, 30))), c=cons()).run()
    return run.options[0]


async def test_recheck_ok():
    world = standard_world()
    opt = await _one_option(world)
    r = await recheck(opt, world.scenes, world.dineout, cons(), *PIN, table_only=False)
    assert r.status == "ok" and r.item_id == "708583-735129"


async def test_recheck_show_gone():
    world = standard_world()
    opt = await _one_option(world)
    world.events["100116693"].shows[0].seats = 0
    assert (await recheck(opt, world.scenes, world.dineout, cons(), *PIN, table_only=False)).status == "show_gone"
    assert (await recheck(opt, world.scenes, world.dineout, cons(), *PIN, table_only=True)).status == "ok"


async def test_recheck_slot_gone_offers_nearest_valid_time():
    world = standard_world()
    opt = await _one_option(world)
    world.restaurants["708583"].times = [t for t in evening() if t != ist(2026, 10, 3, 22, 30)]
    r = await recheck(opt, world.scenes, world.dineout, cons(), *PIN, table_only=False)
    assert r.status == "slot_gone" and r.reservation_time in (T(22, 15), T(22, 45))
    assert r.reservation_time >= opt.event.end + 20 * 60


async def test_recheck_no_free_deal():
    world = standard_world()
    opt = await _one_option(world)
    world.restaurants["708583"].free_ticket = None
    r = await recheck(opt, world.scenes, world.dineout, cons(), *PIN, table_only=False)
    assert r.status == "slot_gone" and r.reservation_time is None


def test_fake_world_dates():
    assert ist(2026, 10, 3, 20).date() == dt.date(2026, 10, 3)
    assert FakeRestaurant("1", "x", [], 1).free_ticket


async def test_event_attempt_timeout_still_leaves_time_for_dinner():
    world = standard_world()
    now = {"t": 0.0}

    class SlowEvents(Scripted):
        def start(self, system, user, tools):
            if "search_events" in [t["name"] for t in tools]:
                now["t"] = 60.0  # the event attempt burns past its own deadline (90 - 35 = 55 s)
            return super().start(system, user, tools)

    script = SlowEvents([ModelTurn([call("search_events", query="comedy")]),
                         ModelTurn([call("search_restaurants", query="italian")]),
                         ModelTurn([call("get_slots", restaurantId="864731")]),
                         ModelTurn([call("propose_plans", plans=[{"title": "Sourdough", "restaurantId": "864731",
                                                                  "reservationTime": T(21)}])])])
    run = await planner(world, script, clock=lambda: now["t"]).run()
    assert [o.title for o in run.options] == ["Sourdough"] and not run.partial
