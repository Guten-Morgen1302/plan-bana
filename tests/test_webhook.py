import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from maa.app import create_app
from maa.store import Store, connect

SECRET = "app-secret"
VERIFY = "verify-me"
MOM = "919800000001"
T0 = 1_000_000.0


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def voice_payload(sender=MOM, wamid="wamid.A", ts=T0):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [{
            "from": sender, "id": wamid, "timestamp": str(int(ts)), "type": "audio",
            "audio": {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus", "voice": True},
        }]}}]}],
    }


@pytest.fixture
def env(tmp_path):
    store = Store(connect(tmp_path / "s.db"))
    app = create_app(store, verify_token=VERIFY, app_secret=SECRET, mom_number="+91 98000 00001", clock=lambda: T0)
    return TestClient(app), store


def post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post("/webhook/whatsapp", content=body,
                       headers={"x-hub-signature-256": signature or sign(body), "content-type": "application/json"})


def test_verify_handshake(env):
    client, _ = env
    ok = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_bad_signature_rejected_and_nothing_queued(env):
    client, store = env
    assert post(client, voice_payload(), signature="sha256=deadbeef").status_code == 401
    assert store.claim("w", T0) is None


def test_mom_voice_note_is_queued_once(env):
    client, store = env
    assert post(client, voice_payload()).status_code == 200
    assert post(client, voice_payload()).status_code == 200  # Meta redelivery
    job = store.claim("w", T0)
    assert job.kind == "inbound" and job.payload["media_id"] == "MEDIA1" and job.payload["sent_at"] == T0
    store.complete(job.id, "w")
    assert store.claim("w", T0) is None


def test_stranger_is_ignored(env):
    client, store = env
    assert post(client, voice_payload(sender="919999999999")).status_code == 200
    assert store.claim("w", T0) is None


def test_status_callbacks_are_acked_without_jobs(env):
    client, store = env
    status_only = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "read"}]}}]}]}
    assert post(client, status_only).status_code == 200
    assert store.claim("w", T0) is None
