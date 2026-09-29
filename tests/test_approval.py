from contextlib import asynccontextmanager

import httpx
import pytest

from maa.agent import ModelTurn, ToolCall
from maa.approval import CHILD_ID_KEY, handle_child_action, handle_telegram_update, new_bind_code
from maa.store import Store, connect
from maa.swiggy import OrderBlocked
from maa.worker import Deps, handle_inbound
from tests.fake_swiggy import FakeSwiggy

ADDR = "ADDR_MOM"
T0 = 1_000_000.0
CHILD = 777
MILK = {"spinId": "SPIN_MILK_500", "skuId": "SKU_MILK_500", "quantity": 2, "name": "Amul Taaza 500 ml", "price": 28}


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)

    def start(self, *a):
        return self.turns.pop(0)

    def send_tool_results(self, _):
        return self.turns.pop(0)


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []

    async def send(self, chat_id, text, buttons=None):
        self.sent.append((chat_id, text, buttons))
        return len(self.sent)

    async def edit(self, chat_id, message_id, text):
        self.edits.append(text)

    async def answer_callback(self, cid, text=""):
        self.answers.append(text)


class OrderingSwiggy(FakeSwiggy):
    """FakeSwiggy plus place_order and the real cart's selectedAddress field."""

    def __init__(self, order_result=None, order_exc=None):
        super().__init__()
        self.order_result, self.order_exc, self.orders = order_result, order_exc, []

    def _get_cart(self, args):
        res = super()._get_cart(args)
        res["parsed"]["selectedAddress"] = self.address_id
        return res

    async def place_order(self, name, arguments):
        self.orders.append((name, arguments))
        if self.order_exc:
            raise self.order_exc
        return self.order_result or {"is_error": False, "text": "Order placed", "parsed": {"orderId": "O1"}}


@pytest.fixture
def env(tmp_path):
    store = Store(connect(tmp_path / "s.db"))
    sw = OrderingSwiggy(order_exc=OrderBlocked("DRY_RUN is on"))
    tg, mom = FakeTelegram(), []

    @asynccontextmanager
    async def swiggy():
        yield sw

    async def send(text):
        mom.append(text)

    turns = [[ModelTurn([ToolCall("search_products", {"query": "milk"})]),
              ModelTurn([ToolCall("propose_cart", {"items": [MILK]})])]]
    deps = Deps(store=store, http=httpx.AsyncClient(), send_to_mom=send, swiggy=swiggy,
                make_model=lambda: Scripted(turns.pop(0)), whatsapp_token="t", sarvam_key="k",
                address_id=ADDR, clock=lambda: T0, telegram=tg)
    return deps, store, sw, tg, mom


async def bind(deps, store):
    code = new_bind_code(store)
    upd = {"message": {"text": f"/start {code}", "from": {"id": CHILD}, "chat": {"id": CHILD}}}
    return await handle_telegram_update(deps, upd, T0)


def inbound(store, text):
    store.enqueue("mom", "inbound", {"kind": "text", "text": text, "sent_at": T0}, run_at=T0, now=T0)
    return store.claim("w", T0)


async def to_child_approval(deps, store):
    await handle_inbound(deps, j := inbound(store, "do packet doodh"))
    store.complete(j.id, "w")
    await handle_inbound(deps, j := inbound(store, "haan"))
    store.complete(j.id, "w")
    return store.open_draft("mom")


async def press(deps, store, tg, user=CHILD, data=None):
    draft = store.open_draft("mom")
    if data is None:
        data = tg.sent[-1][2][0][0][1]  # the Approve button
    cb = {"callback_query": {"id": "c1", "from": {"id": user}, "data": data,
                             "message": {"chat": {"id": CHILD}, "message_id": 5}}}
    outcome = await handle_telegram_update(deps, cb, T0)
    job = store.claim("w", T0)
    result = await handle_child_action(deps, job) if job else None
    if job:
        store.complete(job.id, "w")
    return outcome, result, draft


async def test_bind_is_single_use_and_rejects_strangers(env):
    deps, store, *_ = env
    assert await bind(deps, store) == "bound"
    assert store.get_setting(CHILD_ID_KEY) == str(CHILD)
    stranger = {"message": {"text": "/start guess", "from": {"id": 999}, "chat": {"id": 999}}}
    assert await handle_telegram_update(deps, stranger, T0) == "bind_rejected"
    assert store.get_setting(CHILD_ID_KEY) == str(CHILD)


async def test_haan_sends_child_approval_with_buttons(env):
    deps, store, _sw, tg, _mom = env
    await bind(deps, store)
    draft = await to_child_approval(deps, store)
    assert draft["state"] == "CHILD_APPROVAL_PENDING"
    _chat, text, buttons = tg.sent[-1]
    assert "Amul Taaza" in text and "₹86" in text
    assert buttons[0][0][1].startswith(f"approve:{draft['id']}:")


async def test_approve_in_dry_run_places_nothing(env):
    deps, store, sw, tg, mom = env
    await bind(deps, store)
    await to_child_approval(deps, store)
    queued, result, draft = await press(deps, store, tg)
    assert (queued, result) == ("queued", "dry_run")
    assert store.draft_state(draft["id"]) == "TEST_ONLY"
    assert "NOT placed" in tg.edits[-1] and "test" in mom[-1]
    assert sw.orders == [("checkout", {"addressId": ADDR, "paymentMethod": "Cash"})]  # attempted, blocked by lock
    assert sw.cart  # cart left intact


async def test_stranger_button_press_is_ignored(env):
    deps, store, sw, tg, _ = env
    await bind(deps, store)
    await to_child_approval(deps, store)
    outcome, result, _ = await press(deps, store, tg, user=999)
    assert outcome == "wrong_user" and result is None and sw.orders == []


async def test_cart_changed_since_confirm_blocks_order(env):
    deps, store, sw, tg, mom = env
    await bind(deps, store)
    await to_child_approval(deps, store)
    sw.cart[0]["quantity"] = 5  # Mom edited in the Swiggy app, or stock changed
    _, result, draft = await press(deps, store, tg)
    assert result == "cart_changed" and sw.orders == []
    assert store.draft_state(draft["id"]) == "CANCELLED" and "badal" in mom[-1]


async def test_cancel_clears_cart_and_tells_mom(env):
    deps, store, sw, tg, mom = env
    await bind(deps, store)
    draft = await to_child_approval(deps, store)
    _, result, _ = await press(deps, store, tg, data=f"cancel:{draft['id']}")
    assert result == "cancelled" and sw.cart == [] and sw.orders == [] and "cancel" in mom[-1]


async def test_second_press_after_decision_is_stale(env):
    deps, store, sw, tg, _ = env
    await bind(deps, store)
    await to_child_approval(deps, store)
    data = tg.sent[-1][2][0][0][1]
    await press(deps, store, tg, data=data)
    _, again, _ = await press(deps, store, tg, data=data)
    assert again == "stale_button" and len(sw.orders) == 1


async def test_real_order_path_success_tells_mom_cash_amount(env):
    deps, store, sw, tg, mom = env
    sw.order_exc = None
    await bind(deps, store)
    await to_child_approval(deps, store)
    _, result, draft = await press(deps, store, tg)
    assert result == "placed" and store.draft_state(draft["id"]) == "PLACED"
    assert "₹86" in mom[-1] and "zyada mat dena" in mom[-1]


async def test_network_error_during_checkout_needs_review_not_retry(env):
    deps, store, sw, tg, _ = env
    sw.order_exc = httpx.ReadTimeout("slow")
    await bind(deps, store)
    await to_child_approval(deps, store)
    _, result, draft = await press(deps, store, tg)
    assert result == "needs_review" and store.draft_state(draft["id"]) == "NEEDS_REVIEW" and len(sw.orders) == 1


async def test_cod_unavailable_blocks_order(env):
    deps, store, sw, tg, _mom = env
    sw.order_exc = None
    sw.cod_available = False
    await bind(deps, store)
    await to_child_approval(deps, store)
    _, result, draft = await press(deps, store, tg)
    assert result == "cod_unavailable" and sw.orders == []
    assert store.draft_state(draft["id"]) == "CANCELLED" and "Cash on Delivery" in tg.edits[-1]


async def test_checkout_is_always_cash(env):
    deps, store, sw, tg, _ = env
    sw.order_exc = None
    await bind(deps, store)
    await to_child_approval(deps, store)
    await press(deps, store, tg)
    assert sw.orders == [("checkout", {"addressId": ADDR, "paymentMethod": "Cash"})]


async def test_partial_or_pending_payment_reply_needs_review(env):
    deps, store, sw, tg, mom = env
    sw.order_exc = None
    sw.order_result = {"is_error": False, "text": "Order created, status PENDING_PAYMENT", "parsed": None}
    await bind(deps, store)
    await to_child_approval(deps, store)
    _, result, draft = await press(deps, store, tg)
    assert result == "needs_review" and store.draft_state(draft["id"]) == "NEEDS_REVIEW"
    assert "zyada mat dena" not in mom[-1]
