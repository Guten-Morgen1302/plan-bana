"""Voice in/out: WhatsApp media download + Sarvam speech-to-text.

Sarvam /speech-to-text (checked 2026-09-29, docs.sarvam.ai):
- multipart `file`; OGG/OPUS accepted as-is, so WhatsApp voice notes need no conversion
- model `saaras:v4` (default), mode `codemix` keeps Hinglish as Hindi words + English words
- sync endpoint is for clips under 30 s (longer notes are rejected before upload; eng R2-13)
- no transcript confidence field; only `language_probability`. So "low confidence" is decided
  downstream: no item recognised -> ask Mom to repeat (design doc, CEO D4)
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

GRAPH = "https://graph.facebook.com/v25.0"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
MAX_VOICE_SECONDS = 30
# Brand/product words Sarvam should spell the way Swiggy's catalogue does.
KEYTERMS = ["Amul", "Taaza", "Britannia", "Harvest Gold", "Aashirvaad", "Fortune", "Tata", "Parle", "Maggi", "Instamart"]


class MediaFetchError(Exception):
    pass


class ProviderError(Exception):
    """Upstream STT/TTS failure (5xx, 429, timeout). Retryable per CEO D4."""


class EmptyTranscript(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    language_probability: float | None


async def download_media(client: httpx.AsyncClient, media_id: str, token: str) -> tuple[bytes, str]:
    """WhatsApp media is two hops: GET /{media_id} for a short-lived URL, then GET that URL with the token."""
    auth = {"Authorization": f"Bearer {token}"}
    try:
        meta = await client.get(f"{GRAPH}/{media_id}", headers=auth, timeout=20)
        meta.raise_for_status()
        info = meta.json()
        blob = await client.get(info["url"], headers=auth, timeout=60)
        blob.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise MediaFetchError(f"media {media_id}: {type(e).__name__}: {e}") from e
    return blob.content, info.get("mime_type") or blob.headers.get("content-type", "audio/ogg")


async def transcribe(client: httpx.AsyncClient, audio: bytes, mime: str, api_key: str) -> Transcript:
    ext = "ogg" if "ogg" in mime or "opus" in mime else mime.split("/")[-1].split(";")[0]
    try:
        resp = await client.post(
            SARVAM_STT_URL,
            headers={"api-subscription-key": api_key},
            files={"file": (f"voice.{ext}", audio, mime.split(";")[0])},
            data={
                "model": "saaras:v4",
                "mode": "codemix",
                "language_code": "unknown",
                "keyterms": json.dumps(KEYTERMS),
            },
            timeout=45,
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"Sarvam STT: {type(e).__name__}: {e}") from e
    if resp.status_code == 429 or resp.status_code >= 500:
        raise ProviderError(f"Sarvam STT HTTP {resp.status_code}: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise ProviderError(f"Sarvam STT rejected request HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    text = (body.get("transcript") or "").strip()
    if not text:
        raise EmptyTranscript("Sarvam returned an empty transcript")
    return Transcript(text=text, language=body.get("language_code"), language_probability=body.get("language_probability"))
