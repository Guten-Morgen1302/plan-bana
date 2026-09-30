"""SQLite state: durable jobs with leases, timers and settings.

Job lease lifecycle:

    enqueue ──► pending ──claim──► leased(owner, expires) ──complete──► done
                   ▲                    │
                   └── lease expired ───┘  (reclaim_expired, run at startup and before claims)

    Timer jobs (kind="timer") for the same draft/round that are all overdue collapse to one:
    the highest-priority action runs, the rest are marked skipped (re-review D1).

Rules enforced here:
- A family's job (one Telegram group = one family) is claimable only while no other job of
  that family is leased, so one group's work runs serially while different groups run in parallel.
- complete()/fail() only succeed for the current lease owner, so a stale worker can't
  overwrite a newer owner's result.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LEASE_S = 120

# Higher wins when several timers of one draft are overdue at once.
TIMER_PRIORITY = {"cancel": 3, "expire": 3, "review_reminder": 1, "reminder": 1}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    draft_id      TEXT,
    payload       TEXT NOT NULL DEFAULT '{}',
    run_at        REAL NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',   -- pending | leased | done | failed | skipped
    lease_owner   TEXT,
    lease_expires REAL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs (state, run_at, id);
CREATE INDEX IF NOT EXISTS jobs_family ON jobs (family_id, state);
CREATE INDEX IF NOT EXISTS jobs_draft ON jobs (draft_id, kind, state);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

"""


@dataclass(frozen=True)
class Job:
    id: int
    family_id: str
    kind: str
    draft_id: str | None
    payload: dict[str, Any]
    run_at: float
    attempts: int
    lease_owner: str


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- settings ----------

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def take_setting(self, key: str) -> str | None:
        """Read and delete in one step (single-use codes)."""
        with self._tx():
            value = self.get_setting(key)
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        return value

    # ---------- jobs ----------

    def enqueue(
        self,
        family_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        run_at: float,
        now: float,
        draft_id: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO jobs (family_id, kind, draft_id, payload, run_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (family_id, kind, draft_id, json.dumps(payload or {}), run_at, now),
        )
        return int(cur.lastrowid)

    def reclaim_expired(self, now: float) -> int:
        return self.conn.execute(
            "UPDATE jobs SET state = 'pending', lease_owner = NULL, lease_expires = NULL "
            "WHERE state = 'leased' AND lease_expires <= ?",
            (now,),
        ).rowcount

    def collapse_overdue_timers(self, now: float) -> int:
        """For each draft with several overdue timers, keep only the winner; skip the rest."""
        rows = self.conn.execute(
            "SELECT id, draft_id, payload, run_at FROM jobs "
            "WHERE kind = 'timer' AND state = 'pending' AND run_at <= ? AND draft_id IS NOT NULL",
            (now,),
        ).fetchall()
        by_draft: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_draft.setdefault(row["draft_id"], []).append(row)
        skipped = 0
        for timers in by_draft.values():
            if len(timers) < 2:
                continue
            winner = max(
                timers,
                key=lambda r: (TIMER_PRIORITY.get(json.loads(r["payload"]).get("action", ""), 0), r["run_at"], r["id"]),
            )
            losers = [r["id"] for r in timers if r["id"] != winner["id"]]
            self.conn.execute(
                f"UPDATE jobs SET state = 'skipped' WHERE id IN ({','.join('?' * len(losers))})", losers
            )
            skipped += len(losers)
        return skipped

    def claim(self, owner: str, now: float, lease_s: float = DEFAULT_LEASE_S) -> Job | None:
        """Lease the oldest due job whose family has nothing else leased (CEO D6)."""
        with self._tx():
            self.reclaim_expired(now)
            self.collapse_overdue_timers(now)
            row = self.conn.execute(
                """
                UPDATE jobs
                   SET state = 'leased', lease_owner = ?, lease_expires = ?, attempts = attempts + 1
                 WHERE id = (
                    SELECT j.id FROM jobs j
                     WHERE j.state = 'pending' AND j.run_at <= ?
                       AND NOT EXISTS (
                           SELECT 1 FROM jobs k
                            WHERE k.family_id = j.family_id AND k.state = 'leased'
                       )
                     ORDER BY j.run_at, j.id
                     LIMIT 1
                 )
                RETURNING id, family_id, kind, draft_id, payload, run_at, attempts, lease_owner
                """,
                (owner, now + lease_s, now),
            ).fetchone()
        if row is None:
            return None
        return Job(
            id=row["id"],
            family_id=row["family_id"],
            kind=row["kind"],
            draft_id=row["draft_id"],
            payload=json.loads(row["payload"]),
            run_at=row["run_at"],
            attempts=row["attempts"],
            lease_owner=row["lease_owner"],
        )

    def complete(self, job_id: int, owner: str) -> bool:
        return self._finish(job_id, owner, "done", None)

    def fail(self, job_id: int, owner: str, error: str) -> bool:
        return self._finish(job_id, owner, "failed", error)

    def job_state(self, job_id: int) -> str | None:
        row = self.conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row["state"] if row else None

    def _finish(self, job_id: int, owner: str, state: str, error: str | None) -> bool:
        cur = self.conn.execute(
            "UPDATE jobs SET state = ?, last_error = ?, lease_owner = NULL, lease_expires = NULL "
            "WHERE id = ? AND state = 'leased' AND lease_owner = ?",
            (state, error, job_id, owner),
        )
        return cur.rowcount == 1

    def _tx(self):
        return _Immediate(self.conn)


class _Immediate:
    """BEGIN IMMEDIATE so claim's read-then-write can't interleave with another writer."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
