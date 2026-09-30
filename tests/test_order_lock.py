"""Nothing can place a real Swiggy order unless DRY_RUN=0 and place_order() is used."""

import pytest

from plan_bana.swiggy import PLACE_ORDER_TOOLS, OrderBlocked, SwiggySession, orders_enabled


class RecordingSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(name)

        class R:
            content, isError, structuredContent = [], False, None

        return R()


@pytest.fixture
def sw():
    return SwiggySession(RecordingSession(), "im")


@pytest.mark.parametrize("tool", sorted(PLACE_ORDER_TOOLS))
async def test_call_never_reaches_swiggy_for_order_tools(sw, tool, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")  # even with orders enabled, call() refuses
    with pytest.raises(OrderBlocked):
        await sw.call(tool, {"addressId": "A"})
    assert sw.session.calls == []


@pytest.mark.parametrize("value", [None, "", "1", "true", "yes", "false", " 1 "])
async def test_place_order_blocked_unless_dry_run_is_exactly_zero(sw, value, monkeypatch):
    if value is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", value)
    assert not orders_enabled()
    with pytest.raises(OrderBlocked):
        await sw.place_order("checkout", {"addressId": "A", "paymentMethod": "Cash"})
    assert sw.session.calls == []


async def test_place_order_goes_through_only_with_dry_run_zero(sw, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    await sw.place_order("checkout", {"addressId": "A"})
    assert sw.session.calls == ["checkout"]


async def test_read_tools_unaffected(sw, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    await sw.call("get_cart", {})
    assert sw.session.calls == ["get_cart"]
