# Maa ka Swiggy

Mom sends a Hinglish WhatsApp voice note ("doodh, bread, ande bhej do"). An AI agent builds a Swiggy Instamart cart, reads it back to her as a voice note, and her child approves the order on Telegram. Built on [Swiggy Builders Club](https://mcp.swiggy.com/builders/) MCP.

**Safety design:** the LLM only gets read and cart tools. Orders are placed by plain code, and only after two human approvals (Mom's "haan" plus the child's tap). Checkout is never blindly retried, so a network error can't create a duplicate order.

## Status
Work in progress (Day 1-2 of a 10-day build).

| Part | State |
|---|---|
| Swiggy OAuth 2.1 + PKCE login (`maa/auth.py`) | done |
| Swiggy MCP client + response parsing (`maa/swiggy.py`) | done |
| Durable SQLite jobs, dedup, per-family serial processing (`maa/store.py`) | done |
| Gemini agent with guarded tools (`maa/agent.py`, `maa/gemini.py`) | done |
| Commit state machine (approvals, COD checkout, reconciliation) | next |
| Telegram child bot, WhatsApp webhook, Sarvam voice | planned |

## Setup
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use .venv/bin/python elsewhere
cp .env.example .env                               # fill in the keys
python scripts/check_keys.py                       # PASS/FAIL per API key
python scripts/probe.py login                      # Swiggy phone + OTP in the browser
python scripts/try_agent.py "do packet doodh aur ek brown bread"
pytest -q
```

There is no Swiggy sandbox: every call hits production on your own account. `DRY_RUN=1` is the default, and `scripts/probe.py place ...` requires typing `PLACE`.

## Docs
- [docs/design/maa-ka-swiggy-design.md](docs/design/maa-ka-swiggy-design.md): design and every review decision
- [docs/design/test-plan.md](docs/design/test-plan.md): the test plan
- [docs/swiggy/](docs/swiggy/): snapshot of the Swiggy Builders Club docs
- [TODOS.md](TODOS.md): deferred work
