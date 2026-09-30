"""Chat → Constraints. Gemini only reports *statements* (who said what); code decides (CEO D6, R2-1).

    buffer rows ─► Gemini report_statements([{message_id, field, value}])  (1 retry on bad output)
                ─► validate: drop unknown message_ids / fields / unparseable values
                ─► reduce(): latest statement per person per field wins, then group rules:
                     date = most ✓ (tie → gap with the tied dates), window = intersection,
                     budget = minimum, genre = most mentioned (tie → both), headcount = max(stated, people)
                ─► gaps in priority date > time > budget > genre > headcount; defaults fill the rest

user_id and names always come from the buffer row the model cited, never from the model.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from plan_bana.llm import AgentOutputError, Model
from plan_bana.model import IST, WINDOWS, Constraints, Person

FIELDS = ("date_ok", "date_no", "time_window", "budget", "veg", "genre", "not_coming", "headcount")
GAP_ORDER = ("date", "time", "budget", "genre", "headcount")
GENRES = ("comedy", "music")
MAX_DAYS_AHEAD = 14

SYSTEM = """You read a WhatsApp-style group chat (Hinglish, English or Hindi) where friends are planning an outing.
Today is {today} ({weekday}), time now {now} IST.

Report every planning statement by calling report_statements ONCE. One statement = one fact from one message:
- date_ok: a date the sender can do / wants. value = ISO date (YYYY-MM-DD). "sat" = the coming Saturday
  (today if today is Saturday), "kal" = tomorrow, "aaj" = today, "weekend" = both Sat and Sun (two statements).
- date_no: a date the sender cannot do. value = ISO date.
- time_window: value = one of subah, dopahar, shaam, raat, or "HH:MM-HH:MM" (24h).
- budget: per-head rupees the sender is OK with. value = integer ("800 max" -> 800, "1k" -> 1000).
- veg: sender is vegetarian -> "true"; eats non-veg -> "false".
- genre: comedy | music | any | none (none = only dinner, no show).
- not_coming: sender says they can't come at all -> "true".
- headcount: total people mentioned for the plan ("4 log") -> integer.
Rules: message_id must be the [number] of the message the fact came from. Use only what people actually said;
skip jokes, sarcasm and questions nobody answered. If a later message corrects an earlier one, report both;
code keeps the latest. If there are no planning facts, call report_statements with an empty list."""

REPORT_TOOL = {
    "name": "report_statements",
    "description": "Report the planning statements found in the chat.",
    "parameters": {
        "type": "object",
        "properties": {
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "integer"},
                        "field": {"type": "string", "enum": list(FIELDS)},
                        "value": {"type": "string"},
                    },
                    "required": ["message_id", "field", "value"],
                },
            }
        },
        "required": ["statements"],
    },
}


class ExtractError(AgentOutputError):
    """Gemini gave no usable statements after one retry (CEO D4: "Samajh nahi aaya…")."""


@dataclass(frozen=True)
class Line:
    message_id: int
    user_id: int
    name: str
    text: str
    ts: float


@dataclass(frozen=True)
class Statement:
    message_id: int
    user_id: int
    field: str
    value: Any
    ts: float


def chat_prompt(lines: Sequence[Line]) -> str:
    out = []
    for ln in lines:
        when = dt.datetime.fromtimestamp(ln.ts, IST).strftime("%a %H:%M")
        out.append(f"[{ln.message_id}] {ln.name} ({when}): {ln.text}")
    return "\n".join(out)


def parse_window(value: str) -> tuple[int, int] | None:
    v = value.strip().lower()
    if v in WINDOWS:
        return WINDOWS[v]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", v)
    if not m:
        return None
    a = int(m[1]) * 60 + int(m[2])
    b = int(m[3]) * 60 + int(m[4])
    return (a, b) if 0 <= a < b <= 24 * 60 else None


def _parse_value(field: str, raw: Any, today: dt.date) -> Any:
    v = str(raw).strip().lower()
    if field in ("date_ok", "date_no"):
        d = dt.date.fromisoformat(v)
        if not today <= d <= today + dt.timedelta(days=MAX_DAYS_AHEAD):
            raise ValueError("date out of range")
        return d.isoformat()
    if field == "time_window":
        w = parse_window(v)
        if w is None:
            raise ValueError("bad window")
        return v if v in WINDOWS else w
    if field in ("budget", "headcount"):
        n = int(float(re.sub(r"[^\d.]", "", v)))
        limit = (50, 20000) if field == "budget" else (1, 20)
        if not limit[0] <= n <= limit[1]:
            raise ValueError("out of range")
        return n
    if field in ("veg", "not_coming"):
        if v not in ("true", "false"):
            raise ValueError("bad bool")
        return v == "true"
    if field == "genre":
        if v not in (*GENRES, "any", "none"):
            raise ValueError("bad genre")
        return v
    raise ValueError(field)


def validate(raw: Any, lines: Sequence[Line], today: dt.date) -> list[Statement]:
    """Keep only statements that cite a real buffered message and parse cleanly."""
    by_id = {ln.message_id: ln for ln in lines}
    items = raw.get("statements") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ExtractError("report_statements without a statements list")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("message_id"))
        except (TypeError, ValueError):
            continue
        ln, field = by_id.get(mid), it.get("field")
        if ln is None or field not in FIELDS:
            continue
        try:
            value = _parse_value(field, it.get("value"), today)
        except (ValueError, TypeError):
            continue
        out.append(Statement(mid, ln.user_id, field, value, ln.ts))
    return out


async def extract_statements(make_model: Callable[[], Model], lines: Sequence[Line], now: float
                             ) -> list[Statement]:
    today_dt = dt.datetime.fromtimestamp(now, IST)
    system = SYSTEM.format(today=today_dt.date().isoformat(), weekday=today_dt.strftime("%A"),
                           now=today_dt.strftime("%H:%M"))
    prompt = chat_prompt(lines)
    last_err = "no attempt"
    for _ in range(2):  # CEO D4: one retry, then give up visibly
        model = make_model()  # fresh history per attempt; model calls run off the event loop (eng E2)
        try:
            turn = await asyncio.to_thread(model.start, system, prompt, [REPORT_TOOL])
        except Exception as e:  # noqa: BLE001 (provider errors of any kind count as a failed attempt)
            last_err = f"{type(e).__name__}: {e}"
            continue
        call = next((c for c in turn.calls if c.name == "report_statements"), None)
        if call is None:
            last_err = f"no report_statements call: {turn.text[:200]!r}"
            continue
        try:
            return validate(call.args, lines, today_dt.date())
        except ExtractError as e:
            last_err = str(e)
    raise ExtractError(last_err)


# ---------------------------------------------------------------- reduce


def _upcoming_saturday(today: dt.date) -> dt.date:
    return today + dt.timedelta(days=(5 - today.weekday()) % 7)


def reduce(statements: Sequence[Statement], lines: Sequence[Line], organizer: tuple[int, str],
           now: float) -> Constraints:
    today = dt.datetime.fromtimestamp(now, IST).date()
    names = {ln.user_id: ln.name for ln in lines}
    names.setdefault(organizer[0], organizer[1])
    people: dict[int, Person] = {}
    windows: dict[int, tuple[int, int]] = {}
    genre_votes: Counter[str] = Counter()
    stated_heads: list[int] = []

    for st in sorted(statements, key=lambda s: (s.ts, s.message_id)):  # latest wins per person/field
        p = people.setdefault(st.user_id, Person(st.user_id, names.get(st.user_id, "?")))
        if st.field == "date_ok":
            p.available[st.value] = True
        elif st.field == "date_no":
            p.available[st.value] = False
        elif st.field == "time_window":
            windows[st.user_id] = WINDOWS[st.value] if isinstance(st.value, str) else tuple(st.value)
        elif st.field == "budget":
            p.budget = st.value
        elif st.field == "veg":
            p.veg = st.value
        elif st.field == "not_coming":
            p.not_coming = st.value
        elif st.field == "genre":
            genre_votes[st.value] += 1
        elif st.field == "headcount":
            stated_heads.append(st.value)
    people.setdefault(organizer[0], Person(organizer[0], organizer[1]))

    c = Constraints(people=people)
    coming = c.coming()
    gaps: list[str] = []

    # date: the date with the most ✓ among people still coming
    counts: Counter[str] = Counter()
    for p in coming:
        for d, ok in p.available.items():
            if ok:
                counts[d] += 1
    if counts:
        best = max(counts.values())
        top = sorted(d for d, n in counts.items() if n == best)
        if len(top) == 1:
            c.date = top[0]
        else:
            c.tie_dates = top[:4]
            gaps.append("date")
    else:
        gaps.append("date")

    # time window: intersection of everyone's latest window
    if windows:
        lo = max(w[0] for w in windows.values())
        hi = min(w[1] for w in windows.values())
        if lo < hi:
            c.window = (lo, hi)
            labels = {name for name, w in WINDOWS.items() if w == c.window}
            c.window_label = next(iter(labels)) if labels else ("raat" if lo >= 19 * 60 else "shaam")
        else:
            gaps.append("time")
    else:
        gaps.append("time")

    budgets = [p.budget for p in coming if p.budget]
    if budgets:
        c.budget = min(budgets)
    else:
        gaps.append("budget")

    if genre_votes:
        best = max(genre_votes.values())
        top = sorted(g for g, n in genre_votes.items() if n == best)
        chosen: list[str] = []
        for g in top:
            if g == "any":
                chosen += list(GENRES)
            elif g in GENRES:
                chosen.append(g)
        c.genres = [] if top == ["none"] else sorted(set(chosen))
    else:
        c.genres = list(GENRES)
        gaps.append("genre")

    c.stated_headcount = max(stated_heads) if stated_heads else None
    c.headcount = max(len(coming), c.stated_headcount or 0)
    if c.headcount <= 1 and c.stated_headcount is None:
        gaps.append("headcount")

    c.gaps = [g for g in GAP_ORDER if g in gaps]
    c.assumed = [ASSUMED[g](c, today) for g in c.gaps[1:]]  # the top gap is asked, the rest default
    for g in c.gaps[1:]:
        apply_default(c, g, today)
    return c


ASSUMED = {
    "date": lambda c, today: f"din kisi ne pakka nahi kiya → {_upcoming_saturday(today).strftime('%a')}",
    "time": lambda c, today: "time kisi ne nahi bola → raat",
    "budget": lambda c, today: "budget kisi ne nahi bola → koi limit nahi",
    "genre": lambda c, today: "kya karna hai kisi ne nahi bola → comedy ya music",
    "headcount": lambda c, today: f"kitne log pata nahi → {max(c.headcount, 2)}",
}


def apply_default(c: Constraints, gap: str, today: dt.date) -> None:
    if gap == "date":
        c.date = c.tie_dates[0] if c.tie_dates else _upcoming_saturday(today).isoformat()
    elif gap == "time":
        c.window, c.window_label = WINDOWS["raat"], "raat"
    elif gap == "budget":
        c.budget = None
    elif gap == "genre":
        c.genres = list(GENRES)
    elif gap == "headcount":
        c.headcount = max(c.headcount, 2)


def apply_answer(c: Constraints, gap: str, value: str, today: dt.date) -> Constraints:
    """The organizer answered the one gap question; every other gap keeps its default."""
    if gap == "date":
        d = dt.date.fromisoformat(value)
        if d < today:
            raise ValueError("date in the past")
        c.date = d.isoformat()
    elif gap == "time":
        c.window, c.window_label = WINDOWS[value], value
    elif gap == "budget":
        c.budget = int(value) or None
    elif gap == "genre":
        c.genres = [] if value == "none" else list(GENRES) if value == "any" else [value]
    elif gap == "headcount":
        c.stated_headcount = int(value)
        c.headcount = max(len(c.coming()), int(value))
    else:
        raise ValueError(gap)
    c.gaps = []  # only one question per round (design); everything else already defaulted
    return c
