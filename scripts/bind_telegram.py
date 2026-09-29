"""Print a one-time Telegram link that binds YOUR Telegram account as the approver.

  python scripts/bind_telegram.py
Open the link on your phone and tap Start. The server must be running.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.approval import new_bind_code
from maa.store import Store, connect

load_dotenv(ROOT / ".env")

if __name__ == "__main__":
    store = Store(connect(ROOT / os.getenv("STATE_DB", "state.db")))
    me = httpx.get(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getMe", timeout=15).json()
    username = me["result"]["username"]
    print(f"Open on your phone and tap START:\nhttps://t.me/{username}?start={new_bind_code(store)}")
