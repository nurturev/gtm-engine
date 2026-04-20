---
name: waterfall-enrichment
description: Explains why nrev-lite does not implement multi-provider waterfall enrichment, and how to pick the single best integrated provider per data type. Use when the user asks about waterfall enrichment, multi-provider fallback, why one provider was chosen, or how to handle missing data after enrichment.
---

# Enrichment Strategy (No Waterfall in nrev-lite)

nrev-lite does **not** implement waterfall enrichment (trying multiple providers in sequence for the same record). Only a single provider is called per enrichment request. Pick the best provider up front for the data type you need — do not attempt to chain providers in a workflow to "maximize coverage".

## Integrated Enrichment Providers

These are the only providers nrev-lite can actually call today. Anything else (BetterContact, Hunter, Clearbit, Lusha, ZeroBounce) appears in the dashboard catalog as "Coming Soon" and will fail if invoked.

| Use case | Provider |
|---|---|
| Person enrichment by email / name / LinkedIn URL | Apollo |
| Person enrichment when you need phone numbers | RocketReach |
| Alumni search, school-based filtering | RocketReach (only provider with a working `school` / `previous_employer` filter) |
| Company enrichment by domain | Apollo |
| Company signals (jobs, tech stack, news, funding) | PredictLeads |

See the `provider-selection` skill for the full matrix and credit costs.

## If Data Is Missing After Enrichment

If Apollo or RocketReach does not return the field the user wants (e.g. no phone number, no email), do not silently try the other provider as a fallback — that burns credits without user consent. Instead:

1. Report the miss honestly ("Apollo did not return a phone for this record").
2. Ask the user if they want to retry on the alternate provider (e.g. RocketReach for phones).
3. Only proceed once they confirm.

## Why No Waterfall?

- Waterfall logic is only worthwhile when a second provider can plausibly fill the gap. With two integrated enrichment vendors (Apollo, RocketReach) and substantial overlap in their data, a naive waterfall wastes more credits than it saves.
- Users who genuinely need waterfall coverage should use a dedicated waterfall provider (e.g. BetterContact) directly outside nrev-lite, or wait for it to be integrated. See the [provider-selection](../provider-selection/SKILL.md) skill's "Out-of-Scope Capabilities" section.
