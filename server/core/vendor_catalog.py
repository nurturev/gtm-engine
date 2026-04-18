"""Vendor catalog: Classified registry of all supported providers.

This is the single source of truth for vendor categories, credit costs,
and available-vs-provisioned key display in the dashboard.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Vendor Categories
# ---------------------------------------------------------------------------

VENDOR_CATALOG: dict[str, dict] = {
    # ── Enrichment ─────────────────────────────────────────────────────
    "apollo": {
        "category": "enrichment",
        "name": "Apollo.io",
        "description": "B2B person & company enrichment, people search",
        "operations": ["search_people", "enrich_person", "enrich_company"],
        "credit_costs": {"search_people": 2, "enrich_person": 1, "enrich_company": 1},
        "platform_key": True,  # nRev has a platform key
        "byok": True,
    },
    "rocketreach": {
        "category": "enrichment",
        "name": "RocketReach",
        "description": "Contact enrichment, phone numbers, alumni search",
        "operations": [
            "search_people",
            "enrich_person",
            "enrich_company",
            "search_companies",
        ],
        "credit_costs": {
            "search_people": 3,
            "enrich_person": 3,
            "enrich_company": 3,
            "search_companies": 3,
        },
        "platform_key": True,
        "byok": True,
    },
    "fresh_linkedin": {
        "category": "enrichment",
        "name": "Fresh LinkedIn",
        "description": "LinkedIn profile + company + post enrichment — fresher data direct from LinkedIn",
        "operations": [
            "enrich_person",
            "enrich_company",
            "fetch_profile_posts",
            "fetch_company_posts",
            "fetch_post_details",
            "fetch_post_reactions",
            "fetch_post_comments",
            "search_posts",
        ],
        "credit_costs": {
            "enrich_person": 3,
            "enrich_company": 3,
            "fetch_profile_posts": 3,
            "fetch_company_posts": 3,
            "fetch_post_details": 3,
            "fetch_post_reactions": 3,
            "fetch_post_comments": 3,
            "search_posts": 3,
        },
        "platform_key": True,
        "byok": True,
    },
    "bettercontact": {
        "category": "enrichment",
        "name": "BetterContact",
        "description": "Waterfall enrichment (email + phone finding across 15+ providers)",
        "operations": ["enrich_person"],
        "credit_costs": {"enrich_person": 0},  # BYOK only
        "platform_key": False,
        "byok": True,
    },
    "hunter": {
        "category": "enrichment",
        "name": "Hunter.io",
        "description": "Email finding, domain search, email verification",
        "operations": ["find_email", "verify_email", "domain_search"],
        "credit_costs": {"find_email": 0, "verify_email": 0, "domain_search": 0},
        "platform_key": False,
        "byok": True,
    },
    "clearbit": {
        "category": "enrichment",
        "name": "Clearbit",
        "description": "Company enrichment, visitor reveal, person enrichment",
        "operations": ["enrich_person", "enrich_company"],
        "credit_costs": {"enrich_person": 0, "enrich_company": 0},
        "platform_key": False,
        "byok": True,
    },
    "lusha": {
        "category": "enrichment",
        "name": "Lusha",
        "description": "B2B contact enrichment (emails, phone numbers)",
        "operations": ["enrich_person"],
        "credit_costs": {"enrich_person": 0},
        "platform_key": False,
        "byok": True,
    },

    # ── Verification ───────────────────────────────────────────────────
    "zerobounce": {
        "category": "verification",
        "name": "ZeroBounce",
        "description": "Email verification (deliverability, catch-all, disposable detection)",
        "operations": ["verify_email"],
        "credit_costs": {"verify_email": 0},
        "platform_key": False,
        "byok": True,
    },

    # ── Search & Scraping ──────────────────────────────────────────────
    "rapidapi": {
        "category": "search",
        "name": "Google Search (RapidAPI)",
        "description": "Google SERP, web search, site: operators",
        "operations": ["search_web", "google_search"],
        "credit_costs": {"search_web": 1, "google_search": 1},
        "platform_key": True,
        "byok": True,
    },
    "parallel": {
        "category": "search",
        "name": "Parallel Web",
        "description": "Web scraping, content extraction (anti-bot capable)",
        "operations": ["scrape_page"],
        "credit_costs": {"scrape_page": 1},
        "platform_key": True,
        "byok": True,
    },

    # ── Signals & Intelligence ─────────────────────────────────────────
    "predictleads": {
        "category": "signals",
        "name": "PredictLeads",
        "description": "Company signals — job openings, tech stack, funding, news",
        "operations": ["company_signals"],
        "credit_costs": {"company_signals": 1},
        "platform_key": True,
        "byok": True,
    },

    # ── Outreach ───────────────────────────────────────────────────────
    # Instantly and Lemlist removed from Data Providers —
    # they are connected as Apps (OAuth via Composio) in the Apps tab.

    # ── LLM / AI Research ─────────────────────────────────────────────
    "openai": {
        "category": "llm",
        "name": "OpenAI",
        "description": "GPT models for research, summarization, analysis",
        "operations": ["llm_completion"],
        "credit_costs": {"llm_completion": 0},
        "platform_key": False,
        "byok": True,
    },
    "anthropic": {
        "category": "llm",
        "name": "Anthropic (Claude)",
        "description": "Claude models for research, analysis, content generation",
        "operations": ["llm_completion"],
        "credit_costs": {"llm_completion": 0},
        "platform_key": False,
        "byok": True,
    },
    "perplexity": {
        "category": "llm",
        "name": "Perplexity",
        "description": "AI-powered web research and question answering",
        "operations": ["llm_completion"],
        "credit_costs": {"llm_completion": 0},
        "platform_key": False,
        "byok": True,
    },
}

# Category display metadata
VENDOR_CATEGORIES = {
    "enrichment": {"name": "Enrichment", "icon": "🔍", "description": "Contact & company data enrichment"},
    "verification": {"name": "Verification", "icon": "✓", "description": "Email & data verification"},
    "search": {"name": "Search & Scraping", "icon": "🌐", "description": "Web search, SERP, content extraction"},
    "signals": {"name": "Signals & Intelligence", "icon": "📊", "description": "Company signals, job postings, tech stack"},
    # outreach category removed — Instantly/Lemlist are now in Apps tab (Composio OAuth)
    "llm": {"name": "LLM / AI Research", "icon": "🤖", "description": "AI models for research, summarization, row-wise analysis"},
}



# Vendors with actual server-side execution handlers
INTEGRATED_PROVIDERS = {"apollo", "rocketreach", "predictleads", "parallel", "rapidapi", "fresh_linkedin"}

# All others are BYOK-only with skill files but no server-side handler yet
# They appear in the catalog as "Coming Soon" in the dashboard
COMING_SOON_PROVIDERS = {
    k for k in VENDOR_CATALOG if k not in INTEGRATED_PROVIDERS
}

def get_credit_cost(operation: str) -> int:
    """Get the credit cost for an operation. Returns 0 for BYOK-only operations.

    Delegates to the DB-backed cost cache when available, falling back to
    the hardcoded VENDOR_CATALOG values.
    """
    from server.billing.cost_config_service import get_base_cost, _cache_loaded
    if _cache_loaded:
        return int(get_base_cost(operation))
    # Fallback to hardcoded catalog (before DB cache is loaded)
    for vendor in VENDOR_CATALOG.values():
        if operation in vendor.get("credit_costs", {}):
            return vendor["credit_costs"][operation]
    return 1


def get_provider_for_operation(operation: str) -> str | None:
    """Get the primary provider for an operation."""
    for provider, vendor in VENDOR_CATALOG.items():
        if operation in vendor.get("operations", []):
            return provider
    return None


def get_vendors_by_category() -> dict[str, list[dict]]:
    """Group vendors by category for dashboard display."""
    grouped: dict[str, list[dict]] = {}
    for provider, info in VENDOR_CATALOG.items():
        cat = info["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append({"provider": provider, **info})
    return grouped
