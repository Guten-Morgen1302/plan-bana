# TODOS

## Product

### In-chat UPI payment (child pays remotely)

**What:** Add Swiggy's headless UPI flow. After the child taps "Pay UPI", call `checkout(paymentMethod:"UPI")` and send the returned `bridgeUrl` to the child on Telegram. A payment worker then polls `check_payment_status` (capped), calls `confirm_order` (up to 3 retries), and handles a failed payment with retry or a switch to COD (max 2 UPI retries).

**Why:** Lets the child pay for Mom's groceries from another city. This is the strongest family-pair moment, and it's cut from v1 only to protect the filming date.

**Context:** Deferred by the CEO review (D2, 2026-09-29). The full design was already approved in the eng review: D6 (failed-payment retry), D10 (confirm_order retries), and D17 journeys 2-3, all recorded in the design doc. First verify that Instamart `checkout` returns `bridgeUrl` the way the Food docs describe (Open Question 4) using one budgeted real order.

**Effort:** M
**Priority:** P2
**Depends on:** v1 COD path shipped

### "Maa is okay" rhythm alerts

**What:** Learn Mom's reorder cadence per item from PLACED orders, and alert the child on Telegram when an item is twice as overdue as usual. Optionally, send Mom a proactive "kal doodh bhej doon?" through an approved WhatsApp template.

**Why:** Turns grocery ordering into a quiet wellbeing signal for an ageing parent. This is the pitch's "what's next" slide (Approach C).

**Context:** Not built in v1 because two weeks of data can't honestly show a learned rhythm. Maa's list already stores `last_ordered_at` and `count` per phrase, so start there. Proactive messages to Mom need a Meta-approved template because they fall outside the 24h window. Decided in plan-eng-review D19 (2026-09-29).

**Effort:** M
**Priority:** P3
**Depends on:** v1 shipped, plus at least 4 weeks of real orders

### Food ordering v2

**What:** Add the Swiggy Food server (`/food`) to the same guarded agent and commit layer, so Mom can ask for cooked food.

**Why:** "Aaj khana nahi banaya, kuch mangwa do" is the next most common thing a parent asks for after groceries.

**Context:** The approval, payment and idempotency machinery is reusable. Food differs from Instamart in four ways:
- A cart binds to one restaurant, so warn before the cart is flushed.
- There is a ₹1000 cart cap for Builders Club orders.
- `place_food_order` is not idempotent, so reconcile via `get_food_orders` before retrying.
- Food's `confirm_order` takes `orderId` + `addressId` + `lat` + `lng` (no `paasId`).

See the Swiggy docs: `docs/build/recipes/order-food.md` and `pay-with-upi.md`. Decided in plan-eng-review D20 (2026-09-29).

**Effort:** L
**Priority:** P3
**Depends on:** v1 shipped

## Infrastructure

### Always-on VPS deploy

**What:** Move the bot from the laptop + cloudflared tunnel to a small VPS, using Docker Compose, Caddy for automatic HTTPS, and a real domain. Then update the OAuth redirect URI and the prod-access form to the new domain.

**Why:** In v1 the bot only works while the laptop is awake. Mom's real daily use needs it running all the time.

**Context:** Deferred by the CEO review (D3, 2026-09-29). v1 uses a cloudflared named tunnel, which gives a stable HTTPS hostname. The app is a single uvicorn process with SQLite (eng D1), so it deploys as one container plus a volume.

**Effort:** S
**Priority:** P3
**Depends on:** v1 shipped

### Dedicated family Swiggy account

**What:** Move Mom's orders to a separate Swiggy account (its own phone number), so the child's personal Instamart cart never collides with Mom's.

**Why:** Instamart has one cart per account, and `update_cart` replaces the whole cart. Today, if the child shops on Instamart while a Mom draft is active, one cart overwrites the other.

**Context:** For v1 the bot just warns the child not to use Instamart during Mom's drafts. A new account starts with no order history, so `your_go_to_items` will be empty at first; Maa's list covers that gap. Decided in plan-eng-review D21 (2026-09-29).

**Effort:** S
**Priority:** P3
**Depends on:** v1 shipped

## Completed
