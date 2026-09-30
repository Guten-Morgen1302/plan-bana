import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from maa.store import Store
from maa.telegram import TelegramError
from plan_bana.bot import MessageRefresher, PlanTelegram
from plan_bana.db import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def tg_with(responses):
    calls = []
    queue = list(responses)

    def handler(request):
        calls.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PlanTelegram(client, "TOKEN"), calls


# ---------------------------------------------------------------- PlanTelegram (eng E5)

async def test_429_waits_retry_after_then_succeeds(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    tg, calls = tg_with([(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}}),
                         (200, {"ok": True, "result": {"message_id": 7}})])
    assert await tg.send(1, "hi") == 7
    assert slept == [3.0] and len(calls) == 2


async def test_not_modified_is_swallowed():
    tg, _ = tg_with([(400, {"ok": False, "description": "Bad Request: message is not modified"})])
    await tg.edit(1, 2, "same")


async def test_other_errors_still_raise():
    tg, _ = tg_with([(400, {"ok": False, "description": "Bad Request: chat not found"})])
    with pytest.raises(TelegramError):
        await tg.send(1, "x")


async def test_second_429_gives_up(monkeypatch):
    async def fake_sleep(s):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    tg, _ = tg_with([(429, {"ok": False, "parameters": {"retry_after": 1}})] * 2)
    with pytest.raises(TelegramError):
        await tg.send(1, "x")


async def test_edit_sends_keyboard_and_empty_keyboard_removes_it():
    tg, calls = tg_with([(200, {"ok": True, "result": True})] * 2)
    await tg.edit(1, 2, "t", [[("A", "v:abcd2345:1")]])
    await tg.edit(1, 2, "t")
    assert calls[0][1]["reply_markup"] == {"inline_keyboard": [[{"text": "A", "callback_data": "v:abcd2345:1"}]]}
    assert calls[1][1]["reply_markup"] == {"inline_keyboard": []}
    assert calls[0][1]["parse_mode"] == "HTML"


async def test_force_reply_markup():
    tg, calls = tg_with([(200, {"ok": True, "result": {"message_id": 5}})])
    await tg.send(1, "reply karo", force_reply=True)
    assert calls[0][1]["reply_markup"] == {"force_reply": True, "selective": True}


# ---------------------------------------------------------------- MessageRefresher (eng E7)

class RecTG:
    def __init__(self):
        self.edits = []

    async def edit(self, chat, mid, text, buttons=None):
        self.edits.append(text)


@pytest.mark.parametrize("finish_first", ["A", "B"])
async def test_last_edit_shows_latest_state_in_both_orders(finish_first):
    tg, state = RecTG(), {"votes": 0}
    gate = {"A": asyncio.Event(), "B": asyncio.Event()}
    ref = MessageRefresher(tg, min_gap=0)

    async def tap(name):
        await gate[name].wait()
        state["votes"] += 1

        async def render():
            return f"votes={state['votes']}", None

        ref.mark(1, 10, render)

    tasks = [asyncio.create_task(tap("A")), asyncio.create_task(tap("B"))]
    gate[finish_first].set()
    await asyncio.sleep(0)
    gate["B" if finish_first == "A" else "A"].set()
    await asyncio.gather(*tasks)
    await ref.drain()
    assert tg.edits[-1] == "votes=2"


async def test_burst_is_coalesced_to_at_most_one_edit_per_gap():
    tg, clock = RecTG(), {"t": 0.0}
    slept = []

    async def sleep(s):
        slept.append(s)
        clock["t"] += s

    ref = MessageRefresher(tg, min_gap=1.0, sleep=sleep, clock=lambda: clock["t"])
    n = {"v": 0}

    async def render():
        return f"v={n['v']}", None

    for _ in range(10):
        n["v"] += 1
        ref.mark(1, 10, render)
    await ref.drain()
    assert len(tg.edits) <= 2 and tg.edits[-1] == "v=10"


async def test_refresh_errors_are_logged_not_raised():
    class Boom:
        async def edit(self, *a):
            raise RuntimeError("deleted")

    ref = MessageRefresher(Boom(), min_gap=0)

    async def render():
        return "x", None

    ref.mark(1, 10, render)
    await ref.drain()


# ---------------------------------------------------------------- workers (eng E3)

async def test_groups_run_in_parallel_one_group_stays_serial(tmp_path):
    import serve_plan

    store = Store(connect(tmp_path / "p.db"))
    for fam, kind in (("tg:A", "a1"), ("tg:A", "a2"), ("tg:B", "b1")):
        store.enqueue(fam, kind, {"rid": "x"}, run_at=0, now=0)
    running, overlaps, done = set(), [], asyncio.Event()
    stop = asyncio.Event()

    class R:
        async def sweep(self, now):
            return 0

        async def on_job(self, job):
            running.add(job.kind)
            overlaps.append(frozenset(running))
            await asyncio.sleep(0.05)
            running.discard(job.kind)
            if job.kind == "a2":
                done.set()
            return "ok"

    workers = [asyncio.create_task(serve_plan.worker(R(), store, f"w{i}", stop)) for i in range(3)]
    await asyncio.wait_for(done.wait(), 5)
    stop.set()
    await asyncio.gather(*workers)
    assert any({"a1", "b1"} <= s for s in overlaps)          # two groups at once
    assert not any({"a1", "a2"} <= s for s in overlaps)      # one group never overlaps itself
