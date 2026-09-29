"""Child approval on Telegram (eng D9) and the code-only order step.

    Mom "haan" ─► draft CHILD_APPROVAL_PENDING ─► Telegram: cart + [✅ Approve COD] [❌ Cancel]
    button press (Telegram poller) ─► enqueue job "child_action" (so it's serial with Mom's jobs, CEO D6)
    child_action job:
        wrong user / stale draft / hash mismatch ─► rejected, nothing happens
        cancel  ─► CANCELLED + clear_cart, Mom told
        approve ─► live cart re-check (items + total + Mom's address must match the approved snapshot)
                    mismatch ─► back to AWAITING_PARENT, Mom re-confirms
                    match ─► PLACING ─► place_order("checkout", COD)
                               DRY_RUN on (OrderBlocked) ─► TEST_ONLY, nothing ordered, both told
                               success ─► PLACED, Mom told the exact cash amount (eng D13)
                               Swiggy says no (is_error) ─► FAILED, both told (eng D7)
                               network/timeout ─► NEEDS_REVIEW, never retried blindly (eng D3)
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from maa.cart import CartWriteError, live_snapshot
from maa.store import Job
from maa.swiggy import OrderBlocked

log = logging.getLogger("maa.approval")

BIND_CODE_KEY = "telegram_bind_code"
CHILD_ID_KEY = "telegram_child_user_id"
CHILD_CHAT_KEY = "telegram_child_chat_id"
OFFSET_KEY = "telegram_offset"


def new_bind_code(store) -> str:
    code = secrets.token_urlsafe(9)
    store.set_setting(BIND_CODE_KEY, code)
    return code


def child_summary(draft_row) -> str:
    snap = json.loads(draft_row["snapshot"])
    lines = [f"🛒 Maa ka order: “{draft_row['transcript']}”", ""]
    for ln in snap["lines"]:
        price = f" – ₹{ln['unit_price'] * ln['quantity']:g}" if ln.get("unit_price") is not None else ""
        lines.append(f"• {ln['quantity']} x {ln['name']}{price}")
    if snap.get("item_total") is not None and snap.get("total") is not None:
        lines.append(f"\nItems ₹{snap['item_total']:g} + fees ₹{snap['total'] - snap['item_total']:g}")
    if snap.get("total") is not None:
        lines.append(f"To pay (cash on delivery): ₹{snap['total']:g}")
    lines.append("\nMaa said haan. Approve?")
    return "\n".join(lines)


def approval_buttons(draft_id: str, snap_hash: str) -> list[list[tuple[str, str]]]:
    return [[("✅ Approve (COD)", f"approve:{draft_id}:{snap_hash[:16]}"), ("❌ Cancel", f"cancel:{draft_id}")]]


async def handle_telegram_update(deps, update: dict[str, Any], now: float) -> str:
    """Runs in the Telegram poller. Binding happens here; button presses become serial jobs."""
    store = deps.store
    if msg := update.get("message"):
        text = (msg.get("text") or "").strip()
        user_id, chat_id = msg["from"]["id"], msg["chat"]["id"]
        if text.startswith("/start"):
            code = text.removeprefix("/start").strip()
            bound = store.get_setting(CHILD_ID_KEY)
            if bound and int(bound) == user_id:
                await deps.telegram.send(chat_id, "✅ Already linked. You'll get Maa's orders here.")
                return "already_bound"
            expected = store.get_setting(BIND_CODE_KEY)
            if code and expected and secrets.compare_digest(code, expected):
                store.take_setting(BIND_CODE_KEY)  # single use
                store.set_setting(CHILD_ID_KEY, str(user_id))
                store.set_setting(CHILD_CHAT_KEY, str(chat_id))
                await deps.telegram.send(chat_id, "✅ Linked! Maa's orders will come here for your approval.")
                return "bound"
            await deps.telegram.send(chat_id, "This bot is private.")
            return "bind_rejected"
        return "ignored_message"

    if cb := update.get("callback_query"):
        user_id = cb["from"]["id"]
        bound = store.get_setting(CHILD_ID_KEY)
        if not bound or int(bound) != user_id:
            await deps.telegram.answer_callback(cb["id"], "Not allowed")
            return "wrong_user"
        parts = (cb.get("data") or "").split(":")
        action, draft_id = parts[0], parts[1] if len(parts) > 1 else ""
        store.enqueue(
            "mom",
            "child_action",
            {
                "action": action,
                "draft_id": draft_id,
                "hash": parts[2] if len(parts) > 2 else None,
                "callback_id": cb["id"],
                "chat_id": cb["message"]["chat"]["id"],
                "message_id": cb["message"]["message_id"],
            },
            run_at=now,
            now=now,
            draft_id=draft_id,
        )
        await deps.telegram.answer_callback(cb["id"], "Working on it…")
        return "queued"
    return "ignored"


async def request_child_approval(deps, draft_id: str) -> bool:
    chat = deps.store.get_setting(CHILD_CHAT_KEY)
    draft = deps.store.get_draft(draft_id)
    if not chat or draft is None or deps.telegram is None:
        log.warning("child not linked on Telegram; draft %s waits", draft_id)
        return False
    snap_hash = json.loads(draft["snapshot"])["hash"]
    await deps.telegram.send(int(chat), child_summary(draft), approval_buttons(draft_id, snap_hash))
    return True


async def handle_child_action(deps, job: Job) -> str:
    p, now, store = job.payload, deps.clock(), deps.store
    chat_id, message_id = p["chat_id"], p["message_id"]

    async def tell_child(text: str) -> None:
        await deps.telegram.edit(chat_id, message_id, text)

    draft = store.get_draft(p["draft_id"])
    if draft is None or draft["state"] != "CHILD_APPROVAL_PENDING":
        await tell_child(f"This order is no longer waiting ({draft['state'] if draft else 'unknown'}).")
        return "stale_button"
    snapshot = json.loads(draft["snapshot"])

    if p["action"] == "cancel":
        if store.set_draft_state(draft["id"], "CHILD_APPROVAL_PENDING", "CANCELLED", now):
            async with deps.swiggy() as sw:
                await sw.call("clear_cart", {})
            await deps.send_to_mom(f"{deps.child_name} ne abhi yeh order cancel kiya 🙏")
            await tell_child("❌ Cancelled. Cart cleared, Maa informed.")
        return "cancelled"

    if p["action"] != "approve" or p.get("hash") != snapshot["hash"][:16]:
        await tell_child("⚠️ Button doesn't match the current cart. Nothing ordered.")
        return "hash_mismatch"

    async with deps.swiggy() as sw:
        try:
            live, address = await live_snapshot(sw)
        except CartWriteError:
            await tell_child("⚠️ Couldn't read the Swiggy cart. Nothing ordered; try again in a minute.")
            return "cart_read_failed"
        if address != deps.address_id:
            store.set_draft_state(draft["id"], "CHILD_APPROVAL_PENDING", "CANCELLED", now)
            await tell_child("⚠️ The Swiggy cart is set to a different address. Nothing ordered; cancelled.")
            return "wrong_address"
        if live.hash != snapshot["hash"]:
            store.set_draft_state(draft["id"], "CHILD_APPROVAL_PENDING", "CANCELLED", now)
            await tell_child("⚠️ The cart changed since Maa confirmed (price/stock). Nothing ordered; asking Maa again.")
            await deps.send_to_mom("Cart mein kuch badal gaya hai (stock ya daam). Ek baar phir bata dijiye kya chahiye 🙏")
            return "cart_changed"

        # COD only. Re-check right before ordering that Swiggy offers cash for THIS cart;
        # otherwise never order (checkout would reject or fall back to another method).
        opts = await sw.call("get_payment_options", {})
        cod = ((opts.get("parsed") or {}).get("cod") or {}) if not opts.get("is_error") else {}
        if not cod.get("available"):
            store.set_draft_state(draft["id"], "CHILD_APPROVAL_PENDING", "CANCELLED", now)
            await tell_child("⚠️ Swiggy is not offering Cash on Delivery for this cart right now. Nothing ordered.")
            await deps.send_to_mom("Abhi cash on delivery nahi mil raha, order nahi gaya. Harsh dekh rahe hain 🙏")
            return "cod_unavailable"

        if not store.set_draft_state(draft["id"], "CHILD_APPROVAL_PENDING", "PLACING", now):
            return "raced"
        try:
            res = await sw.place_order("checkout", {"addressId": deps.address_id, "paymentMethod": "Cash"})
        except OrderBlocked:
            store.set_draft_state(draft["id"], "PLACING", "TEST_ONLY", now)
            await tell_child(
                f"🧪 TEST MODE (DRY_RUN=1): order NOT placed.\n"
                f"Everything else worked: cart ₹{live.total:g} is sitting in Swiggy for Maa's address."
            )
            await deps.send_to_mom("Abhi test chal raha hai, order nahi gaya 🙏")
            return "dry_run"
        except Exception as e:  # noqa: BLE001 (outcome unknown: never retry, ask a human; eng D3/D5)
            store.set_draft_state(draft["id"], "PLACING", "NEEDS_REVIEW", now)
            await tell_child(f"⚠️ Swiggy didn't answer clearly ({type(e).__name__}). Check the Swiggy app before retrying.")
            await deps.send_to_mom("Thoda ruko, confirm kar rahe hain 🙏")
            return "needs_review"

    store.set_setting(f"checkout_response:{draft['id']}", (res.get("text") or "")[:20000])  # for review
    if res.get("is_error"):
        store.set_draft_state(draft["id"], "PLACING", "FAILED", now)
        await tell_child(f"❌ Swiggy refused the order: {res.get('text', '')[:300]}")
        await deps.send_to_mom("Order nahi ja paya, Harsh dekh rahe hain 🙏")
        return "failed"

    text = (res.get("text") or "").lower()
    if "pending_payment" in text or "partial" in text:
        # Not a clean COD placement (multi-store partial success, or a payment step): a human checks.
        store.set_draft_state(draft["id"], "PLACING", "NEEDS_REVIEW", now)
        await tell_child(f"⚠️ Swiggy's reply needs a look before we tell Maa:\n{res.get('text', '')[:600]}")
        await deps.send_to_mom("Thoda ruko, confirm kar rahe hain 🙏")
        return "needs_review"

    store.set_draft_state(draft["id"], "PLACING", "PLACED", now)
    total = live.total
    await tell_child(f"✅ Order placed (COD ₹{total:g}).\nSwiggy: {res.get('text', '')[:300]}")
    await deps.send_to_mom(
        f"{deps.child_name} ne bhej diya! Delivery wale ko ₹{total:g} dena hai, isse zyada mat dena 🙏"
    )
    return "placed"
