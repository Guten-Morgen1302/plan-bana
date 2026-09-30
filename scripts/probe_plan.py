"""Read-only probe of Swiggy Scenes + Dineout for Plan Bana (design task T1). Books nothing.

  python scripts/probe_plan.py [lat lng]

Answers the design's open questions: what events exist near the group, which fields shows carry
(time, duration, price, venue coords), which fields Dineout results carry (veg, cost for two,
distance), what FREE slots look like, and how long a full planning fan-out takes.
Raw responses go to probe_out/plan-*.json (gitignored: may contain personal data).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.auth import CredentialStore
from maa.swiggy import connect

load_dotenv(ROOT / ".env")
OUT = ROOT / "probe_out"


def save(name: str, args: dict, res: dict) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"plan-{name}.json").write_text(
        json.dumps({"args": args, "result": res}, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def show(name: str, res: dict, n: int = 1500) -> None:  # noqa: D401
    body = res["structured"] or res["parsed"] or res["text"]
    print(f"\n=== {name} is_error={res['is_error']}")
    print(json.dumps(body, ensure_ascii=False)[:n] if not isinstance(body, str) else body[:n])


async def main(lat: float, lng: float) -> None:
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    base = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
    loc = {"latitude": lat, "longitude": lng}
    t0 = time.time()
    async with connect(base, "scenes", creds.access_token) as sc, connect(base, "dineout", creds.access_token) as do:
        disc = await sc.call("discover_events", loc)
        save("discover", loc, disc)
        show("discover_events", disc, 3000)
        for q in ("comedy", "music"):
            r = await sc.call("search_events", {"query": q, **loc})
            save(f"search-{q}", {"query": q, **loc}, r)
            show(f"search_events {q}", r, 1500)
        rs = await do.call("search_restaurants_dineout", {"query": "Italian", "limit": 5, **loc})
        save("dineout-search", {"query": "Italian", **loc}, rs)
        show("search_restaurants_dineout", rs, 3000)
        sugg = ((await sc.call("search_events", {"query": "comedy", **loc})).get("structured") or {}).get("suggestions") or []
        if sugg:
            eid = sugg[0]["eventId"]
            det = await sc.call("get_event_details", {"eventId": eid, **loc})
            save("event-details", {"eventId": eid}, det)
            show("get_event_details", det, 2500)
            venue = str(((det.get("structured") or {}).get("venueId")) or "")
            sh = await sc.call("list_event_shows", {"eventId": eid, **({"venueId": venue} if venue else {}), **loc})
            save("event-shows", {"eventId": eid, "venueId": venue}, sh)
            show("list_event_shows", sh, 3000)
        import re
        ids = re.findall(r"\(ID: (\d+)\)", rs.get("text", ""))
        rid = ids[2] if len(ids) > 2 else (ids[0] if ids else None)
        if rid:
            det = await do.call("get_restaurant_details", {"restaurantId": rid, **loc})
            save("dineout-details", {"restaurantId": rid}, det)
            show("get_restaurant_details", det, 2500)
            day = (dt.date.today() + dt.timedelta(days=(5 - dt.date.today().weekday()) % 7)).isoformat()
            sl = await do.call("get_available_slots", {"restaurantId": rid, "date": day, **loc})
            save("dineout-slots", {"restaurantId": rid, "date": day, **loc}, sl)
            show("get_available_slots", sl, 3000)
            print("\nSTRUCTURED slots keys:", list((sl.get("structured") or {}).keys())[:20])
    print(f"\nprobe took {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [float(a) for a in sys.argv[1:3]]
    lat, lng = args if len(args) == 2 else (float(os.environ["PLAN_LAT"]), float(os.environ["PLAN_LNG"]))
    asyncio.run(main(lat, lng))
