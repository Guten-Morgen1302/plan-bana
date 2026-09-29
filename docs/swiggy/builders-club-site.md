# Swiggy Builders Club site: the pages not in llms-full.txt

This file captures the marketing, access and FAQ pages at https://mcp.swiggy.com/builders/ as they read on 2026-09-29. The technical docs, tool reference and blog are in `llms-full.txt` in this folder.

## Home (/builders/)
- **Pitch:** "Build AI agents, apps, and integrations on Swiggy's Food, Instamart, and Dineout APIs. For developers, startups, and enterprises."
- **Headline numbers:** "3 MCP Servers · 49 API Tools". This is outdated: the docs say 4 servers (with Scenes) and 66 tools.
- **Prod access:** "No access needed until prod - build on localhost".
- **Value props:** Build Real Products, AI-Native from Day One, Experiment Freely, Get Noticed by Swiggy ("we hire from this program").
- **How it works:**
  1. Start Building: no approval needed, prototype on localhost, free.
  2. Apply for Prod Access: who you are, what you built, a demo.
  3. Quick Review: use case, security setup, fit.
  4. Go Live: production credentials.
  5. Show Us What You Built: great projects get featured, and standout builders often join the team.
- **What you get:** live API access, generous rate limits, room to experiment, co-branding ("Powered by Swiggy"), direct support (real engineers and a Slack channel), and a growth partnership.

### Home FAQ (answers are hidden behind expand buttons on the page)
- **What is the Swiggy Builders Club?** Swiggy's MCP platform for developers and enterprises (startups, enterprises and individual developers) to build AI agents, apps and tools on Food, Instamart and Dineout.
- **I'm an individual developer, not a company. Can I join?** "Absolutely... Some of our best projects have come from solo developers with a weekend and a wild idea."
- **What can I build?** "Anything that makes commerce better for users": end-to-end food ordering agents, grocery restock bots, dining reservation assistants, multi-modal ordering pipelines, group ordering tools.
- **Can I send you a demo?** "Please do!" Send demos, video walkthroughs or GitHub repos to builders@swiggy.in. "Standout projects get featured on our channels, and yes - exceptional demos have directly led to job offers at Swiggy."
- **Do you really hire from this program?** "Yes. If your project shows strong engineering thinking, product sense, and creativity, our recruiting team will reach out."
- **How does the application process work?** Apply through the form, then review of use case, security practices and fit. Once approved you get API keys and docs. It "typically takes a couple of weeks."
- **Are there rate limits?** "Yes, but they're generous." Expansion requests are reviewed quickly.
- **What happens if I break something?** "Honest mistakes happen... We only take action for deliberate misuse."

## For Developers (/builders/developers/)
- **Tagline:** "Real APIs. Real users. Real chance to get hired."
- **Idea starters:**
  - Voice agent (end-to-end food ordering).
  - Auto-restock (Instamart, learns household consumption).
  - Group ordering (Slack/Teams lunch bot).
  - Dietary planner (macros, allergies).
  - Reservation agent (group availability and budget).
  - Multi-modal agent (dish photo to cart).
- **Toolkit highlights:**
  - Food: search_restaurants, search_menu, get_restaurant_menu, update_food_cart, get_food_cart, place_food_order, track_food_order.
  - Instamart: search_products, update_cart, get_cart, checkout, track_order, get_orders.
  - Dineout: search_restaurants_dineout, get_restaurant_details, get_available_slots, book_table, get_booking_status.
- **"Not a typo. We actually hire from this program."** The path: build, send a demo or GitHub link, get featured, then recruiting reaches out. "No whiteboard puzzles."

### Developer FAQ
- **Need approval to start?** No. Prototype on localhost with no approval and no cost.
- **Solo developer?** Yes. Apply when you're ready for production.
- **Which servers?** Food, Instamart and Dineout.
- **Authentication?** Credentials and redirect URIs after approval; auth is handled via MCP and scoped to the user's session.
- **Chain multiple servers?** "Absolutely. That's where it gets interesting." Their example: Dineout plus Instamart ingredients for the same cuisine.
- **What does a good demo look like?** "Show us the full loop: a real use case, the agent workflow, and a working demo (video, deployed app, or GitHub repo with clear setup instructions). We care about product thinking as much as technical execution."
- **Sandbox?** "You work directly with production APIs - real restaurants, real menus, real inventory... Rate limits and order caps keep things safe during development."

## For Enterprises (/builders/enterprises/)
- **Positioning:** Swiggy as your commerce backend. You own the frontend; Swiggy powers catalog, logistics and inventory.
- **Includes:** production API access, enterprise rate limits, co-branding, dedicated support (named partner manager, priority Slack), custom integration, growth partnership.
- **FAQ:**
  - Onboarding takes 2-4 weeks, and most integrations go live in 4-6 weeks.
  - Compliance help covers DPDP, data agreements and audits.
  - Rate limits are designed around your traffic.
  - White-label: yes, but "Powered by Swiggy" is required.
  - Ongoing: a partner manager, business reviews and early access to features.

## Access and guidelines (/builders/access/)
### What to include in the application
- Who you are (company or individual profile)
- What you're building (use case)
- How it works (integration architecture)
- Redirect URI(s)
- Static IP ranges or gateway IP(s)
- Security contact
- Data handling and privacy declaration
- Environment and infrastructure setup
- Acknowledgement of the Swiggy MCP terms
- Optional: security audit summary, SOC2/ISO certification, expected traffic and scaling plan

### What Swiggy checks
Security check, compliance review, use-case fit, gradual rollout, ongoing partnership.

### Ground rules
- **Encouraged:**
  - Apps, agents and tools that improve ordering, discovery or dining.
  - AI assistants and copilots.
  - Side projects, hackathon builds and prototypes.
  - Following the security and branding guidelines.
  - Sharing demos.
  - Commercial partnerships.
- **Restricted:**
  - Reselling or sharing access.
  - Aggregation layers that hide Swiggy's brand.
  - Misrepresenting prices, availability or ETAs.
  - Scraping beyond the APIs.
  - Competitive intelligence or benchmarking.
  - Bypassing rate limits, logging or safeguards.
- **Zero tolerance:**
  - Manipulating orders, incentives or ranking.
  - Dark patterns, deceptive UX or misattributing where data comes from.
  - Fake traffic or rate-limit abuse.
  - Harvesting data beyond scope.
  - Reverse engineering MCP internals.
  - Circumventing whitelisting or access controls.
  - Violating privacy or security law.
- **Things to know:** stay in scope, respect the brand, user data is sacred, and Swiggy monitors usage.
- **Legal framework:** MCP integration agreement, data protection terms, liability and misuse provisions, termination and revocation rights. Custom enterprise terms take 4+ weeks.

### Application forms (Google Forms, sign-in required)
- **Developer:** https://forms.gle/4vkeKyqm15Qb6fnJA
  - Fields: Email; Full Name; Applying as (Individual Developer / Small Team 2-5 / Startup); Team/Project Name; GitHub or Portfolio URL; LinkedIn; What are you building; MCP servers needed (Food / Instamart / Dineout); Integration type (AI Agent / Web App / Mobile App / Slack-Teams Bot / CLI / Other); Tech stack and architecture.
  - **Production redirect URI(s):** required, HTTPS, no localhost.
  - **Expected request volume:** <1K, 1K-10K or 10K-100K per day, or Not sure.
  - **Working demo video link:** required, and it must be viewable without requesting access.
  - Acknowledgement of the MCP integration terms.
- **Enterprise:** https://forms.gle/U6hYGYLYWQPHWY826

## Contacts
- General questions and demos: builders@swiggy.in
- Security issues: security@swiggy.in (90-day responsible disclosure)

## Inconsistencies noticed across the site
- The home page says 3 servers and 49 tools; the docs say 4 servers and 66 tools. The Food tool count also varies: 14 (coding-agents smoke test), 18 (changelog) and 20 ("What is Swiggy MCP").
- The changelog says MCP-layer rate limiting isn't enforced in v1.0, but the rate-limits page says 70 req/min per user per server (30/min for writes) is enforced.
- The UPI blog lists the Payment stage on 3 servers; the pay-with-upi recipe says 4 (it adds Scenes).
