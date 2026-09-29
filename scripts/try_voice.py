"""Take the latest queued WhatsApp voice note, transcribe it with Sarvam, run the agent (read-only).

  python scripts/try_voice.py            # latest inbound job
  python scripts/try_voice.py --no-agent # transcript only

Does not write the Swiggy cart, place orders, reply on WhatsApp, or change the job's state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.agent import run_turn
from maa.auth import CredentialStore
from maa.gemini import GeminiModel
from maa.swiggy import connect
from maa.voice import download_media, transcribe

load_dotenv(ROOT / ".env")


def latest_voice_payload() -> dict:
    conn = sqlite3.connect(ROOT / os.getenv("STATE_DB", "state.db"))
    rows = conn.execute("SELECT payload FROM jobs WHERE kind = 'inbound' ORDER BY id DESC").fetchall()
    for (raw,) in rows:
        p = json.loads(raw)
        if p.get("kind") == "audio" and p.get("media_id"):
            return p
    sys.exit("No queued voice note. Send one from Mom's WhatsApp to the test number.")


async def main(run_agent: bool) -> None:
    p = latest_voice_payload()
    async with httpx.AsyncClient() as client:
        audio, mime = await download_media(client, p["media_id"], os.environ["WHATSAPP_TOKEN"])
        print(f"downloaded {len(audio) / 1024:.1f} KB ({mime})")
        t = await transcribe(client, audio, mime, os.environ["SARVAM_API_KEY"])
    print(f"transcript: {t.text!r}  [lang={t.language} p={t.language_probability}]")
    if not run_agent:
        return
    creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    model = GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    async with connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "im", creds.access_token) as sw:
        result = await run_turn(model, sw, os.environ["MOM_ADDRESS_ID"], t.text)
    if result.kind == "question":
        print(f"bot asks Mom: {result.question}")
        return
    total = sum((i.price or 0) * i.quantity for i in result.items)
    for i in result.items:
        print(f"  {i.quantity} x {i.name}  (₹{i.price})  <- '{i.phrase}'")
    print(f"total ≈ ₹{total:g}" + (f"\nnote: {result.note}" if result.note else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-agent", action="store_true")
    asyncio.run(main(not ap.parse_args().no_agent))
