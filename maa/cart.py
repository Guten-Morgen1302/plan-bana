"""Code-owned cart writes (the LLM never calls these; design doc "Address and cart binding").

write_cart:  clear_cart -> update_cart(Mom's address, items) -> get_cart -> CartSnapshot
The snapshot (items + Swiggy's own bill total) is what Mom confirms and what approvals bind to.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from maa.agent import CartItem


class CartWriteError(Exception):
    pass


class SwiggyCart(Protocol):
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SnapshotLine:
    spin_id: str
    name: str
    quantity: int
    unit_price: float | None


@dataclass(frozen=True)
class CartSnapshot:
    lines: list[SnapshotLine]
    total: float | None           # Swiggy's "To Pay" (includes fees), authoritative for Mom's amount
    raw_cart: dict[str, Any]
    item_total: float | None = None  # "Item Total" line; total - item_total = fees + GST

    @property
    def hash(self) -> str:
        key = json.dumps(
            {"lines": sorted((ln.spin_id, ln.quantity) for ln in self.lines), "total": self.total}, sort_keys=True
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(
            {"lines": [asdict(ln) for ln in self.lines], "total": self.total, "item_total": self.item_total,
             "hash": self.hash}
        )

    @property
    def extra_charges(self) -> float | None:
        if self.total is None or self.item_total is None:
            return None
        return round(self.total - self.item_total, 2)


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_item_total(cart: dict[str, Any]) -> float | None:
    for line in (cart.get("billBreakdown") or {}).get("lineItems") or []:
        if str(line.get("label", "")).strip().lower() == "item total":
            return _to_float(line.get("value"))
    return None


def parse_total(cart: dict[str, Any]) -> float | None:
    to_pay = ((cart.get("billBreakdown") or {}).get("toPay") or {}).get("value")
    return _to_float(to_pay) if to_pay is not None else _to_float(cart.get("cartTotalAmount"))


def _cart_spin_quantities(cart: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in cart.get("items") or []:
        spin = line.get("spinId") or (line.get("variation") or {}).get("spinId")
        qty = line.get("quantity")
        if spin and isinstance(qty, int | float):
            out[str(spin)] = out.get(str(spin), 0) + int(qty)
    return out


async def write_cart(sw: SwiggyCart, address_id: str, items: list[CartItem]) -> CartSnapshot:
    cleared = await sw.call("clear_cart", {})
    if cleared.get("is_error"):
        raise CartWriteError(f"clear_cart failed: {cleared.get('text', '')[:300]}")
    payload = [
        {"spinId": i.spin_id, **({"skuId": i.sku_id} if i.sku_id else {}), "quantity": i.quantity} for i in items
    ]
    updated = await sw.call("update_cart", {"selectedAddressId": address_id, "items": payload})
    if updated.get("is_error"):
        raise CartWriteError(f"update_cart failed: {updated.get('text', '')[:300]}")
    got = await sw.call("get_cart", {})
    cart = got.get("parsed")
    if got.get("is_error") or not isinstance(cart, dict):
        raise CartWriteError(f"get_cart failed after update: {got.get('text', '')[:300]}")
    if cart.get("cartAbsent"):
        raise CartWriteError("cart is empty after update_cart")

    in_cart = _cart_spin_quantities(cart)
    wanted = {i.spin_id: i.quantity for i in items}
    if in_cart and in_cart != wanted:
        # Swiggy changed something (stock/max qty). Mom must confirm what is really in the cart.
        by_spin = {i.spin_id: i for i in items}
        lines = [
            SnapshotLine(s, by_spin[s].name if s in by_spin else s, q, by_spin[s].price if s in by_spin else None)
            for s, q in in_cart.items()
        ]
    else:
        lines = [SnapshotLine(i.spin_id, i.name, i.quantity, i.price) for i in items]
    return CartSnapshot(lines=lines, total=parse_total(cart), raw_cart=cart, item_total=parse_item_total(cart))


async def live_snapshot(sw: SwiggyCart) -> tuple[CartSnapshot, str | None]:
    """Re-read the real cart right before ordering. Returns (snapshot, selectedAddress id)."""
    got = await sw.call("get_cart", {})
    cart = got.get("parsed")
    if got.get("is_error") or not isinstance(cart, dict):
        raise CartWriteError(f"get_cart failed: {got.get('text', '')[:300]}")
    names = {str(i.get("spinId")): f"{i.get('itemName', '')} {i.get('itemVariant', '')}".strip() for i in cart.get("items") or []}
    prices = {str(i.get("spinId")): _to_float(i.get("discountedFinalPrice")) for i in cart.get("items") or []}
    lines = [SnapshotLine(s, names.get(s) or s, q, prices.get(s)) for s, q in _cart_spin_quantities(cart).items()]
    snap = CartSnapshot(lines=lines, total=parse_total(cart), raw_cart=cart, item_total=parse_item_total(cart))
    return snap, cart.get("selectedAddress")


def same_address(cart_address: str | None, address_id: str) -> bool:
    """get_addresses ids look like "<base>__<suffix>"; get_cart reports only "<base>".
    Compare the base part (observed live 2026-09-29)."""
    if not cart_address or not address_id:
        return False
    return cart_address.split("__", 1)[0] == address_id.split("__", 1)[0]
