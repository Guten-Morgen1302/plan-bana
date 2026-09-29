"""FastAPI app: WhatsApp webhook (Meta).

GET  /webhook/whatsapp   Meta's verify handshake (hub.mode / hub.verify_token / hub.challenge)
POST /webhook/whatsapp   signature check -> Mom-only allowlist -> wamid dedup -> enqueue job -> 200 fast

Processing (STT, agent, replies) runs later from the jobs table, never inside the request (eng D2).
Run: uvicorn maa.app:app --port 8000   (one worker only: SQLite jobs + per-family serial claim)
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from maa.store import InboundClaim, Store, connect
from maa.whatsapp import normalize_number, parse_messages, valid_signature

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
log = logging.getLogger("maa.webhook")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

FAMILY_ID = "mom"


def start_worker_thread() -> tuple[threading.Thread, Callable[[], None]]:
    """Worker runs in its own thread + event loop + SQLite connection, so slow STT/LLM calls
    never delay the webhook's fast 200 to Meta."""
    import asyncio

    import httpx

    from maa.auth import CredentialStore
    from maa.gemini import GeminiModel
    from maa.swiggy import connect as swiggy_connect
    from maa.wa_send import WhatsAppSender
    from maa.worker import Deps, run_worker

    loop = asyncio.new_event_loop()
    stop = asyncio.Event()

    def swiggy():
        creds = CredentialStore(ROOT / ".secrets" / "swiggy.json").load()
        if not creds or not creds.is_valid():
            raise RuntimeError("Swiggy token missing/expired: run python scripts/probe.py login")
        return swiggy_connect(os.getenv("SWIGGY_BASE_URL", "https://mcp.swiggy.com"), "im", creds.access_token)

    async def main() -> None:
        async with httpx.AsyncClient() as http:
            sender = WhatsAppSender(http, os.environ["WHATSAPP_TOKEN"], os.environ["WHATSAPP_PHONE_NUMBER_ID"])
            mom = normalize_number(os.environ["MOM_WHATSAPP_NUMBER"])
            deps = Deps(
                store=Store(connect(ROOT / os.getenv("STATE_DB", "state.db"))),
                http=http,
                send_to_mom=lambda text: sender.send_text(mom, text),
                swiggy=swiggy,
                make_model=lambda: GeminiModel(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash")),
                whatsapp_token=os.environ["WHATSAPP_TOKEN"],
                sarvam_key=os.environ["SARVAM_API_KEY"],
                address_id=os.environ["MOM_ADDRESS_ID"],
                child_name=os.getenv("CHILD_NAME", "Harsh"),
            )
            await run_worker(deps, stop)

    thread = threading.Thread(target=lambda: loop.run_until_complete(main()), name="maa-worker", daemon=True)
    thread.start()
    return thread, lambda: loop.call_soon_threadsafe(stop.set)


def create_app(
    store: Store | None = None,
    *,
    run_worker_thread: bool = False,
    verify_token: str | None = None,
    app_secret: str | None = None,
    mom_number: str | None = None,
    clock=time.time,
) -> FastAPI:
    store = store or Store(connect(ROOT / os.getenv("STATE_DB", "state.db")))
    verify_token = verify_token if verify_token is not None else os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    app_secret = app_secret if app_secret is not None else os.getenv("WHATSAPP_APP_SECRET", "")
    mom = normalize_number(mom_number if mom_number is not None else os.getenv("MOM_WHATSAPP_NUMBER", ""))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stopper = None
        if run_worker_thread:
            _thread, stopper = start_worker_thread()
            log.info("worker started")
        yield
        if stopper:
            stopper()

    app = FastAPI(title="Maa ka Swiggy", lifespan=lifespan)
    app.state.store = store

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/webhook/whatsapp")
    def verify(request: Request) -> Response:
        q = request.query_params
        if verify_token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhook/whatsapp")
    async def receive(request: Request) -> Response:
        body = await request.body()
        if not valid_signature(app_secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("webhook: bad signature")
            return PlainTextResponse("bad signature", status_code=401)
        try:
            payload = await request.json()
        except ValueError:
            return PlainTextResponse("ok")  # malformed but signed: ack so Meta doesn't retry forever
        now = clock()
        for msg in parse_messages(payload):
            if not mom or msg.sender != mom:
                # CEO D5: only Mom's number is processed; never call STT/LLM for strangers.
                log.info("webhook: ignored sender %s", hashlib.sha256(msg.sender.encode()).hexdigest()[:12])
                continue
            if store.claim_inbound(msg.wamid, FAMILY_ID, now) is InboundClaim.DUPLICATE:
                continue
            store.enqueue(
                FAMILY_ID,
                "inbound",
                {
                    "wamid": msg.wamid,
                    "kind": msg.kind,
                    "text": msg.text,
                    "media_id": msg.media_id,
                    "mime_type": msg.mime_type,
                    "sent_at": msg.timestamp,
                },
                run_at=now,
                now=now,
            )
        return PlainTextResponse("ok")

    return app


def __getattr__(name: str):
    # `uvicorn maa.app:app` builds the real app lazily so tests can import create_app without env.
    if name == "app":
        return create_app(run_worker_thread=True)
    raise AttributeError(name)
