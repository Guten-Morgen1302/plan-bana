"""Run the Plan Bana planner once against real Gemini + real Swiggy Scenes/Dineout (read-only; books nothing).

  python scripts/try_plan.py "saturday raat 4 log, comedy show aur phir pizza"
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.auth import CredentialStore
from maa.gemini import GeminiModel
from maa.plan import plan_outing, plans_message
from maa.swiggy import connect

load_dotenv(ROOT / ".env")


async def main(request: str) -> None:
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    base = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
    lat, lng = float(os.environ["PLAN_LAT"]), float(os.environ["PLAN_LNG"])
    model = GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    t0 = time.time()
    async with connect(base, "scenes", creds.access_token) as scenes, connect(base, "dineout", creds.access_token) as dineout:
        result = await plan_outing(model, scenes, dineout, lat, lng, os.getenv("PLAN_AREA", "Mulund/Thane, Mumbai"), request)
    print(f"tools: {' -> '.join(result.tool_log)}  ({time.time() - t0:.0f} s)\n")
    print(result.question if result.kind == "question" else plans_message(result.plans, result.headcount, request))
    if result.kind == "plans":
        for p in result.plans:
            print(f"  [ids] rest={p.restaurant_id} item={p.item_id} slot={p.slot_id} t={p.reservation_time} event={p.event_id}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(" ".join(sys.argv[1:]) or "saturday raat 4 log, comedy show aur phir pizza"))
