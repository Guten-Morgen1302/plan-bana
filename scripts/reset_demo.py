"""Start the demo recording clean: forget the groups' buffered chat and old plan rounds.
Keeps each group's location pin, so you don't have to send it again on camera. Books/cancels nothing.

  python scripts/reset_demo.py          # asks before deleting
  python scripts/reset_demo.py --yes
  python scripts/reset_demo.py --pin    # also forget the pins (the demo will ask for 📍 again)

The bot can keep running; restart it anyway if a round was mid-way.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

TABLES = ("chat_messages", "statements", "people", "votes", "rsvps", "round_events", "plan_rounds")


def main(args: list[str]) -> None:
    db = ROOT / os.getenv("PLAN_DB_PATH", "plan_bana.db")
    if not db.exists():
        sys.exit(f"No database at {db}: nothing to reset.")
    conn = sqlite3.connect(db)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    print("Will delete:", ", ".join(f"{t}={n}" for t, n in counts.items()),
          "+ group pins" if "--pin" in args else "(group pins kept)")
    if "--yes" not in args and input("Type RESET to continue: ").strip() != "RESET":
        sys.exit("Aborted.")
    with conn:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM jobs WHERE family_id LIKE 'tg:%'")
        if "--pin" in args:
            conn.execute("UPDATE group_settings SET lat = NULL, lng = NULL, area = NULL")
    print("Done. In the group, start typing: the bot only reads messages sent from now on.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
