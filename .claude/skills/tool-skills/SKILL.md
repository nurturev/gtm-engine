---
name: tool-skills
description: Index of tactical reference sheets covering API quirks, field formats, and gotchas for the integrated data providers — Apollo, RocketReach, PredictLeads, Parallel Web, and Google (RapidAPI) search patterns. Use BEFORE making API calls to a specific provider to avoid wasted credits and bad data.
---

# Tool Skills — Provider API Quirks & Best Practices

These are NOT strategy files. These are **tactical reference sheets** containing the specific quirks, field formats, gotchas, and best practices for each integrated data provider's API.

## When to Activate

Auto-load the relevant tool skill BEFORE making any API call to that provider. These skills prevent common mistakes that waste credits and return bad data.

## Files

- `apollo-quirks.md` — Apollo API field formats, filter behaviors, URL requirements
- `rocketreach-quirks.md` — RocketReach previous employer format, search quirks
- `predictleads-quirks.md` — PredictLeads dual-key auth, signal categories, coverage notes
- `linkedin-scraping-quirks.md` — Fresh LinkedIn Profile Data (RapidAPI) — endpoint polymorphism, URN extraction, dependency chains, engagement explosion guardrails
- `google-search-patterns.md` — Site: operators, URL structures by platform
- `parallel-web-quirks.md` — Parallel Web enrichment capabilities and limits

Reference sheets for non-integrated providers (`bettercontact-quirks.md`, `hunter-quirks.md`, `zerobounce-quirks.md`, `instantly-quirks.md`) exist on disk but are dormant — do not route to them as data providers. nrev-lite cannot currently execute calls against those providers as data providers. (Instantly is reachable through the Composio App, not as a data provider.)
