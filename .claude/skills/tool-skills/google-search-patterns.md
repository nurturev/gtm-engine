# Google Search Patterns — Site Operators & URL Structures

## Always call nrev_search_patterns() first
Before constructing any Google query, call `nrev_search_patterns()` to get the latest platform-specific URL patterns. The patterns below are reference — the server-side patterns are the source of truth and may be updated without a CLI release.

## Core Site: Operators by Platform

### LinkedIn
```
# People profiles
site:linkedin.com/in [name] [title] [company]

# Company pages
site:linkedin.com/company [company name]

# Posts (engagement monitoring)
site:linkedin.com/posts [person OR company] [topic]

# IMPORTANT: Extract profile handles from post URLs
# URL format: linkedin.com/posts/HANDLE_rest-of-slug
# Example: linkedin.com/posts/sayantaghosh_most-founders-treat-linkedin...
#   → handle = "sayantaghosh" (everything between /posts/ and first _)
#
# Monitor specific people by handle (batch up to 10 per query):
# Do NOT quote the handles — unquoted gives better recall
site:linkedin.com/posts (handle1 OR handle2 OR handle3) [topic]
#
# IMPORTANT: Use the queries[] param for parallel execution on the server.
# The server runs queries concurrently via asyncio.gather (up to 10 at once).
# Do NOT call the API sequentially in a loop — pass all queries at once.
#
# CRITICAL: Google returns false positives. ALWAYS post-filter by extracting
# the handle from each result URL and matching against your watchlist.
#
# Date batching for better coverage on large time ranges:
# Instead of one query for 60 days, run 6 queries of 10 days each:
#   tbs=cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY
# This avoids Google's result truncation on broad time ranges.

# Job postings
site:linkedin.com/jobs/view [role] [location]

# Job search results
site:linkedin.com/jobs/search [keywords]
```

### Instagram
```
# Profiles (businesses, influencers)
site:instagram.com [business name] [location]

# Posts specifically (use /p path)
site:instagram.com/p [keyword] [location]

# Reels
site:instagram.com/reel [keyword]
```
**Quirk:** Adding `/p` after instagram.com targets posts only, giving you content where businesses actively market themselves. Without `/p` you get profile pages.

### Twitter/X
```
site:twitter.com [person] [topic]
site:x.com [person] [topic]
```
**Note:** Both domains work. x.com is newer but twitter.com often has better historical indexing.

### LinkedIn Indexing Lag — Be Aware
Google indexes LinkedIn posts with a **significant delay** (hours to days). Time-based search (`tbs`) behavior:
- `qdr:h2` (2 hours) → **may return 0 results** due to indexing lag. Still try it if the user asks — but warn them upfront and be ready to widen the window if empty.
- `qdr:d` (24 hours) → usually works but may miss some recent posts.
- `qdr:w` (1 week) → most reliable for comprehensive LinkedIn post discovery.
- **Always respect the user's requested time window.** If they say "last 2 hours", search with `qdr:h2` first. If 0 results, explain the lag and offer to widen (don't silently override).
- This lag does NOT affect other platforms (Reddit, Twitter, news sites are indexed near-real-time).

### Job Boards (Hiring Signal Discovery)
```
# Greenhouse (most common for tech)
site:boards.greenhouse.io [company]
site:boards.greenhouse.io/[company_slug]

# Lever
site:jobs.lever.co [company]
site:jobs.lever.co/[company_slug]

# Ashby (growing fast in startups)
site:jobs.ashbyhq.com [company]

# Workday (enterprise)
site:[company].wd5.myworkdayjobs.com

# General job boards
site:indeed.com [role] [company]
site:glassdoor.com [company] jobs
```

### Software Review & Tech Discovery
```
# G2 (software research intent)
site:g2.com/products [software category]
site:g2.com/compare [product1] vs [product2]

# Capterra
site:capterra.com [category]

# TrustRadius
site:trustradius.com/products [category]

# ProductHunt (startups and new products)
site:producthunt.com [category OR product]

# BuiltWith (tech stack)
site:builtwith.com [technology]
```

### Startup & Funding Intelligence
```
# Crunchbase
site:crunchbase.com/organization [company]
site:crunchbase.com/funding_round [company]

# AngelList / Wellfound
site:wellfound.com/company [company]
site:angel.co/company [company]

# PitchBook (limited public data)
site:pitchbook.com [company]
```

### Developer & Technical
```
# GitHub (find developers, open source projects)
site:github.com [technology] [keyword]
site:github.com/[org_name]

# Stack Overflow (find technical discussions)
site:stackoverflow.com [technology] [problem]

# Dev.to / Hashnode (developer content)
site:dev.to [topic]
site:hashnode.dev [topic]
```

### Local Business Discovery
```
# Yelp (best for local — individual biz pages are rich with phone, address, hours)
site:yelp.com/biz [business type] [location]
site:yelp.com [business type] [location]

# Instagram (find businesses with social presence)
site:instagram.com [business type] [location]

# TripAdvisor (hospitality)
site:tripadvisor.com [business type] [location]

# Healthgrades (medical)
site:healthgrades.com [specialty] [location]

# Clutch.co (agencies/consultancies)
site:clutch.co [service type] [location]

# BBB (general businesses)
site:bbb.org [business type] [location]
```
**IMPORTANT — Google Maps `site:` does NOT work.** Maps pages are not indexed in web search. Instead:
- Search `site:yelp.com` + `site:instagram.com` (both well-indexed) to discover businesses
- Then use Parallel Web Extract to scrape individual Yelp/Instagram pages for structured data
- Or search each business name directly for contact details (website, phone, email)

### Professional Communities
```
# Reddit (high intent B2B discussions)
site:reddit.com/r/sales [topic]
site:reddit.com/r/SaaS [topic]
site:reddit.com/r/startups [topic]
site:reddit.com/r/devops [technology]
site:reddit.com/r/sysadmin [product category]

# Quora
site:quora.com [question topic]
```

## Advanced Operators

```
# Combine site: with other operators
site:linkedin.com/in "VP Sales" AND ("Series B" OR "Series C") fintech

# Exclude results
site:linkedin.com/in CTO -recruiter -consultant

# Time-restricted (use tbs parameter in nrev_google_search)
# tbs=qdr:d  → past 24 hours
# tbs=qdr:w  → past week
# tbs=qdr:m  → past month
# tbs=qdr:y  → past year

# Exact phrase matching
site:linkedin.com/posts "just raised" "Series B"

# OR grouping
site:linkedin.com/in (CTO OR "VP Engineering" OR "Head of Engineering") (fintech OR "financial technology")
```

## Platform-by-Use-Case Quick Reference

| I need to find... | Use this pattern |
|-------------------|-----------------|
| People at a company | `site:linkedin.com/in [title] [company]` |
| Companies hiring | `site:boards.greenhouse.io [keyword]` |
| Local businesses | `site:yelp.com [type] [city]` + `site:instagram.com [type] [city]` (NOT google.com/maps — not indexed) |
| Software buyers (in evaluation) | `site:g2.com/compare [category]` |
| Startup funding | `site:crunchbase.com/funding_round [company]` |
| Competitor prospects | `site:linkedin.com/posts [competitor rep name]` |
| Technical decision makers | `site:github.com [org]` + enrich via Apollo |
| Industry conversations | `site:reddit.com/r/[subreddit] [topic]` |
| Content engagement | `site:linkedin.com/posts [your name] commented` |
