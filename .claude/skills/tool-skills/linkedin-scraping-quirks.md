# LinkedIn Scraping (Fresh LinkedIn Profile Data) — Tool Quirks & Best Practices

Underlying provider: **RapidAPI — Fresh LinkedIn Profile Data** (`fresh-linkedin-profile-data.p.rapidapi.com`).

## When to Use

- **Live profile/company data** — most recent source of truth (LinkedIn directly), beats cached B2B databases on freshness
- **Fields no other provider has** — follower count, connection count, skills, education, company `id` (integer), specialties, current headline
- **Engagement mining** — discover people via reactions/comments on a target post (warm intent signal)
- **Post discovery** — search posts by keyword/boolean query, optionally filtered by company mention or author title
- **No email/phone needed** — if outreach is via LinkedIn DM/connect, skip Apollo/RocketReach entirely

**Not for:** email or phone enrichment (no contact data), bulk B2B people search (use Apollo), CRM sync (use Salesforce/HubSpot).

---

## Endpoint Index

All endpoints share the same auth headers:

```
X-RapidAPI-Key: <your_key>
X-RapidAPI-Host: fresh-linkedin-profile-data.p.rapidapi.com
```

| RapidAPI Endpoint | HTTP | What It Returns | Input Identifier |
|---|---|---|---|
| `Get Profile Details` | GET | Person profile (name, title, headline, company, employment history, education, skills, location) | `linkedin_url` (person) |
| `Get Company by URL` | GET | Company profile (name, employee count, industry, specialties, follower count, **company `id`**) | `linkedin_url` (company) |
| `Get Company by Domain` | GET | Same as above | `domain` |
| `Get Profile's Posts` | GET | Posts/comments/reactions by a person — **`type` enum switches behavior** | `linkedin_url` + `type ∈ {posts, comments, reactions}` |
| `Get Company's Posts` | GET | Posts authored by a company page | `linkedin_url` (company) |
| `Get Post's Comments` | GET | All commenters on a single post | `post_urn` |
| `Get Post's Reactions` | GET | All reactors on a single post (broader than commenters) | `post_urn` |
| `Search Posts` | POST | Posts matching keyword/boolean + optional company-mention/author-title filters | `search_keywords`, optional `mentioning_company` (array of integers), `author_keyword`, `date_posted`, `sort_by`, `page` |

---

## Critical Gotchas (Read Before ANY Call)

### 1. `mentioning_company` requires an ARRAY of INTEGER company IDs — NOT URLs or domains

```
// WRONG ❌
"mentioning_company": "https://www.linkedin.com/company/stripe"
"mentioning_company": "stripe.com"
"mentioning_company": ["stripe.com"]

// CORRECT ✅
"mentioning_company": [1715]
```

The integer ID is only obtainable via `Get Company by URL` or `Get Company by Domain`. **Always resolve company → id BEFORE running Search Posts with company-mention filter.** This is the #1 source of empty result sets.

### 2. `Get Profile's Posts` is polymorphic — three behaviors via `type`

One endpoint, three distinct outputs depending on `type`:

| `type` value | Returns | Output prefix |
|---|---|---|
| `posts` (default) | Posts authored by the person | `post_*` |
| `comments` | Comments the person has made elsewhere | `comment_*`, `post_urn` |
| `reactions` | Posts the person has reacted to | `reaction_type`, `post_*` |

When the agent thinks of these as three "tools," remember they hit the same endpoint — credit cost and rate-limit budget is shared.

### 3. `search_keywords` does NOT accept comma-separated keywords

```
// WRONG ❌ — returns nothing
"search_keywords": "shopify, abandoned cart, checkout"

// CORRECT ✅ — single term or boolean expression
"search_keywords": "shopify"
"search_keywords": "(\"shopify\" OR \"#shopify\") AND (\"abandoned cart\" OR \"cart abandonment\")"
```

For multiple distinct topics, **split into separate rows upstream and run one query per row.**

### 4. Engagement capture EXPLODES — mandatory user prompt before fan-out

`Get Posts → Get Post Comments / Get Post Reactors` is the most common cost trap. Math:

```
N people × 50 posts each × 200 comments × 500 reactors = 5,000,000 rows worst case
```

**Before fanning out, always ask the user:**

1. **Recency filter** — "Last 7 / 14 / 30 days, or all?" (Filter on `posted` after Get Posts)
2. **Exclude reposts?** — "Skip rows where `reposted == True`?" (reposts often double-count engagement and aren't authored content)
3. **Reactor cap** — "Cap reactors per post at 100/200/500?" (default 500 in nRev nodes)
4. **Pilot the first post first** — fetch comments/reactors for ONE post, show row count, confirm before the rest

Default safe combo for first runs: `last 14 days + exclude reposts + 100 reactors/post`.

### 5. Company by Domain returns empty for niche/SMB/non-canonical domains

Common scenarios where `Get Company by Domain` returns nothing:
- Company uses a non-primary domain (acquired, rebranded, country-specific TLD)
- Personal-brand companies, agencies with generic names
- Very new or very small companies

**Fallback chain when domain lookup fails:**

```
Get Company by Domain
  ↓ (empty)
Apollo Company Enrich (apollo enrich_company by domain → returns linkedin_url)
  ↓ (still empty)
RapidAPI Google search_web ("{company name} site:linkedin.com/company")
  ↓ (if RapidAPI rate-limited or empty)
Parallel Web search (backup-only SERP)
  ↓ extract LinkedIn URL from top result
Retry: Get Company by URL with the discovered linkedin_url
```

This fallback is mandatory before declaring "company not found." Most domain misses are cache/canonicalization issues, not real absence.

### 6. URN extraction — two paths

The `urn` (post identifier) is the linking key for `Get Post's Comments` and `Get Post's Reactions`. Two ways to get it:

**Path A — Direct from a posts-feed node** (preferred):
- `Get Posts by Person/Company`, `Get Profile's Posts`, `Search Posts` all return `urn` as a column. Use `{{urn}}` directly downstream.

**Path B — Parse from a LinkedIn post URL** (when you got the URL from Google search or a CRM field):

LinkedIn post URLs follow this pattern:
```
https://www.linkedin.com/posts/{author-handle}_{title-slug}-activity-{URN}-{noise}
```

Example:
```
https://www.linkedin.com/posts/stuhirst_the-ciso-2026-mind-map-is-out-our-yearly-activity-7450620213202522113-eHGv
                                                                                          ^^^^^^^^^^^^^^^^^^^
                                                                                                URN
```

Extract the digit run between `activity-` and the next `-`:

```python
import re
def extract_urn(url: str) -> str | None:
    m = re.search(r'activity-(\d+)-', url)
    return m.group(1) if m else None
```

The author handle (between `/posts/` and `_`) is also useful — it's the person's LinkedIn vanity URL (`linkedin.com/in/{handle}`).

### 7. Live scraping = rate-limited

Don't batch-fan more than the RapidAPI plan allows. When iterating a column of LinkedIn URLs, expect throttling. Apply per-row delays or process in chunks. RapidAPI returns 429 on overage — handle by backing off, not retrying immediately.

---

## Dependency Map (Cheatsheet)

```
Need company posts mentioning a competitor?
    → Search Posts (mentioning_company=[INT])
        ← needs integer company_id (in an array)
            ← Get Company by URL/Domain
                ← needs LinkedIn URL or domain
                    ← if missing: Apollo enrich_company → RapidAPI Google search (Parallel Search as backup)

Need post engagers (commenters/reactors)?
    → Get Post's Comments / Get Post's Reactions
        ← needs post_urn
            ← Path A: Get Posts by Person/Company/Search Posts (returns urn directly)
            ← Path B: parse from LinkedIn post URL via regex

Need a person's profile?
    → Get Profile Details
        ← needs LinkedIn person URL (linkedin.com/in/{handle})

Need what a person engages with elsewhere?
    → Get Profile's Posts (type=comments) — what they've commented on
    → Get Profile's Posts (type=reactions) — what they've reacted to
        ← both need LinkedIn person URL
```

---

## Common Workflows

### 1. Engagement mining from a target's content

```
person URL → Get Profile's Posts (type=posts, recency_filter, exclude_reposts)
           → Get Post's Reactions (urn) + Get Post's Comments (urn)  [parallel]
           → normalize column prefixes (reactor_/commenter_ → engager_)
           → merge → unified engager list
           → Apollo/Waterfall enrich (if email needed)
```

### 2. Competitor mention monitoring

```
competitor domain → Get Company by Domain → company_id
                  → Search Posts (mentioning_company=[company_id], date_posted="Past week")
                  → Get Post's Comments / Reactions
                  → enrich + classify
```

#### Verified payload — Search Posts

Real-world example: find CEOs posting about "AI GTM" that mention Clay.com in the last 24 hours. Clay's LinkedIn `company_id` is `13018048` (resolved upstream via Get Company by Domain or Get Company by URL).

```json
{
  "search_keywords": "AI GTM",
  "sort_by": "Latest",
  "date_posted": "Past 24 hours",
  "mentioning_company": [13018048],
  "author_keyword": "CEO",
  "page": 1
}
```

**What this payload demonstrates:**
- `mentioning_company` is an **array of integer company IDs** — never URLs or domains. Resolve upstream via Get Company first.
- `date_posted` accepts the enum string `"Past 24 hours"` (along with other windows like `"Past week"`, `"Past month"`).
- `author_keyword` filters by author title — `"CEO"` here narrows to founder/CEO-level posters.
- `sort_by: "Latest"` returns most recent first; use `"Relevance"` if recency isn't critical.
- `page` is the only pagination control — increment for additional pages.

### 3. Live profile enrichment (when Apollo data is stale)

```
LinkedIn URL → Get Profile Details
             → use headline, current title, follower count, skills
             → if email needed: Apollo enrich (use returned domain) or Waterfall
```

### 4. Person interest mapping

```
LinkedIn URL → Get Profile's Posts (type=reactions, limit=50)
             + Get Profile's Posts (type=comments, limit=50)
             → Ask AI: extract topic clusters
             → use as personalisation hooks in outreach
```

---

## Field-Level Reference

### Get Profile Details — guaranteed output
`name`, `first_name`, `last_name`, `title`. Plus optional: `headline`, `company_name`, `company_domain`, `company_linkedin_url`, `seniority`, `functions`, `employment_history` (JSON array), `city`, `country`, `photo_url`, `twitter_url`.

### Get Company Profile — guaranteed
`company_name`. Plus optional: `company_description`, `employee_count`, `industry`, `specialties`, `follower_count`, `hq_city`, `hq_country`, `website`, `company_linkedin_url`, **`id`** (integer — needed for post-mention filter).

### Get Posts (Person or Company) — guaranteed
`post_text`, `post_url`, `urn`. Plus: `posted`, `num_likes`, `num_comments`, `num_shares`, `reposted` (bool), `original_poster_linkedin_url`.

### Get Post's Comments — guaranteed
`commenter_name`, `commenter_linkedin_url`, `comment_text`. Plus: `commenter_title`, `commenter_company`, `comment_time`.

### Get Post's Reactions — guaranteed
`reactor_name`, `reactor_linkedin_url`. Plus: `reactor_title`, `reactor_company`, `reaction_type` (LIKE / CELEBRATE / SUPPORT / LOVE / INSIGHTFUL / FUNNY).

### Search Posts (V1) — guaranteed
`post_text`, `post_url`, `urn`. Plus: `author_name`, `author_linkedin_url`, `author_title`, `posted`, `num_likes`, `num_comments`.

### Search Posts (V2 — author-centric) — guaranteed
`name`, `linkedin_url`. Plus: `current_title`, `current_employer`, `current_employer_domain`, `city`, `country`, `teaser`. **Prefer V2 when you need the author's company domain in one call.**

---

## Limits & Defaults (nRev wrappers)

| Operation | Default Limit | Hard Ceiling |
|---|---|---|
| Get Posts by Person/Company | 50 | provider-bounded |
| Get Profile's Posts (any `type`) | 50 | provider-bounded |
| Get Post's Comments | 200 | popular posts may exceed |
| Get Post's Reactions | 500 | viral posts may exceed |
| Search Posts | 1000 | start at 100 for testing |

---

## When NOT to Use LinkedIn Scraping

- **Email/phone needed** → Apollo/RocketReach/Waterfall (LinkedIn returns no contact data)
- **High-volume bulk people search** (10K+ rows) → Apollo (cached, faster, cheaper)
- **School/alumni filter** → RocketReach (LinkedIn has no `school` filter on Search Posts/Profiles)
- **Tech stack discovery** → PredictLeads (`company_technologies`)
- **Funding signal** → PredictLeads (`company_financing`)
- **Qualitative LinkedIn searches that can't be expressed as keyword + author + company-mention + date filters** → **Parallel Web Task** (e.g., "find all criticisms of ABC company on LinkedIn", "find posts complaining about pricing in fintech"). Heuristic: ≥5 keyword expansions needed, OR sentiment dimension, OR entity-relationship dimension. See `parallel-web-quirks.md`.

See `provider-selection/SKILL.md` for the full cross-provider decision matrix.
