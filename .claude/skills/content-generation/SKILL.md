---
name: content-generation
description: Produce personalised, brand-consistent content — email sequences, cold emails, LinkedIn comments, connection messages, talking points, multi-channel campaigns. Use when entities have sufficient enrichment context and need deliverable content. Core pattern: template sourcing → context assembly → AI generation → maker-checker verification. Requires high-quality models. Output: verified content per entity row. Choose over Research when the goal is creating sendable content, not gathering data.
---

# Content Generation

Answers: **what do I say to this entity, in what format, at what step?**

## Core Pattern (template-content-generation)

```
Template Source → Template Preparation → Context Assembly (Merge) →
Optional Pre-Generation Checks → Content Generator (Ask AI) →
Quality Gate → Content Verifier (Ask AI) → Final Quality Gate
```

## Cohort-to-Template Mapping
Entities map to templates via a **cohort key** (merge field). Sources:
- Nomination output (persona labels)
- Qualification score bands
- Campaign/source tag
- Any custom field

Inner join excludes entities without matching templates — validation by design.

## Template Management
- **External (Google Sheets)**: Multiple cohorts, non-technical stakeholders, A/B testing, complex templates.
- **Inline in prompt**: Single template, experimental, cohort-invariant. A proper inline template includes: role definition, voice pillars, banned words, subject line toolkit, CTA guidelines, quality checklist.

## Content Types
| Type | Key Characteristics |
|---|---|
| Email sequences | Array output, per-step evaluation fields, cross-step consistency |
| Cold emails (angle-based) | Multiple angles, 40-70 word caps, conditional PATH A/B per angle |
| LinkedIn comments | Multi-task prompt (relevance → score → draft), comment_score >= 8, batch-level constraints |
| Talking points / briefs | Free text, consumes scored research, verifier optional for internal |
| Multi-channel | Email + LinkedIn + Slack in one call, per-channel evaluation |

## Key Decisions
1. External templates vs inline? (cohort variance, stakeholder access)
2. Which content type? (drives prompt structure and output schema)
3. Maker-checker needed? (yes for production content, optional for internal/experimental)
4. Pre-generation checks? (external system state affects generation rules)

## Boundaries
Does NOT include: assembling entity lists (List Building), enriching entities (Research), scoring fit (Qualification), selecting targets (Nomination), sending/logging (GTM Automations).
