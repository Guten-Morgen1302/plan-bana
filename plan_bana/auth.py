"""Swiggy MCP OAuth 2.1 + PKCE, following https://mcp.swiggy.com/builders/docs/start/authenticate.md

We don't use the MCP SDK's OAuthClientProvider: Swiggy's /.well-known/oauth-protected-resource
returns 404, so the SDK falls back to expecting issuer "https://mcp.swiggy.com/" while Swiggy
advertises "https://mcp.swiggy.com/auth", and the SDK rejects the mismatch.

Flow: dynamic client registration -> browser login (phone + OTP) -> callback -> token.
No refresh tokens; the access token lives 5 days, then we re-run this flow.

Callbacks: a localhost redirect (http://localhost:8765/callback) is served directly. A public HTTPS redirect
(e.g. https://<name>.ngrok-free.app/oauth/callback) is served on 127.0.0.1:CALLBACK_PORT and reached through
a tunnel that forwards the public host to that port (scripts/probe.py login starts ngrok for it).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

SCOPE = "mcp:tools"
CLIENT_NAME = "plan-bana"
REFRESH_MARGIN_S = 60
CALLBACK_PORT = 8765  # local port behind a public (tunnelled) redirect URI


def is_local(redirect_uri: str) -> bool:
    return (urlparse(redirect_uri).hostname or "") in ("localhost", "127.0.0.1")


def listen_address(redirect_uri: str, public_port: int = CALLBACK_PORT) -> tuple[str, int]:
    """Where to bind the one-shot callback server for this redirect URI."""
    parsed = urlparse(redirect_uri)
    if is_local(redirect_uri):
        return parsed.hostname or "localhost", parsed.port or 80
    return "127.0.0.1", public_port


class AuthError(Exception):
    pass


@dataclass
class SwiggyCredentials:
    client_id: str
    redirect_uri: str
    access_token: str | None = None
    expires_at: float | None = None  # unix seconds

    def is_valid(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.access_token and self.expires_at and now < self.expires_at - REFRESH_MARGIN_S)

    def seconds_left(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return (self.expires_at or 0) - now


class CredentialStore:
    """Plain JSON file under .secrets/ (gitignored). store.py will move this into encrypted SQLite."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> SwiggyCredentials | None:
        if not self.path.exists():
            return None
        return SwiggyCredentials(**json.loads(self.path.read_text()))

    def save(self, creds: SwiggyCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(creds), indent=2))


def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register_client(base_url: str, redirect_uri: str) -> str:
    resp = httpx.post(
        f"{base_url}/auth/register",
        json={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
        timeout=20,
    )
    if resp.status_code >= 400:
        raise AuthError(f"client registration failed: HTTP {resp.status_code} {resp.text[:300]}")
    client_id = resp.json().get("client_id")
    if not client_id:
        raise AuthError(f"client registration returned no client_id: {resp.text[:300]}")
    return client_id


def authorize_url(base_url: str, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": SCOPE,
    }
    return f"{base_url}/auth/authorize?{urlencode(params)}"


def wait_for_callback(redirect_uri: str, expected_state: str, timeout_s: float = 300,
                      public_port: int = CALLBACK_PORT) -> str:
    """Serve the redirect URI's path until the login callback arrives; return the authorization code."""
    parsed = urlparse(redirect_uri)
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != parsed.path or done.is_set():
                self.send_response(404)  # favicon, health checks, or a second hit after we're done
                self.end_headers()
                return
            query = parse_qs(url.query)
            if query.get("state", [""])[0] != expected_state:
                # Not our login (a scanner or a forged redirect hitting the public URL): refuse it but keep
                # waiting, so a stray request can't abort the real login.
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if "code" in query:
                result["code"] = query["code"][0]
            else:
                result["error"] = query.get("error_description", query.get("error", ["no code"]))[0]
            ok = "code" in result
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Swiggy connected. You can close this tab." if ok else f"Login failed: {result['error']}"
            self.wfile.write(f"<h2>{msg}</h2>".encode())
            done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(listen_address(redirect_uri, public_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout_s):
            raise AuthError(f"no login callback within {int(timeout_s)} s")
    finally:
        server.shutdown()
    if "code" not in result:
        raise AuthError(f"authorization failed: {result.get('error')}")
    return result["code"]


def exchange_code(base_url: str, client_id: str, redirect_uri: str, code: str, verifier: str) -> dict:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    # Swiggy's docs show a JSON body; fall back to standard form encoding if JSON is rejected.
    resp = httpx.post(f"{base_url}/auth/token", json=body, timeout=20)
    if resp.status_code >= 400:
        resp = httpx.post(f"{base_url}/auth/token", data=body, timeout=20)
    if resp.status_code >= 400:
        raise AuthError(f"token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    token = resp.json()
    if "access_token" not in token:
        raise AuthError(f"token response has no access_token: {list(token)}")
    return token


def login(base_url: str, redirect_uri: str, store: CredentialStore, open_browser: bool = True) -> SwiggyCredentials:
    creds = store.load()
    if creds is None or creds.redirect_uri != redirect_uri:
        creds = SwiggyCredentials(client_id=register_client(base_url, redirect_uri), redirect_uri=redirect_uri)
        store.save(creds)

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)
    url = authorize_url(base_url, creds.client_id, redirect_uri, challenge, state)
    print(f"\nOpen this link and log in with your Swiggy phone number + OTP:\n{url}\n", flush=True)
    if open_browser:
        webbrowser.open(url)

    code = wait_for_callback(redirect_uri, state)
    token = exchange_code(base_url, creds.client_id, redirect_uri, code, verifier)
    creds.access_token = token["access_token"]
    creds.expires_at = time.time() + float(token.get("expires_in", 0))
    store.save(creds)
    return creds
