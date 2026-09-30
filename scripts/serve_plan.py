"""Run the Plan Bana bot: Telegram long polling + 3 worker loops, one process, no public URL.

  python scripts/serve_plan.py

Needs .env: PLAN_TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, PLAN_DB_PATH (default plan_bana.db), a valid Swiggy
login (python scripts/probe.py login) and optionally PLAN_OWNER_TELEGRAM_ID for /debug.
DRY_RUN stays 1 unless you set it to 0 yourself: then book_table really books.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plan_bana.auth import CredentialStore
from plan_bana.bot import MessageRefresher, PlanTelegram
from plan_bana.db import PlanDB
from plan_bana.db import connect as connect_db
from plan_bana.gemini import GeminiModel
from plan_bana.planner import SwiggyAuthError
from plan_bana.rounds import Deps, Rounds
from plan_bana.store import Store
from plan_bana.swiggy import connect, orders_enabled

load_dotenv(ROOT / ".env")
log = logging.getLogger("plan_bana")
OFFSET_KEY = "plan_telegram_offset"
WORKERS = 3  # eng E3: different groups plan in parallel; one group's jobs stay serial
LEASE_S = 180
TICK_S = 60


def require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        sys.exit(f"Missing {name} in .env")
    return val


async def poller(rounds: Rounds, tg: PlanTelegram, store: Store, stop: asyncio.Event) -> None:
    while not stop.is_set():
        raw = store.get_setting(OFFSET_KEY)
        try:
            updates = await tg.get_updates(int(raw) if raw else None)
        except Exception:
            log.warning("getUpdates failed", exc_info=True)
            await asyncio.sleep(3)
            continue
        for upd in updates:
            store.set_setting(OFFSET_KEY, str(upd["update_id"] + 1))  # at-most-once: a crash never replays a tap
            try:
                outcome = await rounds.on_update(upd)
                log.info("update %s -> %s", upd["update_id"], outcome)
            except Exception:
                log.exception("update %s failed", upd["update_id"])


async def worker(rounds: Rounds, store: Store, owner: str, stop: asyncio.Event) -> None:
    last_tick = 0.0
    while not stop.is_set():
        now = time.time()
        if now - last_tick > TICK_S:
            last_tick = now
            try:
                await rounds.sweep(now)
            except Exception:
                log.exception("sweep failed")
        store.reclaim_expired(now)
        job = store.claim(owner, now, lease_s=LEASE_S)
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
            continue
        try:
            outcome = await rounds.on_job(job)
            log.info("%s job %s %s -> %s", owner, job.id, job.kind, outcome)
            store.complete(job.id, owner)
        except Exception as e:
            log.exception("job %s failed", job.id)
            store.fail(job.id, owner, f"{type(e).__name__}: {e}"[:500])


async def main() -> None:
    token = require("PLAN_TELEGRAM_BOT_TOKEN")
    gemini_key = require("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    base = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
    cred_store = CredentialStore(ROOT / ".secrets" / "swiggy.json")
    creds = cred_store.load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    log.info("Swiggy token valid for %.1f h; DRY_RUN=%s", creds.seconds_left() / 3600,
             "off (REAL bookings)" if orders_enabled() else "on (nothing is booked)")

    conn = connect_db(ROOT / os.getenv("PLAN_DB_PATH", "plan_bana.db"))
    store, db = Store(conn), PlanDB(conn)

    @asynccontextmanager
    async def sessions():
        c = cred_store.load()
        if not c or not c.is_valid():
            raise SwiggyAuthError("Swiggy token missing or expired")
        async with connect(base, "scenes", c.access_token) as sc, connect(base, "dineout", c.access_token) as do:
            yield sc, do

    async with httpx.AsyncClient() as http:
        tg = PlanTelegram(http, token)
        me = await tg.get_me()
        owner = os.getenv("PLAN_OWNER_TELEGRAM_ID", "").strip()
        deps = Deps(db=db, store=store, tg=tg, sessions=sessions,
                    make_model=lambda: GeminiModel(gemini_key, model_name, thinking_level=os.getenv("GEMINI_THINKING", "low") or None),
                    refresher=MessageRefresher(tg), clock=time.time,
                    owner_id=int(owner) if owner.isdigit() else None, bot_id=me["id"])
        rounds = Rounds(deps)
        log.info("Plan Bana is up as @%s", me.get("username"))
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        await rounds.sweep(time.time())  # retention also runs at startup (R3-10)
        await asyncio.gather(poller(rounds, tg, store, stop),
                             *(worker(rounds, store, f"plan-{i + 1}", stop) for i in range(WORKERS)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
