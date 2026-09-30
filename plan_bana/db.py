"""Plan Bana's SQLite tables, next to maa.store's jobs table in the same file (eng D1/E1: plan_bana.db).

    chat_messages   rolling buffer of human text per group (24 h, max 50 rows per chat)
    group_settings  the group's area pin + whether the join notice went out
    plan_rounds     one row per /plan; state machine column + the 4 message ids (design DR2)
    statements      what extraction read from whom (kept for /forget and debugging)
    people/votes/rsvps  who is in the round, what they voted, whether they are coming
    round_events    append-only trail: state changes, tool calls, check verdicts (CEO D4, /debug)

Retention (CEO D6 R3-10): chat rows older than 24 h and rounds ended more than 7 days ago are purged
on every insert/plan and on every worker tick, across all chats.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from maa.store import connect as connect_store
from plan_bana.model import TERMINAL

BUFFER_KEEP_S = 24 * 3600
BUFFER_MAX_ROWS = 50
ROUND_KEEP_S = 7 * 24 * 3600
_B32 = "abcdefghijklmnopqrstuvwxyz234567"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    text        TEXT NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS chat_messages_ts ON chat_messages (chat_id, ts);

CREATE TABLE IF NOT EXISTS group_settings (
    chat_id      INTEGER PRIMARY KEY,
    lat          REAL,
    lng          REAL,
    area         TEXT,
    notice_sent  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plan_rounds (
    id              TEXT PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    organizer_id    INTEGER NOT NULL,
    organizer_name  TEXT NOT NULL,
    state           TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    last_activity   REAL NOT NULL,
    ended_at        REAL,
    constraints     TEXT,
    plans           TEXT,
    gap             TEXT,
    locked_idx      INTEGER,
    guests          INTEGER NOT NULL DEFAULT 0,
    party           INTEGER,
    book_requested  INTEGER NOT NULL DEFAULT 0,
    table_only      INTEGER NOT NULL DEFAULT 0,
    alt_time        INTEGER,
    samjha_msg      INTEGER,
    plans_msg       INTEGER,
    control_msg     INTEGER,
    rsvp_msg        INTEGER,
    badlo_prompt    INTEGER,
    result          TEXT
);
CREATE INDEX IF NOT EXISTS plan_rounds_chat ON plan_rounds (chat_id, state);
CREATE INDEX IF NOT EXISTS plan_rounds_ended ON plan_rounds (ended_at);

CREATE TABLE IF NOT EXISTS statements (
    round_id    TEXT NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    field       TEXT NOT NULL,
    value       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS statements_round ON statements (round_id);

CREATE TABLE IF NOT EXISTS people (
    round_id  TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    source    TEXT NOT NULL,
    PRIMARY KEY (round_id, user_id)
);

CREATE TABLE IF NOT EXISTS votes (
    round_id  TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    idx       INTEGER NOT NULL,
    ts        REAL NOT NULL,
    PRIMARY KEY (round_id, user_id)
);

CREATE TABLE IF NOT EXISTS rsvps (
    round_id  TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    status    TEXT NOT NULL,          -- t = ticket bought | c = coming | n = not coming
    ts        REAL NOT NULL,
    PRIMARY KEY (round_id, user_id)
);

CREATE TABLE IF NOT EXISTS round_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id  TEXT NOT NULL,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS round_events_round ON round_events (round_id, ts);
"""

ROUND_FIELDS = frozenset({
    "constraints", "plans", "gap", "locked_idx", "guests", "party", "book_requested", "table_only", "alt_time",
    "samjha_msg", "plans_msg", "control_msg", "rsvp_msg", "badlo_prompt", "result", "organizer_id",
    "organizer_name",
})
CHILD_TABLES = ("statements", "people", "votes", "rsvps", "round_events")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = connect_store(path)  # jobs/settings tables + WAL + busy_timeout
    conn.executescript(SCHEMA)
    return conn


def new_round_id() -> str:
    return "".join(secrets.choice(_B32) for _ in range(8))


class PlanDB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- chat buffer ----------

    def add_message(self, chat_id: int, message_id: int, user_id: int, name: str, text: str, ts: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO chat_messages (chat_id, message_id, user_id, name, text, ts) VALUES (?,?,?,?,?,?)",
            (chat_id, message_id, user_id, name, text[:1000], ts),
        )
        self._trim_buffer(chat_id, ts)

    def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        cur = self.conn.execute(
            "UPDATE chat_messages SET text = ? WHERE chat_id = ? AND message_id = ?", (text[:1000], chat_id, message_id)
        )
        return cur.rowcount == 1

    def _trim_buffer(self, chat_id: int, now: float) -> None:
        self.conn.execute("DELETE FROM chat_messages WHERE chat_id = ? AND ts < ?", (chat_id, now - BUFFER_KEEP_S))
        self.conn.execute(
            "DELETE FROM chat_messages WHERE chat_id = ? AND message_id NOT IN "
            "(SELECT message_id FROM chat_messages WHERE chat_id = ? ORDER BY ts DESC, message_id DESC LIMIT ?)",
            (chat_id, chat_id, BUFFER_MAX_ROWS),
        )

    def recent_messages(self, chat_id: int, now: float) -> list[sqlite3.Row]:
        """Last 24 h, max 50, oldest first (R3-9: filter at read time too)."""
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE chat_id = ? AND ts >= ? ORDER BY ts DESC, message_id DESC LIMIT ?",
            (chat_id, now - BUFFER_KEEP_S, BUFFER_MAX_ROWS),
        ).fetchall()
        return list(reversed(rows))

    def message_count(self, chat_id: int, now: float) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE chat_id = ? AND ts >= ?", (chat_id, now - BUFFER_KEEP_S)
        ).fetchone()[0]

    def forget_user(self, chat_id: int, user_id: int) -> int:
        n = self.conn.execute(
            "DELETE FROM chat_messages WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).rowcount
        self.conn.execute(
            "DELETE FROM statements WHERE user_id = ? AND round_id IN (SELECT id FROM plan_rounds WHERE chat_id = ?)",
            (user_id, chat_id),
        )
        return n

    # ---------- group settings ----------

    def area(self, chat_id: int) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,)).fetchone()
        return row if row and row["lat"] is not None else None

    def set_area(self, chat_id: int, lat: float, lng: float, label: str) -> None:
        self.conn.execute(
            "INSERT INTO group_settings (chat_id, lat, lng, area) VALUES (?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET lat = excluded.lat, lng = excluded.lng, area = excluded.area",
            (chat_id, lat, lng, label),
        )

    def claim_notice(self, chat_id: int) -> bool:
        """True exactly once per group: whoever flips notice_sent sends the join notice."""
        self.conn.execute("INSERT OR IGNORE INTO group_settings (chat_id) VALUES (?)", (chat_id,))
        return self.conn.execute(
            "UPDATE group_settings SET notice_sent = 1 WHERE chat_id = ? AND notice_sent = 0", (chat_id,)
        ).rowcount == 1

    # ---------- rounds ----------

    def create_round(self, chat_id: int, organizer_id: int, organizer_name: str, state: str, now: float) -> str:
        for _ in range(5):
            rid = new_round_id()
            try:
                self.conn.execute(
                    "INSERT INTO plan_rounds (id, chat_id, organizer_id, organizer_name, state, created_at, "
                    "updated_at, last_activity) VALUES (?,?,?,?,?,?,?,?)",
                    (rid, chat_id, organizer_id, organizer_name, state, now, now, now),
                )
            except sqlite3.IntegrityError:
                continue
            self.log(rid, "state", {"to": state}, now)
            return rid
        raise RuntimeError("could not allocate a round id")

    def round(self, rid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM plan_rounds WHERE id = ?", (rid,)).fetchone()

    def open_round(self, chat_id: int) -> sqlite3.Row | None:
        marks = ",".join("?" * len(TERMINAL))
        return self.conn.execute(
            f"SELECT * FROM plan_rounds WHERE chat_id = ? AND state NOT IN ({marks}) ORDER BY created_at DESC LIMIT 1",
            (chat_id, *sorted(TERMINAL)),
        ).fetchone()

    def last_round(self, chat_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM plan_rounds WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1", (chat_id,)
        ).fetchone()

    def cas_state(self, rid: str, expected: str | tuple[str, ...], new: str, now: float, **fields: Any) -> bool:
        """Compare-and-set the state (plus optional fields). Only one caller can move a round."""
        exp = (expected,) if isinstance(expected, str) else tuple(expected)
        sets, vals = self._field_sql(fields)
        ended = ", ended_at = ?" if new in TERMINAL else ""
        params = [new, now, now, *vals] + ([now] if ended else []) + [rid, *exp]
        cur = self.conn.execute(
            f"UPDATE plan_rounds SET state = ?, updated_at = ?, last_activity = ?{sets}{ended} "
            f"WHERE id = ? AND state IN ({','.join('?' * len(exp))})",
            params,
        )
        if cur.rowcount == 1:
            self.log(rid, "state", {"from": list(exp), "to": new}, now)
            return True
        return False

    def update(self, rid: str, now: float | None = None, **fields: Any) -> None:
        sets, vals = self._field_sql(fields)
        if not sets and now is None:
            return
        touch = ", last_activity = ?, updated_at = ?" if now is not None else ""
        self.conn.execute(
            f"UPDATE plan_rounds SET id = id{sets}{touch} WHERE id = ?",
            [*vals, *([now, now] if now is not None else []), rid],
        )

    def claim_booking(self, rid: str, now: float) -> bool:
        """book_requested 0 -> 1 while BOOK_PENDING: exactly one Book tap enqueues a booking job."""
        return self.conn.execute(
            "UPDATE plan_rounds SET book_requested = 1, last_activity = ? "
            "WHERE id = ? AND state = 'BOOK_PENDING' AND book_requested = 0",
            (now, rid),
        ).rowcount == 1

    @staticmethod
    def _field_sql(fields: dict[str, Any]) -> tuple[str, list[Any]]:
        bad = set(fields) - ROUND_FIELDS
        if bad:
            raise ValueError(f"unknown round fields {bad}")
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v for v in fields.values()]
        return "".join(f", {k} = ?" for k in fields), vals

    # ---------- people / votes / rsvps ----------

    def add_person(self, rid: str, user_id: int, name: str, source: str) -> bool:
        return self.conn.execute(
            "INSERT OR IGNORE INTO people (round_id, user_id, name, source) VALUES (?,?,?,?)",
            (rid, user_id, name, source),
        ).rowcount == 1

    def people(self, rid: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM people WHERE round_id = ? ORDER BY rowid", (rid,)).fetchall()

    def set_vote(self, rid: str, user_id: int, idx: int, now: float) -> None:
        self.conn.execute(
            "INSERT INTO votes (round_id, user_id, idx, ts) VALUES (?,?,?,?) "
            "ON CONFLICT(round_id, user_id) DO UPDATE SET idx = excluded.idx, ts = excluded.ts",
            (rid, user_id, idx, now),
        )

    def tally(self, rid: str) -> dict[int, int]:
        rows = self.conn.execute("SELECT idx, COUNT(*) n FROM votes WHERE round_id = ? GROUP BY idx", (rid,))
        return {r["idx"]: r["n"] for r in rows}

    def set_rsvp(self, rid: str, user_id: int, status: str, now: float) -> None:
        self.conn.execute(
            "INSERT INTO rsvps (round_id, user_id, status, ts) VALUES (?,?,?,?) "
            "ON CONFLICT(round_id, user_id) DO UPDATE SET status = excluded.status, ts = excluded.ts",
            (rid, user_id, status, now),
        )

    def rsvps(self, rid: str) -> dict[int, str]:
        return {r["user_id"]: r["status"] for r in
                self.conn.execute("SELECT user_id, status FROM rsvps WHERE round_id = ?", (rid,))}

    def save_statements(self, rid: str, rows: list[tuple[int, int, str, str]]) -> None:
        self.conn.execute("DELETE FROM statements WHERE round_id = ?", (rid,))
        self.conn.executemany(
            "INSERT INTO statements (round_id, message_id, user_id, field, value) VALUES (?,?,?,?,?)",
            [(rid, *r) for r in rows],
        )

    # ---------- event trail ----------

    def log(self, rid: str, kind: str, data: dict[str, Any], now: float) -> None:
        self.conn.execute(
            "INSERT INTO round_events (round_id, ts, kind, data) VALUES (?,?,?,?)",
            (rid, now, kind, json.dumps(data, ensure_ascii=False, default=str)[:4000]),
        )

    def events(self, rid: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM round_events WHERE round_id = ? ORDER BY id", (rid,)).fetchall()

    # ---------- retention ----------

    def purge(self, now: float) -> int:
        n = self.conn.execute("DELETE FROM chat_messages WHERE ts < ?", (now - BUFFER_KEEP_S,)).rowcount
        old = [r["id"] for r in self.conn.execute(
            "SELECT id FROM plan_rounds WHERE ended_at IS NOT NULL AND ended_at < ?", (now - ROUND_KEEP_S,))]
        for rid in old:
            for table in CHILD_TABLES:
                self.conn.execute(f"DELETE FROM {table} WHERE round_id = ?", (rid,))
            self.conn.execute("DELETE FROM plan_rounds WHERE id = ?", (rid,))
        return n + len(old)
