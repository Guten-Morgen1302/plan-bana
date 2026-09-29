"""Start the webhook server and the ngrok tunnel on the fixed dev domain.

  python scripts/serve.py        # Ctrl+C stops both

Meta webhook callback URL = PUBLIC_BASE_URL + /webhook/whatsapp
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
PORT = int(os.getenv("PORT", "8000"))


def main() -> int:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    ngrok = os.getenv("NGROK_BIN") or "ngrok"
    if not base:
        sys.exit("Set PUBLIC_BASE_URL in .env (your ngrok dev domain).")
    tunnel = subprocess.Popen([ngrok, "http", str(PORT), "--url", base, "--log", "false"])
    print(f"Webhook callback URL: {base}/webhook/whatsapp", flush=True)
    try:
        return subprocess.call(
            [sys.executable, "-m", "uvicorn", "maa.app:app", "--port", str(PORT), "--workers", "1"], cwd=ROOT
        )
    except KeyboardInterrupt:
        return 0
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    sys.exit(main())
