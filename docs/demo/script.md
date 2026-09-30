# Plan Bana: demo video script (60–90 s)

**Who's in it:** you (**Harsh**, organizer) on your phone + your **second Telegram account** ("Dost") on the other device. Record the **phone** screen.
**Goal:** a messy group chat becomes a booked Swiggy Dineout table + a show, in about a minute.

## Before you press record (5 min)
1. `python scripts/preflight.py`. It should say **GO** with at least one comedy show that has seats.
   - If it says dinner-only, record earlier in the day, or use a date that has shows (`python scripts/preflight.py 2026-10-08`) and say that date in the chat.
2. `DRY_RUN` in `.env`:
   - `0`: Book makes a real FREE table reservation. It shows in the app and on WhatsApp. **Cancel it after recording.**
   - `1`: Book ends in 🧪 test mode.
3. `python scripts/reset_demo.py --yes`, then start the bot: `python scripts/serve_plan.py`.
4. Both accounts in the group. The bot is admin so the final card can be pinned. Phone on Do Not Disturb, battery icon clean.
5. Start the phone screen recorder. Scroll the group to the bottom.

## Shots

| # | Time | Who does what | On-screen caption | Voice-over (optional) |
|---|---|---|---|---|
| 1 | 0:00–0:04 | Text card (or first frame of the chat) | **Group chat mein plan kabhi final hota hai? 😅** | "Every friend group has this chat." |
| 2 | 0:04–0:16 | **Harsh:** `aaj raat comedy show chalein?` · **Dost:** `hum 3 log hai, budget 1000 tak` · **Dost:** `ek dost veg hai` | **Normal messy chat** | "Everyone says something, nobody decides." |
| 3 | 0:16–0:26 | **Harsh:** `/plan` → 🧠 Samjha appears → tap **✅ Sahi hai** | **Bot ne poori chat padh li** | "Plan Bana reads the chat and says what it understood." |
| 4 | 0:26–0:40 | "🔎 Shows dhoondh raha hu…" → 3 plans appear (**cut or ⏩ the ~45 s wait**) | **Real Swiggy shows + FREE tables, checked by code** | "It finds real shows on Swiggy Scenes, and a free Dineout table near the venue after the show." |
| 5 | 0:40–0:50 | **Dost** votes plan 1 → **Harsh** votes plan 1 → 🔒 locks → **Dost** taps **🎟 Ticket le liya** → **Harsh** taps **➕ Guest** | **Sab vote karte hain** | "The group votes. Majority wins." |
| 6 | 0:50–1:00 | **Harsh:** 🍽 **3 logon ki table book karo** → **✅ Haan, book karo** → "✅ Table book ho gayi!" + 🎉 card gets pinned | **Organizer confirms, table booked** | "Only the organizer can book, and it re-checks the slot live first." |
| 7 | 1:00–1:10 | Swiggy app → Dineout booking · WhatsApp "Dineout Reservation confirmed" | **Real booking. No payment.** | "A real reservation, on Swiggy, zero payment." |
| 8 | 1:10–1:20 | Screen-record `docs/demo/end-card.html` | (the card is the caption) | "Gemini only reads. Code checks every ID. The bot never pays." |

## Editing notes
- Keep it **under 90 s**. Speed up waiting (shots 3–4) 4–8×, and never cut away from a result.
- Put captions at the top, since the Telegram keyboard covers the bottom.
- Zoom (Ken Burns) on: the Samjha message, one plan card, and "✅ Table book ho gayi!".
- Music: low, upbeat, no lyrics. No music under the voice-over.
- End frame: the end card for 3 s, with the GitHub link visible.

## After recording
1. **Cancel the reservation** in the Swiggy app (Dineout → booking → Cancel) unless you're going.
2. Set `DRY_RUN=1` in `.env` and restart the bot.
3. Tell me, and I'll draft the email to builders@swiggy.in.

## If something goes off script
| What you see | Do this |
|---|---|
| Plans are dinner-only | Shows sold out or closed. Re-run preflight, pick a date with ✅ shows, `reset_demo.py`, retake. |
| "Kuch fit nahi hua" | Raise the budget in the chat (e.g. 1500), retake. |
| Bot silent after `/plan` | Is the bot running? Is privacy off? Is it in the group? Check the `serve_plan.py` terminal. |
| "Swiggy login expire" | `python scripts/probe.py login`, restart the bot. |
