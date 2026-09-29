# Docs

## Design (Maa ka Swiggy)
- [design/maa-ka-swiggy-design.md](design/maa-ka-swiggy-design.md): the full design doc and decision record, in the order the reviews happened:
  1. Office-hours design, with the approaches considered.
  2. First eng review: decisions D1-D21 and the implementation tasks.
  3. CEO review (scope reduction): decisions D1-D8, error and failure-mode registries, and the CEO tasks.
  4. Eng re-review: decision D1 and the tunnel prerequisite.
  5. The final review report.
- [design/test-plan.md](design/test-plan.md): what to test and where (COD-only v1).
- [../TODOS.md](../TODOS.md): deferred work (UPI payment, always-on VPS, rhythm alerts, Food v2, dedicated account).

## Swiggy Builders Club reference
- [swiggy/builders-club-site.md](swiggy/builders-club-site.md): the marketing, FAQ, access, guidelines and application-form pages (the ones not in `llms-full.txt`).
- [swiggy/llms.txt](swiggy/llms.txt): index of every docs page (snapshot of https://mcp.swiggy.com/builders/llms.txt).
- [swiggy/llms-full.txt](swiggy/llms-full.txt): full text of every docs page, including all 66 tool schemas, recipes, auth, rate limits and errors (snapshot of https://mcp.swiggy.com/builders/llms-full.txt, taken 2026-09-29).

These are snapshots. For anything schema-related, re-fetch the live `.md` page (append `.md` to any `https://mcp.swiggy.com/builders/docs/...` URL) before coding against it.

## Status
The CEO and eng reviews both passed. v1 is COD-only, hosted on a laptop behind a Cloudflare named tunnel (which needs your own domain on Cloudflare DNS). Day 1 plan:
1. Domain and tunnel.
2. `git init`.
3. The probe: OAuth, Mom's address, `your_go_to_items`, one real ₹99+ COD order, save fixtures.
