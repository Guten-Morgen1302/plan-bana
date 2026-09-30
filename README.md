# Plan Bana

Add the bot to your friends' Telegram group. Chat like normal ("sat raat free hu", "budget 800 max", "veg hai main"), then type `/plan`. The bot reads the chat and says what it understood. It proposes up to 3 real plans (a Swiggy Scenes show plus a FREE Dineout table nearby, after the show ends). Friends vote, and the organizer taps Book. Built on [Swiggy Builders Club](https://mcp.swiggy.com/builders/) MCP.

**Safety design:** the LLM only gets read tools. Every ID in a plan must come from a Swiggy response, and code checks the timing, budget and veg rules. Tables are booked by plain code, and only after the organizer taps Book. `DRY_RUN=1` blocks every booking.

## Status
Design approved (office hours + CEO + eng review). Build not started.

| Part | State |
|---|---|
| Swiggy OAuth 2.1 + PKCE login (`maa/auth.py`) | done (reused) |
| Swiggy MCP client, DRY_RUN booking lock (`maa/swiggy.py`) | done (reused) |
| Durable SQLite jobs, per-group serial processing (`maa/store.py`) | done (reused) |
| Gemini adapter (`maa/gemini.py`, `maa/llm.py`), Telegram client (`maa/telegram.py`) | done (reused) |
| Draft planner (`maa/plan.py`, `scripts/try_plan.py`) | draft |
| `plan_bana/` package (buffer, extraction, checks, rounds, bot) | next |

## Setup
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use .venv/bin/python elsewhere
cp .env.example .env                               # fill in the keys
python scripts/check_keys.py                       # PASS/FAIL per API key
python scripts/probe.py login                      # Swiggy phone + OTP in the browser
python scripts/try_plan.py "saturday raat 4 log, comedy show aur phir pizza"
pytest -q
```

There is no Swiggy sandbox: every call hits production on your own account. `DRY_RUN=1` is the default.

## Docs
- [docs/design/plan-bana-design.md](docs/design/plan-bana-design.md): design, every review decision, and the implementation tasks
- [docs/swiggy/](docs/swiggy/): snapshot of the Swiggy Builders Club docs
