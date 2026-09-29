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
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return "missing TELEGRAM_BOT_TOKEN"
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"HTTP {r.status_code}: {data.get('description')}")
    return f"bot @{data['result']['username']}"


def check_sarvam() -> str:
    key = os.getenv("SARVAM_API_KEY")
    if not key:
        return "missing SARVAM_API_KEY"
    r = httpx.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": key},
        json={"text": "haan", "target_language_code": "hi-IN"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    audios = r.json().get("audios") or []
    return f"text-to-speech returned {len(audios)} audio clip(s)"


def main() -> int:
    failed = 0
    for name, fn in [("Gemini", check_gemini), ("Telegram", check_telegram), ("Sarvam", check_sarvam)]:
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
