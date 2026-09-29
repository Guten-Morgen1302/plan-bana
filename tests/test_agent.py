import pytest

from maa.agent import AgentOutputError, ModelTurn, ToolCall, run_turn
from tests.fake_swiggy import FakeSwiggy

ADDR = "ADDR_MOM"


class ScriptedModel:
    """Replays a fixed list of turns and records the tool results it was sent."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.received = []

    def start(self, system, user_text, tools):
        self.system, self.user_text, self.tools = system, user_text, tools
        return self.turns.pop(0)

    def send_tool_results(self, results):
        self.received.append(results)
        return self.turns.pop(0)


def calls(*pairs):
    return ModelTurn(calls=[ToolCall(n, a) for n, a in pairs])


MILK = {"spinId": "SPIN_MILK_500", "skuId": "SKU_MILK_500", "quantity": 2, "name": "Amul Taaza 500 ml", "price": 28}


async def test_happy_path_go_to_then_propose():
    sw = FakeSwiggy(go_to=["milk"])
    model = ScriptedModel([
        calls(("your_go_to_items", {})),
        calls(("propose_cart", {"items": [MILK]})),
    ])
    result = await run_turn(model, sw, ADDR, "do doodh bhej do")
    assert result.kind == "cart"
    assert [(i.spin_id, i.quantity) for i in result.items] == [("SPIN_MILK_500", 2)]
    assert sw.calls == [("your_go_to_items", {"addressId": ADDR})]
    assert "do doodh bhej do" in model.user_text


async def test_address_is_injected_even_if_model_passes_another():
    sw = FakeSwiggy()
    model = ScriptedModel([
        calls(("search_products", {"query": "bread", "addressId": "SOMEONE_ELSE"})),
        calls(("propose_cart", {"items": [{"spinId": "SPIN_BREAD_400", "quantity": 1, "name": "bread"}]})),
    ])
    await run_turn(model, sw, ADDR, "bread")
    assert sw.calls == [("search_products", {"addressId": ADDR, "query": "bread"})]


async def test_checkout_is_refused_and_never_reaches_swiggy():
    sw = FakeSwiggy(go_to=["milk"])
    model = ScriptedModel([
        calls(("your_go_to_items", {})),
        calls(("checkout", {"addressId": ADDR, "paymentMethod": "Cash"})),
        calls(("propose_cart", {"items": [MILK]})),
    ])
    result = await run_turn(model, sw, ADDR, "doodh")
    assert result.kind == "cart"
    assert [n for n, _ in sw.calls] == ["your_go_to_items"]
    assert model.received[1] == [("checkout", {"error": "tool checkout is not available"})]


async def test_invented_spin_id_is_rejected_then_corrected():
    sw = FakeSwiggy(go_to=["milk"])
    model = ScriptedModel([
        calls(("your_go_to_items", {})),
        calls(("propose_cart", {"items": [{"spinId": "MADE_UP", "quantity": 1, "name": "x"}]})),
        calls(("propose_cart", {"items": [MILK]})),
    ])
    result = await run_turn(model, sw, ADDR, "doodh")
    assert result.items[0].spin_id == "SPIN_MILK_500"
    assert "unknown spinId" in model.received[1][0][1]["error"]


async def test_ask_mom_returns_question():
    sw = FakeSwiggy()
    model = ScriptedModel([
        calls(("search_products", {"query": "eggs"})),
        calls(("ask_mom", {"question": "Ande 6 wale ya 12 wale?"})),
    ])
    result = await run_turn(model, sw, ADDR, "ande bhej do")
    assert result.kind == "question" and "12" in result.question


async def test_model_stops_without_tool_call_is_agent_output_error():
    model = ScriptedModel([ModelTurn(calls=[], text="I can't help with that.")])
    with pytest.raises(AgentOutputError, match="without a tool call"):
        await run_turn(model, FakeSwiggy(), ADDR, "doodh")


@pytest.mark.parametrize("bad", [
    {"items": []},
    {"items": [{"spinId": "SPIN_MILK_500", "quantity": 0, "name": "milk"}]},
    {"items": [{"spinId": "SPIN_MILK_500", "quantity": 50, "name": "milk"}]},
    {"items": [{"quantity": 1, "name": "milk"}]},
])
async def test_invalid_cart_raises(bad):
    sw = FakeSwiggy(go_to=["milk"])
    model = ScriptedModel([calls(("your_go_to_items", {})), calls(("propose_cart", bad))])
    with pytest.raises(AgentOutputError):
        await run_turn(model, sw, ADDR, "doodh")


async def test_step_limit():
    model = ScriptedModel([calls(("search_products", {"query": "milk"}))] * 9)
    with pytest.raises(AgentOutputError, match="no cart after"):
        await run_turn(model, FakeSwiggy(), ADDR, "doodh")


def test_only_safe_tools_are_offered():
    from maa.agent import TOOL_SPECS

    assert {t["name"] for t in TOOL_SPECS} == {"your_go_to_items", "search_products", "propose_cart", "ask_mom"}


async def test_repeat_query_hits_swiggy_once_and_parallel_results_keep_order():
    sw = FakeSwiggy(go_to=["milk"])
    model = ScriptedModel([
        calls(("your_go_to_items", {}), ("search_products", {"query": "bread"}), ("search_products", {"query": "Bread"})),
        calls(("propose_cart", {"items": [MILK]})),
    ])
    await run_turn(model, sw, ADDR, "doodh aur bread")
    assert [n for n, _ in sw.calls] == ["your_go_to_items", "search_products"]
    assert [name for name, _ in model.received[0]] == ["your_go_to_items", "search_products", "search_products"]
    assert model.received[0][1] == model.received[0][2]


async def test_search_cap_returns_error_instead_of_calling_swiggy():
    from maa.agent import MAX_SEARCHES_PER_TURN

    sw = FakeSwiggy()
    many = [("search_products", {"query": f"item {i}"}) for i in range(MAX_SEARCHES_PER_TURN + 2)]
    model = ScriptedModel([
        calls(*many),
        calls(("ask_mom", {"question": "Kya chahiye?"})),
    ])
    await run_turn(model, sw, ADDR, "sab kuch")
    assert len(sw.calls) == MAX_SEARCHES_PER_TURN
    assert "search limit" in model.received[0][-1][1]["error"]


def test_compact_products_keeps_ids_and_drops_noise():
    from maa.agent import compact_products

    data = {
        "products": [{
            "brand": "Amul", "displayName": "Amul Taaza", "badges": [1],
            "variations": [{"spinId": "S", "skuId": "K", "quantityDescription": "500 ml",
                            "price": {"mrp": 30, "offerPrice": 28}, "isInStockAndAvailable": True,
                            "imageUrl": "http://x", "rating": {"value": "4.6"}}],
        }],
        "similarProducts": [],
    }
    assert compact_products(data) == {"products": [{
        "product": "Amul Taaza",
        "variations": [{"spinId": "S", "skuId": "K", "size": "500 ml", "price": 28, "inStock": True}],
    }]}
    assert compact_products({"cartAbsent": True}) == {"cartAbsent": True}
