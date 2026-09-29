"""Conversation agent: turns Mom's transcript into a proposed Instamart cart.

The model only ever sees these tools:
    your_go_to_items()          read   Mom's usual items at her address
    search_products(query)      read   catalogue search at her address
    propose_cart(items, note)   local  ends the turn with a cart proposal
    ask_mom(question)           local  ends the turn with one short question

Code, never the model, injects Mom's addressId, writes the cart, and places orders
(office-hours premise 4). Any other tool name is refused and reported back to the model.

    transcript ─► model ──tool call──► dispatcher ──► Swiggy (read only)
                    ▲                      │
                    └──── tool result ─────┘
                  ends when model calls propose_cart / ask_mom, or MAX_STEPS
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_STEPS = 8
MAX_TOOL_RESULT_CHARS = 6000

SYSTEM_PROMPT = """You help an Indian mother order groceries on Swiggy Instamart from a Hinglish voice note.
You will get the transcript of what she said. Work out which items she wants and in what quantity.

Rules:
- First call your_go_to_items to see what she usually buys. Prefer those exact products and pack sizes.
- For anything not in her usual items, call search_products with a short product query (e.g. "amul taaza milk").
- Only choose variations that are in stock (isInStockAndAvailable true).
- If she did not say a quantity, use 1.
- If an item is genuinely ambiguous (e.g. several very different pack sizes and no usual item to go by),
  call ask_mom with ONE short Hinglish question offering at most 2 choices. Otherwise do not ask.
- When you know the cart, call propose_cart once with every item (spinId, skuId, quantity, name, price).
- Never invent spinId or skuId values; copy them from tool results.
- You cannot place orders and must not talk about payment."""

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "your_go_to_items",
        "description": "Mom's frequently ordered Instamart items at her delivery address.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_products",
        "description": "Search Instamart products available at Mom's delivery address.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Short product query, e.g. 'brown bread'"}},
            "required": ["query"],
        },
    },
    {
        "name": "propose_cart",
        "description": "Finish: propose the full cart for Mom to confirm.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "spinId": {"type": "string"},
                            "skuId": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1, "maximum": 20},
                            "name": {"type": "string", "description": "Brand + product + pack size"},
                            "price": {"type": "number", "description": "Offer price per unit in rupees"},
                            "phrase": {"type": "string", "description": "What Mom said for this item, e.g. 'doodh'"},
                        },
                        "required": ["spinId", "quantity", "name"],
                    },
                },
                "note": {"type": "string", "description": "Anything Mom asked for that could not be found"},
            },
            "required": ["items"],
        },
    },
    {
        "name": "ask_mom",
        "description": "Finish: ask Mom one short Hinglish question (at most 2 choices).",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]

READ_TOOLS = frozenset({"your_go_to_items", "search_products"})
FINISH_TOOLS = frozenset({"propose_cart", "ask_mom"})
ALLOWED_TOOLS = READ_TOOLS | FINISH_TOOLS


class AgentOutputError(Exception):
    """The model refused, stopped without finishing, or produced an invalid cart (CEO D4)."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    calls: list[ToolCall]
    text: str = ""


class Model(Protocol):
    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> ModelTurn: ...
    def send_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> ModelTurn: ...


class SwiggyReader(Protocol):
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CartItem:
    spin_id: str
    sku_id: str | None
    quantity: int
    name: str
    price: float | None
    phrase: str | None


@dataclass
class AgentResult:
    kind: str  # "cart" | "question"
    items: list[CartItem] = field(default_factory=list)
    note: str = ""
    question: str = ""
    tool_log: list[str] = field(default_factory=list)


def parse_cart(args: dict[str, Any]) -> list[CartItem]:
    items = []
    for raw in args.get("items") or []:
        spin = str(raw.get("spinId") or "").strip()
        qty = raw.get("quantity")
        if not spin:
            raise AgentOutputError("propose_cart item without spinId")
        if not isinstance(qty, int | float) or not 1 <= int(qty) <= 20:
            raise AgentOutputError(f"propose_cart item {spin} has invalid quantity {qty!r}")
        items.append(
            CartItem(
                spin_id=spin,
                sku_id=(str(raw["skuId"]) if raw.get("skuId") else None),
                quantity=int(qty),
                name=str(raw.get("name") or spin),
                price=float(raw["price"]) if isinstance(raw.get("price"), int | float) else None,
                phrase=raw.get("phrase"),
            )
        )
    if not items:
        raise AgentOutputError("propose_cart with no items")
    return items


def _known_spin_ids(payload: Any) -> set[str]:
    """Collect every spinId that appeared in a tool result, to reject invented ids."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == "spinId" and isinstance(v, str):
                found.add(v)
            else:
                found |= _known_spin_ids(v)
    elif isinstance(payload, list):
        for v in payload:
            found |= _known_spin_ids(v)
    return found


async def run_turn(model: Model, swiggy: SwiggyReader, address_id: str, transcript: str) -> AgentResult:
    log: list[str] = []
    seen_spins: set[str] = set()
    turn = model.start(SYSTEM_PROMPT, f"Mom said: {transcript}", TOOL_SPECS)

    for _ in range(MAX_STEPS):
        if not turn.calls:
            raise AgentOutputError(f"model stopped without a tool call: {turn.text[:200]!r}")
        results: list[tuple[str, dict[str, Any]]] = []
        for call in turn.calls:
            log.append(call.name)
            if call.name not in ALLOWED_TOOLS:
                results.append((call.name, {"error": f"tool {call.name} is not available"}))
                continue
            if call.name == "ask_mom":
                question = str(call.args.get("question") or "").strip()
                if not question:
                    raise AgentOutputError("ask_mom without a question")
                return AgentResult(kind="question", question=question, tool_log=log)
            if call.name == "propose_cart":
                items = parse_cart(call.args)
                invented = [i.spin_id for i in items if i.spin_id not in seen_spins]
                if invented:
                    results.append((call.name, {"error": f"unknown spinId(s) {invented}; use ids from tool results"}))
                    continue
                return AgentResult(kind="cart", items=items, note=str(call.args.get("note") or ""), tool_log=log)
            args = {"addressId": address_id}  # injected by code; the model never chooses the address
            if call.name == "search_products":
                query = str(call.args.get("query") or "").strip()
                if not query:
                    results.append((call.name, {"error": "query is required"}))
                    continue
                args["query"] = query[:256]
            res = await swiggy.call(call.name, args)
            data = res.get("parsed")
            seen_spins |= _known_spin_ids(data)
            if res.get("is_error"):
                results.append((call.name, {"error": res.get("text", "")[:500]}))
            else:
                body = json.dumps(data, ensure_ascii=False) if data is not None else res.get("text", "")
                results.append((call.name, {"result": body[:MAX_TOOL_RESULT_CHARS]}))
        turn = model.send_tool_results(results)

    raise AgentOutputError(f"no cart after {MAX_STEPS} steps")
