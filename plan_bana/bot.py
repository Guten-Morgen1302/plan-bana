"""Telegram plumbing for Plan Bana: the client subclass (eng E5) and the single-flight message refresher (E7).

PlanTelegram adds, on top of plan_bana.telegram.TelegramBot (the minimal base client):
  - HTML parse mode, inline keyboards on edit (empty list/None removes the keyboard), force_reply, pin
  - 429: wait retry_after once and retry; "message is not modified": ignored; anything else: TelegramError

MessageRefresher keeps live messages (vote tally, RSVP list, organizer controls) correct under bursts:
  tap A ─┐                      one task per (chat, message):
  tap B ─┼─► mark dirty ──────► wait ≥ min_gap since last edit ─► render FROM THE DB ─► edit ─► loop while dirty
  tap C ─┘
so the last edit always shows the latest state, whatever order taps finished in, at ≤ 1 edit/s per message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from plan_bana.telegram import TelegramBot, TelegramError

log = logging.getLogger("plan_bana.bot")

Buttons = list[list[tuple[str, str]]]


def _markup(buttons: Buttons | None) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": lbl, "callback_data": data} for lbl, data in row] for row in buttons or []]}


class PlanTelegram(TelegramBot):
    async def _call(self, method: str, *, http_timeout: float = 20, **params: Any) -> Any:
        for attempt in (1, 2):
            try:
                resp = await self.client.post(f"{self.base}/{method}", json=params, timeout=http_timeout)
            except Exception as e:
                raise TelegramError(f"{method}: {type(e).__name__}: {e}") from e
            data = resp.json() if resp.content else {}
            if data.get("ok"):
                return data["result"]
            desc = str(data.get("description") or "")
            if "message is not modified" in desc:
                return None
            retry = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and attempt == 1:
                await asyncio.sleep(min(float(retry or 1), 30.0))
                continue
            raise TelegramError(f"{method}: HTTP {resp.status_code} {desc}")
        raise TelegramError(f"{method}: gave up after 429")

    async def get_updates(self, offset: int | None, timeout_s: int = 25) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout_s,
                                  "allowed_updates": ["message", "edited_message", "callback_query", "my_chat_member"]}
        if offset is not None:
            params["offset"] = offset
        return await self._call("getUpdates", http_timeout=timeout_s + 10, **params) or []

    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None, *, force_reply: bool = False,
                   reply_to: int | None = None) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
                                  "link_preview_options": {"is_disabled": True}}
        if buttons:
            params["reply_markup"] = _markup(buttons)
        elif force_reply:
            params["reply_markup"] = {"force_reply": True, "selective": True}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return (await self._call("sendMessage", **params))["message_id"]

    async def edit(self, chat_id: int, message_id: int, text: str, buttons: Buttons | None = None) -> None:
        await self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096],
                         parse_mode="HTML", link_preview_options={"is_disabled": True}, reply_markup=_markup(buttons))

    async def pin(self, chat_id: int, message_id: int) -> None:
        await self._call("pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=False)

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")


Render = Callable[[], Awaitable[tuple[str, Buttons | None] | None]]


class MessageRefresher:
    def __init__(self, tg: Any, min_gap: float = 1.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.tg, self.min_gap, self.sleep, self.clock = tg, min_gap, sleep, clock
        self._dirty: dict[tuple[int, int], Render] = {}
        self._tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._last: dict[tuple[int, int], float] = {}

    def mark(self, chat_id: int, message_id: int | None, render: Render) -> None:
        if not message_id:
            return
        key = (chat_id, message_id)
        self._dirty[key] = render
        task = self._tasks.get(key)
        if task is None or task.done():
            self._tasks[key] = asyncio.get_running_loop().create_task(self._run(key))

    async def _run(self, key: tuple[int, int]) -> None:
        while key in self._dirty:
            wait = self._last.get(key, -1e9) + self.min_gap - self.clock()
            if wait > 0:
                await self.sleep(wait)
            render = self._dirty.pop(key)
            try:
                out = await render()
                if out is not None:
                    await self.tg.edit(key[0], key[1], out[0], out[1])
            except Exception:
                log.exception("refresh %s failed", key)
            self._last[key] = self.clock()

    async def drain(self) -> None:
        while any(not t.done() for t in self._tasks.values()):
            await asyncio.gather(*[t for t in self._tasks.values() if not t.done()], return_exceptions=True)
