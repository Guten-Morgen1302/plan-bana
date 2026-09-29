from maa.swiggy import embedded_json

CART_TEXT = (
    "Your Instamart cart is empty. Add items with update_cart.\n\n"
    "⚠️ A rich UI widget may be shown to the user with this data.\n"
    '{\n  "cartTotalAmount": "0",\n  "items": [],\n  "billBreakdown": {"toPay": {"label": "To Pay", "value": "0"}},'
    '\n  "cartAbsent": true\n}'
)


def test_extracts_trailing_json_after_prose():
    obj = embedded_json(CART_TEXT)
    assert obj["cartTotalAmount"] == "0"
    assert obj["billBreakdown"]["toPay"]["value"] == "0"


def test_prose_only_returns_none():
    assert embedded_json("Found 5 saved addresses (page 1):\n1. [Home] ... (ID: abc)") is None


def test_pure_json_text():
    assert embedded_json('{"a": 1}') == {"a": 1}


def test_braces_inside_prose_are_skipped():
    text = 'Tip: pass {spinId, skuId}\n{not json}\n{"products": [{"spinId": "X1"}]}'
    assert embedded_json(text) == {"products": [{"spinId": "X1"}]}


def test_json_not_at_end_is_ignored():
    assert embedded_json('intro\n{"a": 1}\ntrailing prose') is None


def test_empty():
    assert embedded_json("") is None
