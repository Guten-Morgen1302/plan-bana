"""Full Plan Bana rounds through the real state machine: fake Telegram + fake Swiggy (live formats) + scripted
Gemini. DRY_RUN is on unless a test turns it off."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from plan_bana import copy
from plan_bana.bot import MessageRefresher
from plan_bana.db import PlanDB, connect
from plan_bana.llm import ModelTurn, ToolCall
from plan_bana.rounds import IDLE_EXPIRE_S, Deps, Rounds
from plan_bana.store import Store
from tests.fake_plan_swiggy import ist, standard_world

CHAT = -1001
BOT = 999
OWNER = 1
SAT = "2026-10-03"
NOW0 = ist(2026, 9, 30, 18, 0).timestamp()
USERS = {1: "Harsh", 2: "Rohan", 3: "Priya", 4: "Aman"}


def T(h, m=0):
    return int(ist(2026, 10, 3, h, m).timestamp())


class FakeTelegram:
    def __init__(self):
        self.messages: dict[int, dict] = {}
        self.sent: list[int] = []
        self.answers: list[str] = []
        self.pins: list[int] = []
        self.edits = 0
        self.fail_pin = False
        self._next = 100

    async def send(self, chat_id, text, buttons=None, *, force_reply=False, reply_to=None):
        self._next += 1
        self.messages[self._next] = {"text": text, "buttons": buttons, "force_reply": force_reply}
        self.sent.append(self._next)
        return self._next

    async def edit(self, chat_id, message_id, text, buttons=None):
        self.edits += 1
        self.messages[message_id] = {"text": text, "buttons": buttons, "force_reply": False}

    async def answer_callback(self, cid, text=""):
        self.answers.append(text)

    async def pin(self, chat_id, message_id):
        if self.fail_pin:
            raise RuntimeError("not enough rights to pin")
        self.pins.append(message_id)

    def text(self, mid):
        return self.messages[mid]["text"]

    def button(self, mid, label_start):
        for row in self.messages[mid]["buttons"] or []:
            for label, data in row:
                if label.startswith(label_start):
                    return data
        raise AssertionError(f"no button {label_start!r} in {self.messages[mid]['buttons']}")


class Script:
    """make_model() for both extraction and planning: turns are consumed in order."""

    def __init__(self):
        self.turns: list = []

    def __call__(self):
        return self

    def start(self, *a):
        return self.turns.pop(0) if self.turns else ModelTurn([], "nothing")

    def send_tool_results(self, results):
        return self.turns.pop(0) if self.turns else ModelTurn([], "nothing")


def call(name, **args):
    return ToolCall(name, args)


def report(*stmts):
    return ModelTurn([call("report_statements", statements=[
        {"message_id": m, "field": f, "value": v} for m, f, v in stmts])])


def event_plan_turns(reservation=None):
    return [
        ModelTurn([call("search_events", query="comedy")]),
        ModelTurn([call("get_event", eventId="100116693")]),
        ModelTurn([call("search_restaurants", query="Majiwada", near_eventId="100116693")]),
        ModelTurn([call("get_slots", restaurantId="708583")]),
        ModelTurn([call("propose_plans", plans=[{"title": "Comedy + Pizza", "eventId": "100116693",
                                                 "showId": "s1", "restaurantId": "708583",
                                                 "reservationTime": reservation or T(22, 30)}])]),
        ModelTurn([call("search_restaurants", query="italian")]),
        ModelTurn([call("get_slots", restaurantId="864731")]),
        ModelTurn([call("propose_plans", plans=[
            {"title": "Sourdough Shaam", "restaurantId": "864731", "reservationTime": T(21)}])]),
    ]


class Harness:
    def __init__(self, tmp_path, world=None):
        conn = connect(tmp_path / "plan.db")
        self.db, self.store = PlanDB(conn), Store(conn)
        self.tg, self.script, self.world = FakeTelegram(), Script(), world or standard_world()
        self.now = NOW0
        self._mid = 0

        @asynccontextmanager
        async def sessions():
            yield self.world.scenes, self.world.dineout

        self.refresher = MessageRefresher(self.tg, min_gap=0)
        self.rounds = Rounds(Deps(db=self.db, store=self.store, tg=self.tg, sessions=sessions,
                                  make_model=self.script, refresher=self.refresher, clock=lambda: self.now,
                                  owner_id=OWNER, owner_name="Harsh", bot_id=BOT))

    async def say(self, uid, text, **extra):
        self._mid += 1
        msg = {"message_id": self._mid, "date": self.now, "chat": {"id": CHAT, "type": "group"},
               "from": {"id": uid, "first_name": USERS.get(uid, f"U{uid}")}, "text": text, **extra}
        out = await self.rounds.on_update({"update_id": self._mid, "message": msg})
        await self.refresher.drain()
        return out

    async def pin(self, uid, lat=19.1726, lng=72.9565):
        self._mid += 1
        msg = {"message_id": self._mid, "date": self.now, "chat": {"id": CHAT, "type": "group"},
               "from": {"id": uid, "first_name": USERS.get(uid, "U")}, "location": {"latitude": lat, "longitude": lng}}
        return await self.rounds.on_update({"update_id": self._mid, "message": msg})

    async def tap(self, uid, data, chat=CHAT):
        out = await self.rounds.on_update({"update_id": 0, "callback_query": {
            "id": f"cb{uid}", "from": {"id": uid, "first_name": USERS.get(uid, f"U{uid}")}, "data": data,
            "message": {"message_id": 1, "chat": {"id": chat, "type": "group"}}}})
        await self.refresher.drain()
        return out

    async def jobs(self, until=None):
        outs = []
        while (job := self.store.claim("w", until or self.now)) is not None:
            outs.append(await self.rounds.on_job(job))
            self.store.complete(job.id, "w")
        await self.refresher.drain()
        return outs

    def round(self):
        return self.db.last_round(CHAT)


async def to_confirming(h, with_area=True):
    if with_area:
        h.db.set_area(CHAT, 19.1726, 72.9565, "Mulund West")
    await h.say(2, "sat raat free hu, budget 900 max")   # mid 1
    await h.say(3, "veg hu main, sat ok")                 # mid 2
    await h.say(1, "comedy chalega?")                     # mid 3
    h.script.turns.append(report((1, "date_ok", SAT), (1, "budget", "900"), (1, "time_window", "raat"),
                                 (2, "veg", "true"), (2, "date_ok", SAT), (3, "genre", "comedy"),
                                 (3, "date_ok", SAT)))
    assert await h.say(1, "/plan") == "extracting"
    assert await h.jobs() == ["confirming"]
    return h.round()


async def to_voting(h):
    r = await to_confirming(h)
    h.script.turns += event_plan_turns()
    assert await h.tap(1, copy.encode("s", r["id"])) == "planning"
    assert await h.jobs() == ["voting"]
    return h.round()


async def to_rsvp(h):
    r = await to_voting(h)
    for uid in (2, 3):
        await h.tap(uid, copy.encode("v", r["id"], 1))
    assert h.round()["state"] == "RSVP"
    return h.round()


# ================================================================ the demo path


async def test_full_round_chat_to_test_only_booking(tmp_path):
    h = Harness(tmp_path)
    assert await h.rounds.on_update({"my_chat_member": {"chat": {"id": CHAT, "type": "group"},
                                                        "new_chat_member": {"status": "member"}}}) == "joined"
    notice = h.tg.sent[0]
    assert "Google Gemini" in h.tg.text(notice) and "/forget" in h.tg.text(notice)

    await h.say(2, "sat raat free hu, budget 900 max")
    await h.say(3, "veg hu main, sat ok")
    await h.say(1, "comedy chalega?")
    assert await h.say(1, "/plan") == "waiting_pin"          # no area yet
    assert await h.pin(2) == "location_ignored"              # only the organizer's pin counts
    h.script.turns.append(report((1, "date_ok", SAT), (1, "budget", "900"), (1, "time_window", "raat"),
                                 (2, "veg", "true"), (2, "date_ok", SAT), (3, "genre", "comedy")))
    assert await h.pin(1) == "pin_resumed"
    assert await h.jobs() == ["confirming"]
    r = h.round()
    samjha = r["samjha_msg"]
    assert "Sat 3 Oct" in h.tg.text(samjha) and "₹900/head tak · veg: Priya" in h.tg.text(samjha)

    assert await h.tap(2, h.tg.button(samjha, "✅ Sahi hai")) == "not_organizer"
    assert "organizer" in h.tg.answers[-1]
    h.script.turns += event_plan_turns()
    assert await h.tap(1, h.tg.button(samjha, "✅ Sahi hai")) == "planning"
    assert h.tg.messages[samjha]["buttons"] is None           # stale keyboard removed
    assert await h.jobs() == ["voting"]
    r = h.round()
    plans = h.tg.text(r["plans_msg"])
    assert "2 plan mile" in plans and "Comedy In Thane" in plans and "FREE table" in plans
    assert "Vote karo" in plans

    await h.tap(2, h.tg.button(r["plans_msg"], "1 ·"))
    assert await h.tap(4, h.tg.button(r["plans_msg"], "1 ·")) == "voted"   # quiet friend can vote (DR4)
    assert h.round()["state"] == "VOTING"                    # 2 of 4 known people: no majority yet
    assert "Plan 1 aage hai (2 vote)" in h.tg.text(r["control_msg"])
    assert await h.tap(1, h.tg.button(r["control_msg"], "🔒 Plan 1")) == "locked"
    r = h.round()
    assert r["state"] == "RSVP" and "✅ Plan 1 final" in h.tg.text(r["plans_msg"])
    assert h.tg.messages[r["plans_msg"]]["buttons"] is None

    await h.tap(2, h.tg.button(r["rsvp_msg"], "🎟 Ticket le liya"))
    await h.tap(3, h.tg.button(r["rsvp_msg"], "🙋 Aa raha"))
    await h.tap(4, h.tg.button(r["rsvp_msg"], "❌ Nahi"))
    assert await h.tap(2, h.tg.button(r["control_msg"], "➕ Guest")) == "not_organizer"
    await h.tap(1, h.tg.button(r["control_msg"], "➕ Guest"))
    assert "3 aa rahe + 1 guest = 4" in h.tg.text(r["control_msg"])
    assert "Ticket baaki: Harsh, Priya" in h.tg.text(r["rsvp_msg"])

    await h.tap(1, h.tg.button(r["control_msg"], "🍽 4 logon ki table"))
    assert h.round()["state"] == "BOOK_PENDING"
    assert "cancel sirf Swiggy app se hota hai" in h.tg.text(r["control_msg"])
    assert await h.tap(1, h.tg.button(r["control_msg"], "✅ Haan")) == "booking_queued"
    assert await h.tap(3, copy.encode("r", r["id"], "n")) == "frozen"   # party frozen at the Book tap
    assert await h.jobs() == ["test_only"]

    r = h.round()
    assert r["state"] == "TEST_ONLY" and h.world.booked == []            # DRY_RUN: nothing booked
    final = h.tg.text(r["rsvp_msg"])
    assert final.startswith("🎉 Sat ka plan pakka!") and "4 logon ki table" in final
    assert final.splitlines()[-1] == "🧪 Test mode: table asli mein book nahi hui"
    assert "Test mode" in h.tg.text(r["control_msg"]) and h.tg.pins == [r["rsvp_msg"]]
    attempt = next(json.loads(e["data"]) for e in h.db.events(r["id"]) if e["kind"] == "book_attempt")
    assert attempt["guestCount"] == 4 and attempt["itemId"] == "708583-735129"
    assert attempt["reservationTime"] == T(22, 30)
    # design DR2: 4 new messages per round (+ the one-time join notice and pin prompt)
    assert len(h.tg.sent) == 1 + 4


async def test_real_booking_path_when_dry_run_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["booked"]
    assert h.world.booked[0]["guestCount"] == 2 and h.round()["state"] == "BOOKED"
    assert "Table book ho gayi" in h.tg.text(h.round()["control_msg"])


async def test_swiggy_refuses_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    h.world.fail["book_table"] = "error"
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["failed"]
    assert "Swiggy ne mana kiya" in h.tg.text(h.round()["control_msg"])


async def test_network_error_during_booking_needs_review_not_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    h.world.fail["book_table"] = TimeoutError("read timeout")
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["needs_review"]
    assert len(h.world.booked) == 1 and "dobara mat dabao" in h.tg.text(h.round()["control_msg"])


async def test_reclaimed_booking_job_never_books_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    h.db.cas_state(r["id"], "BOOK_PENDING", "BOOKING", h.now)  # worker died after the CAS, lease expired
    assert await h.jobs() == ["needs_review_reclaimed"]
    assert h.world.booked == [] and h.round()["state"] == "NEEDS_REVIEW"


# ================================================================ Book re-check branches


async def test_slot_gone_offers_alternative_and_needs_new_tap(tmp_path):
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    rest = h.world.restaurants["708583"]
    rest.times = [t for t in rest.times if t != ist(2026, 10, 3, 22, 30)]
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["slot_gone"]
    r = h.round()
    assert r["state"] == "BOOK_PENDING" and r["book_requested"] == 0 and r["alt_time"] in (T(22, 15), T(22, 45))
    assert "Woh slot gaya" in h.tg.text(r["control_msg"])
    assert await h.tap(1, copy.encode("a", r["id"], r["alt_time"])) == "alt_accepted"
    assert "cancel sirf Swiggy app se hota hai" in h.tg.text(r["control_msg"])
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["test_only"]


async def test_show_gone_asks_table_only(tmp_path):
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    h.world.events["100116693"].shows[0].seats = 0
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["show_gone"]
    assert "Show ab list nahi hai" in h.tg.text(h.round()["control_msg"])
    assert await h.tap(1, copy.encode("t", r["id"])) == "table_only"
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["test_only"]


async def test_party_below_two_cannot_book(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    await h.tap(1, copy.encode("v", r["id"], 1))
    await h.tap(1, copy.encode("l", r["id"], 1))
    r = h.round()
    assert "Sirf 1 log?" in h.tg.text(r["control_msg"])
    assert await h.tap(1, copy.encode("b", r["id"])) == "stale"


async def test_rsvp_deadline_prompts_but_never_books(tmp_path):
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    assert await h.jobs(until=h.now + 31 * 60) == ["book_prompt"]
    r = h.round()
    assert r["state"] == "BOOK_PENDING" and not r["book_requested"] and h.world.booked == []
    assert "Haan, book karo" in str(h.tg.messages[r["control_msg"]]["buttons"])


async def test_ruko_goes_back_to_rsvp(tmp_path):
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    await h.tap(1, copy.encode("b", r["id"]))
    assert await h.tap(1, copy.encode("w", r["id"])) == "back_to_rsvp"
    assert h.round()["state"] == "RSVP"


# ================================================================ voting, gaps, Badlo


async def test_majority_auto_locks_and_ties_need_organizer(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    await h.tap(2, copy.encode("v", r["id"], 1))
    await h.tap(3, copy.encode("v", r["id"], 2))
    assert "barabar" in h.tg.text(r["control_msg"])
    assert await h.tap(1, copy.encode("l", r["id"], 2)) == "locked"
    assert h.round()["locked_idx"] == 2


async def test_lock_only_the_leader(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    await h.tap(2, copy.encode("v", r["id"], 1))
    assert await h.tap(1, copy.encode("l", r["id"], 2)) == "not_leader"


async def test_vote_for_unknown_plan_rejected(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    assert await h.tap(2, copy.encode("v", r["id"], 9)) == "bad_vote"


async def test_gap_question_then_samjha(tmp_path):
    h = Harness(tmp_path)
    h.db.set_area(CHAT, 19.1726, 72.9565, "Mulund West")
    await h.say(2, "budget 900 max")
    h.script.turns.append(report((1, "budget", "900")))
    await h.say(1, "/plan")
    await h.jobs()
    r = h.round()
    assert r["gap"] == "date" and "kaunsa din?" in h.tg.text(r["samjha_msg"])
    assert await h.tap(1, copy.encode("s", r["id"])) == "stale"   # can't confirm before answering
    assert await h.tap(1, copy.encode("q", r["id"], f"date={SAT}")) == "gap_answered"
    assert "Sat 3 Oct" in h.tg.text(r["samjha_msg"]) and h.round()["gap"] is None


async def test_badlo_reply_only_from_organizer_to_that_prompt(tmp_path):
    h = Harness(tmp_path)
    r = await to_confirming(h)
    await h.tap(1, copy.encode("e", r["id"]))
    prompt = h.round()["badlo_prompt"]
    assert h.tg.messages[prompt]["force_reply"]
    assert await h.say(2, "budget 500", reply_to_message={"message_id": prompt}) == "buffered"   # friend
    assert await h.say(1, "budget 500 kar do") == "buffered"                                     # not a reply
    h.script.turns.append(report((1, "date_ok", SAT), (1, "budget", "500")))
    assert await h.say(1, "budget 500", reply_to_message={"message_id": prompt}) == "badlo_reextract"
    assert await h.jobs() == ["confirming"]
    c = json.loads(h.round()["constraints"])
    assert c["extra"] == "budget 500" and c["budget"] == 500


# ================================================================ failures are always visible


async def test_zero_plans_message_and_cancel(tmp_path):
    h = Harness(tmp_path)
    r = await to_confirming(h)
    h.script.turns += [ModelTurn([call("propose_plans", plans=[])]), ModelTurn([call("propose_plans", plans=[])])]
    await h.tap(1, copy.encode("s", r["id"]))
    assert await h.jobs() == ["zero_plans"]
    assert "Kuch fit nahi hua" in h.tg.text(h.round()["plans_msg"]) and h.round()["state"] == "CANCELLED"


async def test_swiggy_login_expired_during_planning(tmp_path):
    h = Harness(tmp_path)
    r = await to_confirming(h)
    h.world.fail["search_events"] = "auth"
    h.script.turns += event_plan_turns()
    await h.tap(1, copy.encode("s", r["id"]))
    assert await h.jobs() == ["auth_failed"]
    assert "login expire" in h.tg.text(h.round()["plans_msg"])


async def test_extraction_failure_is_shown(tmp_path):
    h = Harness(tmp_path)
    h.db.set_area(CHAT, 19.1726, 72.9565, "Mulund West")
    await h.say(2, "hello")
    h.script.turns += [ModelTurn([], "no"), ModelTurn([], "no")]
    await h.say(1, "/plan")
    assert await h.jobs() == ["extract_failed"]
    assert "Samajh nahi aaya" in h.tg.text(h.round()["samjha_msg"])


async def test_empty_chat(tmp_path):
    h = Harness(tmp_path)
    h.db.set_area(CHAT, 19.1726, 72.9565, "Mulund West")
    await h.say(1, "/plan")
    assert await h.jobs() == ["empty"]
    assert "plan ki baat nahi dikhi" in h.tg.text(h.round()["samjha_msg"])


async def test_plan_args_are_read(tmp_path):
    h = Harness(tmp_path)
    h.db.set_area(CHAT, 19.1726, 72.9565, "Mulund West")
    h.script.turns.append(report((1, "date_ok", SAT)))
    await h.say(1, "/plan sat raat comedy")
    assert await h.jobs() == ["confirming"]


# ================================================================ open rounds, expiry, commands


async def test_second_plan_blocked_then_replace_by_organizer(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    assert await h.say(2, "/plan") == "plan_running"
    assert await h.say(1, "/plan") == "replace_prompt"
    prompt = h.tg.sent[-1]
    assert await h.tap(2, h.tg.button(prompt, "Haan")) == "stale"      # only the requester answers
    assert await h.tap(1, h.tg.button(prompt, "Haan")) == "extracting"
    assert h.db.round(r["id"])["state"] == "CANCELLED" and h.round()["id"] != r["id"]


async def test_anyone_can_replace_idle_open_round_but_locked_needs_3h(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    h.now += 31 * 60
    assert await h.say(2, "/plan") == "replace_prompt"
    (tmp_path / "b").mkdir(exist_ok=True)
    h2 = Harness(tmp_path / "b")
    r2 = await to_rsvp(h2)
    h2.now += 31 * 60
    assert await h2.say(4, "/plan") == "plan_running"
    h2.now += 3 * 3600
    assert await h2.say(4, "/plan") == "replace_prompt"
    assert r2["state"] == "RSVP" and r["state"] == "VOTING"


async def test_lazy_expiry_and_sweeper(tmp_path):
    h = Harness(tmp_path)
    r = await to_voting(h)
    h.now += IDLE_EXPIRE_S + 1
    assert await h.rounds.sweep(h.now) == 1
    assert h.db.round(r["id"])["state"] == "EXPIRED"
    assert "Plan band" in h.tg.text(r["plans_msg"]) and h.tg.messages[r["plans_msg"]]["buttons"] is None
    assert await h.tap(2, copy.encode("v", r["id"], 1)) == "closed"


async def test_cancel_removes_all_keyboards(tmp_path):
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    assert await h.tap(1, copy.encode("c", r["id"])) == "cancelled"
    r = h.round()
    for f in ("plans_msg", "rsvp_msg", "control_msg"):
        assert h.tg.messages[r[f]]["buttons"] is None


async def test_old_and_garbage_buttons(tmp_path):
    h = Harness(tmp_path)
    assert await h.tap(1, "approve:x:y") == "bad_callback"
    assert await h.tap(1, copy.encode("v", "zzzzzzzz", 1)) == "closed"


async def test_forget_status_debug_and_private_chat(tmp_path):
    h = Harness(tmp_path)
    await h.say(2, "sat free")
    await h.say(3, "veg hu")
    assert await h.say(3, "/forget") == "forgot"
    assert "1 message" in h.tg.text(h.tg.sent[-1])
    assert await h.say(2, "/status") == "status" and "1 message" in h.tg.text(h.tg.sent[-1])
    assert await h.say(2, "/debug") == "debug_denied"
    assert await h.say(OWNER, "/debug") == "debug"
    out = await h.rounds.on_update({"message": {"message_id": 1, "chat": {"id": 5, "type": "private"},
                                                "from": {"id": 5}, "text": "/start"}})
    assert out == "private"


async def test_area_command_rules(tmp_path):
    h = Harness(tmp_path)
    await to_voting(h)
    assert await h.say(2, "/area") == "area_blocked"
    await h.tap(1, copy.encode("c", h.round()["id"]))
    assert await h.say(2, "/area") == "area_denied"          # only the last organizer
    assert await h.say(1, "/area") == "area_pending"
    assert await h.pin(2) == "location_ignored"
    assert await h.pin(1, 19.0, 72.8) == "area_set"
    assert h.db.area(CHAT)["lat"] == 19.0


async def test_bots_and_non_text_are_not_buffered(tmp_path):
    h = Harness(tmp_path)
    await h.rounds.on_update({"message": {"message_id": 9, "date": h.now, "chat": {"id": CHAT, "type": "group"},
                                          "from": {"id": 50, "is_bot": True}, "text": "spam"}})
    await h.rounds.on_update({"message": {"message_id": 10, "date": h.now, "chat": {"id": CHAT, "type": "group"},
                                          "from": {"id": 2}, "sticker": {}}})
    assert h.db.recent_messages(CHAT, h.now) == []


async def test_edited_message_updates_buffer(tmp_path):
    h = Harness(tmp_path)
    await h.say(2, "sat free")
    await h.rounds.on_update({"edited_message": {"message_id": 1, "chat": {"id": CHAT, "type": "group"},
                                                 "from": {"id": 2}, "text": "sun free"}})
    assert h.db.recent_messages(CHAT, h.now)[0]["text"] == "sun free"


async def test_pin_failure_is_logged_not_raised(tmp_path):
    h = Harness(tmp_path)
    h.tg.fail_pin = True
    r = await to_rsvp(h)
    await h.tap(4, copy.encode("r", r["id"], "c"))
    await h.tap(1, copy.encode("b", r["id"]))
    await h.tap(1, copy.encode("k", r["id"]))
    assert await h.jobs() == ["test_only"]
    assert any(e["kind"] == "pin_failed" for e in h.db.events(r["id"]))


@pytest.mark.parametrize("n_taps", [5])
async def test_vote_burst_keeps_final_tally(tmp_path, n_taps):
    h = Harness(tmp_path)
    r = await to_voting(h)
    await asyncio.gather(*(h.rounds.on_update({"callback_query": {
        "id": str(i), "from": {"id": 100 + i, "first_name": f"F{i}"}, "data": copy.encode("v", r["id"], 2),
        "message": {"message_id": 1, "chat": {"id": CHAT, "type": "group"}}}})
        for i in range(n_taps)))
    await h.refresher.drain()
    assert h.round()["state"] == "RSVP" or "(" in h.tg.text(r["plans_msg"])


async def test_taps_from_another_chat_are_refused(tmp_path):
    """CSO F1: a round only accepts button taps from its own group, even with a valid round id."""
    h = Harness(tmp_path)
    r = await to_rsvp(h)
    other = -2002
    assert await h.tap(4, copy.encode("v", r["id"], 1), chat=other) == "wrong_chat"
    assert await h.tap(4, copy.encode("r", r["id"], "c"), chat=other) == "wrong_chat"
    assert await h.tap(1, copy.encode("b", r["id"]), chat=other) == "wrong_chat"   # even the organizer
    assert 4 not in h.db.rsvps(r["id"]) and h.round()["state"] == "RSVP"
    assert await h.tap(4, copy.encode("r", r["id"], "c")) == "rsvp"                 # same group still works
