---
name: qualification
description: Evaluate entities against criteria to determine fit — binary classification, multi-category labelling, or numeric scoring with rationale. Use when entities exist and need a fit/unfit determination before action. Two paths: Path A for per-entity individual evaluation, Path B for group-level scoring using aggregated signals. Output: entities with score/label/flag. Choose over Nomination when the goal is broad filtering, not selecting specific entities for action.
---

# Qualification and Disqualification

Answers: **is this entity worth pursuing?**

## Qualification vs Disqualification

| Aspect | Qualification | Disqualification |
|---|---|---|
| Default assumption | Unqualified until proven | Qualified until flagged |
| Filter direction | Keep rows that pass | Remove rows that fail |
| Typical use | Building target list from broad pool | Cleaning list, handling bounces/unsubs |
| Where it appears | After list building or research | In event-driven listeners reacting to negative signals |

## Path Selection

### Path A — Individual Evaluation (classify-and-filter)
Per-entity AI evaluation. Two modes:
- **Binary**: `{qualified: true/false, reason: "..."}` → Filter on qualified.
- **Score-based**: `{score: 75, rationale: "..."}` → Filter on score >= threshold.

Use when: each entity can be evaluated independently with its own row data.

### Path B — Group-Level Scoring (group-and-synthesise)
Aggregate context required — AI needs multiple signals grouped per entity.
- Groups research events by entity → single AI scoring pass → overall_score + section_wise_scoring + rationale.
- Use a high-quality model (gpt-5.2) — this is user-facing judgment.
- Inject scoring framework via workflow variable (`<<wf_var.uuid>>`).

Use when: qualification requires seeing the full signal picture (e.g., scoring based on collective hiring + tech + post signals).

## Research as Prerequisite
Qualification often follows research. Chain: parallel-multi-signal-research → event convergence → group-and-synthesise (scoring) → Filter.

## Scoring Framework Pattern
For complex qualification, inject scoring criteria via `<<wf_var.uuid>>`:
1. Input: grouped events/signals
2. Framework reference: `<<wf_var.scoring_framework_uuid>>`
3. Score ranges: defined bands mapping signal strength to numeric ranges
4. Output: overall_score + section_wise_scoring + rationale

## Boundaries
Does NOT include: assembling entity lists (List Building), gathering intelligence (Research), selecting specific entities (Nomination), executing actions (GTM Automations).
