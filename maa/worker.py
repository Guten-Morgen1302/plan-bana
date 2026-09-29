"""Processes queued inbound WhatsApp messages from Mom.

    inbound job
      ├─ not audio/text ............................ "bolke bata do"
      ├─ audio ─► download ─► Sarvam STT (retry 2x) ─► transcript
      ├─ older than 30 min (laptop was asleep) ....... "purana message mila, phir se bolo"   (CEO D7)
      ├─ open draft AWAITING_PARENT + reply is:
      │     yes ─► CHILD_APPROVAL_PENDING, "Harsh se confirm kar rahe hain"
      │     no  ─► CANCELLED + clear_cart, "theek hai, nahi bheja"
      │     other ─► cancel old draft, treat as a new request
      ├─ open draft past AWAITING_PARENT ............. "pichla order abhi confirm ho raha hai"
      └─ new request ─► agent ─► question? ask Mom
                               cart? ─► write_cart (Swiggy cart) ─► draft AWAITING_PARENT ─► readback to Mom

Orders are never placed here. Checkout lives behind SwiggySession.place_order + DRY_RUN=0 and the
child's approval (next step). Provider failures always end in a visible message to Mom (CEO D4).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import httpx

from maa.agent import AgentOutputError, Model, run_turn
from maa.cart import CartSnapshot, CartWriteError, write_cart
from maa.classify import Reply, classify
from maa.store import Job, Store
from maa.voice import EmptyTranscript, MediaFetchError, ProviderError, download_media, transcribe

log = logging.getLogger("maa.worker")

STALE_AFTER_S = 30 * 60
STT_RETRIES = 2

MSG_SAY_IT = "Awaaz mein bolke bata dijiye kya chahiye 🙏"
MSG_REPEAT = "Samajh nahi aaya, ek baar phir bolna 🙏"
MSG_TRY_LATER = "Abhi thodi dikkat hai, 5 minute baad phir se bolna 🙏"
MSG_CANCELLED = "Theek hai, nahi bheja 👍"
MSG_WAIT_CHILD = "Theek hai! {child} se confirm karke bhejte hain 🙏"
MSG_BUSY = "Pichla order abhi confirm ho raha hai, uske baad yeh bhi mangwa denge 🙏"


def readback_text(snapshot: CartSnapshot, note: str) -> str:
    lines = ["Maine yeh cart mein daala hai:"]
    for n, ln in enumerate(snapshot.lines, 1):
        price = f" – ₹{ln.unit_price * ln.quantity:g}" if ln.unit_price is not None else ""
        lines.append(f"{n}. {ln.quantity} x {ln.name}{price}")
    extra = snapshot.extra_charges
    if snapshot.total is not None and extra:
        lines.append(f"\nSaaman: ₹{snapshot.item_total:g}")
        lines.append(f"Delivery, handling aur GST: ₹{extra:g}")
        lines.append(f"*Kul: ₹{snapshot.total:g}*")
    elif snapshot.total is not None:
        lines.append(f"\n*Kul: ₹{snapshot.total:g}*")
    if note:
        lines.append(f"\n{note}")
    lines.append("\nBhej doon? *haan* ya *nahi* bolo")
    return "\n".join(lines)


@dataclass
class Deps:
    store: Store
    http: httpx.AsyncClient
    send_to_mom: Callable[[str], Awaitable[Any]]
    swiggy: Callable[[], AbstractAsyncContextManager[Any]]  # opens a SwiggySession
    make_model: Callable[[], Model]
    whatsapp_token: str
    sarvam_key: str
    address_id: str
    child_name: str = "Harsh"
    clock: Callable[[], float] = time.time


async def _transcript(deps: Deps, payload: dict[str, Any]) -> str | None:
    if payload.get("kind") == "text":
        return (payload.get("text") or "").strip() or None
    if payload.get("kind") != "audio" or not payload.get("media_id"):
        return None
    audio, mime = await download_media(deps.http, payload["media_id"], deps.whatsapp_token)
    last: Exception | None = None
    for attempt in range(STT_RETRIES + 1):
        try:
            return (await transcribe(deps.http, audio, mime, deps.sarvam_key)).text
        except ProviderError as e:
            last = e
            await asyncio.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


async def handle_inbound(deps: Deps, job: Job) -> str:
    """Returns a short outcome label (for logs/tests)."""
    payload, now = job.payload, deps.clock()
    try:
        text = await _transcript(deps, payload)
    except MediaFetchError:
        await deps.send_to_mom("Awaaz nahi aayi, ek baar phir bhejna 🙏")
        return "media_failed"
    except EmptyTranscript:
        await deps.send_to_mom(MSG_REPEAT)
        return "empty_transcript"
    except ProviderError:
        await deps.send_to_mom(MSG_TRY_LATER)
        return "stt_failed"
    if text is None:
        await deps.send_to_mom(MSG_SAY_IT)
        return "not_voice"

    sent_at = float(payload.get("sent_at") or now)
    if now - sent_at > STALE_AFTER_S:
        await deps.send_to_mom(f"Aapka purana message abhi mila: “{text}”. Ab bhi chahiye toh ek baar phir bol dijiye 🙏")
        return "stale"

    draft = deps.store.open_draft(job.family_id)
    if draft is not None and draft["state"] == "AWAITING_PARENT":
        reply = classify(text)
        if reply is Reply.YES:
            if deps.store.set_draft_state(draft["id"], "AWAITING_PARENT", "CHILD_APPROVAL_PENDING", now):
                await deps.send_to_mom(MSG_WAIT_CHILD.format(child=deps.child_name))
            return "parent_yes"
        if reply is Reply.NO:
            deps.store.set_draft_state(draft["id"], "AWAITING_PARENT", "CANCELLED", now)
            async with deps.swiggy() as sw:
                await sw.call("clear_cart", {})
            await deps.send_to_mom(MSG_CANCELLED)
            return "parent_no"
        deps.store.set_draft_state(draft["id"], "AWAITING_PARENT", "CANCELLED", now)  # changed request: start over
    elif draft is not None:
        await deps.send_to_mom(MSG_BUSY)
        return "busy"

    try:
        async with deps.swiggy() as sw:
            result = await run_turn(deps.make_model(), sw, deps.address_id, text)
            if result.kind == "question":
                await deps.send_to_mom(result.question)
                return "asked"
            snapshot = await write_cart(sw, deps.address_id, result.items)
    except (AgentOutputError, CartWriteError) as e:
        log.warning("agent/cart failed: %s", e)
        await deps.send_to_mom(MSG_TRY_LATER)
        return "agent_failed"

    # Send first: a draft only waits for "haan" if Mom actually received the readback.
    await deps.send_to_mom(readback_text(snapshot, result.note))
    deps.store.create_draft(uuid.uuid4().hex[:12], job.family_id, "AWAITING_PARENT", text, snapshot.to_json(), now)
    return "readback"


async def run_worker(deps: Deps, stop: asyncio.Event, owner: str = "worker-1", poll_s: float = 1.0) -> None:
    while not stop.is_set():
        job = deps.store.claim(owner, deps.clock(), lease_s=180)
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except TimeoutError:
                pass
            continue
        try:
            if job.kind == "inbound":
                outcome = await handle_inbound(deps, job)
                log.info("job %s inbound -> %s", job.id, outcome)
                if payload_wamid := job.payload.get("wamid"):
                    deps.store.complete_inbound(payload_wamid, deps.clock())
            deps.store.complete(job.id, owner)
        except Exception as e:
            log.exception("job %s failed", job.id)
            deps.store.fail(job.id, owner, f"{type(e).__name__}: {e}"[:500])
            try:
                await deps.send_to_mom(MSG_TRY_LATER)
            except Exception:
                log.exception("could not notify Mom about job %s", job.id)
