"""Check that the API keys in .env work. Prints PASS/FAIL per service, never the keys."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def check_gemini() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return "missing GEMINI_API_KEY"
    from google import genai

    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    client = genai.Client(api_key=key)
    reply = client.models.generate_content(model=model, contents="Reply with the single word: ok")
    return f"model {model} replied {reply.text.strip()[:20]!r}"


def check_telegram() -> str:
    token = os.getenv("PLAN_TELEGRAM_BOT_TOKEN")
    if not token:
        return "missing PLAN_TELEGRAM_BOT_TOKEN"
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"HTTP {r.status_code}: {data.get('description')}")
    return f"bot @{data['result']['username']}"


def main() -> int:
    failed = 0
    for name, fn in [("Gemini", check_gemini), ("Telegram", check_telegram)]:
        try:
            msg = fn()
            status = "FAIL" if msg.startswith("missing") else "PASS"
        except Exception as e:  # noqa: BLE001 (diagnostic script: any provider error is a FAIL line)
            status, msg = "FAIL", f"{type(e).__name__}: {str(e)[:200]}"
        failed += status == "FAIL"
        print(f"{status:4}  {name:9} {msg}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
