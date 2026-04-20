---
name: nomination
description: Select specific entities from a qualified list for a particular action — picking best candidates for outreach, prioritising accounts, or choosing representatives per group. Use when a qualified list exists and must be narrowed to actionable targets. Three mechanisms: persona gate (Path A classification), best-per-group (Path B synthesis), score-and-rank (Limit node). Output: ordered subset of entities selected for action. Choose over Qualification when the goal is selecting, not filtering.
---

# Nomination

Answers: **which specific entities should I act on, and in what order?**

## How It Differs from Qualification

| Aspect | Qualification | Nomination |
|---|---|---|
| Question | Is this entity worth pursuing? | Which specific entities do I pursue? |
| Output | Score/label on every entity | A subset selected for action |
| Volume reduction | Moderate (remove clearly unfit) | Aggressive (pick top N, best per group) |

## Nomination Mechanisms

### 1. Persona Gate (Path A — classify-and-filter)
Classify each entity by persona → filter to keep only target personas.
- Categories more selective than qualification (e.g., "Finance Leaders", "RevOps", "Others").
- `reason` in output serves dual purpose: debugging AND downstream content personalisation.

### 2. Best-Per-Group (Path B — group-and-synthesise)
Group by company → AI picks best entry point from the group → one nominee per company.
- Use when multiple people qualify at the same company and you need exactly one.
- Path A won't work here — comparative context across candidates is needed.

### 3. Score-and-Rank (Limit Node)
Sort by score → Limit to top N per group.
- `limit_across_groups: true` + `grouping_keys` ensures per-group limits.
- `column_to_sort` = score or priority column from upstream.
- This is the nomination mechanism in queue-based-drip (nomination-over-time).

### 4. Priority Queue (Nomination-Over-Time)
Scheduler-triggered, Limit node selects top N per Owner per run. Continuous nomination — each run picks the next batch.

## Downstream
Nomination feeds directly into: Content Generation (personalised outreach), GTM Automations (send actions), Queue insertion (drip processing), Human review (Google Sheets export).

## Boundaries
Does NOT include: assembling entity lists (List Building), gathering intelligence (Research), broad fit determination (Qualification), executing actions (GTM Automations).
