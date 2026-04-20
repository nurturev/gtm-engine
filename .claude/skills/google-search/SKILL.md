---
name: google-search
description: Google Search patterns and best practices for GTM intelligence
---

# Google Search — When & How to Use It

## When to Use Google Search

Google search via `google_search` is the most versatile GTM intelligence tool. Use it when:

- **Finding people**: LinkedIn profiles by title/company/location
- **Finding content**: LinkedIn posts, tweets, Reddit threads, YouTube videos
- **Hiring signals**: Job listings on LinkedIn to gauge company growth
- **Competitive intel**: G2 reviews, Reddit discussions, pricing pages
- **Funding/news**: Recent announcements, press releases
- **Non-traditional list building**: Instagram businesses, Yelp listings — businesses NOT in Apollo/LinkedIn
- **Tech stack discovery**: GitHub repos, StackShare profiles
- **Buying intent**: Reddit "alternative to X" threads, Twitter recommendations

## How to Use It (Dynamic Pattern Discovery)

**NEVER guess platform-specific query patterns.** Always follow this flow:

### Step 1: Get the pattern
Look up existing search patterns for the target platform (e.g., `linkedin_jobs`).
Returns: site_prefix, query_template, examples, tips, recommended_params.

### Step 2: Construct the query using the pattern
```
google_search(
    query='site:linkedin.com/jobs/view "Stripe"',
    tbs="qdr:m",
    num_results=50
)
```

### Available Platforms
Key platforms for search patterns:
- `linkedin_profiles`, `linkedin_posts`, `linkedin_jobs`, `linkedin_companies`
- `twitter_posts`, `twitter_profiles`
- `reddit_discussions`
- `instagram_businesses`
- `youtube_content`, `github_repos`
- `g2_reviews`, `crunchbase_companies`, `local_businesses`

### Available GTM Use Cases
- `funding_news`, `hiring_signals`, `leadership_changes`
- `competitor_intelligence`, `tech_stack_discovery`
- `non_traditional_list_building`, `content_research`, `buying_intent`

## Key Parameters

### tbs (Time-Based Search) — Critical for Recency
| Value | Meaning |
|-------|---------|
| `hour` | Last 1 hour |
| `day` | Last 24 hours |
| `week` | Last 7 days |
| `month` | Last 30 days |
| `qdr:h2` | Last 2 hours |
| `qdr:h6` | Last 6 hours |
| `qdr:d3` | Last 3 days |
| `qdr:w2` | Last 2 weeks |
| `qdr:m3` | Last 3 months |
| Custom | `cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY` |

### site (Convenience Restriction)
Pass `site="linkedin.com/in"` instead of embedding `site:` in the query.

### queries (Bulk Search)
Pass multiple queries for concurrent execution:
```
google_search(queries=["Acme funding", "Acme hiring", "Acme reviews"])
```

## Common Mistakes to Avoid
1. **Not using tbs for LinkedIn posts** — posts have short shelf life, always add recency
2. **Using wrong URL path** — e.g. `/jobs/search` instead of `/jobs/view` for LinkedIn jobs
3. **Not quoting exact phrases** — `VP Sales` matches loosely, `"VP Sales"` matches exactly
4. **Forgetting OR must be uppercase** — `or` doesn't work, `OR` does

## Google Search vs LinkedIn Search Posts (Decision)

**Default to LinkedIn Search Posts** for content discovery. It's purpose-built, returns structured author data, and supports the `mentioning_company` filter (after resolving the integer company_id via `Get Company Profile`).

**Switch to Google Search (`site:linkedin.com/posts`) only when:**
- The competitor/company mention filter is needed AND you cannot resolve a `company_id` (company not on LinkedIn, lookup returns empty even after the full fallback chain: Apollo enrich → RapidAPI Google → Parallel Search backup)
- **Full post text is NOT required** — you only need to discover post URLs and the author. The LinkedIn post URL itself encodes everything you need:

  ```
  https://www.linkedin.com/posts/{author-handle}_{title-slug}-activity-{URN}-{noise}
  ```

  From this URL alone you can extract:
  - **Author handle** → the person's vanity LinkedIn URL (`linkedin.com/in/{handle}`)
  - **URN** → the digit run between `activity-` and the next `-` — usable directly in `Get Post's Comments` / `Get Post's Reactions` without calling Search Posts at all

- The boolean query is too complex or rejected by LinkedIn's native search

**Cost note:** Google Search → URN parse → Get Post's Comments/Reactions can be cheaper than Search Posts → Get Post's Comments/Reactions when you only need engagers from a known set of posts. Search Posts is for *discovery* of posts; Google + URN parse is for *targeted retrieval* when you already have the post URL.

**Result validation reminder:** Google `site:linkedin.com/posts ("handle")` returns posts that merely MENTION a handle in comments/reshares — not just posts BY that handle. Post-filter by extracting the handle from the URL and matching it to your watchlist (see `gtm-builder/SKILL.md`).
