"""Go/no-go check right before recording the demo. Read-only: books nothing, sends nothing.

  python scripts/preflight.py            # today, comedy + music
  python scripts/preflight.py 2026-10-08 # another date

Checks the Swiggy login, DRY_RUN, the bot, the group pin, and which shows near the pin still have seats
on that date (Scenes only lists each event's next show, and tonight's shows close a few hours before).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plan_bana.auth import CredentialStore
from plan_bana.corpus import haversine_km, parse_event_details, parse_shows, parse_suggestions
from plan_bana.model import IST
from plan_bana.planner import _day_bounds
from plan_bana.swiggy import connect, orders_enabled

load_dotenv(ROOT / ".env")
OK, BAD, WARN = "✅", "❌", "⚠️"


def group_pin() -> tuple[float, float, str]:
    db = ROOT / os.getenv("PLAN_DB_PATH", "plan_bana.db")
    if db.exists():
        row = sqlite3.connect(db).execute(
            "SELECT lat, lng FROM group_settings WHERE lat IS NOT NULL ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            return row[0], row[1], "group pin"
    return float(os.environ["PLAN_LAT"]), float(os.environ["PLAN_LNG"]), ".env PLAN_LAT/LNG"


async def main(date: str) -> int:
    problems = 0
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    hours = creds.seconds_left() / 3600 if creds and creds.access_token else 0
    if hours < 2:
        print(f"{BAD} Swiggy login: {hours:.1f} h left → run: python scripts/probe.py login")
        return 1
    print(f"{OK} Swiggy login: {hours:.0f} h left")
    print(f"{WARN if orders_enabled() else OK} DRY_RUN={'0 → Book makes a REAL free reservation' if orders_enabled() else '1 → Book ends in test mode'}")

    token = os.getenv("PLAN_TELEGRAM_BOT_TOKEN", "")
    try:
        async with httpx.AsyncClient() as http:
            me = (await http.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)).json()["result"]
        reads = me.get("can_read_all_group_messages")
        print(f"{OK if reads else BAD} Bot @{me['username']} ({me['first_name']}), reads group chat: {reads}")
        problems += not reads
    except Exception as e:  # noqa: BLE001 (diagnostic script)
        print(f"{BAD} Bot token: {type(e).__name__}")
        problems += 1

    lat, lng, src = group_pin()
    print(f"{OK} Pin ({src}): {lat:.4f}, {lng:.4f}")

    now = dt.datetime.now(IST)
    print(f"\nShows on {date} near the pin (≤ 30 km), checked {now:%H:%M} IST:")
    good = 0
    async with connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "scenes", creds.access_token) as sc:
        loc = {"latitude": lat, "longitude": lng}
        seen: set[str] = set()
        for genre in ("comedy", "music"):
            sugg = parse_suggestions((await sc.call("search_events", {"query": genre, **loc}))["structured"])
            for s in sugg[:12]:
                if s["eventId"] in seen:
                    continue
                seen.add(s["eventId"])
                d = parse_event_details((await sc.call("get_event_details", {"eventId": s["eventId"], **loc}))["structured"])
                if d is None or d.venue_lat is None:
                    continue
                km = haversine_km(lat, lng, d.venue_lat, d.venue_lng)
                if km > 30:
                    continue
                a, b = _day_bounds(date)
                shows = parse_shows((await sc.call("list_event_shows", {
                    "eventId": d.event_id, "venueId": d.venue_id, "showStartTime": a, "showEndTime": b, **loc}))["structured"])
                for sh in shows:
                    t = dt.datetime.fromtimestamp(sh.start, IST)
                    if t.date().isoformat() != date:
                        continue
                    live = sh.available and sh.ticket_price is not None and t > now
                    good += live
                    mark = OK if live else BAD
                    price = f"₹{sh.ticket_price}" if sh.ticket_price is not None else "no tickets"
                    print(f"  {mark} {genre:6} {t:%H:%M}  {d.name[:38]:38} {km:4.1f} km  {price}")
    if good:
        print(f"\n{OK} GO: {good} show(s) bookable. Chat about '{genre_hint(date, now)}' and the plans will include shows.")
    else:
        print(f"\n{WARN} No bookable show on {date}: plans will be dinner-only. Try another date "
              f"(python scripts/preflight.py YYYY-MM-DD) or record earlier in the day.")
    return 1 if problems else 0


def genre_hint(date: str, now: dt.datetime) -> str:
    return "aaj raat comedy" if date == now.date().isoformat() else f"{dt.date.fromisoformat(date):%d %b} ko comedy"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    day = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(IST).date().isoformat()
    sys.exit(asyncio.run(main(day)))
