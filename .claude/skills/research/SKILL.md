---
name: research
description: Gather multi-dimensional intelligence about entities (hiring signals, tech stack, posts, M&A, funding). Use when entity list exists and user needs depth beyond basic enrichment. Two modes: batch (process a list via parallel swimlanes) or on-demand (Slack/webhook trigger, research one entity, score, respond). Output: enriched entities with structured signals. Choose over Qualification when the goal is gathering data, not scoring fit.
---

# Research

Answers: **what do I know about this entity?**

## Mode Selection

| Mode | When | Subworkflow |
|---|---|---|
| Batch | Processing a list through parallel research swimlanes | parallel-multi-signal-research |
| On-demand | Slack/webhook trigger, research + respond | interactive-on-demand-research |

## Dimension Selection Guide

| Dimension | Data Available Via |
|---|---|
| Hiring signals | Platform node (Fetch Jobs) |
| Tech stack | Platform node (Get Company Technology) |
| Company posts | Platform node (Get Posts by Company) |
| Person posts | Platform node (Get Post by Person) — requires persona gate |
| M&A, funding, competitive | Web research (Ask AI with web_search) |
| Firmographic validation | Web research |
| Employment history | Enrich Person |

**Principle:** Prefer platform nodes when available. Web research for everything else.

## Swimlane Architecture
Each swimlane investigates one independent dimension and produces one row per entity. Two types:
- **Platform node swimlanes**: 1-to-many data → apply group-and-synthesise internally → standardised events.
- **Web research swimlanes**: Single Ask AI with web_search → events directly.

All swimlanes output standardised event schema: `{event_summary, event_url, event_type, event_date, event_category}`.

## Multi-Tier Model Strategy

| Stage | Model | Rationale |
|---|---|---|
| Entity extraction | gpt-5-mini | Simple parsing |
| Persona classification | gpt-4.1-mini | Short input, deterministic output |
| Event extraction (all swimlanes) | core-fast | Web search capable, structured input |
| Final scoring & synthesis | gpt-5.2 | Complex multi-signal reasoning |
| Talking points / outreach | gpt-5.2 | Creative synthesis |

## Key Decisions
1. Batch vs on-demand?
2. Which dimensions? (user objectives → swimlane selection)
3. Does research feed into scoring? (Qualification follows)
4. Does research feed into content? (Content Generation follows)

## Boundaries
Does NOT include: assembling entity lists (List Building), scoring fit (Qualification), generating outreach (Content Generation), executing actions (GTM Automations).
