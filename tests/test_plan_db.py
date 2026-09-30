import pytest

from plan_bana.db import BUFFER_KEEP_S, ROUND_KEEP_S, PlanDB, connect

T0 = 1_790_000_000.0
CHAT = -100123


@pytest.fixture
def db(tmp_path):
    return PlanDB(connect(tmp_path / "plan.db"))


def test_buffer_keeps_24h_and_50_rows(db):
    db.add_message(CHAT, 1, 7, "Rohan", "old", T0 - BUFFER_KEEP_S - 5)
    for i in range(2, 60):
        db.add_message(CHAT, i, 7, "Rohan", f"m{i}", T0 + i)
    rows = db.recent_messages(CHAT, T0 + 100)
    assert len(rows) == 50 and rows[0]["text"] == "m10" and rows[-1]["text"] == "m59"


def test_recent_filters_stale_rows_even_without_new_inserts(db):
    db.add_message(CHAT, 1, 7, "Rohan", "last weekend", T0)
    assert db.recent_messages(CHAT, T0 + BUFFER_KEEP_S + 1) == []  # R3-9: quiet chat, old plans don't leak


def test_edit_updates_text(db):
    db.add_message(CHAT, 1, 7, "Rohan", "sat free", T0)
    assert db.edit_message(CHAT, 1, "sun free")
    assert db.recent_messages(CHAT, T0)[0]["text"] == "sun free"


def test_forget_removes_only_that_user(db):
    db.add_message(CHAT, 1, 7, "Rohan", "a", T0)
    db.add_message(CHAT, 2, 8, "Priya", "b", T0)
    rid = db.create_round(CHAT, 7, "Rohan", "EXTRACTING", T0)
    db.save_statements(rid, [(1, 7, "budget", "800"), (2, 8, "veg", "true")])
    assert db.forget_user(CHAT, 7) == 1
    assert [r["name"] for r in db.recent_messages(CHAT, T0)] == ["Priya"]
    left = db.conn.execute("SELECT user_id FROM statements").fetchall()
    assert [r[0] for r in left] == [8]


def test_notice_claimed_once(db):
    assert db.claim_notice(CHAT) and not db.claim_notice(CHAT)


def test_area_roundtrip(db):
    assert db.area(CHAT) is None
    db.set_area(CHAT, 19.17, 72.95, "Mulund")
    assert db.area(CHAT)["area"] == "Mulund"


def test_round_ids_are_8_char_base32(db):
    rid = db.create_round(CHAT, 7, "Rohan", "EXTRACTING", T0)
    assert len(rid) == 8 and rid.isalnum() and rid == rid.lower()


def test_cas_state_only_one_winner_and_terminal_sets_ended(db):
    rid = db.create_round(CHAT, 7, "Rohan", "VOTING", T0)
    assert db.cas_state(rid, "VOTING", "RSVP", T0 + 1, locked_idx=2)
    assert not db.cas_state(rid, "VOTING", "CANCELLED", T0 + 2)
    r = db.round(rid)
    assert r["state"] == "RSVP" and r["locked_idx"] == 2 and r["ended_at"] is None
    assert db.cas_state(rid, ("RSVP", "BOOK_PENDING"), "CANCELLED", T0 + 3)
    assert db.round(rid)["ended_at"] == T0 + 3
    assert db.open_round(CHAT) is None


def test_update_rejects_unknown_fields(db):
    rid = db.create_round(CHAT, 7, "Rohan", "VOTING", T0)
    with pytest.raises(ValueError):
        db.update(rid, state="BOOKED")
    db.update(rid, T0 + 5, plans=[{"a": 1}], guests=2)
    r = db.round(rid)
    assert r["plans"] == '[{"a": 1}]' and r["guests"] == 2 and r["last_activity"] == T0 + 5


def test_claim_booking_once_and_only_when_pending(db):
    rid = db.create_round(CHAT, 7, "Rohan", "RSVP", T0)
    assert not db.claim_booking(rid, T0)
    db.cas_state(rid, "RSVP", "BOOK_PENDING", T0)
    assert db.claim_booking(rid, T0) and not db.claim_booking(rid, T0)


def test_votes_move_and_tally(db):
    rid = db.create_round(CHAT, 7, "Rohan", "VOTING", T0)
    db.set_vote(rid, 7, 1, T0)
    db.set_vote(rid, 8, 1, T0)
    db.set_vote(rid, 8, 2, T0 + 1)
    assert db.tally(rid) == {1: 1, 2: 1}


def test_rsvp_toggle(db):
    rid = db.create_round(CHAT, 7, "Rohan", "RSVP", T0)
    db.set_rsvp(rid, 7, "c", T0)
    db.set_rsvp(rid, 7, "t", T0 + 1)
    assert db.rsvps(rid) == {7: "t"}


def test_purge_drops_old_rounds_everywhere_but_keeps_recent(db):
    old = db.create_round(CHAT, 7, "Rohan", "VOTING", T0)
    db.set_vote(old, 7, 1, T0)
    db.cas_state(old, "VOTING", "CANCELLED", T0)
    fresh = db.create_round(-5, 9, "Aman", "VOTING", T0)
    db.cas_state(fresh, "VOTING", "CANCELLED", T0 + ROUND_KEEP_S)
    db.purge(T0 + ROUND_KEEP_S + 10)
    assert db.round(old) is None and db.tally(old) == {} and db.events(old) == []
    assert db.round(fresh) is not None


def test_state_changes_are_logged(db):
    rid = db.create_round(CHAT, 7, "Rohan", "VOTING", T0)
    db.cas_state(rid, "VOTING", "RSVP", T0)
    kinds = [(e["kind"], e["data"]) for e in db.events(rid)]
    assert kinds[0][0] == "state" and '"to": "RSVP"' in kinds[1][1]
