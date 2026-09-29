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

    @property
    def hash(self) -> str:
        key = json.dumps(
            {"lines": sorted((ln.spin_id, ln.quantity) for ln in self.lines), "total": self.total}, sort_keys=True
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps({"lines": [asdict(ln) for ln in self.lines], "total": self.total, "hash": self.hash})


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
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
    return CartSnapshot(lines=lines, total=parse_total(cart), raw_cart=cart)
