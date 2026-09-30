"""Day-1 probe against the REAL Swiggy account (there is no sandbox).

  python scripts/probe.py login                       # browser: phone + OTP
  python scripts/probe.py login --public              # same, via the production redirect URI (starts ngrok)
  python scripts/probe.py status                      # token validity
  python scripts/probe.py tools [--server scenes|dineout|im|food]         # list tool names
  python scripts/probe.py call <tool> '<json args>'   # read/cart tools only
  python scripts/probe.py place <tool> '<json args>'  # real order: asks you to type PLACE

Every call's raw response is saved under probe_out/ (gitignored: contains real addresses).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plan_bana.auth import CALLBACK_PORT, CredentialStore, is_local, login
from plan_bana.swiggy import PLACE_ORDER_TOOLS, connect, orders_enabled

load_dotenv(ROOT / ".env")
BASE_URL = os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com")
REDIRECT_URI = os.getenv("SWIGGY_REDIRECT_URI", "http://localhost:8765/callback")
PUBLIC_REDIRECT_URI = os.getenv("SWIGGY_PUBLIC_REDIRECT_URI", "")
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


async def run_tool(server: str, tool: str, args: dict, *, order: bool = False) -> None:
    async with connect(BASE_URL, server, require_token()) as sw:
        result = await (sw.place_order(tool, args) if order else sw.call(tool, args))
    path = save(server, tool, args, result)
    body = result["parsed"] if result["parsed"] is not None else (result["structured"] or result["text"])
    print(json.dumps(body, indent=2, ensure_ascii=False)[:4000])
    print(f"\n[is_error={result['is_error']}] saved -> {path.relative_to(ROOT)}")


@contextmanager
def tunnel(redirect_uri: str):
    """For a public https redirect: forward its host to the local callback port with ngrok while logging in."""
    if is_local(redirect_uri):
        yield
        return
    ngrok = os.getenv("NGROK_BIN") or shutil.which("ngrok")
    if not ngrok:
        sys.exit("A public redirect URI needs ngrok (install it or set NGROK_BIN).")
    host = urlparse(redirect_uri).hostname
    proc = subprocess.Popen([ngrok, "http", f"--url=https://{host}", str(CALLBACK_PORT), "--log=stdout"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    time.sleep(4)
    if proc.poll() is not None:
        sys.exit("ngrok exited: is the auth token set (ngrok config add-authtoken ...) and the domain yours?")
    print(f"Tunnel up: https://{host} → 127.0.0.1:{CALLBACK_PORT}"
          " (ngrok may show a 'Visit Site' warning page first; click through it)")
    try:
        yield
    finally:
        proc.terminate()


async def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_login = sub.add_parser("login")
    p_login.add_argument("--public", action="store_true", help="use SWIGGY_PUBLIC_REDIRECT_URI via ngrok")
    sub.add_parser("status")
    p_tools = sub.add_parser("tools")
    p_tools.add_argument("--server", default="scenes")
    for name in ("call", "place"):
        p = sub.add_parser(name)
        p.add_argument("tool")
        p.add_argument("args", nargs="?", default="{}")
        p.add_argument("--server", default="scenes")
    ns = parser.parse_args()

    if ns.cmd == "login":
        redirect = PUBLIC_REDIRECT_URI if ns.public else REDIRECT_URI
        if not redirect:
            sys.exit("Set SWIGGY_PUBLIC_REDIRECT_URI in .env to use --public.")
        with tunnel(redirect):
            creds = login(BASE_URL, redirect, STORE)
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
        if not orders_enabled():
            sys.exit("DRY_RUN is on in .env: real orders are blocked. Set DRY_RUN=0 only when you mean it.")
        print(f"This places a REAL order on your Swiggy account: {ns.server}.{ns.tool} {ns.args}")
        if input("Type PLACE to continue: ").strip() != "PLACE":
            sys.exit("Aborted.")
        await run_tool(ns.server, ns.tool, json.loads(ns.args), order=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252; responses carry ₹ etc.
    asyncio.run(main())
