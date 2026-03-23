# Enrichment Strategy (No Waterfall Needed)

BetterContact handles waterfall enrichment (trying multiple providers in sequence) automatically. Do NOT implement provider waterfall logic in nrev-lite workflows.

## Instead:
1. Use the provider-selection skill to pick the **best single provider** for each data type
2. Send enrichment requests to that provider
3. If data is missing, suggest the user configure BetterContact for automatic fallback

## Provider Selection Quick Reference:
- **Person enrichment by email/name**: Apollo
- **Person enrichment needing phone**: RocketReach
- **Alumni/school search**: RocketReach (only provider with previous_employer filter)
- **Company enrichment**: Apollo
- **Company signals (jobs/tech/news)**: PredictLeads
- **Email verification**: ZeroBounce

## Why Not Waterfall in nrev-lite?
- BetterContact already does this better — it handles 15+ providers, dedup, and quality scoring
- Implementing waterfall in nrev-lite wastes credits on redundant provider calls
- Focus nrev-lite on discovery, scoring, and creative sourcing — let BetterContact handle the fill-rate problem
