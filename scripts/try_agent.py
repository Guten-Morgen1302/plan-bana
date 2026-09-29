"""Run the agent once against real Gemini + real Swiggy (read tools only; cart is NOT written).

  python scripts/try_agent.py "do packet amul doodh aur brown bread bhej do"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.agent import run_turn
from maa.auth import CredentialStore
from maa.gemini import GeminiModel
from maa.swiggy import connect

load_dotenv(ROOT / ".env")


async def main(transcript: str) -> None:
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    model = GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    async with connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "im", creds.access_token) as sw:
        result = await run_turn(model, sw, os.environ["MOM_ADDRESS_ID"], transcript)
    print(f"tools used: {' -> '.join(result.tool_log)}")
    if result.kind == "question":
        print(f"asks Mom: {result.question}")
        return
    total = 0.0
    for item in result.items:
        line = (item.price or 0) * item.quantity
        total += line
        print(f"  {item.quantity} x {item.name}  (₹{item.price})  <- '{item.phrase}'")
    print(f"total ≈ ₹{total:g}" + (f"\nnote: {result.note}" if result.note else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(" ".join(sys.argv[1:]) or "do packet amul doodh aur ek brown bread bhej do"))
