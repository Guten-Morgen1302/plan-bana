"""Every word the bot says (design DR5): one Hinglish voice, Telegram HTML, compact buttons.

Rules (design DR5/DR11/DR13, eng E6/E11):
- Roman-script Hinglish, times as "Sat 5 Oct · raat 10 baje", money "₹750", estimates "~".
- At most one emoji per line, as a line marker; every ✓/✗/? has a word next to it.
- Button payloads are "<action>:<round8>:<arg>" and must fit Telegram's 64-byte callback_data cap.
- Names are truncated to 40 chars; a rendered plans message must stay under 3500 chars.
"""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass

from plan_bana.model import IST, Constraints, PlanOption

Buttons = list[list[tuple[str, str]]]

MAX_CALLBACK_BYTES = 64
MAX_NAME = 40
MAX_PLANS_CHARS = 3500
MAX_BUTTON_TITLE = 18

# One-letter actions keep callback_data tiny (eng E6).
ACTIONS = {
    "s": "samjha_ok",
    "e": "badlo",
    "q": "gap_answer",
    "v": "vote",
    "l": "lock",
    "c": "cancel",
    "r": "rsvp",
    "g": "guest",
    "b": "book_now",
    "k": "book_confirm",
    "w": "book_wait",
    "t": "table_only",
    "a": "alt_slot",
    "n": "replace",
}


# ---------- formatting ----------

def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def trunc(s: str, n: int = MAX_NAME) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{esc(trunc(name, 30))}</a>'


def money(n: float | None) -> str:
    return "?" if n is None else f"₹{round(n):,}"


def day_label(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def part_of_day(hour: int) -> str:
    if 4 <= hour < 12:
        return "subah"
    if 12 <= hour < 16:
        return "dopahar"
    if 16 <= hour < 19:
        return "shaam"
    return "raat"


def time_label(ts: float, approx: bool = False) -> str:
    t = dt.datetime.fromtimestamp(ts, IST)
    h12 = t.hour % 12 or 12
    clock = f"{h12}" if t.minute == 0 else f"{h12}:{t.minute:02d}"
    return f"{'~' if approx else ''}{part_of_day(t.hour)} {clock} baje"


def minutes_label(m: int) -> str:
    h, mm = divmod(m, 60)
    h12 = h % 12 or 12
    return f"{h12}" if mm == 0 else f"{h12}:{mm:02d}"


def window_label(c: Constraints) -> str:
    start, end = c.window
    return f"{c.window_label} {minutes_label(start)}–{minutes_label(end)} baje"


# ---------- buttons (eng E6) ----------

def encode(action: str, rid: str, arg: object = "") -> str:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    data = f"{action}:{rid}:{arg}"
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data too long ({len(data.encode())} bytes): {data!r}")
    return data


@dataclass(frozen=True)
class Callback:
    action: str
    rid: str
    arg: str


def decode(data: str | None) -> Callback | None:
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] not in ACTIONS or not parts[1]:
        return None
    return Callback(parts[0], parts[1], parts[2])


# ---------- fixed lines ----------

JOIN_NOTICE = (
    "👋 Namaste! Main Plan Bana hu.\n"
    "/plan likhne pe main pichle 24 ghante ki chat padhta hu aur plan banata hu.\n"
    "Chat plan banane ke liye Google Gemini ko jaati hai. Chat 24 ghante baad aur plan ki details 7 din baad delete.\n"
    "Apne messages hatane ke liye: /forget"
)
READING = "🧠 Chat padh raha hu…"
EMPTY_CHAT = "🤔 Abhi chat mein plan ki baat nahi dikhi. Kab, kitne log, budget? Likho phir /plan"
EXTRACT_FAILED = "😕 Samajh nahi aaya. Thoda detail mein likho (kab, kitne log, budget) phir /plan"
SWIGGY_DOWN = "😕 Swiggy abhi jawab nahi de raha. 2 minute baad /plan karo"
FORGOT = "🧹 Ho gaya. Tumhare {n} message hata diye."
STATUS = "📊 Mere paas is group ke {n} message hain (pichle 24 ghante)."
PIN_SAVED = "📍 Area set: {label}"

TOAST_ONLY_ORGANIZER = "Yeh sirf {name} (organizer) dabaa sakta hai"
TOAST_CLOSED = "Yeh plan band ho gaya"
TOAST_OLD = "Yeh button purana hai"
TOAST_VOTED = "Vote ho gaya"
TOAST_RSVP = "Note kar liya"
TOAST_WORKING = "Kar raha hu…"
TOAST_BOOKING = "Booking chal rahi hai"


def pin_prompt(org_id: int, org_name: str) -> str:
    return (f"📍 {mention(org_id, org_name)}, group ka area batao: phone se 📎 → Location bhejo "
            "(desktop se nahi hota). Phir main plan shuru karunga.")


def swiggy_login_expired(owner: str) -> str:
    return f"🔑 Swiggy login expire ho gaya. {esc(owner)} ko bolo login kare (scripts/probe.py login)."


def plan_running(org_name: str) -> str:
    return f"Ek plan chal raha hai (organizer: {esc(org_name)}). Uske khatam hone ka wait karo."


def replace_prompt(org_id: int, org_name: str, rid: str) -> tuple[str, Buttons]:
    return (f"{mention(org_id, org_name)}, purana plan band karke naya shuru karein?",
            [[("Haan, naya plan", encode("n", rid, "y")), ("Nahi", encode("n", rid, "n"))]])


# ---------- Samjha (design DR5/DR6) ----------

def samjha(c: Constraints, org_id: int, org_name: str, rid: str) -> tuple[str, Buttons]:
    lines = ["🧠 Samjha:"]
    if c.date:
        lines.append(f"{day_label(c.date)} · {window_label(c)}")
    people = c.coming()
    names = ", ".join(esc(trunc(p.name, 20)) for p in people)
    count = f"{c.headcount} log" + (f" ({len(people)} chat mein)" if c.headcount != len(people) else "")
    lines.append(f"{count}: {names}" if names else count)
    veg = [esc(trunc(p.name, 20)) for p in people if p.veg]
    money_line = f"{money(c.budget)}/head tak" if c.budget else "budget: koi limit nahi"
    lines.append(money_line + (f" · veg: {', '.join(veg)}" if veg else ""))
    lines.append(" + ".join(g.capitalize() for g in c.genres) if c.genres else "Sirf dinner")
    if c.assumed:
        lines.append("🤖 Maan liya: " + "; ".join(esc(a) for a in c.assumed))
    lines.append(f"{mention(org_id, org_name)}, sahi hai?")
    return "\n".join(lines), [[("✅ Sahi hai", encode("s", rid)), ("✏️ Badlo", encode("e", rid))]]


GAP_QUESTIONS = {
    "date": "kaunsa din?",
    "time": "kis time?",
    "budget": "budget kitna (per head)?",
    "genre": "kya karna hai?",
    "headcount": "kitne log?",
}


def gap_buttons(gap: str, rid: str, options: list[tuple[str, str]]) -> Buttons:
    return [[(label, encode("q", rid, f"{gap}={value}")) for label, value in options]]


def gap_options(gap: str, today: dt.date, tie_dates: list[str]) -> list[tuple[str, str]]:
    if gap == "date":
        dates = tie_dates or [(today + dt.timedelta(days=i)).isoformat() for i in range(3)]
        return [(day_label(d), d) for d in dates[:4]]
    if gap == "time":
        return [("Shaam", "shaam"), ("Raat", "raat")]
    if gap == "budget":
        return [("₹500", "500"), ("₹800", "800"), ("₹1200", "1200"), ("Koi limit nahi", "0")]
    if gap == "genre":
        return [("Comedy", "comedy"), ("Music", "music"), ("Kuch bhi", "any"), ("Sirf dinner", "none")]
    if gap == "headcount":
        return [("2", "2"), ("3", "3"), ("4", "4"), ("5+", "5")]
    raise ValueError(gap)


def gap_question(gap: str, org_id: int, org_name: str, rid: str, today: dt.date,
                 tie_dates: list[str]) -> tuple[str, Buttons]:
    text = f"🧠 {mention(org_id, org_name)}, ek cheez batao: {GAP_QUESTIONS[gap]}"
    return text, gap_buttons(gap, rid, gap_options(gap, today, tie_dates))


def badlo_prompt(org_id: int, org_name: str) -> str:
    return f"✏️ {mention(org_id, org_name)}, kya galat hai? Isi message pe reply karo."


# ---------- progress + plans (design DR1/DR2/DR9) ----------

PROGRESS = {
    "events": "🔎 Shows dhoondh raha hu…",
    "tables": "🍽 Tables check kar raha hu…",
    "checks": "✅ Plans check kar raha hu…",
}


def _fit_line(p: PlanOption) -> str:
    bad = [(n, f, s) for n, f, s in p.fit if s != "ok"]
    total = len({n for n, _, _ in p.fit})
    if not bad:
        return f"Sab fit ✓ ({total}/{total})" if total else ""
    words = {"veg": "veg", "budget": "budget", "date": "din"}
    parts = []
    for n, f, s in bad[:3]:
        mark = "✗ nahi" if s == "bad" else "? pata nahi"
        parts.append(f"{esc(trunc(n, 15))}: {words.get(f, f)} {mark}")
    more = f" (+{len(bad) - 3})" if len(bad) > 3 else ""
    return " · ".join(parts) + more


def plan_block(p: PlanOption) -> list[str]:
    lines = [f"{p.idx} · {esc(trunc(p.title, 30))}"]
    r = p.restaurant
    if p.event:
        e = p.event
        day = dt.datetime.fromtimestamp(e.start, IST).strftime("%a")
        lines.append(f"🎭 {day} {time_label(e.start)} · {esc(trunc(e.name))} · {esc(trunc(e.venue_name, 30))}")
    dist = ""
    if r.distance_km is not None and r.searched_at_venue:
        dist = f" · {r.distance_km:g} km"
    lead = "🍽"
    when = time_label(p.reservation_time)
    if not p.event:
        when = dt.datetime.fromtimestamp(p.reservation_time, IST).strftime("%a") + " " + when
    lines.append(f"{lead} {when} · {esc(trunc(r.name))}{dist} · FREE table")
    cost = f"~{money(p.per_head)}/head" if p.per_head is not None else "khana ka rate pata nahi"
    if p.event and p.event.ticket_price is not None and p.per_head is not None:
        food = p.per_head - p.event.ticket_price
        cost += f" (ticket {money(p.event.ticket_price)} + khana ~{money(food)})"
    elif p.ticket_unknown:
        cost += " + ticket"
    fit = _fit_line(p)
    lines.append(f"💸 {cost}" + (f" · {fit}" if fit else ""))
    return lines


def plans_message(options: list[PlanOption], rid: str, tally: dict[int, int], voted: int, known: int,
                  header: str | None = None) -> tuple[str, Buttons]:
    n = len(options)
    lines = [header or f"{n} plan mile 👇", ""]
    for p in options:
        lines += plan_block(p) + [""]
    lines.append(f"Vote karo 👇 ({voted}/{known} ne kiya)")
    text = "\n".join(lines)
    if len(text) > MAX_PLANS_CHARS:  # eng E11: never let Telegram reject the send
        text = text[: MAX_PLANS_CHARS - 1] + "…"
    buttons = [[(f"{p.idx} · {trunc(p.title, MAX_BUTTON_TITLE)} ({tally.get(p.idx, 0)})", encode("v", rid, p.idx))]
               for p in options]
    return text, buttons


def plans_locked(p: PlanOption) -> str:
    return "\n".join([f"✅ Plan {p.idx} final: {esc(trunc(p.title, 30))}", ""] + plan_block(p))


def zero_plans(reasons: list[str]) -> str:
    why = "; ".join(esc(r) for r in reasons[:2]) or "koi slot/show nahi mila"
    return f"😕 Kuch fit nahi hua. Wajah: {why}.\nBudget badhao, din badlo, ya naya /plan karo."


def partial_header(n: int) -> str:
    return f"⏱ Sirf {n} plan mile (time khatam). Inme se chuno 👇"


def round_closed(kind: str) -> str:
    return {"EXPIRED": "⌛ Plan band (6 ghante chup). Naya: /plan",
            "CANCELLED": "❌ Plan cancel. Naya: /plan"}.get(kind, "Plan band. Naya: /plan")


# ---------- organizer control message (design DR3) ----------

def control_voting(org_id: int, org_name: str, rid: str, leader: int | None, votes: int, tie: list[int]
                   ) -> tuple[str, Buttons]:
    head = f"🎛 {mention(org_id, org_name)} (organizer)"
    if leader is None:
        return f"{head}\nVote aane do…", [[("❌ Plan cancel", encode("c", rid))]]
    if tie:
        rows = [[(f"🔒 Plan {i} final karo", encode("l", rid, i)) for i in tie]]
        return f"{head}\nPlan {' aur '.join(map(str, tie))} barabar hain ({votes} vote). Tum chuno:", rows + [
            [("❌ Plan cancel", encode("c", rid))]]
    return (f"{head}\nPlan {leader} aage hai ({votes} vote).",
            [[(f"🔒 Plan {leader} final karo", encode("l", rid, leader))], [("❌ Plan cancel", encode("c", rid))]])


def control_rsvp(org_id: int, org_name: str, rid: str, confirmed: int, guests: int) -> tuple[str, Buttons]:
    party = confirmed + guests
    text = (f"🎛 {mention(org_id, org_name)} (organizer)\n"
            f"Table kitne logon ki? {confirmed} aa rahe + {guests} guest = {party}")
    rows = [[("➕ Guest", encode("g", rid, "+")), ("➖ Guest", encode("g", rid, "-"))]]
    if party >= 2:
        rows.append([(f"🍽 {party} logon ki table book karo", encode("b", rid))])
    else:
        text += "\nSirf 1 log? Doston ko 'Aa raha' dabane do, ya plan cancel karo."
    rows.append([("❌ Plan cancel", encode("c", rid))])
    return text, rows


def control_confirm(org_id: int, org_name: str, rid: str, p: PlanOption, party: int) -> tuple[str, Buttons]:
    day = dt.datetime.fromtimestamp(p.reservation_time, IST).strftime("%a")
    text = (f"🎛 {mention(org_id, org_name)} (organizer)\n"
            f"{esc(trunc(p.restaurant.name))} · {day} {time_label(p.reservation_time)} · {party} log.\n"
            "⚠️ Table cancel sirf Swiggy app se hota hai.")
    return text, [[("✅ Haan, book karo", encode("k", rid)), ("Ruko", encode("w", rid))]]


def control_booking(org_id: int, org_name: str) -> str:
    return f"🎛 {mention(org_id, org_name)} (organizer)\n⏳ Kar raha hu…"


def control_show_gone(org_id: int, org_name: str, rid: str) -> tuple[str, Buttons]:
    return (f"🎛 {mention(org_id, org_name)} (organizer)\n🎭 Show ab list nahi hai. Sirf table?",
            [[("🍽 Sirf table", encode("t", rid)), ("❌ Cancel", encode("c", rid))]])


def control_slot_gone(org_id: int, org_name: str, rid: str, alt_ts: int | None) -> tuple[str, Buttons]:
    head = f"🎛 {mention(org_id, org_name)} (organizer)\n"
    if alt_ts is None:
        return head + "😕 Woh slot gaya aur koi FREE slot fit nahi hua.", [[("❌ Cancel", encode("c", rid))]]
    return (head + f"😕 Woh slot gaya. {time_label(alt_ts)} chalega?",
            [[(f"✅ {time_label(alt_ts)} book karo", encode("a", rid, alt_ts)), ("❌ Cancel", encode("c", rid))]])


def control_result(org_id: int, org_name: str, state: str, detail: str = "") -> str:
    head = f"🎛 {mention(org_id, org_name)} (organizer)\n"
    return head + {
        "BOOKED": "✅ Table book ho gayi!",
        "TEST_ONLY": "🧪 Test mode: sab check ho gaya, table asli mein book nahi hui.",
        "FAILED": f"❌ Swiggy ne mana kiya: {esc(trunc(detail, 200))}. Table nahi hui.",
        "NEEDS_REVIEW": ("⚠️ Swiggy se clear jawab nahi aaya. Swiggy app → Dineout bookings check karo, "
                         "dobara mat dabao."),
        "CANCELLED": "❌ Plan cancel.",
    }.get(state, state)


# ---------- RSVP + final card (design DR8/DR14/DR15) ----------

def _names(items: list[str]) -> str:
    return ", ".join(esc(trunc(n, 20)) for n in items) if items else "koi nahi abhi"


def rsvp_message(p: PlanOption, rid: str, ticket: list[str], coming: list[str], no: list[str]
                 ) -> tuple[str, Buttons]:
    lines = [f"🎉 Plan {p.idx} final: {esc(trunc(p.title, 30))}"]
    if p.event:
        e = p.event
        lines[0] += f" · {dt.datetime.fromtimestamp(e.start, IST).strftime('%a')} {time_label(e.start)}"
        lines.append(f"🎟 Ticket khud lo: Swiggy app → Scenes → {esc(trunc(e.name))}")
    lines.append(f"Aa rahe: {_names(ticket + coming)}")
    if p.event and coming:
        lines.append(f"Ticket baaki: {_names(coming)}")
    if no:
        lines.append(f"Nahi aa rahe: {_names(no)}")
    if p.event:
        rows = [[("🎟 Ticket le liya", encode("r", rid, "t"))], [("🙋 Aa raha, ticket baaki", encode("r", rid, "c"))],
                [("❌ Nahi aa paunga", encode("r", rid, "n"))]]
    else:
        rows = [[("🙋 Aa raha", encode("r", rid, "c")), ("❌ Nahi aa paunga", encode("r", rid, "n"))]]
    return "\n".join(lines), rows


def maps_link(name: str, area: str) -> str:
    from urllib.parse import quote_plus
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{name} {area}".strip())


def final_card(p: PlanOption, people: list[str], guests: int, ticket_pending: list[str], state: str) -> str:
    r = p.restaurant
    d = dt.datetime.fromtimestamp(p.reservation_time, IST)
    lines = [f"🎉 {d.strftime('%a')} ka plan pakka!"]
    if p.event:
        e = p.event
        lines.append(f"🎭 {time_label(e.start)} · {esc(trunc(e.name))} · {esc(trunc(e.venue_name, 30))}")
    party = len(people) + guests
    lines.append(f"🍽 {time_label(p.reservation_time)} · {esc(trunc(r.name))} · {party} logon ki table")
    who = _names(people) + (f" + {guests} guest" if guests else "")
    lines.append(f"👥 {who}")
    if p.per_head is not None:
        lines.append(f"💸 ~{money(p.per_head)}/head")
    lines.append(f'📍 <a href="{esc(maps_link(r.name, r.area))}">Map pe dekho</a>')
    if p.event and ticket_pending:
        lines.append(f"🎟 Ticket baaki: {_names(ticket_pending)}")
    footer = {
        "TEST_ONLY": "🧪 Test mode: table asli mein book nahi hui",
        "FAILED": "❌ Table book nahi hui (upar dekho)",
        "NEEDS_REVIEW": "⚠️ Table ka status Swiggy app mein check karo",
    }.get(state)
    if footer:
        lines.append(footer)
    return "\n".join(lines)
