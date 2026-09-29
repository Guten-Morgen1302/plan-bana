from contextlib import asynccontextmanager

import httpx
import pytest

from maa.agent import ModelTurn, ToolCall
from maa.store import Store, connect
from maa.worker import (
    MSG_BUSY,
    MSG_CANCELLED,
    MSG_CONFIRM_AGAIN,
    MSG_SAY_IT,
    MSG_TRY_LATER,
    STALE_AFTER_S,
    Deps,
    handle_inbound,
)
from tests.fake_swiggy import FakeSwiggy

ADDR = "ADDR_MOM"
T0 = 1_000_000.0
MILK = {"spinId": "SPIN_MILK_500", "skuId": "SKU_MILK_500", "quantity": 2, "name": "Amul Taaza 500 ml", "price": 28}


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)

    def start(self, system, user_text, tools):
        return self.turns.pop(0)

    def send_tool_results(self, results):
        return self.turns.pop(0)


def cart_turns():
    return [
        ModelTurn(calls=[ToolCall("search_products", {"query": "milk"})]),
        ModelTurn(calls=[ToolCall("propose_cart", {"items": [MILK], "note": ""})]),
    ]


@pytest.fixture
def env(tmp_path):
    store = Store(connect(tmp_path / "s.db"))
    sw = FakeSwiggy()
    sent: list[str] = []
    turns: list = []

    @asynccontextmanager
    async def swiggy():
        yield sw

    async def send(text):
        sent.append(text)

    deps = Deps(
        store=store, http=httpx.AsyncClient(), send_to_mom=send, swiggy=swiggy,
        make_model=lambda: Scripted(turns.pop(0)), whatsapp_token="t", sarvam_key="k",
        address_id=ADDR, clock=lambda: T0,
    )
    return deps, store, sw, sent, turns


def job(store, text=None, kind="text", sent_at=T0):
    store.enqueue("mom", "inbound", {"wamid": f"w{len(text or '')}", "kind": kind, "text": text, "sent_at": sent_at},
                  run_at=T0, now=T0)
    return store.claim("w", T0)


def called(sw):
    return [n for n, _ in sw.calls]


async def test_request_writes_cart_and_reads_back(env):
    deps, store, sw, sent, turns = env
    turns.append(cart_turns())
    assert await handle_inbound(deps, job(store, "do packet doodh")) == "readback"
    assert sw.cart == [{"spinId": "SPIN_MILK_500", "skuId": "SKU_MILK_500", "quantity": 2}]
    msg = sent[-1]
    assert "2 x Amul Taaza 500 ml – ₹56" in msg and "haan" in msg
    assert "Saaman: ₹56" in msg and "Delivery, handling aur GST: ₹30" in msg and "Kul: ₹86" in msg
    assert store.open_draft("mom")["state"] == "AWAITING_PARENT"
    assert "checkout" not in called(sw)


async def test_haan_moves_to_child_approval_without_ordering(env):
    deps, store, sw, sent, turns = env
    turns.append(cart_turns())
    await handle_inbound(deps, job(store, "do packet doodh"))
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, "haan beta bhej do")) == "parent_yes"
    assert store.open_draft("mom")["state"] == "CHILD_APPROVAL_PENDING"
    assert "Harsh" in sent[-1]
    assert "checkout" not in called(sw) and sw.cart  # cart kept, nothing ordered


async def test_nahi_cancels_and_clears_cart(env):
    deps, store, sw, sent, turns = env
    turns.append(cart_turns())
    await handle_inbound(deps, job(store, "do packet doodh"))
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, "nahi")) == "parent_no"
    assert store.open_draft("mom") is None and sw.cart == [] and sent[-1] == MSG_CANCELLED


BREAD = {"spinId": "SPIN_BREAD_400", "skuId": "SKU_BREAD_400", "quantity": 1, "name": "Brown Bread", "price": 55}


def cart_with_bread_turns():
    return [
        ModelTurn(calls=[ToolCall("search_products", {"query": "bread"})]),
        ModelTurn(calls=[ToolCall("propose_cart", {"items": [MILK, BREAD]})]),
    ]


async def test_change_request_updates_cart_and_replaces_draft(env):
    deps, store, sw, _sent, turns = env
    turns.append(cart_turns())
    turns.append(cart_with_bread_turns())
    await handle_inbound(deps, job(store, "do packet doodh"))
    first = store.open_draft("mom")["id"]
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, "haan aur bread bhi")) == "readback"
    assert store.draft_state(first) == "CANCELLED"
    assert store.open_draft("mom")["id"] != first
    assert {i["spinId"] for i in sw.cart} == {"SPIN_MILK_500", "SPIN_BREAD_400"}


async def test_unclear_reply_with_same_cart_asks_plainly_and_keeps_draft(env):
    deps, store, sw, sent, turns = env
    turns.append(cart_turns())
    turns.append([ModelTurn(calls=[ToolCall("propose_cart", {"items": [MILK]})])])
    await handle_inbound(deps, job(store, "do packet doodh"))
    first = store.open_draft("mom")["id"]
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, "hmm dekho zara")) == "confirm_again"
    assert store.open_draft("mom")["id"] == first and store.draft_state(first) == "AWAITING_PARENT"
    assert sent[-1] == MSG_CONFIRM_AGAIN and sw.cart  # cart untouched


async def test_agent_question_keeps_draft_open(env):
    deps, store, sw, _sent, turns = env
    turns.append(cart_turns())
    turns.append([ModelTurn(calls=[ToolCall("ask_mom", {"question": "Kaunsa doodh?"})])])
    await handle_inbound(deps, job(store, "do packet doodh"))
    first = store.open_draft("mom")["id"]
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, "doodh ka kya")) == "asked"
    assert store.draft_state(first) == "AWAITING_PARENT" and sw.cart


@pytest.mark.parametrize(("reply", "outcome", "state"), [
    ("nahii", "parent_no", None),                                   # bug 2026-09-29: was treated as a new order
    ("theek hai order place kardo", "parent_yes", "CHILD_APPROVAL_PENDING"),  # bug: said "cart empty"
    ("ठीक है ऑर्डर प्लेस कर दो", "parent_yes", "CHILD_APPROVAL_PENDING"),
])
async def test_reported_replies(env, reply, outcome, state):
    deps, store, _sw, _sent, turns = env
    turns.append(cart_turns())
    await handle_inbound(deps, job(store, "do packet doodh"))
    store.complete(1, "w")
    assert await handle_inbound(deps, job(store, reply)) == outcome
    draft = store.open_draft("mom")
    assert (draft["state"] if draft else None) == state


async def test_busy_while_waiting_for_child(env):
    deps, store, _sw, sent, turns = env
    turns.append(cart_turns())
    await handle_inbound(deps, job(store, "doodh"))
    store.complete(1, "w")
    await handle_inbound(deps, job(store, "haan"))
    store.complete(2, "w")
    assert await handle_inbound(deps, job(store, "aur ande bhi")) == "busy"
    assert sent[-1] == MSG_BUSY


async def test_stale_note_is_not_acted_on(env):
    deps, store, sw, sent, _turns = env
    assert await handle_inbound(deps, job(store, "doodh", sent_at=T0 - STALE_AFTER_S - 1)) == "stale"
    assert sw.calls == [] and store.open_draft("mom") is None and "doodh" in sent[-1]


async def test_sticker_gets_voice_prompt(env):
    deps, store, sw, sent, _ = env
    assert await handle_inbound(deps, job(store, None, kind="sticker")) == "not_voice"
    assert sent == [MSG_SAY_IT] and sw.calls == []


async def test_agent_failure_is_visible_to_mom(env):
    deps, store, _sw, sent, turns = env
    turns.append([ModelTurn(calls=[], text="refuse")])
    assert await handle_inbound(deps, job(store, "doodh")) == "agent_failed"
    assert sent == [MSG_TRY_LATER] and store.open_draft("mom") is None


async def test_failed_readback_leaves_no_draft_waiting(env):
    deps, store, _sw, _sent, turns = env
    turns.append(cart_turns())

    async def broken(_text):
        from maa.wa_send import NotifyError
        raise NotifyError("131030 not in allowed list")

    deps.send_to_mom = broken
    with pytest.raises(Exception, match="131030"):
        await handle_inbound(deps, job(store, "doodh"))
    assert store.open_draft("mom") is None
