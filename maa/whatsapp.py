"""WhatsApp Cloud API: webhook signature check and inbound message parsing.

Meta signs each webhook POST body with HMAC-SHA256(app_secret) in X-Hub-Signature-256: sha256=<hex>.
Inbound payload: entry[].changes[].value.messages[] with from, id, timestamp, type and a
type-specific object (audio: {id, mime_type}, text: {body}).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


def valid_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def normalize_number(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


@dataclass(frozen=True)
class InboundMessage:
    wamid: str
    sender: str          # digits with country code
    timestamp: float     # unix seconds, when Mom sent it (CEO D7 staleness uses this)
    kind: str            # audio | text | image | sticker | ...
    text: str | None = None
    media_id: str | None = None
    mime_type: str | None = None


def parse_messages(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract messages; ignores status callbacks (sent/delivered/read) which carry no 'messages'."""
    out: list[InboundMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                kind = m.get("type", "unknown")
                media = m.get(kind) if isinstance(m.get(kind), dict) else {}
                out.append(
                    InboundMessage(
                        wamid=m["id"],
                        sender=normalize_number(m.get("from", "")),
                        timestamp=float(m.get("timestamp") or 0),
                        kind=kind,
                        text=(m.get("text") or {}).get("body"),
                        media_id=media.get("id"),
                        mime_type=media.get("mime_type"),
                    )
                )
    return out
