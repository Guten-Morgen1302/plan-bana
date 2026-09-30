"""End-to-end rehearsal: a scripted group chat through the REAL bot logic with real Gemini + real Swiggy.
Only Telegram is simulated (a bot cannot post as your friends); every bot message is printed.
DRY_RUN stays on, so the Book step ends in TEST_ONLY and nothing is booked.

  python scripts/sim_group.py            # tonight: "aaj raat comedy"
  python scripts/sim_group.py sat        # Saturday dinner chat
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.auth import CredentialStore
from maa.gemini import GeminiModel
from maa.store import Store
from maa.swiggy import connect
from plan_bana import copy
from plan_bana.bot import MessageRefresher
from plan_bana.db import PlanDB
from plan_bana.db import connect as connect_db
from plan_bana.rounds import Deps, Rounds

load_dotenv(ROOT / ".env")
CHAT = -42
USERS = {1: "Harsh", 2: "Rohan", 3: "Priya", 4: "Aman"}
CHATS = {
    "tonight": [(2, "aaj raat kuch plan karte hai kya?"), (3, "haan, comedy show chalega, main veg hu yaad rakhna"),
                (4, "budget 1000 tak theek hai"), (1, "done, aaj raat 4 log")],
    "sat": [(2, "sat raat dinner?"), (3, "sat ok, veg hu main"), (4, "800 max yaar"), (1, "sat pakka, sirf dinner")],
}


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class PrintTelegram:
    def __init__(self):
        self.messages: dict[int, tuple[str, list | None]] = {}
        self.n = 0

    def _show(self, tag: str, mid: int, text: str, buttons) -> None:
        print(f"\n┌─ bot {tag} #{mid}")
        for line in plain(text).splitlines():
            print(f"│ {line}")
        for row in buttons or []:
            print("│ " + "  ".join(f"[{lbl}]" for lbl, _ in row))
        print("└─")

    async def send(self, chat_id, text, buttons=None, *, force_reply=False, reply_to=None):
        self.n += 1
        self.messages[self.n] = (text, buttons)
        self._show("sends", self.n, text, buttons)
        return self.n

    async def edit(self, chat_id, message_id, text, buttons=None):
        if self.messages.get(message_id) == (text, buttons):
            return
        self.messages[message_id] = (text, buttons)
        self._show("edits", message_id, text, buttons)

    async def answer_callback(self, cid, text=""):
        if text:
            print(f"   (toast: {text})")

    async def pin(self, chat_id, message_id):
        print(f"   (pinned #{message_id})")

    def button(self, mid: int, start: str) -> str:
        for row in self.messages[mid][1] or []:
            for label, data in row:
                if label.startswith(start):
                    return data
        raise SystemExit(f"no button {start!r} on #{mid}")


async def main(which: str) -> None:
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    base = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
    conn = connect_db(Path(tempfile.mkdtemp()) / "sim.db")
    db, store, tg = PlanDB(conn), Store(conn), PrintTelegram()

    @asynccontextmanager
    async def sessions():
        async with connect(base, "scenes", creds.access_token) as sc, connect(base, "dineout", creds.access_token) as do:
            yield sc, do

    refresher = MessageRefresher(tg, min_gap=0.2)
    rounds = Rounds(Deps(db=db, store=store, tg=tg, sessions=sessions,
                         make_model=lambda: GeminiModel(os.environ["GEMINI_API_KEY"],
                                                        os.getenv("GEMINI_MODEL", "gemini-3.8-flash")),
                         refresher=refresher, clock=time.time, owner_id=1, owner_name="Harsh"))
    mid = {"n": 1000}

    async def say(uid: int, text: str, **extra):
        mid["n"] += 1
        print(f"\n{USERS[uid]}: {text}")
        out = await rounds.on_update({"message": {"message_id": mid["n"], "date": time.time(),
                                                  "chat": {"id": CHAT, "type": "group"},
                                                  "from": {"id": uid, "first_name": USERS[uid]}, "text": text, **extra}})
        await refresher.drain()
        return out

    async def tap(uid: int, data: str):
        print(f"\n{USERS[uid]} taps {data}")
        out = await rounds.on_update({"callback_query": {"id": "x", "from": {"id": uid, "first_name": USERS[uid]},
                                                         "data": data}})
        await refresher.drain()
        return out

    async def jobs(until: float | None = None):
        while (job := store.claim("sim", until or time.time())) is not None:
            t0 = time.time()
            out = await rounds.on_job(job)
            print(f"   (job {job.kind} -> {out} in {time.time() - t0:.1f}s)")
            store.complete(job.id, "sim")
        await refresher.drain()

    await rounds.on_update({"my_chat_member": {"chat": {"id": CHAT, "type": "group"},
                                               "new_chat_member": {"status": "member"}}})
    db.set_area(CHAT, float(os.environ["PLAN_LAT"]), float(os.environ["PLAN_LNG"]),
                os.getenv("PLAN_AREA", "Mulund, Mumbai"))
    for uid, text in CHATS[which]:
        await say(uid, text)
    await say(1, "/plan")
    await jobs()
    r = db.last_round(CHAT)
    if r["state"] != "CONFIRMING":
        raise SystemExit(f"stopped at {r['state']}")
    if r["gap"]:
        first = tg.messages[r["samjha_msg"]][1][0][0][1]
        await tap(1, first)
    await tap(1, copy.encode("s", r["id"]))
    await jobs()
    r = db.last_round(CHAT)
    if r["state"] != "VOTING":
        raise SystemExit(f"stopped at {r['state']}")
    for uid in (2, 3, 4):
        await tap(uid, copy.encode("v", r["id"], 1))
    r = db.last_round(CHAT)
    await tap(2, copy.encode("r", r["id"], "t" if locked_event(r) else "c"))
    await tap(3, copy.encode("r", r["id"], "c"))
    await tap(4, copy.encode("r", r["id"], "c"))
    await tap(1, copy.encode("g", r["id"], "+"))
    await tap(1, copy.encode("b", r["id"]))
    await tap(1, copy.encode("k", r["id"]))
    await jobs()
    r = db.last_round(CHAT)
    print(f"\nFINAL STATE: {r['state']}  (new bot messages this round: {tg.n - 1})")


def locked_event(r) -> bool:
    import json
    return any(p.get("event") for p in json.loads(r["plans"] or "[]") if p["idx"] == r["locked_idx"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "tonight"))
