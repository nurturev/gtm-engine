---
name: tool-skills
description: Provider API quirks and best practices for each data provider
---

# Tool Skills — Provider API Quirks & Best Practices

These are NOT strategy files. These are **tactical reference sheets** containing the specific quirks, field formats, gotchas, and best practices for each data provider's API.

## When to Activate

Auto-load the relevant tool skill BEFORE making any API call to that provider. These skills prevent common mistakes that waste credits and return bad data.

## Files

- `apollo-quirks.md` — Apollo API field formats, filter behaviors, URL requirements
- `rocketreach-quirks.md` — RocketReach previous employer format, search quirks
- `predictleads-quirks.md` — PredictLeads company signals API, dual-key auth
- `linkedin-scraping-quirks.md` — Fresh LinkedIn Profile Data (RapidAPI) — endpoint polymorphism, URN extraction, dependency chains, engagement explosion guardrails
- `google-search-patterns.md` — Site: operators, URL structures by platform
- `parallel-web-quirks.md` — Parallel Web enrichment capabilities and limits
- `hunter-quirks.md` — Hunter.io email discovery and verification quirks
- `bettercontact-quirks.md` — BetterContact waterfall enrichment quirks
- `instantly-quirks.md` — Instantly.ai campaign management quirks
- `zerobounce-quirks.md` — ZeroBounce email validation quirks
