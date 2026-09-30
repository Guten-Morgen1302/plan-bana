"""Real-Gemini eval for the "Samjha" extraction (eng E4). Costs API calls, so it is opt-in:

    RUN_EVALS=1 pytest tests/evals -q

Each case is a messy group chat plus the facts a human would read from it. Assertions are on the reduced
Constraints (what the group sees), not on the raw statements, so prompt tweaks can reword freely.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from plan_bana.extract import Line, extract_statements, reduce
from plan_bana.model import IST, WINDOWS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
pytestmark = pytest.mark.skipif(os.getenv("RUN_EVALS") != "1" or not os.getenv("GEMINI_API_KEY"),
                                reason="set RUN_EVALS=1 (and GEMINI_API_KEY) to run the real-Gemini eval")

NOW = dt.datetime(2026, 9, 30, 18, 0, tzinfo=IST).timestamp()  # Wednesday evening
SAT, SUN = "2026-10-03", "2026-10-04"
ORG = (1, "Harsh")
NAMES = {1: "Harsh", 2: "Rohan", 3: "Priya", 4: "Aman"}


def chat(*msgs: tuple[int, str]) -> list[Line]:
    return [Line(100 + i, uid, NAMES[uid], text, NOW - 3600 + i * 60) for i, (uid, text) in enumerate(msgs)]


def make_model():
    from maa.gemini import GeminiModel
    return GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), temperature=0,
                       thinking_level=os.getenv("GEMINI_THINKING", "low") or None)


async def run(lines):
    statements = await extract_statements(make_model, lines, NOW)
    return reduce(statements, lines, ORG, NOW)


async def test_budget_and_date():
    c = await run(chat((1, "sat raat kuch karte hai?"), (2, "haan sat free hu, budget 800 max yaar"),
                       (3, "same, sat chalega")))
    assert c.date == SAT and c.budget == 800 and c.window == WINDOWS["raat"]


async def test_veg_person():
    c = await run(chat((1, "saturday dinner?"), (3, "main veg hu yaad rakhna"), (2, "sat done")))
    assert c.people[3].veg is True and c.date == SAT


async def test_tie_between_days():
    c = await run(chat((2, "sat free hu"), (3, "sunday better hai mere liye, sat nahi ho payega"),
                       (4, "mujhe dono chalega")))
    assert c.date in (SAT, SUN) or "date" in c.gaps


async def test_not_coming_drops_out():
    c = await run(chat((1, "sat raat comedy?"), (2, "in"), (4, "sorry guys main nahi aa paunga is baar"),
                       (3, "sat ok")))
    assert 4 not in {p.user_id for p in c.coming()}


async def test_sarcasm_is_not_a_budget():
    c = await run(chat((2, "haan haan 10 lakh ka dinner karte hai 😂"), (3, "lol. seriously 600 tak"),
                       (1, "sat raat?"), (3, "sat ok")))
    assert c.budget == 600


async def test_english_only():
    c = await run(chat((1, "Anyone up for a music gig this Saturday night?"), (2, "Yes! Keep it under 1000 each"),
                       (3, "I'm in")))
    assert c.date == SAT and c.budget == 1000 and "music" in c.genres


async def test_devanagari():
    c = await run(chat((1, "शनिवार रात को कॉमेडी शो चलें?"), (2, "हाँ, बजट 700 तक"), (3, "मैं शाकाहारी हूँ")))
    assert c.date == SAT and c.budget == 700 and c.people[3].veg is True and "comedy" in c.genres


async def test_no_plan_talk():
    c = await run(chat((2, "bhai match dekha kal?"), (3, "haan kya game tha")))
    assert c.date is None and "date" in c.gaps and c.budget is None
