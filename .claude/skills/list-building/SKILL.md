---
name: list-building
description: Build target entity lists (people, companies, posts, jobs) from data sources. Use when user has no list and needs to assemble one from search criteria. Four routes: Company First (company-level ICP criteria needing AI), People First (person-level criteria with optional company qualification), Google Search (niche/ unconventional criteria or exact phrase matching), LinkedIn Post Search (company-specific post discovery). Output: entity table with identifiers for downstream operations.
---

# List Building

Answers: **who or what am I targeting?**

## Route Selection

| User's Starting Point | Route | Subworkflow |
|---|---|---|
| Company-level attributes not in person search filters (pricing model, funding stage, tech stack) | Company First | company-first-list-building |
| Person-level filters (title, seniority, location, function) with optional company qualification | People First | people-first-list-building |
| Niche/unconventional criteria, exact phrase matching, non-LinkedIn entities | Google Search | google-search-list-building |
| Posts mentioning specific companies (requires company LinkedIn ID) | LinkedIn Post Search | linkedin-post-search |

**Disambiguation:**
- Topic-based post discovery ("posts about AI in sales") → Google Search with `site:linkedin.com/posts`, NOT LinkedIn Post Search.
- Both person AND company criteria with person as primary driver → People First (Route 2a fork-qualify-rejoin).
- Platform search returns semantic noise (e.g., "travel managers" for "tour managers") → Google Search with quoted phrases.

## Person Search Node Selection

| Criteria includes | Node |
|---|---|
| Years of experience, funding stage, revenue, dept growth | RocketReach |
| Keyword similarity, employment history | Apollo |
| Simple title + location + seniority | Apollo (default) |

## Key Decisions
1. Which route? (driven by where ICP criteria live — company vs person level)
2. Which person search node? (driven by filter requirements)
3. Does the list feed directly into research or qualification?
4. Single run or scheduled/incremental?

## Variants
- **Scheduled**: Scheduler trigger + date thresholds for ongoing TAM expansion.
- **Multi-Source**: Multiple routes merged + deduplicated on linkedin_url or email. Path B applies for dedup.
- **Incremental/Delta**: "Seen" list in Google Sheets + LEFT JOIN anti-join to skip already-processed entities.

## Boundaries
Does NOT include: researching entities (Research), scoring fit (Qualification), selecting for action (Nomination), executing outreach (GTM Automations).
