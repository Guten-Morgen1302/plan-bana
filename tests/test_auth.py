import base64
import hashlib
import threading
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from maa.auth import (
    AuthError,
    CredentialStore,
    SwiggyCredentials,
    authorize_url,
    make_pkce,
    wait_for_callback,
)

REDIRECT = "http://localhost:8799/callback"


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = make_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_has_required_params():
    url = authorize_url("https://mcp.swiggy.com", "cid", REDIRECT, "chal", "st")
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).path == "/auth/authorize"
    assert q["code_challenge_method"] == ["S256"]
    assert q["client_id"] == ["cid"] and q["state"] == ["st"] and q["scope"] == ["mcp:tools"]


def test_credentials_validity_uses_refresh_margin():
    now = 1_000_000.0
    assert SwiggyCredentials("c", REDIRECT, "tok", now + 3600).is_valid(now)
    assert not SwiggyCredentials("c", REDIRECT, "tok", now + 30).is_valid(now)  # inside 60 s margin
    assert not SwiggyCredentials("c", REDIRECT, None, now + 3600).is_valid(now)


def test_store_roundtrip(tmp_path):
    store = CredentialStore(tmp_path / "s" / "swiggy.json")
    assert store.load() is None
    store.save(SwiggyCredentials("c", REDIRECT, "tok", 123.0))
    assert store.load() == SwiggyCredentials("c", REDIRECT, "tok", 123.0)


def _hit_later(url: str) -> None:
    def go():
        time.sleep(0.3)
        try:
            urllib.request.urlopen(url, timeout=5)
        except HTTPError:
            pass

    threading.Thread(target=go, daemon=True).start()


def test_callback_returns_code_on_matching_state():
    _hit_later(f"{REDIRECT}?code=abc&state=good")
    assert wait_for_callback(REDIRECT, "good", timeout_s=5) == "abc"


def test_callback_rejects_state_mismatch():
    _hit_later(f"{REDIRECT}?code=abc&state=evil")
    with pytest.raises(AuthError, match="state mismatch"):
        wait_for_callback(REDIRECT, "good", timeout_s=5)


def test_callback_times_out():
    with pytest.raises(AuthError, match="no login callback"):
        wait_for_callback(REDIRECT, "good", timeout_s=0.5)
