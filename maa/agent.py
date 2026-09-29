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

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_STEPS = 8
MAX_TOOL_RESULT_CHARS = 6000
MAX_SEARCHES_PER_TURN = 6  # hard cap; the prompt asks for one search per item

SYSTEM_PROMPT = """You help an Indian mother order groceries on Swiggy Instamart from a Hinglish voice note.
You will get the transcript of what she said. Work out which items she wants and in what quantity.

Rules:
- Be fast: she is waiting for a reply. Call your_go_to_items and one search_products per item IN THE SAME
  TURN (parallel calls). Search at most once per item; never re-search an item you already have results for.
- Prefer her usual items (your_go_to_items) with the exact same product and pack size.
- Otherwise use a short query of brand + product (e.g. "amul taaza milk", "brown bread").
- Only choose variations that are in stock (isInStockAndAvailable true).
- Indian household pack sizes: "packet"/"thaili" of milk means the standard 500 ml pouch (not a 200 ml tetra);
  bread means a regular loaf (~400 g); eggs default to 6 pcs; atta default 5 kg; oil 1 L; sugar 1 kg.
  Prefer the everyday pack over single-serve or bulk packs unless she said a size.
- Never drop an item she asked for just because the usual pack is missing. Pick the closest available
  option (same brand, other pack size; or same product, other brand) and say what you changed in `note`,
  in short Hinglish (e.g. "Amul Taaza ka 500 ml nahi mila, 200 ml ke 2 pack daale").
  Adjust quantity so the total amount is close to what she meant (2 x 200 ml ≈ one 500 ml packet is fine).
- If she did not say a quantity, use 1.
- If an item is genuinely ambiguous (e.g. several very different pack sizes and no usual item to go by),
  call ask_mom with ONE short Hinglish question offering at most 2 choices. Otherwise do not ask.
- When you know the cart, call propose_cart once with every item (spinId, skuId, quantity, name, price).
- Never invent spinId or skuId values; copy them from tool results.
- If a CURRENT CART is given, Mom has already seen it and her message is about that cart:
  keep every item she did not mention, apply her change (add / remove / quantity / swap), then call
  propose_cart with the FULL updated cart. If her message only confirms or declines without any change,
  call propose_cart with the current cart unchanged. Never ask what she wants when a cart exists.
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


def compact_products(data: Any) -> Any:
    """Keep only what the model needs to pick a variation; drops images, ratings, badges, etc.
    Cuts tool-result tokens several-fold, which is most of the agent's latency."""
    if not isinstance(data, dict) or "products" not in data:
        return data
    out = []
    for p in (data.get("products") or []) + (data.get("similarProducts") or [])[:5]:
        variations = [
            {
                "spinId": v.get("spinId"),
                "skuId": v.get("skuId"),
                "size": v.get("quantityDescription"),
                "price": (v.get("price") or {}).get("offerPrice"),
                "inStock": v.get("isInStockAndAvailable"),
            }
            for v in p.get("variations") or []
        ]
        brand, name = p.get("brand") or "", p.get("displayName") or ""
        label = name if name.lower().startswith(brand.lower()) else f"{brand} {name}".strip()
        out.append({"product": label, "variations": variations})
    return {"products": out}


async def run_turn(
    model: Model, swiggy: SwiggyReader, address_id: str, transcript: str, current_cart: list[dict[str, Any]] | None = None
) -> AgentResult:
    log: list[str] = []
    seen_spins: set[str] = set()
    cache: dict[tuple[str, str], dict[str, Any]] = {}  # same query twice -> no second Swiggy call
    searches = 0
    user_text = f"Mom said: {transcript}"
    if current_cart:
        cart_lines = "\n".join(
            f"- {c['quantity']} x {c['name']} (spinId {c['spin_id']})" for c in current_cart
        )
        user_text = f"CURRENT CART (Mom already saw this):\n{cart_lines}\n\n{user_text}"
        for c in current_cart:
            seen_spins.add(c["spin_id"])
    turn = model.start(SYSTEM_PROMPT, user_text, TOOL_SPECS)

    async def read(name: str, args: dict[str, Any]) -> dict[str, Any]:
        key = (name, args.get("query", "").lower())
        if key not in cache:
            res = await swiggy.call(name, args)
            data = res.get("parsed")
            seen_spins.update(_known_spin_ids(data))
            if res.get("is_error"):
                cache[key] = {"error": res.get("text", "")[:500]}
            else:
                body = json.dumps(compact_products(data), ensure_ascii=False) if data is not None else res.get("text", "")
                cache[key] = {"result": body[:MAX_TOOL_RESULT_CHARS]}
        return cache[key]

    for _ in range(MAX_STEPS):
        if not turn.calls:
            raise AgentOutputError(f"model stopped without a tool call: {turn.text[:200]!r}")
        results: list[tuple[str, Any]] = []
        pending: list[tuple[int, str, dict[str, Any]]] = []  # read calls run concurrently below
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
                searches += 1
                if searches > MAX_SEARCHES_PER_TURN:
                    results.append((call.name, {"error": "search limit reached; propose_cart with what you have"}))
                    continue
                args["query"] = query[:256]
            results.append((call.name, None))
            pending.append((len(results) - 1, call.name, args))

        fetched = await asyncio.gather(*(read(name, args) for _, name, args in pending))
        for (idx, name, _), payload in zip(pending, fetched, strict=True):
            results[idx] = (name, payload)
        turn = model.send_tool_results(results)

    raise AgentOutputError(f"no cart after {MAX_STEPS} steps")
