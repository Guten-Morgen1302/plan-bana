import json

import httpx
import pytest

from plan_bana.telegram import TelegramBot, TelegramError


def bot_with(handler):
    return TelegramBot(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "TOKEN")


async def test_get_updates_sends_long_poll_timeout_and_offset():
    seen = {}

    def handler(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})

    updates = await bot_with(handler).get_updates(offset=5, timeout_s=25)
    assert updates == [{"update_id": 5}]
    assert seen["path"] == "/botTOKEN/getUpdates"
    assert seen["body"]["timeout"] == 25 and seen["body"]["offset"] == 5


async def test_send_with_buttons_builds_inline_keyboard():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    mid = await bot_with(handler).send(1, "hi", [[("Yes", "approve:d:h"), ("No", "cancel:d")]])
    assert mid == 9
    assert seen["body"]["reply_markup"]["inline_keyboard"][0][1] == {"text": "No", "callback_data": "cancel:d"}


async def test_api_error_raises():
    with pytest.raises(TelegramError, match="Unauthorized"):
        await bot_with(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})).send(1, "x")
