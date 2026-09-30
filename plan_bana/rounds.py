"""The plan round: Telegram updates in, state machine, background jobs (design Flow/States, CEO/eng/design rules).

    /plan ─► [no pin] WAITING_PIN ─pin─┐
          └────────────────────────────┴► EXTRACTING ─job─► CONFIRMING (⇄ gap question / Badlo re-extract)
    Sahi hai ─► PLANNING ─job─► VOTING ─lock─► RSVP ─Book─► BOOK_PENDING ─Haan─► job: recheck ─► BOOKING
                                                                                  │ show gone / slot gone:
                                                                                  └─ stays BOOK_PENDING, organizer re-taps
    BOOKING ─► BOOKED | TEST_ONLY (DRY_RUN) | FAILED | NEEDS_REVIEW;  any time: CANCELLED, EXPIRED (6 h idle)

Inline (poller, answered in < 1 s): buffer inserts, votes, RSVPs, guests, organizer taps that only move state.
Queued (per-chat serial jobs, family "tg:<chat>"): extract, plan, book, rsvp_deadline timer (never books).
Every transition is compare-and-set on plan_rounds.state; every button carries the round id (eng E6).
Four new messages per round (design DR2): Samjha, plans, organizer control, RSVP→final card; all edited in place.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from plan_bana import copy
from plan_bana.bot import MessageRefresher
from plan_bana.db import PlanDB
from plan_bana.extract import ExtractError, Line, apply_answer, extract_statements, reduce
from plan_bana.llm import Model
from plan_bana.model import IST, LOCKED_STATES, TERMINAL, Constraints, PlanOption, now_ist
from plan_bana.planner import Planner, SwiggyAuthError, recheck, table_coords
from plan_bana.store import Job, Store
from plan_bana.swiggy import OrderBlocked

log = logging.getLogger("plan_bana.rounds")

IDLE_EXPIRE_S = 6 * 3600
IDLE_REPLACE_OPEN_S = 30 * 60
IDLE_REPLACE_LOCKED_S = 3 * 3600
RSVP_DEADLINE_S = 30 * 60
MAX_GUESTS = 18
AREA_PENDING = "area_pending:{chat}"

Sessions = Callable[[], AbstractAsyncContextManager[tuple[Any, Any]]]


@dataclass
class Deps:
    db: PlanDB
    store: Store
    tg: Any
    sessions: Sessions
    make_model: Callable[[], Model]
    refresher: MessageRefresher
    clock: Callable[[], float]
    owner_id: int | None = None
    owner_name: str = "bot owner"
    bot_id: int | None = None


def family(chat_id: int) -> str:
    return f"tg:{chat_id}"


def display_name(user: dict[str, Any]) -> str:
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    return name or user.get("username") or "Dost"


def constraints_of(r: Any) -> Constraints:
    return Constraints.from_json(json.loads(r["constraints"])) if r["constraints"] else Constraints()


def options_of(r: Any) -> list[PlanOption]:
    return [PlanOption.from_json(o) for o in json.loads(r["plans"])] if r["plans"] else []


def locked_option(r: Any) -> PlanOption | None:
    return next((o for o in options_of(r) if o.idx == r["locked_idx"]), None)


def result_of(r: Any) -> dict[str, Any]:
    return json.loads(r["result"]) if r["result"] else {}


class Rounds:
    def __init__(self, deps: Deps):
        self.d = deps

    # ================================================================ updates (poller, inline)

    async def on_update(self, upd: dict[str, Any]) -> str:
        if cb := upd.get("callback_query"):
            return await self.on_callback(cb)
        if mcm := upd.get("my_chat_member"):
            chat = mcm.get("chat") or {}
            if chat.get("type") in ("group", "supergroup") and (mcm.get("new_chat_member") or {}).get("status") in (
                    "member", "administrator"):
                await self._notice(chat["id"])
                return "joined"
            return "ignored"
        if msg := upd.get("edited_message"):
            if msg.get("text") and not msg["text"].startswith("/"):
                self.d.db.edit_message(msg["chat"]["id"], msg["message_id"], msg["text"])
            return "edited"
        if msg := upd.get("message"):
            return await self.on_message(msg)
        return "ignored"

    async def _notice(self, chat_id: int) -> None:
        if self.d.db.claim_notice(chat_id):
            await self.d.tg.send(chat_id, copy.JOIN_NOTICE)

    async def on_message(self, msg: dict[str, Any]) -> str:
        chat, user = msg.get("chat") or {}, msg.get("from") or {}
        chat_id, now = chat.get("id"), self.d.clock()
        if chat.get("type") not in ("group", "supergroup"):
            if chat_id is not None and (msg.get("text") or "").startswith("/"):
                await self.d.tg.send(chat_id, "👋 Mujhe apne dosto ke group mein add karo, phir wahan /plan likho.")
            return "private"
        if any(m.get("id") == self.d.bot_id for m in msg.get("new_chat_members") or []):
            await self._notice(chat_id)
            return "joined"
        if user.get("is_bot"):
            return "bot"
        if loc := msg.get("location"):
            return await self.on_location(chat_id, user, loc, now)
        text = (msg.get("text") or "").strip()
        if not text:
            return "non_text"
        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            return await self.on_command(chat_id, user, cmd.split("@")[0].lower(), args.strip(), msg, now)
        reply = msg.get("reply_to_message") or {}
        if reply:
            r = self.d.db.open_round(chat_id)
            # eng E10: only the organizer's reply to that exact prompt counts as the correction
            if (r and r["state"] == "CONFIRMING" and reply.get("message_id") == r["badlo_prompt"]
                    and user.get("id") == r["organizer_id"]):
                return await self._badlo_reply(r, text, now)
        self.d.db.add_message(chat_id, msg["message_id"], user["id"], display_name(user), text, float(msg.get("date") or now))
        return "buffered"

    # ---------------------------------------------------------------- commands

    async def on_command(self, chat_id: int, user: dict[str, Any], cmd: str, args: str, msg: dict[str, Any],
                         now: float) -> str:
        uid, name = user["id"], display_name(user)
        if cmd == "/plan":
            await self._notice(chat_id)
            return await self.start_plan(chat_id, uid, name, args, msg["message_id"], now)
        if cmd == "/status":
            await self.d.tg.send(chat_id, copy.STATUS.format(n=self.d.db.message_count(chat_id, now)))
            return "status"
        if cmd == "/forget":
            n = self.d.db.forget_user(chat_id, uid)
            await self.d.tg.send(chat_id, copy.FORGOT.format(n=n))
            return "forgot"
        if cmd == "/area":
            open_r = self.d.db.open_round(chat_id)
            last = self.d.db.last_round(chat_id)
            if open_r is not None:
                await self.d.tg.send(chat_id, "📍 Plan chal raha hai. Area plan khatam hone ke baad badlo.")
                return "area_blocked"
            if last is not None and last["organizer_id"] != uid:
                await self.d.tg.send(chat_id, f"📍 Area sirf {copy.esc(last['organizer_name'])} (pichla organizer) badal sakta hai.")
                return "area_denied"
            self.d.store.set_setting(AREA_PENDING.format(chat=chat_id), str(uid))
            await self.d.tg.send(chat_id, copy.pin_prompt(uid, name))
            return "area_pending"
        if cmd == "/debug":
            if not self.d.owner_id or uid != self.d.owner_id:  # CEO D4: owner-only trail
                return "debug_denied"
            r = self.d.db.last_round(chat_id)
            if r is None:
                await self.d.tg.send(chat_id, "No rounds yet.")
                return "debug"
            lines = [f"round {r['id']} state={r['state']}"]
            for e in self.d.db.events(r["id"])[-25:]:
                t = dt.datetime.fromtimestamp(e["ts"], IST).strftime("%H:%M:%S")
                lines.append(f"{t} {e['kind']} {e['data'][:160]}")
            await self.d.tg.send(chat_id, copy.esc("\n".join(lines))[:4000])
            return "debug"
        return "unknown_command"

    async def start_plan(self, chat_id: int, uid: int, name: str, args: str, message_id: int, now: float,
                         force: bool = False) -> str:
        open_r = self.d.db.open_round(chat_id)
        if open_r is not None:
            idle = now - open_r["last_activity"]
            if idle > IDLE_EXPIRE_S and open_r["state"] != "BOOKING":  # lazy expiry, even without the sweeper
                await self.expire(open_r, now)
            elif not force:
                locked = open_r["state"] in LOCKED_STATES
                can = open_r["state"] != "BOOKING" and (
                    uid == open_r["organizer_id"]
                    or (not locked and idle > IDLE_REPLACE_OPEN_S)
                    or (locked and idle > IDLE_REPLACE_LOCKED_S))
                if not can:
                    await self.d.tg.send(chat_id, copy.plan_running(open_r["organizer_name"]))
                    return "plan_running"
                self.d.store.set_setting(f"replace:{open_r['id']}", json.dumps(
                    {"uid": uid, "name": name, "args": args, "message_id": message_id}))
                text, buttons = copy.replace_prompt(uid, name, open_r["id"])
                await self.d.tg.send(chat_id, text, buttons)
                return "replace_prompt"
        area = self.d.db.area(chat_id)
        state = "EXTRACTING" if area else "WAITING_PIN"
        rid = self.d.db.create_round(chat_id, uid, name, state, now)
        if args:
            self.d.db.add_message(chat_id, message_id, uid, name, args, now)
        if area is None:
            mid = await self.d.tg.send(chat_id, copy.pin_prompt(uid, name))
            self.d.db.update(rid, now, samjha_msg=mid)
            return "waiting_pin"
        mid = await self.d.tg.send(chat_id, copy.READING)
        self.d.db.update(rid, now, samjha_msg=mid)
        self.d.store.enqueue(family(chat_id), "extract", {"rid": rid}, run_at=now, now=now, draft_id=rid)
        return "extracting"

    async def on_location(self, chat_id: int, user: dict[str, Any], loc: dict[str, Any], now: float) -> str:
        uid = user.get("id")
        lat, lng = float(loc["latitude"]), float(loc["longitude"])
        label = f"{lat:.4f}, {lng:.4f}"
        pending_key = AREA_PENDING.format(chat=chat_id)
        if self.d.store.get_setting(pending_key) == str(uid):
            self.d.store.take_setting(pending_key)
            self.d.db.set_area(chat_id, lat, lng, label)
            await self.d.tg.send(chat_id, copy.PIN_SAVED.format(label=label))
            return "area_set"
        r = self.d.db.open_round(chat_id)
        if r is None or r["state"] != "WAITING_PIN" or r["organizer_id"] != uid:
            return "location_ignored"  # R2-21: stray location shares never move the group's area
        self.d.db.set_area(chat_id, lat, lng, label)
        if not self.d.db.cas_state(r["id"], "WAITING_PIN", "EXTRACTING", now):
            return "raced"
        await self.d.tg.edit(chat_id, r["samjha_msg"], copy.READING)
        self.d.store.enqueue(family(chat_id), "extract", {"rid": r["id"]}, run_at=now, now=now, draft_id=r["id"])
        return "pin_resumed"

    async def _badlo_reply(self, r: Any, text: str, now: float) -> str:
        c = constraints_of(r)
        c.extra = (c.extra + "\n" + text).strip()[:500]
        if not self.d.db.cas_state(r["id"], "CONFIRMING", "EXTRACTING", now, constraints=c.to_json(), gap=None):
            return "raced"
        await self.d.tg.edit(r["chat_id"], r["samjha_msg"], copy.READING)
        self.d.store.enqueue(family(r["chat_id"]), "extract", {"rid": r["id"]}, run_at=now, now=now, draft_id=r["id"])
        return "badlo_reextract"

    # ---------------------------------------------------------------- buttons

    async def on_callback(self, cb: dict[str, Any]) -> str:
        user, now = cb.get("from") or {}, self.d.clock()
        parsed = copy.decode(cb.get("data"))
        if parsed is None:
            await self.d.tg.answer_callback(cb["id"], copy.TOAST_OLD)
            return "bad_callback"
        r = self.d.db.round(parsed.rid)
        if r is None or r["state"] in TERMINAL:
            await self.d.tg.answer_callback(cb["id"], copy.TOAST_CLOSED)
            return "closed"
        action = copy.ACTIONS[parsed.action]
        handler = getattr(self, f"_cb_{action}")
        organizer_only = action not in ("vote", "rsvp", "replace")
        if organizer_only and user.get("id") != r["organizer_id"]:
            await self.d.tg.answer_callback(cb["id"], copy.TOAST_ONLY_ORGANIZER.format(name=r["organizer_name"]))
            return "not_organizer"
        return await handler(r, parsed.arg, user, cb, now)

    async def _answer(self, cb: dict[str, Any], text: str = "") -> None:
        try:
            await self.d.tg.answer_callback(cb["id"], text)
        except Exception:
            log.warning("answerCallbackQuery failed", exc_info=True)

    async def _cb_samjha_ok(self, r, arg, user, cb, now) -> str:
        if r["state"] != "CONFIRMING" or r["gap"]:
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        if not self.d.db.cas_state(r["id"], "CONFIRMING", "PLANNING", now):
            await self._answer(cb, copy.TOAST_OLD)
            return "raced"
        await self._answer(cb, copy.TOAST_WORKING)
        c = constraints_of(r)
        text, _ = copy.samjha(c, r["organizer_id"], r["organizer_name"], r["id"])
        await self.d.tg.edit(r["chat_id"], r["samjha_msg"], text.rsplit("\n", 1)[0])  # drop the question + buttons
        mid = await self.d.tg.send(r["chat_id"], copy.PROGRESS["events" if c.genres else "tables"])
        self.d.db.update(r["id"], now, plans_msg=mid)
        self.d.store.enqueue(family(r["chat_id"]), "plan", {"rid": r["id"]}, run_at=now, now=now, draft_id=r["id"])
        return "planning"

    async def _cb_badlo(self, r, arg, user, cb, now) -> str:
        if r["state"] != "CONFIRMING":
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        await self._answer(cb)
        mid = await self.d.tg.send(r["chat_id"], copy.badlo_prompt(r["organizer_id"], r["organizer_name"]),
                                   force_reply=True)
        self.d.db.update(r["id"], now, badlo_prompt=mid)
        return "badlo_prompt"

    async def _cb_gap_answer(self, r, arg, user, cb, now) -> str:
        gap, _, value = arg.partition("=")
        if r["state"] != "CONFIRMING" or r["gap"] != gap:
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        c = constraints_of(r)
        try:
            apply_answer(c, gap, value, now_ist(now).date())
        except (ValueError, KeyError):
            await self._answer(cb, copy.TOAST_OLD)
            return "bad_answer"
        self.d.db.update(r["id"], now, constraints=c.to_json(), gap=None)
        await self._answer(cb)
        text, buttons = copy.samjha(c, r["organizer_id"], r["organizer_name"], r["id"])
        await self.d.tg.edit(r["chat_id"], r["samjha_msg"], text, buttons)
        return "gap_answered"

    async def _cb_vote(self, r, arg, user, cb, now) -> str:
        if r["state"] != "VOTING":
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        try:
            idx = int(arg)
        except ValueError:
            await self._answer(cb, copy.TOAST_OLD)
            return "bad_vote"
        if idx not in {o.idx for o in options_of(r)}:
            await self._answer(cb, copy.TOAST_OLD)
            return "bad_vote"
        self.d.db.add_person(r["id"], user["id"], display_name(user), "tap")  # design DR4: quiet friends vote
        self.d.db.set_vote(r["id"], user["id"], idx, now)
        self.d.db.update(r["id"], now)
        await self._answer(cb, copy.TOAST_VOTED)
        tally, known = self.d.db.tally(r["id"]), len(self.d.db.people(r["id"]))
        top = max(tally.values())
        leaders = [i for i, n in tally.items() if n == top]
        if len(leaders) == 1 and top * 2 > known:  # majority of known people locks it
            return await self.lock(r, leaders[0], now)
        self._refresh(r["id"], "plans_msg", "control_msg")
        return "voted"

    async def _cb_lock(self, r, arg, user, cb, now) -> str:
        if r["state"] != "VOTING":
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        tally = self.d.db.tally(r["id"])
        top = max(tally.values(), default=0)
        idx = int(arg) if arg.isdigit() else -1
        if top < 1 or tally.get(idx, 0) != top:  # only a current leader (or one of the tied leaders)
            await self._answer(cb, copy.TOAST_OLD)
            return "not_leader"
        await self._answer(cb)
        return await self.lock(r, idx, now)

    async def lock(self, r: Any, idx: int, now: float) -> str:
        if not self.d.db.cas_state(r["id"], "VOTING", "RSVP", now, locked_idx=idx):
            return "raced"
        self.d.db.set_rsvp(r["id"], r["organizer_id"], "c", now)  # organizer auto-included
        r = self.d.db.round(r["id"])
        opt = locked_option(r)
        await self.d.tg.edit(r["chat_id"], r["plans_msg"], copy.plans_locked(opt))
        text, buttons = self.render_rsvp_now(r)
        mid = await self.d.tg.send(r["chat_id"], text, buttons)
        self.d.db.update(r["id"], now, rsvp_msg=mid)
        self._refresh(r["id"], "control_msg")
        self.d.store.enqueue(family(r["chat_id"]), "rsvp_deadline", {"rid": r["id"]}, run_at=now + RSVP_DEADLINE_S,
                             now=now, draft_id=r["id"])
        return "locked"

    async def _cb_cancel(self, r, arg, user, cb, now) -> str:
        if r["state"] == "BOOKING" or not self.d.db.cas_state(r["id"], r["state"], "CANCELLED", now):
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        await self._answer(cb)
        await self._close_messages(self.d.db.round(r["id"]), "CANCELLED")
        return "cancelled"

    async def _cb_rsvp(self, r, arg, user, cb, now) -> str:
        if r["state"] not in ("RSVP", "BOOK_PENDING") or arg not in ("t", "c", "n"):
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        if r["book_requested"]:  # eng R3-8: party size is frozen once the organizer confirmed Book
            await self._answer(cb, copy.TOAST_BOOKING)
            return "frozen"
        self.d.db.add_person(r["id"], user["id"], display_name(user), "tap")
        self.d.db.set_rsvp(r["id"], user["id"], arg, now)
        self.d.db.update(r["id"], now)
        await self._answer(cb, copy.TOAST_RSVP)
        self._refresh(r["id"], "rsvp_msg", "control_msg")
        return "rsvp"

    async def _cb_guest(self, r, arg, user, cb, now) -> str:
        if r["state"] != "RSVP":
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        guests = max(0, min(MAX_GUESTS, r["guests"] + (1 if arg == "+" else -1)))
        self.d.db.update(r["id"], now, guests=guests)
        await self._answer(cb)
        self._refresh(r["id"], "control_msg", "rsvp_msg")
        return "guests"

    async def _cb_book_now(self, r, arg, user, cb, now) -> str:
        if r["state"] != "RSVP" or self.party(r) < 2:
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        if not self.d.db.cas_state(r["id"], "RSVP", "BOOK_PENDING", now):
            await self._answer(cb, copy.TOAST_OLD)
            return "raced"
        await self._answer(cb)
        self._refresh(r["id"], "control_msg")
        return "book_pending"

    async def _cb_book_wait(self, r, arg, user, cb, now) -> str:
        if r["state"] != "BOOK_PENDING" or r["book_requested"]:
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        self.d.db.cas_state(r["id"], "BOOK_PENDING", "RSVP", now, result=None, alt_time=None)
        await self._answer(cb)
        self._refresh(r["id"], "control_msg")
        return "back_to_rsvp"

    async def _cb_book_confirm(self, r, arg, user, cb, now) -> str:
        party = self.party(r)
        if r["state"] != "BOOK_PENDING" or party < 2 or not self.d.db.claim_booking(r["id"], now):
            await self._answer(cb, copy.TOAST_BOOKING if r["book_requested"] else copy.TOAST_OLD)
            return "stale"
        self.d.db.update(r["id"], now, party=party)  # frozen here; the job books exactly this many (R3-8)
        await self._answer(cb, copy.TOAST_WORKING)
        await self.d.tg.edit(r["chat_id"], r["control_msg"], copy.control_booking(r["organizer_id"], r["organizer_name"]))
        self.d.store.enqueue(family(r["chat_id"]), "book", {"rid": r["id"], "party": party}, run_at=now, now=now,
                             draft_id=r["id"])
        return "booking_queued"

    async def _cb_table_only(self, r, arg, user, cb, now) -> str:
        if r["state"] != "BOOK_PENDING" or result_of(r).get("recheck") != "show_gone":
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        self.d.db.update(r["id"], now, table_only=1, result=None)
        await self._answer(cb)
        self._refresh(r["id"], "control_msg")
        return "table_only"

    async def _cb_alt_slot(self, r, arg, user, cb, now) -> str:
        if r["state"] != "BOOK_PENDING" or not arg.isdigit() or int(arg) != r["alt_time"]:
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        res = result_of(r)
        opts = options_of(r)
        for o in opts:
            if o.idx == r["locked_idx"]:
                o.reservation_time, o.slot_id, o.item_id = int(arg), res.get("slot_id", o.slot_id), res.get(
                    "item_id", o.item_id)
        self.d.db.update(r["id"], now, plans=[o.to_json() for o in opts], alt_time=None, result=None)
        await self._answer(cb)
        self._refresh(r["id"], "control_msg")
        return "alt_accepted"

    async def _cb_replace(self, r, arg, user, cb, now) -> str:
        raw = self.d.store.get_setting(f"replace:{r['id']}")
        req = json.loads(raw) if raw else None
        if req is None or req["uid"] != user.get("id"):
            await self._answer(cb, copy.TOAST_OLD)
            return "stale"
        self.d.store.take_setting(f"replace:{r['id']}")
        await self._answer(cb)
        if arg != "y":
            return "replace_declined"
        if r["state"] == "BOOKING" or not self.d.db.cas_state(r["id"], r["state"], "CANCELLED", now):
            return "raced"
        await self._close_messages(self.d.db.round(r["id"]), "CANCELLED")
        return await self.start_plan(r["chat_id"], req["uid"], req["name"], req["args"], req["message_id"], now,
                                     force=True)

    # ================================================================ rendering (always from the DB)

    def party(self, r: Any) -> int:
        rs = self.d.db.rsvps(r["id"])
        return sum(1 for s in rs.values() if s in ("t", "c")) + (r["guests"] or 0)

    def _names(self, rid: str) -> dict[int, str]:
        return {p["user_id"]: p["name"] for p in self.d.db.people(rid)}

    def render_plans_now(self, r: Any) -> tuple[str, list | None]:
        opts = options_of(r)
        if r["state"] == "VOTING":
            tally = self.d.db.tally(r["id"])
            header = copy.partial_header(len(opts)) if result_of(r).get("partial") else None
            return copy.plans_message(opts, r["id"], tally, sum(tally.values()), len(self.d.db.people(r["id"])), header)
        opt = locked_option(r)
        if r["state"] in ("CANCELLED", "EXPIRED"):
            return copy.round_closed(r["state"]), None
        return (copy.plans_locked(opt) if opt else copy.round_closed(r["state"])), None

    def render_control_now(self, r: Any) -> tuple[str, list | None]:
        oid, oname, rid = r["organizer_id"], r["organizer_name"], r["id"]
        state = r["state"]
        if state == "VOTING":
            tally = self.d.db.tally(rid)
            if not tally:
                return copy.control_voting(oid, oname, rid, None, 0, [])
            top = max(tally.values())
            leaders = sorted(i for i, n in tally.items() if n == top)
            return copy.control_voting(oid, oname, rid, leaders[0], top, leaders if len(leaders) > 1 else [])
        if state == "RSVP":
            party = self.party(r)
            return copy.control_rsvp(oid, oname, rid, party - (r["guests"] or 0), r["guests"] or 0)
        if state == "BOOK_PENDING":
            res = result_of(r)
            if r["book_requested"]:
                return copy.control_booking(oid, oname), None
            if res.get("recheck") == "show_gone" and not r["table_only"]:
                return copy.control_show_gone(oid, oname, rid)
            if res.get("recheck") == "slot_gone":
                return copy.control_slot_gone(oid, oname, rid, r["alt_time"])
            return copy.control_confirm(oid, oname, rid, locked_option(r), self.party(r))
        if state == "BOOKING":
            return copy.control_booking(oid, oname), None
        return copy.control_result(oid, oname, state, result_of(r).get("detail", "")), None

    def render_rsvp_now(self, r: Any) -> tuple[str, list | None]:
        opt = locked_option(r)
        names = self._names(r["id"])
        rs = self.d.db.rsvps(r["id"])
        by = {s: [names.get(u, "?") for u, st in rs.items() if st == s] for s in ("t", "c", "n")}
        if r["state"] in ("BOOKED", "TEST_ONLY", "FAILED", "NEEDS_REVIEW"):
            coming = by["t"] + by["c"]
            return copy.final_card(opt, coming, r["guests"] or 0, by["c"], r["state"]), None
        if r["state"] in ("CANCELLED", "EXPIRED"):
            return copy.round_closed(r["state"]), None
        return copy.rsvp_message(opt, r["id"], by["t"], by["c"], by["n"])

    def _refresh(self, rid: str, *fields: str) -> None:
        renders = {"plans_msg": self.render_plans_now, "control_msg": self.render_control_now,
                   "rsvp_msg": self.render_rsvp_now}
        r = self.d.db.round(rid)
        for f in fields:
            fn = renders[f]

            async def render(fn=fn) -> tuple[str, list | None]:
                return fn(self.d.db.round(rid))  # re-read at send time (eng E7)

            self.d.refresher.mark(r["chat_id"], r[f], render)

    async def _close_messages(self, r: Any, state: str) -> None:
        """Round ended without booking: every live message loses its buttons (design DR2)."""
        chat = r["chat_id"]
        text = copy.round_closed(state)
        for f in ("plans_msg", "rsvp_msg"):
            if r[f]:
                await self._safe_edit(chat, r[f], text)
        if r["control_msg"]:
            await self._safe_edit(chat, r["control_msg"], copy.control_result(r["organizer_id"], r["organizer_name"],
                                                                              "CANCELLED"))
        if r["samjha_msg"] and r["state"] in ("CANCELLED", "EXPIRED") and not r["plans_msg"]:
            await self._safe_edit(chat, r["samjha_msg"], text)

    async def _safe_edit(self, chat: int, mid: int, text: str, buttons: list | None = None) -> None:
        try:
            await self.d.tg.edit(chat, mid, text, buttons)
        except Exception:
            log.warning("edit %s/%s failed", chat, mid, exc_info=True)

    async def expire(self, r: Any, now: float) -> bool:
        if not self.d.db.cas_state(r["id"], r["state"], "EXPIRED", now):
            return False
        await self._close_messages(self.d.db.round(r["id"]), "EXPIRED")
        return True

    async def sweep(self, now: float) -> int:
        """Worker tick: retention purge (R3-10) + EXPIRED for rounds idle > 6 h."""
        self.d.db.purge(now)
        marks = ",".join("?" * len(TERMINAL))
        rows = self.d.db.conn.execute(
            f"SELECT * FROM plan_rounds WHERE state NOT IN ({marks}) AND state != 'BOOKING' AND last_activity < ?",
            (*sorted(TERMINAL), now - IDLE_EXPIRE_S)).fetchall()
        n = 0
        for r in rows:
            n += await self.expire(r, now)
        return n

    # ================================================================ jobs (worker)

    async def on_job(self, job: Job) -> str:
        r = self.d.db.round(job.payload["rid"])
        if r is None:
            return "gone"
        handler = {"extract": self.job_extract, "plan": self.job_plan, "book": self.job_book,
                   "rsvp_deadline": self.job_rsvp_deadline}[job.kind]
        return await handler(r, job)

    async def job_extract(self, r: Any, job: Job) -> str:
        if r["state"] != "EXTRACTING":
            return "stale"
        now, chat = self.d.clock(), r["chat_id"]
        rows = self.d.db.recent_messages(chat, now)
        lines = [Line(x["message_id"], x["user_id"], x["name"], x["text"], x["ts"]) for x in rows]
        prev = constraints_of(r)
        if prev.extra:
            lines.append(Line(10**12, r["organizer_id"], r["organizer_name"], f"(correction) {prev.extra}", now))
        if not lines:
            self.d.db.cas_state(r["id"], "EXTRACTING", "CANCELLED", now)
            await self._safe_edit(chat, r["samjha_msg"], copy.EMPTY_CHAT)
            return "empty"
        try:
            statements = await extract_statements(self.d.make_model, lines, now)
        except ExtractError as e:
            self.d.db.log(r["id"], "extract_failed", {"error": str(e)[:300]}, now)
            self.d.db.cas_state(r["id"], "EXTRACTING", "CANCELLED", now)
            await self._safe_edit(chat, r["samjha_msg"], copy.EXTRACT_FAILED)
            return "extract_failed"
        c = reduce(statements, lines, (r["organizer_id"], r["organizer_name"]), now)
        c.extra = prev.extra
        self.d.db.save_statements(r["id"], [(s.message_id, s.user_id, s.field, json.dumps(s.value))
                                            for s in statements])
        self.d.db.log(r["id"], "extract", {"statements": len(statements), "gaps": c.gaps}, now)
        for p in c.people.values():
            self.d.db.add_person(r["id"], p.user_id, p.name, "chat")
        gap = c.gaps[0] if c.gaps else None
        if not self.d.db.cas_state(r["id"], "EXTRACTING", "CONFIRMING", now, constraints=c.to_json(), gap=gap):
            return "raced"
        if gap:
            text, buttons = copy.gap_question(gap, r["organizer_id"], r["organizer_name"], r["id"],
                                              now_ist(now).date(), c.tie_dates)
        else:
            text, buttons = copy.samjha(c, r["organizer_id"], r["organizer_name"], r["id"])
        await self.d.tg.edit(chat, r["samjha_msg"], text, buttons)
        return "confirming"

    async def job_plan(self, r: Any, job: Job) -> str:
        if r["state"] != "PLANNING":
            return "stale"
        now, chat, rid = self.d.clock(), r["chat_id"], r["id"]
        c = constraints_of(r)
        area = self.d.db.area(chat)

        async def progress(stage: str) -> None:
            await self._safe_edit(chat, r["plans_msg"], copy.PROGRESS[stage])

        try:
            async with self.d.sessions() as (scenes, dineout):
                run = await Planner(self.d.make_model, scenes, dineout, c, area["lat"], area["lng"],
                                    area["area"] or "the group's pin",
                                    log=lambda k, data: self.d.db.log(rid, k, data, self.d.clock()),
                                    progress=progress).run()
        except SwiggyAuthError as e:
            self.d.db.log(rid, "swiggy_auth", {"error": str(e)[:300]}, self.d.clock())
            self.d.db.cas_state(rid, "PLANNING", "CANCELLED", self.d.clock())
            await self._safe_edit(chat, r["plans_msg"], copy.swiggy_login_expired(self.d.owner_name))
            return "auth_failed"
        except Exception as e:
            log.exception("planning %s failed", rid)
            self.d.db.log(rid, "plan_error", {"error": f"{type(e).__name__}: {e}"[:300]}, self.d.clock())
            self.d.db.cas_state(rid, "PLANNING", "CANCELLED", self.d.clock())
            await self._safe_edit(chat, r["plans_msg"], copy.SWIGGY_DOWN)
            return "plan_error"
        now = self.d.clock()
        if not run.options:
            self.d.db.cas_state(rid, "PLANNING", "CANCELLED", now)
            await self._safe_edit(chat, r["plans_msg"], copy.zero_plans(list(dict.fromkeys(run.reasons))))
            return "zero_plans"
        if not self.d.db.cas_state(rid, "PLANNING", "VOTING", now, plans=[o.to_json() for o in run.options],
                                   result={"partial": run.partial}):
            return "raced"
        r = self.d.db.round(rid)
        text, buttons = self.render_plans_now(r)
        await self.d.tg.edit(chat, r["plans_msg"], text, buttons)
        text, buttons = self.render_control_now(r)
        mid = await self.d.tg.send(chat, text, buttons)
        self.d.db.update(rid, now, control_msg=mid)
        return "voting"

    async def job_rsvp_deadline(self, r: Any, job: Job) -> str:
        """Moves to the Book prompt and pings the organizer; never books by itself."""
        now = self.d.clock()
        if r["state"] != "RSVP" or self.party(r) < 2:
            return "noop"
        if self.d.db.cas_state(r["id"], "RSVP", "BOOK_PENDING", now):
            self._refresh(r["id"], "control_msg")
            return "book_prompt"
        return "raced"

    async def job_book(self, r: Any, job: Job) -> str:
        now, rid, chat = self.d.clock(), r["id"], r["chat_id"]
        if r["state"] == "BOOKING":  # reclaimed after a crash mid-booking: never call book_table twice
            self.d.db.cas_state(rid, "BOOKING", "NEEDS_REVIEW", now, result={"detail": "worker restarted mid-booking"})
            await self._finish(rid)
            return "needs_review_reclaimed"
        if r["state"] != "BOOK_PENDING" or not r["book_requested"]:
            return "stale"
        opt, c, area = locked_option(r), constraints_of(r), self.d.db.area(chat)
        party = int(job.payload.get("party") or r["party"])
        try:
            async with self.d.sessions() as (scenes, dineout):
                chk = await recheck(opt, scenes, dineout, c, area["lat"], area["lng"], bool(r["table_only"]))
                self.d.db.log(rid, "recheck", {"status": chk.status, "alt": chk.reservation_time}, self.d.clock())
                if chk.status == "show_gone":
                    self.d.db.update(rid, self.d.clock(), book_requested=0, result={"recheck": "show_gone"})
                    self._refresh(rid, "control_msg")
                    return "show_gone"
                if chk.status == "slot_gone":
                    self.d.db.update(rid, self.d.clock(), book_requested=0, alt_time=chk.reservation_time, result={
                        "recheck": "slot_gone", "slot_id": chk.slot_id, "item_id": chk.item_id})
                    self._refresh(rid, "control_msg")
                    return "slot_gone"
                if not self.d.db.cas_state(rid, "BOOK_PENDING", "BOOKING", self.d.clock()):
                    return "raced"
                lat, lng = table_coords(opt, area["lat"], area["lng"])
                args = {"restaurantId": opt.restaurant.restaurant_id, "slotId": chk.slot_id, "itemId": chk.item_id,
                        "reservationTime": chk.reservation_time, "guestCount": party, "latitude": lat, "longitude": lng}
                self.d.db.log(rid, "book_attempt", args, self.d.clock())
                try:
                    res = await dineout.place_order("book_table", args)
                except OrderBlocked:
                    self.d.db.cas_state(rid, "BOOKING", "TEST_ONLY", self.d.clock())
                    await self._finish(rid)
                    return "test_only"
        except SwiggyAuthError as e:
            self.d.db.log(rid, "swiggy_auth", {"error": str(e)[:300]}, self.d.clock())
            state = self.d.db.round(rid)["state"]
            if state == "BOOKING":
                self.d.db.cas_state(rid, "BOOKING", "NEEDS_REVIEW", self.d.clock(), result={"detail": "login expired"})
            else:
                self.d.db.update(rid, self.d.clock(), book_requested=0)
                await self._safe_edit(chat, r["control_msg"], copy.swiggy_login_expired(self.d.owner_name))
                return "auth_failed"
            await self._finish(rid)
            return "needs_review"
        except Exception as e:
            log.exception("booking %s failed", rid)
            self.d.db.log(rid, "book_error", {"error": f"{type(e).__name__}: {e}"[:300]}, self.d.clock())
            state = self.d.db.round(rid)["state"]
            if state == "BOOKING":
                self.d.db.cas_state(rid, "BOOKING", "NEEDS_REVIEW", self.d.clock(), result={"detail": str(e)[:200]})
                await self._finish(rid)
                return "needs_review"
            self.d.db.update(rid, self.d.clock(), book_requested=0)
            self._refresh(rid, "control_msg")
            await self.d.tg.send(chat, copy.SWIGGY_DOWN)
            return "recheck_error"
        self.d.db.log(rid, "book_result", {"is_error": res.get("is_error"), "text": (res.get("text") or "")[:500]},
                      self.d.clock())
        if res.get("is_error"):
            self.d.db.cas_state(rid, "BOOKING", "FAILED", self.d.clock(), result={"detail": (res.get("text") or "")[:200]})
        else:
            self.d.db.cas_state(rid, "BOOKING", "BOOKED", self.d.clock())
        await self._finish(rid)
        return self.d.db.round(rid)["state"].lower()

    async def _finish(self, rid: str) -> None:
        r = self.d.db.round(rid)
        text, _ = self.render_control_now(r)
        await self._safe_edit(r["chat_id"], r["control_msg"], text)
        text, _ = self.render_rsvp_now(r)
        await self._safe_edit(r["chat_id"], r["rsvp_msg"], text)
        if r["state"] in ("BOOKED", "TEST_ONLY") and r["rsvp_msg"]:
            try:
                await self.d.tg.pin(r["chat_id"], r["rsvp_msg"])
            except Exception as e:  # noqa: BLE001 (no pin rights: log only, design DR14)
                self.d.db.log(rid, "pin_failed", {"error": str(e)[:200]}, self.d.clock())
