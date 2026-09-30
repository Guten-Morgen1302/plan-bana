"""Minimal Swiggy MCP client: one persistent session per server, Bearer auth.

Day-1 scope: connect, list tools, call read/cart tools, capture raw responses.
Retry/backoff, reconciliation and the error classifier (eng D3, D7, D10) land here later.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

# Order-placing tools. The LLM never gets these (office-hours premise 4), and call() refuses them:
# the only way through is place_order(), which also needs DRY_RUN=0 in the environment.
PLACE_ORDER_TOOLS = frozenset({"checkout", "place_food_order", "book_table", "place_event_order", "confirm_order"})


class OrderBlocked(Exception):
    """Raised instead of placing a real order."""


def orders_enabled() -> bool:
    """Real orders only when DRY_RUN is explicitly "0". Missing/empty/anything else = blocked."""
    return os.getenv("DRY_RUN", "1").strip() == "0"


class SwiggySession:
    def __init__(self, session: ClientSession, server: str):
        self.session = session
        self.server = server

    async def tool_names(self) -> list[str]:
        result = await self.session.list_tools()
        return [t.name for t in result.tools]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name in PLACE_ORDER_TOOLS:
            raise OrderBlocked(f"{name} places a real order; use place_order()")
        return await self._raw_call(name, arguments)

    async def place_order(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in PLACE_ORDER_TOOLS:
            raise ValueError(f"{name} is not an order tool")
        if not orders_enabled():
            raise OrderBlocked(f"DRY_RUN is on: refusing {self.server}.{name} {arguments}")
        return await self._raw_call(name, arguments)

    async def _raw_call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments or {})
        return result_to_dict(result)


def embedded_json(text: str) -> Any | None:
    """Swiggy returns LLM-oriented prose with a JSON object appended at the end
    (e.g. get_cart, search_products, your_go_to_items). get_addresses has none.
    Return the last top-level JSON object in the text, or None."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    start = text.find("\n{")
    while start != -1:
        try:
            obj, end = decoder.raw_decode(text, start + 1)
        except json.JSONDecodeError:
            start = text.find("\n{", start + 1)
            continue
        if not text[end:].strip():
            return obj
        start = text.find("\n{", end)
    return None


def result_to_dict(result: Any) -> dict[str, Any]:
    """Flatten a CallToolResult into {is_error, structured, text, parsed}."""
    texts = [c.text for c in getattr(result, "content", []) or [] if getattr(c, "type", None) == "text"]
    text = "\n".join(texts)
    parsed = embedded_json(text)
    # mcp>=2 names these is_error / structured_content; older SDKs used the camelCase wire names.
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    return {
        "is_error": bool(is_error),
        "structured": structured,
        "text": text,
        "parsed": parsed,
    }


@asynccontextmanager
async def connect(base_url: str, server: str, access_token: str) -> AsyncIterator[SwiggySession]:
    """server: 'im', 'food', 'dineout' or 'scenes'."""
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {access_token}"})
    async with (
        http,
        streamable_http_client(f"{base_url}/{server}", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield SwiggySession(session, server)
