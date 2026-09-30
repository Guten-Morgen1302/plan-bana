"""Run extraction + planning once against real Gemini + real Swiggy (read-only; books nothing).

  python scripts/try_plan.py "Rohan: sat raat free hu, budget 800" "Priya: veg hu, comedy chalega"

Each argument is one chat message "Name: text". Uses PLAN_LAT/PLAN_LNG/PLAN_AREA from .env as the pin.
If the chat leaves the date open, the coming Saturday is used.
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
from maa.swiggy import connect
from plan_bana import copy
from plan_bana.extract import Line, apply_default, extract_statements, reduce
from plan_bana.model import now_ist
from plan_bana.planner import Planner

load_dotenv(ROOT / ".env")

DEFAULT_CHAT = ["Harsh: sat raat kuch karte hai?", "Rohan: haan sat free hu, budget 900 max",
                "Priya: veg hu main, comedy chalega", "Aman: 4 log honge"]


async def main(messages: list[str]) -> None:
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    lat, lng = float(os.environ["PLAN_LAT"]), float(os.environ["PLAN_LNG"])
    area = os.getenv("PLAN_AREA", "Mulund, Mumbai")

    def make_model() -> GeminiModel:
        return GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

    now = time.time()
    names: dict[str, int] = {}
    lines = []
    for i, m in enumerate(messages):
        name, _, text = m.partition(":")
        uid = names.setdefault(name.strip(), len(names) + 1)
        lines.append(Line(i + 1, uid, name.strip(), text.strip(), now - 600 + i))
    t0 = time.time()
    c = reduce(await extract_statements(make_model, lines, now), lines, (1, lines[0].name), now)
    for g in c.gaps:
        apply_default(c, g, now_ist(now).date())
    print(copy.samjha(c, 1, lines[0].name, "try00000")[0])
    print(f"\n(extraction {time.time() - t0:.1f}s)\n")

    t1 = time.time()
    async with connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "scenes", creds.access_token) as sc, \
            connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "dineout", creds.access_token) as do:
        async def progress(stage: str) -> None:
            print(f"  … {copy.PROGRESS[stage]}")

        run = await Planner(make_model, sc, do, c, lat, lng, area, progress=progress,
                            log=lambda k, d: print(f"  [{k}] {str(d)[:600 if k != 'tool' else 160]}") if k in ("check", "deadline", "tool", "model_stopped", "tool_error") else None
                            ).run()
    print(f"\n(planning {time.time() - t1:.1f}s, {run.calls} Swiggy calls, partial={run.partial})\n")
    if run.options:
        header = copy.partial_header(len(run.options)) if run.partial else None
        print(copy.plans_message(run.options, "try00000", {}, 0, c.headcount, header)[0])
    else:
        print(copy.zero_plans(run.reasons))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1:] or DEFAULT_CHAT))
