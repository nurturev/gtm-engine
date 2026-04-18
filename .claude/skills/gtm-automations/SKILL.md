---
name: gtm-automations
description: Execute actions on entities — CRM writes, message sending, outreach queue management, event-driven reactions, LinkedIn actions. Use when entities are ready for action (enriched, qualified, content generated). Four archetypes: System Writes (CRM/Sheets), Event-Driven Listeners (reactive to webhooks/CRM events), Queue-Based Drip (rate-limited processing), Interactive Response (Slack bot delivery). Output: executed actions in external systems. This is the terminal operation — the "do" step.
---

# GTM Automations

Answers: **what do I do with these entities now?**

## Automation Archetypes

### 1. System Writes (system-writes)
Writing data to CRMs (Salesforce, HubSpot), spreadsheets, databases.
- Patterns: read-before-write, read-compute-writeback, explode+write, chained writes.
- Terminal step in most workflows — pushes enriched/generated data to systems of record.

### 2. Event-Driven Listeners (event-driven-listener)
Reactive workflows triggered by webhooks, CRM events, Slack messages.
- Pattern: Listener → Normalize payload → Audit log → Route (If/Else) → Parallel actions.
- Handles both negative events (bounce → cleanup) and positive events (reply → enrich).

### 3. Queue-Based Drip (queue-based-drip)
Rate-limited processing of backlogs.
- Pattern: Scheduler → Read queue → Dedup (LEFT JOIN) → Limit per group → Execute → Log.
- Sheet-as-queue: human-editable, priority-sorted, multi-owner.
- Rate limit formula: `limit = daily_cap / runs_per_day`.

### 4. Interactive Response (interactive-on-demand-research)
Response delivery aspect of on-demand research.
- Thread replies using preserved payload context (fork-and-rejoin pattern).
- Custom bot identity, mrkdwn formatting, unfurl_links disabled.

## Archetype Selection

| Scenario | Archetype |
|---|---|
| Push enriched leads to CRM | System Writes |
| React to email bounce/unsub | Event-Driven Listener |
| Daily LinkedIn connection requests | Queue-Based Drip |
| Slack bot research response | Interactive Response |
| Create CRM tasks from research | System Writes |
| Positive event → enrichment trigger | Event-Driven Listener |

## Key Decisions
1. Which archetype? (trigger type + rate limit needs)
2. Which target systems? (determines node selection and field mapping)
3. Read-before-write needed? (dedup, conditional writes)
4. Multi-system side effects? (parallel independent paths)

## Boundaries
Does NOT include: assembling entity lists (List Building), gathering intelligence (Research), scoring fit (Qualification), selecting targets (Nomination). However, automations frequently *trigger* other operations (listener → research, queue → qualification).
