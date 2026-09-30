import datetime as dt

import pytest

from plan_bana import copy
from plan_bana.model import IST, Constraints, EventFacts, Person, PlanOption, RestaurantFacts


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=IST).timestamp()


def option(idx=1, name="Pizza Express", event=True, per_head=750, fit=None, title="Comedy + Pizza"):
    ev = EventFacts("100116693", "Comedy In Thane", "1385", "Backspace Thane", 19.21, 72.98, "100418702",
                    ts(2026, 10, 3, 20, 30), ts(2026, 10, 3, 22), False, 249, "Majiwada", 4.6) if event else None
    r = RestaurantFacts("864731", name, ["Italian"], 1000, 1.2, event, "Mulund West", 4.6)
    return PlanOption(idx, title, r, int(ts(2026, 10, 3, 22, 30)), 1, "864731-735129", ev, per_head, False,
                      fit if fit is not None else [("Rohan", "veg", "ok"), ("Priya", "budget", "ok")])


# ---------- time + money (eng E9, design DR11) ----------

def test_time_labels_use_ist_and_parts_of_day():
    assert copy.time_label(ts(2026, 10, 3, 22)) == "raat 10 baje"
    assert copy.time_label(ts(2026, 10, 3, 22, 30)) == "raat 10:30 baje"
    assert copy.time_label(ts(2026, 10, 3, 17, 15)) == "shaam 5:15 baje"
    assert copy.time_label(ts(2026, 10, 3, 13)) == "dopahar 1 baje"
    assert copy.time_label(ts(2026, 10, 3, 9), approx=True) == "~subah 9 baje"


def test_ist_from_utc_timestamp_near_midnight():
    # 18:45 UTC Fri = 00:15 IST Sat: the label must be Saturday's, not Friday's
    utc = dt.datetime(2026, 10, 2, 18, 45, tzinfo=dt.UTC).timestamp()
    assert dt.datetime.fromtimestamp(utc, IST).strftime("%a") == "Sat"
    assert copy.time_label(utc) == "raat 12:15 baje"


def test_day_and_money_labels():
    assert copy.day_label("2026-10-03") == "Sat 3 Oct"
    assert copy.money(750) == "₹750" and copy.money(1500) == "₹1,500" and copy.money(None) == "?"


# ---------- buttons (eng E6) ----------

def test_callback_roundtrip_and_64_byte_cap():
    data = copy.encode("v", "abcd2345", 3)
    assert data == "v:abcd2345:3" and len(data.encode()) <= 64
    cb = copy.decode(data)
    assert (cb.action, cb.rid, cb.arg) == ("v", "abcd2345", "3")
    with pytest.raises(ValueError):
        copy.encode("v", "abcd2345", "x" * 80)
    with pytest.raises(ValueError):
        copy.encode("zz", "abcd2345")


def test_decode_rejects_garbage_and_old_formats():
    assert copy.decode(None) is None
    assert copy.decode("approve:uuid:hash") is None  # old Maa ka Swiggy buttons
    assert copy.decode("v::1") is None


def test_every_mockup_button_fits():
    rid = "abcd2345"
    c = Constraints(date="2026-10-03", people={1: Person(1, "Rohan")}, headcount=4)
    sets = [
        copy.samjha(c, 1, "Harsh", rid)[1],
        copy.gap_question("date", 1, "Harsh", rid, dt.date(2026, 10, 1), [])[1],
        copy.gap_question("budget", 1, "Harsh", rid, dt.date(2026, 10, 1), [])[1],
        copy.plans_message([option(1), option(2), option(3)], rid, {1: 12}, 4, 4)[1],
        copy.control_voting(1, "Harsh", rid, 1, 2, [])[1],
        copy.control_voting(1, "Harsh", rid, 1, 2, [1, 2, 3])[1],
        copy.control_rsvp(1, "Harsh", rid, 3, 1)[1],
        copy.control_confirm(1, "Harsh", rid, option(), 4)[1],
        copy.control_show_gone(1, "Harsh", rid)[1],
        copy.control_slot_gone(1, "Harsh", rid, 1791045900)[1],
        copy.rsvp_message(option(), rid, [], [], [])[1],
        copy.replace_prompt(1, "Harsh", rid)[1],
    ]
    for rows in sets:
        for row in rows:
            for label, data in row:
                assert len(data.encode()) <= 64 and copy.decode(data) is not None
                assert len(label) <= 40


# ---------- messages (design DR1/DR5/DR6/DR13, eng E11) ----------

def test_samjha_marks_assumptions_and_mentions_organizer():
    c = Constraints(date="2026-10-03", people={1: Person(1, "Rohan"), 2: Person(2, "Priya", veg=True)},
                    headcount=4, budget=800, genres=["comedy"], assumed=["area → Mulund West (pin se)"])
    text, rows = copy.samjha(c, 9, "Harsh", "abcd2345")
    assert "Sat 3 Oct · raat 7–11:30 baje" in text
    assert "4 log (2 chat mein): Rohan, Priya" in text
    assert "₹800/head tak · veg: Priya" in text and "Comedy" in text
    assert "🤖 Maan liya: area → Mulund West (pin se)" in text
    assert 'tg://user?id=9' in text
    assert [lbl for lbl, _ in rows[0]] == ["✅ Sahi hai", "✏️ Badlo"]


def test_samjha_escapes_html_in_names():
    c = Constraints(date="2026-10-03", people={1: Person(1, "<b>Evil</b>")}, headcount=1)
    text, _ = copy.samjha(c, 1, "<script>", "abcd2345")
    assert "<b>Evil" not in text and "&lt;b&gt;Evil" in text and "<script>" not in text


def test_plan_block_fit_words_and_costs():
    p = option(fit=[("Rohan", "veg", "ok"), ("Priya", "budget", "unknown"), ("Aman", "veg", "bad")])
    text = "\n".join(copy.plan_block(p))
    assert "1 · Comedy + Pizza" in text
    assert "🎭 Sat raat 8:30 baje · Comedy In Thane · Backspace Thane" in text
    assert "🍽 raat 10:30 baje · Pizza Express · 1.2 km · FREE table" in text
    assert "~₹750/head (ticket ₹249 + khana ~₹501)" in text
    assert "Priya: budget ? pata nahi" in text and "Aman: veg ✗ nahi" in text


def test_all_fit_collapses():
    assert "Sab fit ✓ (2/2)" in "\n".join(copy.plan_block(option()))


def test_dinner_only_block_has_day_and_no_ticket():
    text = "\n".join(copy.plan_block(option(event=False, title="Sirf dinner")))
    assert "🍽 Sat raat 10:30 baje · Pizza Express · FREE table" in text and "🎭" not in text


def test_long_names_are_truncated_and_message_capped():
    long = "X" * 120
    opts = [option(i, name=long, title=long) for i in (1, 2, 3)]
    text, rows = copy.plans_message(opts, "abcd2345", {}, 0, 4)
    assert len(text) <= copy.MAX_PLANS_CHARS
    assert "X" * 41 not in text
    assert all(len(lbl) <= 40 for row in rows for lbl, _ in row)


def test_vote_buttons_show_counts_one_per_row():
    _, rows = copy.plans_message([option(1), option(2)], "abcd2345", {1: 2}, 2, 4)
    assert rows == [[("1 · Comedy + Pizza (2)", "v:abcd2345:1")], [("2 · Comedy + Pizza (0)", "v:abcd2345:2")]]


def test_control_rsvp_hides_book_below_two():
    text, rows = copy.control_rsvp(1, "Harsh", "abcd2345", 1, 0)
    assert "Sirf 1 log?" in text and not any("book" in lbl for row in rows for lbl, _ in row)
    _, rows = copy.control_rsvp(1, "Harsh", "abcd2345", 3, 1)
    assert any(lbl == "🍽 4 logon ki table book karo" for row in rows for lbl, _ in row)


def test_rsvp_buttons_ticket_aware_vs_dinner():
    _, ev_rows = copy.rsvp_message(option(), "abcd2345", [], [], [])
    assert [r[0][0] for r in ev_rows] == ["🎟 Ticket le liya", "🙋 Aa raha, ticket baaki", "❌ Nahi aa paunga"]
    _, din_rows = copy.rsvp_message(option(event=False), "abcd2345", [], [], [])
    assert [lbl for lbl, _ in din_rows[0]] == ["🙋 Aa raha", "❌ Nahi aa paunga"]


def test_final_card_leads_with_celebration_and_test_footer_last():
    text = copy.final_card(option(), ["Harsh", "Rohan", "Priya"], 1, ["Priya"], "TEST_ONLY")
    lines = text.splitlines()
    assert lines[0] == "🎉 Sat ka plan pakka!"
    assert "4 logon ki table" in text and "Harsh, Rohan, Priya + 1 guest" in text
    assert "🎟 Ticket baaki: Priya" in text and "google.com/maps" in text
    assert lines[-1] == "🧪 Test mode: table asli mein book nahi hui"


def test_at_most_one_emoji_per_line_in_final_card():
    import re
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for line in copy.final_card(option(), ["A"], 0, [], "TEST_ONLY").splitlines():
        assert len(emoji.findall(line)) <= 1, line
