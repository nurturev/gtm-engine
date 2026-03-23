# Signal-Based Selling (2025-2026)

Signal-based selling triggers outreach from verified buyer intent, not static lists. 75% of B2B sales engagements now originate from signals.

## Signal Hierarchy (Ranked by Conversion)

### Tier 1 — Highest Conversion (act same-day or within 48 hours)

1. **Past Champion Job Changes** — 37% win rates vs 19% cold. 3x conversion, 114% higher win rates, 12% shorter cycles, 54% larger deals. 30% of people change jobs annually.

2. **New Leadership Hires** — New buyers spend 70% of budget in first 100 days. 2.5x higher conversion. Window: ~90 days from hire.

3. **Funding Rounds** — Series B+ with fresh capital are building infrastructure. Combine with hiring signals for maximum effect.

### Tier 2 — Strong Conversion (act within 1 week)

4. **Job Postings / Hiring Signals** — Reveal strategic priorities before press releases. First-time role creation: 2-3x higher purchase intent vs backfills. One company built 48% of pipeline from hiring signals alone.

5. **Tech Stack Changes** — Companies on a competitor, recently switched (last 90 days), or using complementary tech. 28% higher conversion, 27% shorter cycles.

6. **G2 / Review Site Intent** — Highest fidelity: behavior directly indicates active evaluation. 12M+ buyers visit G2 annually.

### Tier 3 — Supporting (use to validate, not as standalone)

7. **Website Visits / Content Engagement** — Pricing page, demo page, repeated visits. Noisy alone, valuable when stacked.

8. **Third-party Intent Data** (Bombora, 6sense) — Account-level topic research. Best for validating accounts showing other signals.

## The Stacking Principle

Single signal = weak. Stacked signals = strong.

Example: Pricing page visit + VP Sales just changed jobs + job posting for RevOps Manager + competitor raised Series C = high-confidence buying intent.

## Response Rate Benchmarks

| Approach | Reply Rate |
|----------|-----------|
| Generic cold | 1-5% (avg 3.4%) |
| Basic personalization | 5-9% |
| Signal-based | 15-25% |
| Multi-signal stacked | 25-40% |

## Speed Tiers

| Urgency | Signals | SLA |
|---------|---------|-----|
| Same-day | Demo requests, pricing page visits from Tier A, new exec at pipeline account | <4 hours |
| Within 48 hours | Funding, champion job changes, G2 category research | <48 hours |
| Within 1 week | Hiring surges, tech stack changes, competitor review spikes | <7 days |

## Operationalizing at Scale

Build a signal-to-play system:
1. **Map signals to plays** — define exactly what happens when signals fire
2. **Tier your signals** — Tier 1 (immediate rep outreach), Tier 2 (warm sequence), Tier 3 (nurture)
3. **Build signal-to-action table** — Signal -> Action -> Owner -> SLA
4. **Track signal-to-meeting conversion** by signal type. Prune what doesn't convert.

## How nrev-lite Captures Signals
- `nrev_google_search` with time filters to catch funding, hiring, leadership changes
- `nrev_search_patterns` for platform-specific signal queries (LinkedIn jobs, G2 reviews, news)
- `nrev_enrich_company` for technographic data and firmographic changes
- `nrev_enrich_person` to verify champion job changes
- Schedule regular signal sweeps and act within speed tier SLAs
