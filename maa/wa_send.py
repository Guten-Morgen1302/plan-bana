"""Send WhatsApp messages via the Cloud API (Graph v25.0)."""

from __future__ import annotations

import httpx

GRAPH = "https://graph.facebook.com/v25.0"


class NotifyError(Exception):
    pass


class WhatsAppSender:
    def __init__(self, client: httpx.AsyncClient, token: str, phone_number_id: str):
        self.client = client
        self.token = token
        self.phone_number_id = phone_number_id

    async def send_text(self, to: str, body: str) -> str:
        """Returns the outbound wamid. Only works inside the 24 h window after Mom's last message."""
        try:
            resp = await self.client.post(
                f"{GRAPH}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4096]}},
                timeout=20,
            )
        except httpx.HTTPError as e:
            raise NotifyError(f"WhatsApp send: {type(e).__name__}: {e}") from e
        if resp.status_code >= 400:
            raise NotifyError(f"WhatsApp send HTTP {resp.status_code}: {resp.text[:300]}")
        return (resp.json().get("messages") or [{}])[0].get("id", "")
