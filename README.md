# Plan Bana

Add the bot to your friends' Telegram group. Chat like normal ("sat raat free hu", "budget 800 max", "veg hai main"), then type `/plan`. The bot reads the chat and says what it understood ("Samjha: …"). It proposes up to 3 real plans: a Swiggy Scenes show plus a FREE Dineout table nearby after the show, or dinner-only when no show fits. Friends vote, say who's coming, and the organizer taps Book. Built on [Swiggy Builders Club](https://mcp.swiggy.com/builders/) MCP.

**Safety design**
- **Gemini only reads.** It reports what people said and picks among Swiggy results.
- **Code decides everything that matters.** It turns the chat into constraints and checks every plan: IDs must come from Swiggy's replies, the table must come after the show, and budget, veg and distance must fit.
- **Tables are booked by plain code,** only after the organizer confirms, with a live re-check of the show and slot first.
- **`DRY_RUN=1` blocks every booking** (the default).

## What happens in the group
```
friends chat ─► /plan ─► 📍 pin (first time) ─► 🧠 Samjha: Sat · raat · 4 log · ₹800/head · veg: Priya
   ─► ✅ Sahi hai ─► 3 plans (show + FREE table, checked) ─► votes (anyone) ─► 🎉 locked
   ─► RSVP (🎟 ticket / 🙋 aa raha / ❌) + organizer guests ─► 🍽 Book ─► re-check ─► table (or 🧪 test mode)
```
The bot sends at most 4 new messages per plan and edits them in place.

## Status
Built and tested end to end (DRY_RUN on).

| Part | Where | Tests |
|---|---|---|
| Round state machine, commands, jobs | `plan_bana/rounds.py` | `tests/test_plan_rounds.py` (31 full-round tests) |
| Chat → statements → constraints | `plan_bana/extract.py` | unit + 8 real-Gemini evals (`RUN_EVALS=1`) |
| Planner (Gemini tool loop over Swiggy) + checks | `plan_bana/planner.py`, `checks.py`, `corpus.py` | fake Swiggy in live formats + parsers on real fixtures |
| Messages, buttons, IST | `plan_bana/copy.py`, `model.py` | snapshot-style tests |
| Telegram client + live-message refresher | `plan_bana/bot.py` | 429/not-modified/keyboard + ordering tests |
| SQLite (buffer, rounds, votes, trail) | `plan_bana/db.py` + reused `maa/store.py` jobs | |
| Swiggy OAuth + MCP client + DRY_RUN lock | `maa/auth.py`, `maa/swiggy.py` (reused) | |

## Setup
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use .venv/bin/python elsewhere
cp .env.example .env                               # Gemini key, bot token, pin for scripts
python scripts/check_keys.py                       # PASS/FAIL per key
python scripts/probe.py login                      # Swiggy phone + OTP in the browser
pytest -q                                          # 174 tests, offline
```

Try it without Telegram, with real Gemini + real Swiggy (books nothing):
```bash
python scripts/try_plan.py "Rohan: aaj raat comedy?" "Priya: veg hu, 1000 tak"   # extraction + planning
python scripts/sim_group.py                                                     # a whole round, printed
```

Run the bot:
1. In @BotFather: `/setprivacy` → your bot → **Disable** (the bot must read the group chat).
2. Add the bot to the group. If it was already in the group, remove it and add it again.
3. `python scripts/serve_plan.py`
4. In the group: chat, then `/plan`. The first time, the organizer sends a 📍 location pin from their phone.

Other commands: `/status` (how many messages the bot holds), `/forget` (delete yours), `/area` (change the group pin), `/debug` (bot owner only, set `PLAN_OWNER_TELEGRAM_ID`).

There is no Swiggy sandbox: every call hits production on your own account. With `DRY_RUN=1` (the default) the Book step ends in "🧪 Test mode" and nothing is booked.

## Docs
- [docs/design/plan-bana-design.md](docs/design/plan-bana-design.md): design, every review decision (office hours, CEO, eng ×2, design), probe results and tasks
- [docs/swiggy/](docs/swiggy/): snapshot of the Swiggy Builders Club docs
