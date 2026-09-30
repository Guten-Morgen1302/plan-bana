import pytest

from maa.store import Store, connect

T0 = 1_000_000.0


@pytest.fixture
def store(tmp_path):
    return Store(connect(tmp_path / "state.db"))


def test_claim_returns_due_jobs_in_order(store):
    a = store.enqueue("fam1", "voice", {"n": 1}, run_at=T0, now=T0)
    store.enqueue("fam2", "voice", {"n": 2}, run_at=T0 + 1, now=T0)
    job = store.claim("w1", T0 + 2)
    assert job.id == a and job.payload == {"n": 1} and job.attempts == 1


def test_future_job_not_claimable(store):
    store.enqueue("fam", "voice", run_at=T0 + 60, now=T0)
    assert store.claim("w1", T0) is None


def test_same_family_is_serial_other_family_is_not(store):
    first = store.enqueue("fam1", "voice", {"note": "doodh"}, run_at=T0, now=T0)
    second = store.enqueue("fam1", "voice", {"note": "aur bread"}, run_at=T0 + 1, now=T0)
    other = store.enqueue("fam2", "voice", run_at=T0 + 2, now=T0)

    j1 = store.claim("w1", T0 + 5)
    assert j1.id == first
    # fam1 has a leased job, so its second note waits; fam2 is free.
    j2 = store.claim("w2", T0 + 5)
    assert j2.id == other
    assert store.claim("w3", T0 + 5) is None

    assert store.complete(j1.id, "w1")
    assert store.claim("w3", T0 + 6).id == second


@pytest.mark.parametrize("finish_first", ["first", "second_attempt"])
def test_overlapping_notes_both_completion_orders(store, finish_first):
    """Both schedules of two quick notes end with each processed exactly once, in order."""
    n1 = store.enqueue("fam", "voice", {"items": ["milk"]}, run_at=T0, now=T0)
    n2 = store.enqueue("fam", "voice", {"items": ["bread"]}, run_at=T0 + 1, now=T0)
    processed = []

    j = store.claim("wA", T0 + 2)
    if finish_first == "second_attempt":
        # a second worker tries while the first is mid-turn: blocked, not interleaved
        assert store.claim("wB", T0 + 3) is None
    processed.append(j.payload["items"])
    store.complete(j.id, "wA")

    j = store.claim("wB", T0 + 4)
    processed.append(j.payload["items"])
    store.complete(j.id, "wB")

    assert processed == [["milk"], ["bread"]]
    assert store.job_state(n1) == store.job_state(n2) == "done"


def test_expired_lease_is_reclaimed_after_restart(store):
    job_id = store.enqueue("fam", "commit", run_at=T0, now=T0)
    store.claim("crashed-worker", T0, lease_s=30)
    assert store.claim("new-worker", T0 + 10) is None          # still leased
    job = store.claim("new-worker", T0 + 31)                   # lease expired
    assert job.id == job_id and job.attempts == 2


def test_stale_owner_cannot_complete(store):
    store.enqueue("fam", "commit", run_at=T0, now=T0)
    old = store.claim("old", T0, lease_s=30)
    new = store.claim("new", T0 + 31)
    assert not store.complete(old.id, "old")
    assert store.complete(new.id, "new")
    assert store.job_state(new.id) == "done"


def test_fail_records_error(store):
    store.enqueue("fam", "voice", run_at=T0, now=T0)
    job = store.claim("w", T0)
    assert store.fail(job.id, "w", "ProviderError: 529")
    assert store.job_state(job.id) == "failed"


# ---------- overdue timers collapse (re-review D1) ----------

def _timer(store, draft, action, at):
    return store.enqueue("fam", "timer", {"action": action}, run_at=at, now=T0, draft_id=draft)


def test_sleep_past_cancel_runs_only_cancel(store):
    reminder = _timer(store, "d1", "reminder", T0 + 30 * 60)
    cancel = _timer(store, "d1", "cancel", T0 + 2 * 3600)
    job = store.claim("w", T0 + 6 * 3600)          # laptop slept 6 h
    assert job.id == cancel and job.payload["action"] == "cancel"
    assert store.job_state(reminder) == "skipped"


def test_three_overdue_review_reminders_collapse_to_one(store):
    ids = [_timer(store, "d2", "review_reminder", T0 + h * 3600) for h in (1, 3, 5)]
    job = store.claim("w", T0 + 10 * 3600)
    assert job.id == ids[-1]
    assert [store.job_state(i) for i in ids[:2]] == ["skipped", "skipped"]
    store.complete(job.id, "w")
    assert store.claim("w", T0 + 10 * 3600) is None


def test_single_due_timer_runs_normally_and_future_timer_waits(store):
    reminder = _timer(store, "d3", "reminder", T0 + 30 * 60)
    cancel = _timer(store, "d3", "cancel", T0 + 2 * 3600)
    job = store.claim("w", T0 + 31 * 60)
    assert job.id == reminder
    assert store.job_state(cancel) == "pending"


def test_timers_of_different_drafts_do_not_collapse(store):
    a = _timer(store, "dA", "reminder", T0)
    b = _timer(store, "dB", "reminder", T0)
    store.collapse_overdue_timers(T0 + 1)
    assert store.job_state(a) == store.job_state(b) == "pending"
