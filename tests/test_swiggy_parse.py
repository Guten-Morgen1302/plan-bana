from plan_bana.swiggy import embedded_json

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


def test_result_to_dict_reads_mcp2_snake_case_fields():
    import mcp.types as t

    from plan_bana.swiggy import result_to_dict

    res = t.CallToolResult(
        content=[t.TextContent(type="text", text="boom")], is_error=True, structured_content={"a": 1}
    )
    out = result_to_dict(res)
    assert out["is_error"] is True and out["structured"] == {"a": 1} and out["text"] == "boom"


def test_result_to_dict_ok_result_is_not_error():
    import mcp.types as t

    from plan_bana.swiggy import result_to_dict

    out = result_to_dict(t.CallToolResult(content=[t.TextContent(type="text", text="ok")]))
    assert out["is_error"] is False and out["structured"] is None
