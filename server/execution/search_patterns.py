"""Server-side search pattern intelligence.

Platform-specific Google search query patterns, operator usage, and
parameter recommendations. This lives on the server so it can evolve
without client/CLI updates.

The MCP tool `nrev_search_patterns` fetches this data so Claude can
construct optimal queries at runtime.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Google Search Operators Reference
# ---------------------------------------------------------------------------

GOOGLE_OPERATORS = {
    "site": {
        "syntax": "site:domain.com",
        "description": "Restrict results to a specific domain or URL path",
        "examples": [
            "site:linkedin.com/in \"CTO\" \"fintech\"",
            "site:g2.com/products \"Salesforce\" reviews",
        ],
    },
    "inurl": {
        "syntax": "inurl:keyword",
        "description": "Results where the URL contains the keyword",
        "examples": [
            "inurl:pricing SaaS CRM",
            "inurl:careers \"machine learning\"",
        ],
    },
    "intitle": {
        "syntax": "intitle:keyword",
        "description": "Results where the page title contains the keyword",
        "examples": [
            "intitle:\"Series A\" fintech 2026",
            "intitle:review \"HubSpot\" vs \"Salesforce\"",
        ],
    },
    "filetype": {
        "syntax": "filetype:pdf",
        "description": "Restrict to specific file types (pdf, csv, xlsx, pptx, doc)",
        "examples": [
            "filetype:pdf \"market analysis\" SaaS 2026",
            "filetype:csv \"email list\" OR \"contact list\"",
        ],
    },
    "exclude": {
        "syntax": "-keyword",
        "description": "Exclude results containing the keyword",
        "examples": [
            "\"Acme Corp\" funding -reddit -quora",
            "site:linkedin.com/in \"VP Sales\" -recruiter",
        ],
    },
    "exact_match": {
        "syntax": "\"exact phrase\"",
        "description": "Match the exact phrase",
        "examples": [
            "\"raised $10M\" \"Series A\"",
            "\"head of growth\" \"SaaS\"",
        ],
    },
    "or_operator": {
        "syntax": "term1 OR term2",
        "description": "Match either term (must be uppercase OR)",
        "examples": [
            "\"VP Sales\" OR \"Head of Sales\" fintech",
            "\"Series A\" OR \"Series B\" announcement 2026",
        ],
    },
    "wildcard": {
        "syntax": "keyword * keyword",
        "description": "Wildcard matching for unknown words between terms",
        "examples": [
            "\"raised * million\" \"Series *\" 2026",
        ],
    },
}

# ---------------------------------------------------------------------------
# Time-Based Search (tbs parameter)
# ---------------------------------------------------------------------------

TBS_REFERENCE = {
    "description": (
        "The tbs parameter controls time-based filtering. Accept friendly "
        "names (hour, day, week, month, year) or raw Google tbs values for "
        "advanced control."
    ),
    "friendly_names": {
        "hour": {"tbs": "qdr:h", "description": "Last 1 hour"},
        "day": {"tbs": "qdr:d", "description": "Last 24 hours"},
        "week": {"tbs": "qdr:w", "description": "Last 7 days"},
        "month": {"tbs": "qdr:m", "description": "Last 30 days"},
        "year": {"tbs": "qdr:y", "description": "Last 12 months"},
    },
    "raw_tbs_values": {
        "qdr:h": "Last 1 hour",
        "qdr:h2": "Last 2 hours",
        "qdr:h6": "Last 6 hours",
        "qdr:h12": "Last 12 hours",
        "qdr:d": "Last 24 hours",
        "qdr:d3": "Last 3 days",
        "qdr:w": "Last 7 days",
        "qdr:w2": "Last 2 weeks",
        "qdr:m": "Last 30 days",
        "qdr:m3": "Last 3 months",
        "qdr:m6": "Last 6 months",
        "qdr:y": "Last 12 months",
    },
    "custom_date_range": {
        "syntax": "cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY",
        "example": "cdr:1,cd_min:03/01/2026,cd_max:03/16/2026",
        "description": "Custom date range between two specific dates",
    },
}

# ---------------------------------------------------------------------------
# Platform-Specific Search Patterns
# ---------------------------------------------------------------------------

PLATFORM_PATTERNS: dict[str, dict] = {
    # ===== LinkedIn =====
    "linkedin_profiles": {
        "platform": "LinkedIn",
        "category": "people",
        "description": "Find LinkedIn profiles of specific people by title, company, or location",
        "site_prefix": "site:linkedin.com/in",
        "query_template": "site:linkedin.com/in \"{title}\" \"{company}\"",
        "examples": [
            {
                "intent": "Find CTOs at fintech companies",
                "query": "site:linkedin.com/in \"CTO\" OR \"Chief Technology Officer\" \"fintech\"",
            },
            {
                "intent": "Find VP Sales in San Francisco",
                "query": "site:linkedin.com/in \"VP Sales\" OR \"Vice President Sales\" \"San Francisco\"",
            },
            {
                "intent": "Find specific person",
                "query": "site:linkedin.com/in \"John Smith\" \"Acme Corp\"",
            },
        ],
        "tips": [
            "Use /in to get profile pages only, not company pages or posts",
            "Quote exact titles for precision: \"VP Sales\" not VP Sales",
            "Add location in quotes for geo-targeting",
            "Exclude recruiters: -recruiter -staffing -\"talent acquisition\"",
            "Combine with company: \"CTO\" \"Stripe\" site:linkedin.com/in",
        ],
        "recommended_params": {"num": 30},
    },
    "linkedin_posts": {
        "platform": "LinkedIn",
        "category": "content",
        "description": "Find LinkedIn posts and articles by topic, author, or keyword",
        "site_prefix": "site:linkedin.com/posts OR site:linkedin.com/pulse",
        "query_template": "site:linkedin.com/posts \"{topic}\"",
        "examples": [
            {
                "intent": "Find recent posts about GTM engineering",
                "query": "site:linkedin.com/posts \"GTM engineering\" OR \"go-to-market\"",
                "params": {"tbs": "qdr:d"},
            },
            {
                "intent": "Find posts by a specific person",
                "query": "site:linkedin.com/posts \"Kyle Poyar\"",
            },
            {
                "intent": "Find thought leadership articles",
                "query": "site:linkedin.com/pulse \"product-led growth\" 2026",
            },
        ],
        "tips": [
            "Use /posts for feed posts, /pulse for long-form articles",
            "Always add tbs for recency — LinkedIn posts have short shelf life",
            "Author names may appear in URL: site:linkedin.com/posts/kylepoyar",
            "Combine both: site:linkedin.com/posts OR site:linkedin.com/pulse",
        ],
        "recommended_params": {"tbs": "qdr:w", "num": 20},
    },
    "linkedin_jobs": {
        "platform": "LinkedIn",
        "category": "jobs",
        "description": "Find specific job listing pages on LinkedIn",
        "site_prefix": "site:linkedin.com/jobs/view",
        "query_template": "site:linkedin.com/jobs/view \"{title}\" \"{company_or_location}\"",
        "examples": [
            {
                "intent": "Find senior engineer jobs at Series B startups",
                "query": "site:linkedin.com/jobs/view \"senior engineer\" \"Series B\" OR \"startup\"",
            },
            {
                "intent": "Find GTM roles in New York",
                "query": "site:linkedin.com/jobs/view \"go-to-market\" OR \"GTM\" \"New York\"",
            },
            {
                "intent": "Find a company's open roles (hiring signal)",
                "query": "site:linkedin.com/jobs/view \"Acme Corp\"",
                "params": {"tbs": "qdr:m"},
            },
        ],
        "tips": [
            "Use /jobs/view for individual job detail pages (not /jobs/search)",
            "This is a powerful hiring signal — many open jobs = growth mode",
            "Add tbs:qdr:w for recently posted jobs only",
            "Count results (num:100) to gauge hiring velocity",
        ],
        "recommended_params": {"tbs": "qdr:m", "num": 50},
    },
    "linkedin_companies": {
        "platform": "LinkedIn",
        "category": "companies",
        "description": "Find LinkedIn company pages",
        "site_prefix": "site:linkedin.com/company",
        "query_template": "site:linkedin.com/company \"{industry}\" \"{location}\"",
        "examples": [
            {
                "intent": "Find AI startups on LinkedIn",
                "query": "site:linkedin.com/company \"artificial intelligence\" \"startup\" \"San Francisco\"",
            },
        ],
        "tips": [
            "Use /company to get company pages specifically",
            "Results show companies that mention terms in their LinkedIn description",
        ],
        "recommended_params": {"num": 30},
    },

    # ===== Twitter/X =====
    "twitter_posts": {
        "platform": "Twitter/X",
        "category": "content",
        "description": "Find specific tweets and threads by topic or author",
        "site_prefix": "site:x.com/*/status",
        "query_template": "site:x.com/*/status \"{topic}\"",
        "examples": [
            {
                "intent": "Find tweets about a product launch",
                "query": "site:x.com/*/status \"Acme Corp\" \"launch\" OR \"announcing\"",
                "params": {"tbs": "qdr:w"},
            },
            {
                "intent": "Find tweets by a specific person",
                "query": "site:x.com/elonmusk/status",
                "params": {"tbs": "qdr:d"},
            },
            {
                "intent": "Find discussions about a tool",
                "query": "site:x.com/*/status \"Claude\" \"AI coding\" -retweet",
            },
        ],
        "tips": [
            "Use /*/status to match any user's tweets — the * is the username wildcard",
            "Use /username/status to get tweets from a specific user",
            "Add -retweet or -RT to filter out retweets for original content only",
            "Google indexes tweets with a delay — very recent tweets (< 1 hour) may not appear",
            "x.com and twitter.com both work but x.com is more current",
        ],
        "recommended_params": {"tbs": "qdr:w", "num": 20},
    },
    "twitter_profiles": {
        "platform": "Twitter/X",
        "category": "people",
        "description": "Find Twitter/X profiles of specific people or companies",
        "site_prefix": "site:x.com",
        "query_template": "site:x.com \"{name}\" -status -hashtag -i",
        "examples": [
            {
                "intent": "Find a person's Twitter profile",
                "query": "site:x.com \"Jason Lemkin\" SaaS -status -hashtag",
            },
            {
                "intent": "Find SaaS founders on Twitter",
                "query": "site:x.com \"founder\" \"SaaS\" -status -hashtag -/i/",
            },
        ],
        "tips": [
            "Exclude -status to avoid matching individual tweets",
            "Exclude -hashtag to avoid hashtag pages",
            "Exclude -/i/ to avoid Twitter internal pages (lists, moments)",
            "Profile URLs are x.com/username with no path segments",
        ],
        "recommended_params": {"num": 20},
    },

    # ===== Reddit =====
    "reddit_discussions": {
        "platform": "Reddit",
        "category": "content",
        "description": "Find Reddit discussions and reviews about products, tools, or topics",
        "site_prefix": "site:reddit.com",
        "query_template": "site:reddit.com \"{product_or_topic}\"",
        "examples": [
            {
                "intent": "Find honest reviews of a product",
                "query": "site:reddit.com \"HubSpot\" review OR experience OR \"switched from\"",
            },
            {
                "intent": "Find discussions in a specific subreddit",
                "query": "site:reddit.com/r/SaaS \"pricing\" OR \"cost\"",
            },
            {
                "intent": "Find competitor comparisons",
                "query": "site:reddit.com \"Salesforce\" vs \"HubSpot\" OR \"alternative\"",
            },
        ],
        "tips": [
            "Reddit is gold for unfiltered product opinions and comparisons",
            "Use /r/subreddit to target specific communities",
            "Keywords like 'alternative to', 'switched from', 'vs' surface buying-intent discussions",
            "Add tbs:qdr:y for recent but not just today (Reddit discussions age well)",
        ],
        "recommended_params": {"tbs": "qdr:y", "num": 20},
    },

    # ===== Instagram (non-traditional list building) =====
    "instagram_businesses": {
        "platform": "Instagram",
        "category": "businesses",
        "description": "Find local businesses, creators, and brands via Instagram profiles. Powerful for non-traditional list building where Apollo/LinkedIn won't have the data.",
        "site_prefix": "site:instagram.com",
        "query_template": "site:instagram.com \"{business_type}\" \"{location}\"",
        "examples": [
            {
                "intent": "Find bakeries in San Jose",
                "query": "site:instagram.com bakery OR bakeries \"San Jose\"",
            },
            {
                "intent": "Find fitness trainers in Austin",
                "query": "site:instagram.com \"personal trainer\" OR \"fitness coach\" \"Austin\"",
            },
            {
                "intent": "Find restaurants in a neighborhood",
                "query": "site:instagram.com restaurant OR cafe \"Brooklyn\" \"New York\"",
            },
            {
                "intent": "Find DTC brands in a category",
                "query": "site:instagram.com \"skincare\" \"organic\" \"shop\" OR \"order\"",
            },
        ],
        "tips": [
            "Instagram is the best source for local businesses, creators, and DTC brands",
            "These businesses are NOT on Apollo, LinkedIn, or traditional B2B databases",
            "Combine with location for local list building",
            "Look for commercial signals: 'shop', 'order', 'book', 'DM to order'",
            "Scrape the resulting Instagram URLs to extract business info",
        ],
        "recommended_params": {"num": 50},
    },

    # ===== YouTube =====
    "youtube_content": {
        "platform": "YouTube",
        "category": "content",
        "description": "Find YouTube videos by topic, channel, or speaker",
        "site_prefix": "site:youtube.com/watch",
        "query_template": "site:youtube.com/watch \"{topic}\"",
        "examples": [
            {
                "intent": "Find product demos",
                "query": "site:youtube.com/watch \"Acme Corp\" demo OR walkthrough",
            },
            {
                "intent": "Find conference talks",
                "query": "site:youtube.com/watch \"SaaStr\" 2026 \"go-to-market\"",
            },
        ],
        "tips": [
            "Use /watch to get video pages only (not channel pages or playlists)",
            "Great for finding product demos, conference talks, and reviews",
        ],
        "recommended_params": {"tbs": "qdr:m", "num": 20},
    },

    # ===== GitHub =====
    "github_repos": {
        "platform": "GitHub",
        "category": "tech",
        "description": "Find GitHub repositories, tech stack signals, and developer tools",
        "site_prefix": "site:github.com",
        "query_template": "site:github.com \"{technology}\" \"{keyword}\"",
        "examples": [
            {
                "intent": "Find companies using a specific technology",
                "query": "site:github.com \"uses: postgres\" OR \"postgresql\" \"production\"",
            },
            {
                "intent": "Find open-source projects in a category",
                "query": "site:github.com \"CRM\" \"open source\" stars",
            },
        ],
        "tips": [
            "Great for tech stack intelligence",
            "Look at /company-name repos for open-source signals",
        ],
        "recommended_params": {"num": 30},
    },

    # ===== G2 / Review Sites =====
    "g2_reviews": {
        "platform": "G2",
        "category": "reviews",
        "description": "Find G2 product reviews, comparisons, and category pages",
        "site_prefix": "site:g2.com",
        "query_template": "site:g2.com/products \"{product}\"",
        "examples": [
            {
                "intent": "Find product reviews",
                "query": "site:g2.com/products \"Salesforce\" reviews",
            },
            {
                "intent": "Find category comparisons",
                "query": "site:g2.com \"best CRM\" OR \"top CRM\" 2026",
            },
            {
                "intent": "Find alternatives",
                "query": "site:g2.com \"alternative\" \"HubSpot\"",
            },
        ],
        "tips": [
            "G2 is the standard for B2B software reviews",
            "Use /products for product-specific pages",
            "Great for competitive intelligence and buyer sentiment",
        ],
        "recommended_params": {"num": 20},
    },

    # ===== Crunchbase =====
    "crunchbase_companies": {
        "platform": "Crunchbase",
        "category": "companies",
        "description": "Find company funding, acquisition, and profile data",
        "site_prefix": "site:crunchbase.com/organization",
        "query_template": "site:crunchbase.com/organization \"{company}\"",
        "examples": [
            {
                "intent": "Find company profile and funding",
                "query": "site:crunchbase.com/organization \"Acme\"",
            },
            {
                "intent": "Find recently funded companies",
                "query": "site:crunchbase.com \"Series A\" \"announced\" 2026",
                "params": {"tbs": "qdr:m"},
            },
        ],
        "tips": [
            "Use /organization for company profiles",
            "Combine with tbs for recently funded companies",
        ],
        "recommended_params": {},
    },

    # ===== Google Maps / Yelp (local businesses) =====
    "local_businesses": {
        "platform": "Google Maps / Yelp",
        "category": "businesses",
        "description": "Find local businesses for non-traditional list building",
        "site_prefix": "site:yelp.com",
        "query_template": "site:yelp.com \"{business_type}\" \"{city}\"",
        "examples": [
            {
                "intent": "Find dentists in Chicago",
                "query": "site:yelp.com \"dentist\" \"Chicago, IL\"",
            },
            {
                "intent": "Find hair salons in Miami",
                "query": "site:yelp.com \"hair salon\" \"Miami\"",
            },
        ],
        "tips": [
            "Yelp is great for service businesses with physical locations",
            "Combine with Instagram search for full local business coverage",
            "Google Maps results often appear in regular search too — no site: needed",
        ],
        "recommended_params": {"num": 50},
    },

    # ===== Glassdoor (hiring intelligence) =====
    "glassdoor_company": {
        "platform": "Glassdoor",
        "category": "hiring",
        "description": "Find company culture, salary, and hiring insights",
        "site_prefix": "site:glassdoor.com",
        "query_template": "site:glassdoor.com \"{company}\"",
        "examples": [
            {
                "intent": "Find company reviews and culture",
                "query": "site:glassdoor.com/Reviews \"Acme Corp\"",
            },
            {
                "intent": "Find salary data",
                "query": "site:glassdoor.com/Salary \"product manager\" \"San Francisco\"",
            },
        ],
        "tips": [
            "Use /Reviews for employee reviews, /Salary for compensation data",
            "Good for understanding company culture before outreach",
        ],
        "recommended_params": {},
    },
}

# ---------------------------------------------------------------------------
# GTM Use Case Patterns
# ---------------------------------------------------------------------------

GTM_USE_CASES: dict[str, dict] = {
    "funding_news": {
        "description": "Find companies that recently raised funding",
        "query_pattern": "\"{company}\" \"raised\" OR \"funding\" OR \"Series\" OR \"announces\"",
        "recommended_tbs": "qdr:m",
        "tips": [
            "Funded companies are in growth mode — ideal outreach timing",
            "Combine with site:techcrunch.com OR site:crunchbase.com for signal quality",
        ],
    },
    "hiring_signals": {
        "description": "Identify companies actively hiring (growth signal)",
        "query_pattern": "\"{company}\" hiring OR \"job openings\" OR \"we're growing\"",
        "recommended_tbs": "qdr:w",
        "platforms": ["linkedin_jobs"],
        "tips": [
            "Heavy hiring = budget available = good time to sell",
            "Use linkedin_jobs pattern for detailed job listings",
            "Count open roles: many listings in one department = that team is scaling",
        ],
    },
    "leadership_changes": {
        "description": "Find new executive hires or departures",
        "query_pattern": "\"{company}\" \"appointed\" OR \"new CEO\" OR \"joins as\" OR \"promoted to\"",
        "recommended_tbs": "qdr:m",
        "tips": [
            "New leaders often bring new tools — 90 day window is prime outreach time",
            "New CRO/VP Sales often reevaluates the entire sales tech stack",
        ],
    },
    "competitor_intelligence": {
        "description": "Research competitor products, pricing, and positioning",
        "query_pattern": "\"{competitor}\" pricing OR plans OR features",
        "platforms": ["g2_reviews", "reddit_discussions"],
        "tips": [
            "Combine site:g2.com for reviews and site:reddit.com for unfiltered opinions",
            "Search for 'alternative to {competitor}' to find dissatisfied users",
            "Search for '{competitor} vs' to see comparison discussions",
        ],
    },
    "tech_stack_discovery": {
        "description": "Discover what tools/technologies a company uses",
        "query_pattern": "\"{company}\" uses OR \"built with\" OR \"powered by\" OR \"tech stack\"",
        "platforms": ["github_repos"],
        "tips": [
            "Check job listings for required technologies — reveals actual stack",
            "site:stackshare.io \"{company}\" for curated tech stacks",
            "GitHub repos reveal open-source usage and engineering culture",
        ],
    },
    "non_traditional_list_building": {
        "description": "Build lists of businesses NOT in traditional B2B databases (local businesses, creators, DTC brands)",
        "query_pattern": "site:instagram.com \"{business_type}\" \"{location}\"",
        "platforms": ["instagram_businesses", "local_businesses"],
        "tips": [
            "Apollo/LinkedIn don't have bakeries, hair salons, fitness studios, etc.",
            "Instagram + Yelp + Google Maps cover the long tail of local businesses",
            "Combine site:instagram.com results with scraping for contact info",
            "Google search 'business_type near location' without site: to get Google Maps results",
        ],
    },
    "content_research": {
        "description": "Find relevant content, thought leaders, and discussions on a topic",
        "platforms": ["linkedin_posts", "twitter_posts", "reddit_discussions", "youtube_content"],
        "query_pattern": "\"{topic}\" site:linkedin.com/posts OR site:x.com/*/status",
        "recommended_tbs": "qdr:w",
        "tips": [
            "Use multiple platform patterns for comprehensive coverage",
            "Add tbs for recency — content older than a month is stale for most GTM purposes",
            "Identify thought leaders by finding who posts most about a topic",
        ],
    },
    "buying_intent": {
        "description": "Find people or companies showing buying intent for a category",
        "query_pattern": "\"looking for\" OR \"recommend\" OR \"alternative to\" OR \"switched to\" \"{category}\"",
        "platforms": ["reddit_discussions", "twitter_posts"],
        "recommended_tbs": "qdr:m",
        "tips": [
            "Reddit 'what CRM do you use' threads are gold for intent signals",
            "Twitter posts asking for recommendations show active buying intent",
            "'Switched from X to Y' reveals dissatisfied users of X",
        ],
    },
}


# ---------------------------------------------------------------------------
# Dynamic knowledge cache — loaded from DB, refreshed on demand
# ---------------------------------------------------------------------------

_dynamic_search_patterns: dict[str, dict] = {}


def load_dynamic_patterns(patterns: dict[str, dict]) -> None:
    """Replace the in-memory dynamic patterns cache.

    Called at server startup and when an admin merges new search patterns.
    """
    global _dynamic_search_patterns
    _dynamic_search_patterns = dict(patterns)


def get_dynamic_patterns() -> dict[str, dict]:
    """Return the current dynamic patterns cache."""
    return dict(_dynamic_search_patterns)


def _lookup_pattern(key: str) -> tuple[dict | None, str | None]:
    """Look up a pattern by key, checking hardcoded first, then dynamic.

    Returns (pattern_dict, source) where source is 'builtin' or 'dynamic'.
    """
    if key in PLATFORM_PATTERNS:
        return PLATFORM_PATTERNS[key], "builtin"
    if key in _dynamic_search_patterns:
        return _dynamic_search_patterns[key], "dynamic"
    return None, None


# ---------------------------------------------------------------------------
# Build the full response
# ---------------------------------------------------------------------------


def get_search_patterns(
    *,
    platform: str | None = None,
    use_case: str | None = None,
) -> dict:
    """Return search patterns, optionally filtered by platform or use case.

    If platform is specified, returns only that platform's pattern.
    If use_case is specified, returns that use case with relevant platforms.
    If neither, returns the full reference.

    Checks hardcoded PLATFORM_PATTERNS first, then dynamic_knowledge cache.
    """
    # Merged view of all patterns (hardcoded + dynamic)
    all_patterns = {**PLATFORM_PATTERNS, **_dynamic_search_patterns}

    if platform:
        pattern, source = _lookup_pattern(platform)
        if not pattern:
            # Try fuzzy match across both hardcoded and dynamic
            matches = [
                k for k in all_patterns
                if platform.lower() in k.lower()
                or platform.lower() in all_patterns[k].get("platform", "").lower()
            ]
            if matches:
                result_patterns = {}
                for k in matches:
                    p, s = _lookup_pattern(k)
                    if p:
                        result_patterns[k] = {**p, "_source": s}
                return {
                    "patterns": result_patterns,
                    "tbs_reference": TBS_REFERENCE,
                    "operators": GOOGLE_OPERATORS,
                }
            return {
                "error": f"Unknown platform: '{platform}'",
                "available": sorted(all_patterns.keys()),
                "hint": "No pattern found. Use the Experimental Protocol: run a broad search first, analyze URLs, then refine.",
            }
        return {
            "patterns": {platform: {**pattern, "_source": source}},
            "tbs_reference": TBS_REFERENCE,
            "operators": GOOGLE_OPERATORS,
        }

    if use_case:
        uc = GTM_USE_CASES.get(use_case)
        if not uc:
            matches = [
                k for k in GTM_USE_CASES
                if use_case.lower() in k.lower()
            ]
            if matches:
                result_cases = {k: GTM_USE_CASES[k] for k in matches}
                # Include referenced platform patterns
                referenced_platforms = set()
                for case in result_cases.values():
                    referenced_platforms.update(case.get("platforms", []))
                return {
                    "use_cases": result_cases,
                    "platform_patterns": {
                        k: PLATFORM_PATTERNS[k]
                        for k in referenced_platforms
                        if k in PLATFORM_PATTERNS
                    },
                    "tbs_reference": TBS_REFERENCE,
                    "operators": GOOGLE_OPERATORS,
                }
            return {"error": f"Unknown use case: '{use_case}'", "available": sorted(GTM_USE_CASES.keys())}

        # Include referenced platform patterns
        platform_keys = uc.get("platforms", [])
        return {
            "use_cases": {use_case: uc},
            "platform_patterns": {
                k: PLATFORM_PATTERNS[k]
                for k in platform_keys
                if k in PLATFORM_PATTERNS
            },
            "tbs_reference": TBS_REFERENCE,
            "operators": GOOGLE_OPERATORS,
        }

    # Full reference — list available platforms and use cases
    all_platform_summary = {}
    for k, v in PLATFORM_PATTERNS.items():
        all_platform_summary[k] = {"description": v["description"], "site_prefix": v.get("site_prefix", ""), "_source": "builtin"}
    for k, v in _dynamic_search_patterns.items():
        if k not in all_platform_summary:
            all_platform_summary[k] = {"description": v.get("description", ""), "site_prefix": v.get("site_prefix", ""), "_source": "dynamic"}

    return {
        "platforms": all_platform_summary,
        "use_cases": {k: {"description": v["description"]} for k, v in GTM_USE_CASES.items()},
        "tbs_reference": TBS_REFERENCE,
        "operators": GOOGLE_OPERATORS,
        "hint": "Pass ?platform=linkedin_jobs or ?use_case=hiring_signals to get detailed patterns.",
    }
