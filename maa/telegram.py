"""Minimal Telegram Bot API client for the child's side (eng D9): long polling, no public URL needed.

Binding: scripts/bind_telegram.py stores a one-time code and prints a t.me deep link.
Tapping it sends "/start <code>"; the first user presenting the code becomes the family's child.
Every button press is checked against that bound user id before anything happens.
"""

from __future__ import annotations

from typing import Any

import httpx

API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


class TelegramBot:
    def __init__(self, client: httpx.AsyncClient, token: str):
        self.client = client
        self.base = f"{API}/bot{token}"

    async def _call(self, method: str, timeout: float = 20, **params: Any) -> Any:
        try:
            resp = await self.client.post(f"{self.base}/{method}", json=params, timeout=timeout)
        except httpx.HTTPError as e:
            raise TelegramError(f"{method}: {type(e).__name__}: {e}") from e
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            raise TelegramError(f"{method}: HTTP {resp.status_code} {data.get('description')}")
        return data["result"]

    async def get_updates(self, offset: int | None, timeout_s: int = 25) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout_s, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return await self._call("getUpdates", timeout=timeout_s + 10, **params)

    async def send(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in buttons]
            }
        return (await self._call("sendMessage", **params))["message_id"]

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        await self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096])
