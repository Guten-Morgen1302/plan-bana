"""Day-1 probe against the REAL Swiggy account (there is no sandbox).

  python scripts/probe.py login                       # browser: phone + OTP
  python scripts/probe.py status                      # token validity
  python scripts/probe.py tools [--server im]         # list tool names
  python scripts/probe.py call <tool> '<json args>'   # read/cart tools only
  python scripts/probe.py place <tool> '<json args>'  # real order: asks you to type PLACE

Every call's raw response is saved under probe_out/ (gitignored: contains real addresses).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.auth import CredentialStore, login
from maa.swiggy import PLACE_ORDER_TOOLS, connect

load_dotenv(ROOT / ".env")
BASE_URL = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
REDIRECT_URI = os.getenv("SWIGGY_REDIRECT_URI", "http://localhost:8765/callback")
STORE = CredentialStore(ROOT / ".secrets" / "swiggy.json")
OUT = ROOT / "probe_out"


def require_token() -> str:
    creds = STORE.load()
    if not creds or not creds.is_valid():
        sys.exit("No valid Swiggy token. Run: python scripts/probe.py login")
    return creds.access_token  # type: ignore[return-value]


def save(server: str, tool: str, args: dict, result: dict) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-{server}-{tool}.json"
    payload = {"server": server, "tool": tool, "args": args, "result": result}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


async def run_tool(server: str, tool: str, args: dict) -> None:
    async with connect(BASE_URL, server, require_token()) as sw:
        result = await sw.call(tool, args)
    path = save(server, tool, args, result)
    body = result["parsed"] if result["parsed"] is not None else (result["structured"] or result["text"])
    print(json.dumps(body, indent=2, ensure_ascii=False)[:4000])
    print(f"\n[is_error={result['is_error']}] saved -> {path.relative_to(ROOT)}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("status")
    p_tools = sub.add_parser("tools")
    p_tools.add_argument("--server", default="im")
    for name in ("call", "place"):
        p = sub.add_parser(name)
        p.add_argument("tool")
        p.add_argument("args", nargs="?", default="{}")
        p.add_argument("--server", default="im")
    ns = parser.parse_args()

    if ns.cmd == "login":
        creds = login(BASE_URL, REDIRECT_URI, STORE)
        print(f"Logged in. Token valid for {creds.seconds_left() / 3600:.1f} h.")
    elif ns.cmd == "status":
        creds = STORE.load()
        if not creds or not creds.access_token:
            print("not logged in")
        else:
            print(f"client_id={creds.client_id} valid={creds.is_valid()} hours_left={creds.seconds_left() / 3600:.1f}")
    elif ns.cmd == "tools":
        async with connect(BASE_URL, ns.server, require_token()) as sw:
            for name in sorted(await sw.tool_names()):
                print(name)
    elif ns.cmd == "call":
        if ns.tool in PLACE_ORDER_TOOLS:
            sys.exit(f"{ns.tool} places a real order. Use: probe.py place {ns.tool} ...")
        await run_tool(ns.server, ns.tool, json.loads(ns.args))
    elif ns.cmd == "place":
        if ns.tool not in PLACE_ORDER_TOOLS:
            sys.exit(f"{ns.tool} is not an order tool; use 'call'.")
        print(f"This places a REAL order on your Swiggy account: {ns.server}.{ns.tool} {ns.args}")
        if input("Type PLACE to continue: ").strip() != "PLACE":
            sys.exit("Aborted.")
        await run_tool(ns.server, ns.tool, json.loads(ns.args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252; responses carry ₹ etc.
    asyncio.run(main())
