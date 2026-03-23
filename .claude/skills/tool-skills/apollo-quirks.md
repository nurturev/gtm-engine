# Apollo.io API — Tool Quirks & Best Practices

## Critical Gotchas (Read Before ANY API Call)

### 1. `q_organization_domains` is a STRING, not an array
```json
// WRONG ❌
"q_organization_domains": ["apollo.io", "google.com"]

// CORRECT ✅
"q_organization_domains": "apollo.io\ngoogle.com"
```
Multiple domains are **newline-separated** within a single string. This is the #1 source of bugs.

### 2. People Search returns NO contact info
Search is FREE but returns `email_not_unlocked@domain.com` placeholder. You MUST call People Enrichment separately to get actual emails/phones. This is a two-step process:
1. Search (free) → get Apollo person IDs
2. Enrich (costs credits) → get emails, phones

### 3. Phone enrichment is ASYNC
Setting `reveal_phone_number=true` requires a `webhook_url`. Phone numbers are NOT in the synchronous response — they come via webhook later. The webhook must be HTTPS and idempotent.

### 4. Credits consumed on failed enrichments
If Apollo attempts to enrich but finds no match, you may still lose credits. Always search first (free) to confirm the person exists before enriching.

### 5. Employee range format uses COMMAS not dashes
```json
// WRONG ❌
"organization_num_employees_ranges": ["1-10", "11-50"]

// CORRECT ✅
"organization_num_employees_ranges": ["1,10", "11,50", "51,200", "201,500"]
```

### 6. 50,000 record hard ceiling
Max 100 per page × 500 pages = 50,000 records. If your query returns more, you CANNOT access records beyond this. Partition queries by location or seniority to work around it.

### 7. Intent data NOT available via API
Despite being a major UI feature, buying intent filters are API-only-not-exposed. You cannot filter by intent topics through the API.

## Endpoints Quick Reference

| Endpoint | Method | Credits | Notes |
|----------|--------|---------|-------|
| People Search | POST `/api/v1/mixed_people/api_search` | **FREE** | No contact info returned |
| Org Search | POST `/api/v1/mixed_companies/search` | Yes | Not on free plans |
| People Enrich | POST `/api/v1/people/match` | 1 credit/email, 8/phone | Best with email or LinkedIn URL |
| Bulk People Enrich | POST `/api/v1/people/bulk_match` | Same per person | Up to 10 at once, 50% rate limit |
| Org Enrich | GET `/api/v1/organizations/enrich` | Yes | Use domain as identifier |
| Bulk Org Enrich | POST `/api/v1/organizations/bulk_enrich` | Same per org | Up to 10 at once |
| Create Contact | POST `/api/v1/contacts` | Free | Save enriched person to avoid re-charges |
| Search Contacts | POST `/api/v1/contacts/search` | Free | Your saved contacts only |
| Add to Sequence | POST `/api/v1/emailer_campaigns/{id}/add_contact_ids` | Free | |

## People Search Filters

### Person-level
| Parameter | Type | Values/Notes |
|-----------|------|-------------|
| `person_titles` | string[] | OR logic. `["VP Sales", "Sales Director"]` |
| `person_seniorities` | string[] | `c_suite`, `founder`, `owner`, `vp`, `director`, `manager`, `senior`, `head`, `entry`, `intern` |
| `person_departments` | string[] | `engineering`, `sales`, `marketing`, `finance`, `human_resources`, `operations`, `information_technology`, `legal`, `product_management` |
| `person_locations` | string[] | Where person lives (NOT company HQ). `["California, US"]` |
| `q_keywords` | string | Free text across person records |
| `contact_email_status` | string[] | `["verified"]` for best results |
| `include_similar_titles` | boolean | Broadens fuzzy title matching |

### Organization-level (within people search)
| Parameter | Type | Values/Notes |
|-----------|------|-------------|
| `q_organization_domains` | **STRING** | Newline-separated! `"apollo.io\ngoogle.com"` |
| `q_organization_name` | string | Company name search |
| `organization_locations` | string[] | Company HQ. `["San Francisco, CA"]` |
| `organization_not_locations` | string[] | Exclude HQ locations |
| `organization_num_employees_ranges` | string[] | `["1,10"]`, `["51,200"]`, `["501,1000"]` |
| `q_organization_keyword_tags` | string[] | Industry keywords. `["technology", "saas"]` |
| `organization_latest_funding_stage_cd` | string[] | `["seed", "series_a", "series_b"]` |
| `currently_using_any_of_technology_uids` | string[] | Tech UIDs: `["salesforce"]`, `["hubspot"]`. 1,500+ available. |

### Pagination
| Parameter | Type | Notes |
|-----------|------|-------|
| `page` | int | 1-500 max |
| `per_page` | int | 1-100 max |

## People Enrichment — Best Identifiers (Ranked)

1. **email** — near 100% match if person is in Apollo DB
2. **linkedin_url** — near 100% match
3. **first_name + last_name + domain** — good match rate
4. **name + organization_name** — moderate
5. **name alone** — poor, may return wrong person

### Enrichment parameters
```json
{
  "first_name": "John",
  "last_name": "Doe",
  "domain": "company.com",
  "reveal_personal_emails": false,
  "reveal_phone_number": false,
  "run_waterfall_email": true
}
```
- `reveal_personal_emails` — costs credits, returns personal Gmail/Yahoo etc.
- `reveal_phone_number` — costs 8 credits, **requires webhook_url**
- `run_waterfall_email` — tries multiple sources, ~5% more emails, 45% fewer bounces

## Rate Limits

| Plan | Per Minute | Per Day |
|------|-----------|---------|
| Free | 50 | 600 |
| Basic ($49/mo) | 200 | 2,000 |
| Professional ($79/mo) | 200 | 2,000 |
| Organization ($119/mo) | Custom | Custom |

Bulk endpoints throttled to 50% of single endpoint's per-minute limit.

## Credit Costs

| Action | Credits |
|--------|---------|
| People Search | **FREE** |
| Email enrichment | 1 |
| Phone enrichment | 8 |
| Org search | 1 export credit |
| Org enrichment | Credits (varies) |
| Creating contacts/accounts/deals | FREE |
| Searching your own data | FREE |

Free plan: 10,000 email credits/month (with verified corporate domain), only 100 without.
Credits do NOT roll over.

## Accuracy Reality Check

| Data Point | Claimed | Real-World |
|------------|---------|------------|
| Email verification | 91% | 65-70% |
| Bounce rate | Low | 35% reported in some cases |
| Phone numbers | Available | "A disaster" — expensive (8 credits) and often inaccurate |
| European data | Available | Notably weaker than US |
| Company firmographics | Strong | Revenue estimates rough for private companies |

**Always verify Apollo emails through ZeroBounce/NeverBounce before sending campaigns.**

## Advanced Techniques

### Title fallback strategy
Define 3 title tiers per search:
1. Primary: `["CTO", "VP Engineering"]`
2. Fallback: `["Director of Engineering", "Head of Engineering"]`
3. Last resort: `["Engineering Manager", "Senior Engineering Manager"]`

### Technology-based prospecting
Use `currently_using_any_of_technology_uids` with snake_case UIDs: `salesforce`, `hubspot`, `marketo`, `slack`, `intercom`, `zendesk`, etc. 1,500+ technologies tracked.

### Efficient pattern
```
Search (free) → filter → page through results → Bulk Enrich (10/call) → Create Contacts (avoid re-charges)
```

### When Apollo is NOT the right choice
- Alumni/previous employer search → use **RocketReach**
- Non-standard businesses (local, D2C) → use **Google + Parallel Web**
- Enterprise-grade data depth + org charts → use **ZoomInfo** ($15k+/yr)
- Maximum email accuracy → use **waterfall** (Clay/nrev-lite with multiple providers)
- International/European data → Apollo is weak here
