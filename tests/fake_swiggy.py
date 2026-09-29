"""In-memory stand-in for the Swiggy Instamart MCP server.

Responses copy the real shape seen in probe_out/: LLM-oriented prose followed by a
trailing JSON object, returned through maa.swiggy.result_to_dict's dict format.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from maa.swiggy import embedded_json


def _variation(spin, sku, qty_desc, name, brand, price, in_stock=True):
    return {
        "spinId": spin,
        "skuId": sku,
        "quantityDescription": qty_desc,
        "displayName": name,
        "brandName": brand,
        "price": {"mrp": price, "offerPrice": price},
        "isInStockAndAvailable": in_stock,
        "maxQuantity": 20,
    }


def _product(name, brand, *variations):
    return {"displayName": name, "brand": brand, "inStock": True, "isAvail": True, "variations": list(variations)}


CATALOGUE = {
    "milk": _product("Amul Taaza Toned Milk", "Amul", _variation("SPIN_MILK_500", "SKU_MILK_500", "500 ml", "Amul Taaza Toned Milk", "Amul", 28)),
    "bread": _product(
        "Harvest Gold Brown Bread",
        "Harvest Gold",
        _variation("SPIN_BREAD_400", "SKU_BREAD_400", "400 g", "Harvest Gold Brown Bread", "Harvest Gold", 55),
    ),
    "eggs": _product(
        "Eggoz Farm Fresh Eggs",
        "Eggoz",
        _variation("SPIN_EGG_6", "SKU_EGG_6", "6 pcs", "Eggoz Farm Fresh Eggs", "Eggoz", 69),
        _variation("SPIN_EGG_12", "SKU_EGG_12", "12 pcs", "Eggoz Farm Fresh Eggs", "Eggoz", 129),
    ),
}

ALIASES = {"doodh": "milk", "amul": "milk", "ande": "eggs", "anda": "eggs", "egg": "eggs", "brown bread": "bread"}


class FakeSwiggy:
    def __init__(self, go_to: list[str] | None = None, address_id: str = "ADDR_MOM"):
        self.address_id = address_id
        self.go_to = go_to or []
        self.cart: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        self.calls.append((name, args))
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return self._wrap(f"Unknown tool {name}", None, is_error=True)
        return handler(args)

    # ---------- tools ----------

    def _your_go_to_items(self, args):
        self._require_address(args, "addressId")
        products = [copy.deepcopy(CATALOGUE[k]) for k in self.go_to]
        return self._wrap(f"Found {len(products)} Your Go To Items.", {"nextOffset": "0", "products": products})

    def _search_products(self, args):
        self._require_address(args, "addressId")
        q = args.get("query", "").lower()
        keys = [k for k in CATALOGUE if k in q] + [v for a, v in ALIASES.items() if a in q]
        seen, products = set(), []
        for k in keys:
            if k not in seen:
                seen.add(k)
                products.append(copy.deepcopy(CATALOGUE[k]))
        return self._wrap(
            f'Found {len(products)} product(s) matching "{q}".',
            {"nextOffset": "1", "products": products, "similarProducts": []},
        )

    def _update_cart(self, args):
        self._require_address(args, "selectedAddressId")
        self.cart = [dict(i) for i in args.get("items", [])]  # replaces the whole cart, like the real server
        return self._get_cart({})

    cod_available = True

    def _get_payment_options(self, args):
        data = {"cod": {"available": self.cod_available, "id": "COD"}, "allMethods": [{"id": "COD"}]}
        return self._wrap("Payment options.", data)

    def _clear_cart(self, args):
        self.cart = []
        return self._wrap("Cart cleared.", {"cartAbsent": True, "items": []})

    def _get_cart(self, args):
        by_spin = {v["spinId"]: v for p in CATALOGUE.values() for v in p["variations"]}
        items, total = [], 0
        for line in self.cart:
            v = by_spin[line["spinId"]]
            price = v["price"]["offerPrice"]
            total += price * line["quantity"]
            items.append({"spinId": v["spinId"], "name": v["displayName"], "quantity": line["quantity"], "price": price})
        fees = 30 if items else 0  # like the real cart: item total + delivery/handling/GST lines
        data = {
            "cartTotalAmount": str(total),
            "items": items,
            "billBreakdown": {
                "lineItems": [
                    {"label": "Item Total", "value": f"₹{total:.2f}"},
                    {"label": "Delivery Partner Fee", "value": f"₹{fees:.2f}"},
                ],
                "toPay": {"label": "To Pay", "value": f"₹{total + fees}"},
            },
            "cartAbsent": not items,
        }
        return self._wrap("Your Instamart cart." if items else "Your Instamart cart is empty.", data)

    # ---------- helpers ----------

    def _require_address(self, args, key):
        assert args.get(key) == self.address_id, f"{key} must be Mom's address, got {args.get(key)!r}"

    @staticmethod
    def _wrap(prose: str, data: Any, is_error: bool = False) -> dict[str, Any]:
        text = prose if data is None else f"{prose}\n\n⚠️ A rich UI widget may be shown.\n{json.dumps(data, indent=2)}"
        return {"is_error": is_error, "structured": None, "text": text, "parsed": embedded_json(text)}
