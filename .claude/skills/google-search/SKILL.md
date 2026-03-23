# Google Search — When & How to Use It

## When to Use Google Search

Google search via `nrev_google_search` is the most versatile GTM intelligence tool. Use it when:

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

### Step 1: Get the pattern from the server
```
nrev_search_patterns(platform="linkedin_jobs")
→ Returns: site_prefix, query_template, examples, tips, recommended_params
```

### Step 2: Construct the query using the pattern
```
nrev_google_search(
    query='site:linkedin.com/jobs/view "Stripe"',
    tbs="qdr:m",
    num_results=50
)
```

### Available Platforms
Call `nrev_search_patterns()` with no args for the full list. Key ones:
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
nrev_google_search(queries=["Acme funding", "Acme hiring", "Acme reviews"])
```

## Common Mistakes to Avoid
1. **Not using tbs for LinkedIn posts** — posts have short shelf life, always add recency
2. **Using wrong URL path** — e.g. `/jobs/search` instead of `/jobs/view` for LinkedIn jobs
3. **Not quoting exact phrases** — `VP Sales` matches loosely, `"VP Sales"` matches exactly
4. **Forgetting OR must be uppercase** — `or` doesn't work, `OR` does
