# RocketReach API — Tool Quirks & Best Practices

## Critical Gotchas (Read Before ANY API Call)

### 1. `previous_employer` is FREE TEXT array, not a domain
```json
// WRONG
"previous_employer": ["mindtickle.com"]

// CORRECT
"previous_employer": ["mindtickle"]
// Better — cast a wide net with OR logic:
"previous_employer": ["mindtickle", "MindTickle", "Mind Tickle"]
```
This is a **free-text array** with fuzzy/NLP matching against LinkedIn-sourced company names. Multiple values in the array use **OR logic**. Company names vary in RocketReach records — always try multiple variations:
- `["yellow.ai", "Yellow.ai", "Yellow AI"]`
- `["mindtickle", "MindTickle", "Mind Tickle"]`
- `["hubspot", "HubSpot"]`

**For exact match:** Wrap in escaped double quotes: `["\"IBM\""]` — but this EXCLUDES subsidiaries like "IBM UK" or "IBM India".

**Most reliable alternative:** Look up the RocketReach company ID first via Company Search, then use `previous_company_id` instead of free text.

### 2. Lookups are ASYNC by default
Initial lookup response often returns `status: "searching"` or `"progress"`, NOT the final data. You MUST:
- Poll the lookup endpoint with the returned profile `id` (max 1 call/second), OR
- Use a `webhook_id` for async delivery, OR
- Set `return_cached_emails: true` (default) to receive cached results immediately while the live lookup continues

**Do NOT assume the first response is complete.** Check the `status` field.

### 3. All Universal endpoints cost 3 credits per call ($0.03). 18 credits when phone is requested.
```
Universal Person Search   → 3 credits per call
Universal Person Lookup   → 3 credits (email-only) | 18 credits (with reveal_phone: true)
Universal Company Search  → 3 credits per call
Universal Company Lookup  → 3 credits per call
```
Phone enrichment is **6× more expensive** — only set `reveal_phone: true` when phone is actually needed. No credit charged if no verified contact info is returned.

### 4. Search results do NOT include contact info
Despite showing names/titles/companies, search results do NOT include emails or phones. You must do a separate Lookup call for contact data. **Two-step process always** (search 3 credits → lookup 3 credits = 6 credits per enriched person, 21 with phone).

### 5. `current_employer` is also free text array (same rules as previous_employer)
```json
// WRONG
"current_employer": ["salesforce.com"]

// CORRECT
"current_employer": ["Salesforce"]
// For more reliable matching, use company_domain instead:
"company_domain": ["salesforce.com"]
```

### 6. Authentication is via `Api-Key` header
```
Api-Key: your_api_key_here
```
NOT `Authorization: Bearer`. NOT a query parameter. The header name is literally `Api-Key`.

### 7. Pagination `start` is 1-indexed, max 10,000
```json
// First page
"start": 1, "page_size": 100

// Second page
"start": 2, "page_size": 100
```
Max `page_size` is 100. Max `start` is 10,000 — you cannot paginate beyond 10,000 results.

### 8. `return_cached_emails` default changing May 2026
Currently defaults to `true` (returns cached/potentially stale emails immediately). After **May 1, 2026**, defaults to `false`. Set explicitly to avoid surprises.

### 9. Email grades matter — only send to A/A- grades
| Grade | Deliverability | Action |
|-------|---------------|--------|
| A | ~98% | Safe to send |
| A- | ~90% | Safe to send |
| B | 75-85% | Verify independently first |
| F | Invalid | Do NOT send |

### 10. No credit charged if no verified contact found
Unlike Apollo (which may consume credits on failed enrichments), RocketReach only charges when at least one verified data point is returned. Re-lookups of the same profile are also FREE.

### 11. Multi-department queries — single request, OR logic
Multiple departments can be searched in ONE query — no need for separate API calls:
```json
// ONE call, not two ✅
"department": ["Sales", "Marketing"]
```
Same applies to titles, locations, industries, and all other array filters. Multiple values within a single filter use OR logic.

### 12. `include_past_titles` toggle controls which API field is used
- `current_title` — matches **current title only**
- `current_or_previous_title` — matches current OR past titles
- The node uses a boolean `include_past_titles` toggle that switches between these two API fields under the hood. Don't try to set both — the toggle picks one.

### 13. Company Revenue — empty-field handling
The API rejects empty min/max values. Apply these defaults:
- **Both empty:** don't send the parameter at all
- **Only min empty:** send min as `0`
- **Only max empty:** send max as `1000000000000` (1 trillion sentinel)

### 14. Company Size — special max value for `100001+`
If the user selects the open-ended `100001+` bracket, send max as `10000000` (10 million sentinel). The literal `100001+` string will fail validation.

### 15. Department Growth — structured string format
The `growth` filter takes a structured string combining department, time range, and percentage range:
```
min_pct-max_pct::Department,TimeRange
```
- TimeRange values: `six_months`, `one_year`
- Example: `5-30::Engineering,six_months` — 5-30% growth in Engineering over 6 months
- Negative growth: `-10--20::Sales,one_year` — 10-20% decline in Sales over 1 year

**Common growth presets:**
| Pattern | Range |
|---|---|
| Surge | `5-25` |
| Aggressive Hiring | `25-50` |
| Growth Rocket | `50-` |
| Slowdown | `-5--10` |
| Layoffs | `-10-` |

## Endpoints Quick Reference (Universal API only)

RocketReach has consolidated on the **Universal API**. Use ONLY these endpoints — the older `/api/v2/*` endpoints are deprecated for our purposes.

| Endpoint | Reference | Cost |
|---|---|---|
| Universal Person Lookup | [`create_universal_person_lookup`](https://docs.rocketreach.co/reference/create_universal_person_lookup) | 3 credits (email) / 18 credits (with `reveal_phone: true`) |
| Universal Person Search | [`create_universal_person_search`](https://docs.rocketreach.co/reference/create_universal_person_search) | 3 credits per call |
| Universal Company Lookup | [`create_universal_company_lookup`](https://docs.rocketreach.co/reference/create_universal_company_lookup) | 3 credits per call |
| Universal Company Search | [`create_universal_company_search`](https://docs.rocketreach.co/reference/create_universal_company_search) | 3 credits per call |

**Auth:** `Api-Key: <your_key>` header (see Gotcha #6).

**No credit charged** if no verified contact info is returned. Re-lookups of the same profile are FREE.

## Search Filters (People)

### Employment — Current
| Parameter | Type | Notes |
|-----------|------|-------|
| `current_employer` | string[] | **Free text** array — company names, OR logic |
| `current_title` | string[] | OR logic. `["VP Sales", "Director Sales"]` |
| `company_name` | string[] | Employer company names |
| `company_domain` | string[] | **More reliable** than company name — use actual domains |
| `company_id` | string[] | RocketReach company IDs (most reliable) |

### Employment — Previous (Alumni Search)
| Parameter | Type | Notes |
|-----------|------|-------|
| `previous_employer` | string[] | **Free text** — the killer feature |
| `previous_company_id` | string[] | More reliable than free text |
| `previous_title` | string[] | Filter by what they did at previous company |
| `current_or_previous_title` | string[] | Matches either current or past |
| `job_change_range_days` | string[] | Filter by recent job changes — find fresh alumni |

### Company Firmographics (applied to current employer)
| Parameter | Type | Notes |
|-----------|------|-------|
| `company_industry` | string[] | Industry classification |
| `company_industry_keywords` | string[] | Broader keyword match |
| `company_size` | string[] | Employee range (e.g., `["51-200"]`) |
| `company_revenue` | string[] | Revenue brackets |
| `company_funding_min` / `company_funding_max` | number | Funding range filter |
| `company_publicly_traded` | boolean | Public vs private |
| `company_competitors` | string[] | Find people at companies competing with... |
| `company_intent` | string[] | Intent signals like `"hiring"` |
| `company_tag` | string[] | Tags like `"unicorn"` |

### Location
| Parameter | Type | Notes |
|-----------|------|-------|
| `geo` | string[] | Region (e.g., `"North America"`) |
| `country_code` | string[] | Country filter |
| `state` | string[] | State/province |
| `city` | string[] | City |
| `postal_code` | string[] | Zip/postal code |

Supports radius search: append `::~50mi` to location string.

### Professional / Demographics
| Parameter | Type | Notes |
|-----------|------|-------|
| `department` | string[] | Department classification |
| `management_levels` | string[] | `"C-Level"`, `"Director"`, `"VP"` etc. |
| `years_experience` | string[] | Experience range |
| `skills` | string[] | OR logic across skills |
| `all_skills` | string[] | AND logic — must match ALL |
| `contact_method` | string[] | `"mobile"`, `"phone"`, `"personal_email"`, `"work_email"` |

### Education
| Parameter | Type | Notes |
|-----------|------|-------|
| `school` | string[] | University/college — **always send multiple variants** (see School Name Expansion below) |
| `degree` | string[] | Degree type |
| `major` | string[] | Field of study |

#### School Name Expansion (mandatory for alumni search)

RocketReach matches school names against LinkedIn-sourced text, which varies in format. **Always generate multiple variants for every school** — never send a single name.

For every school input, generate ALL of these:
1. **Common abbreviation** — e.g., `IIT KGP`
2. **Abbreviation + city** — e.g., `IIT Kharagpur`
3. **Full institute name + city** — e.g., `Indian Institute of Technology Kharagpur`
4. **Full name with comma for location** — e.g., `Indian Institute of Technology, Kharagpur`

Examples:
```json
// User says "IIT KGP"
"school": [
  "IIT KGP", "IIT Kharagpur",
  "Indian Institute of Technology Kharagpur",
  "Indian Institute of Technology, Kharagpur"
]

// User says "MIT"
"school": [
  "MIT", "Massachusetts Institute of Technology",
  "Massachusetts Institute of Technology, Cambridge"
]

// User says "ISB"
"school": [
  "ISB", "ISB Hyderabad",
  "Indian School of Business",
  "Indian School of Business, Hyderabad"
]
```

### Healthcare (specialized)
| Parameter | Type | Notes |
|-----------|------|-------|
| `health_npi` | string[] | NPI number lookup |
| `health_credentials` | string[] | Medical credentials |
| `health_specialization` | string[] | Medical specialty |

### Boolean Logic Rules
- **Same filter, multiple values = OR**: `current_title: ["CEO", "CTO"]` matches either
- **Different filters = AND**: title + location + industry must ALL match
- **Exclude with `-` prefix**: `current_title: ["Engineer", "-Senior", "-Sr"]`
- **Dedicated exclude fields**: `exclude_current_employer`, `exclude_current_title`, etc.
- **Exact match**: Wrap in escaped double quotes `["\"IBM\""]` — excludes subsidiaries

### Pagination & Sorting
| Parameter | Type | Notes |
|-----------|------|-------|
| `start` | int | Page number, 1-indexed, max 10,000 |
| `page_size` | int | 1-100 max |
| `order_by` | string | `"relevance"`, `"popularity"`, `"score"` |

## The Alumni Search Superpower

RocketReach's `previous_employer` filter is **unique** — Apollo and most other providers can't do this.

### Use Cases
1. **Champion tracking** — Find people who left a customer company (they know your product, may bring it to new company)
2. **Competitor alumni** — People who left a competitor may be frustrated with that product
3. **Network leverage** — "We work with [previous company], and since you were there..."
4. **Recent departures** — Combine with `job_change_range_days` to find fresh alumni

### Alumni Search Pattern
```json
// Step 1: Universal Person Search (3 credits)
POST create_universal_person_search
{
  "query": {
    "previous_employer": ["Yellow.ai", "yellow.ai"],
    "current_title": ["VP", "Director", "Head of"],
    "geo": ["United States"],
    "contact_method": ["work_email"]
  },
  "page_size": 100
}

// Step 2: Universal Person Lookup for each result (3 credits each, 18 with reveal_phone)
GET create_universal_person_lookup?id=12345
```

### Previous Employer Name Variations Strategy
1. **Pass multiple variations as array** — OR logic handles the rest: `["Salesforce", "salesforce.com", "SFDC"]`
2. **Use `previous_company_id`** for reliable matching — look up the company ID first via Company Search
3. **Exact match when needed** — `["\"IBM\""]` for precision (but excludes subsidiaries)
4. **Check results and iterate** — if too few results, broaden the name variations

## Lookup Response Shape

```json
{
  "id": 123456,
  "status": "complete",
  "name": "Jane Smith",
  "first_name": "Jane",
  "last_name": "Smith",
  "current_title": "VP Sales",
  "current_employer": "Acme Corp",
  "current_employer_domain": "acme.com",
  "city": "San Francisco",
  "region": "California",
  "country_code": "US",
  "linkedin_url": "https://www.linkedin.com/in/janesmith",
  "emails": [
    {"email": "jane@acme.com", "smtp_valid": "valid", "type": "professional", "grade": "A"},
    {"email": "jane.smith@gmail.com", "smtp_valid": "valid", "type": "personal", "grade": "A-"}
  ],
  "phones": [
    {"number": "+14155550123", "type": "professional", "validity": "valid"},
    {"number": "+14155550456", "type": "mobile"}
  ],
  "recommended_email": "jane@acme.com",
  "recommended_professional_email": "jane@acme.com",
  "recommended_personal_email": "jane.smith@gmail.com",
  "job_history": [
    {
      "title": "Director Sales", "company_name": "OldCo",
      "start_date": "2019-01", "end_date": "2022-06", "is_current": false
    }
  ],
  "education": [
    {"school": "Stanford University", "degree": "MBA", "major": "Business"}
  ],
  "skills": ["Sales Strategy", "SaaS", "Enterprise Sales"]
}
```

**Critical:** `status` can be `"complete"`, `"searching"`, or `"failed"`. If `"searching"`, re-call Universal Person Lookup with the same `id` (max 1 call/second), or supply `webhook_id` for async delivery.

### Best Lookup Identifiers (Ranked by Match Rate)
1. **LinkedIn URL** — 99% return data
2. **Email** — ~87% return data
3. **Name + current_employer** — good match rate
4. **RocketReach ID** — 100% (from prior search)
5. **Name alone** — poor, may return wrong person

## Rate Limits (Per Plan)

### Global: 10 requests/second across all APIs

### Person Search
| Plan | /min | /hour | /day | /month |
|------|------|-------|------|--------|
| Essentials | 15 | 50 | 500 | 10,000 |
| Pro | 30 | 250 | 750 | 15,000 |
| Ultimate | 60 | 500 | 1,000 | 20,000 |
| Custom | 100 | 1,000 | 10,000 | 200,000 |

### Person Lookup
| Plan | /min | /hour | /day | /month |
|------|------|-------|------|--------|
| Essentials | 15 | 100 | 500 | 5,000 |
| Pro | 50 | 300 | 1,500 | 20,000 |
| Ultimate | 100 | 1,000 | 3,000 | 50,000 |
| Custom | 250 | 2,500 | 10,000 | 200,000 |

**429 responses include `Retry-After` header** — always check it instead of fixed delays.

## Pricing (How nRev Bills)

| Operation | Credits | USD |
|---|---|---|
| Universal Person Search | 3 | $0.03 |
| Universal Person Lookup (email-only) | 3 | $0.03 |
| Universal Person Lookup (with `reveal_phone: true`) | 18 | $0.18 |
| Universal Company Search | 3 | $0.03 |
| Universal Company Lookup | 3 | $0.03 |

**No credit charged** if no verified contact info is found. Re-lookups of the same profile are FREE.

### Vendor-side context (RocketReach plan tiers)

These are RocketReach's public plans — provided for awareness, not billing. nRev consumes the API on the user's behalf at the per-call rates above.

| Plan | Annual | Lookups/Year | Notes |
|---|---|---|---|
| Essentials | $399/yr | 1,200 | Email only, limited API |
| Pro | $899/yr | 3,600 | Email + phone + full API |
| Ultimate | $2,099/yr | 10,000 | Full API + priority support |
| Custom | $6,000+/yr | Negotiable | Enterprise |

**Full API access requires Ultimate plan or higher.** Essentials/Pro have limited/no API access at the vendor level.

## Accuracy Reality Check

| Data Point | Real-World Accuracy |
|------------|-------------------|
| Professional emails (A-grade) | ~98% deliverability |
| Professional emails (A- grade) | ~90% deliverability |
| Personal emails (A-grade) | >99% deliverability (but spotty availability) |
| Phone numbers | ~50% accurate — "not available or have errors more than half the time" per user reports |
| Previous employer data | **Best in class** — LinkedIn-sourced, very reliable |
| Current title/employer | Generally reliable but can lag LinkedIn by days-weeks |
| International data | Strongest in North America + Europe, weaker elsewhere |
| Small company (<20 emp) | Weaker coverage |
| Database size | 700M+ profiles, 85M+ refreshed monthly |

**LinkedIn URL lookups return data 99% of the time** — always prefer LinkedIn URL as the identifier.

## When RocketReach Beats Apollo

| Scenario | Why RocketReach Wins |
|----------|---------------------|
| Alumni/previous employer search | **Only provider with `previous_employer` filter** |
| Phone coverage | Higher hit rate than Apollo, but costs 18 credits per call vs 3 for email-only |
| Email accuracy grades | A/A-/B/F system gives confidence before sending |
| No credit waste on failures | No charge if no verified data found |
| LinkedIn URL enrichment | 99% match rate |
| Database size | 700M+ vs Apollo's ~275M |
| Healthcare (NPI) | Built-in NPI, credentials, specialization filters |
| Job change detection | `job_change_range_days` filter |
| CRM integrations | Native Salesforce, HubSpot, Outreach, Salesloft |

## When Apollo Beats RocketReach

| Scenario | Why Apollo Wins |
|----------|----------------|
| Bulk search (free) | Apollo search returns more data for free |
| Technology stack filtering | 1,500+ technology UIDs |
| Company firmographics | Better funding, tech stack, revenue data |
| Sequences/campaigns | Built-in email sequences |
| Price for high volume | Cheaper per-contact at scale |
| Intent data (UI only) | Apollo has buying intent (not API-accessible) |

## Best Practice: Combined Waterfall

```
1. Apollo search (FREE) — find people, get names/companies
2. Apollo enrich — get email (1 credit, $0.03)
3. If Apollo email bounces → RocketReach Universal Person Lookup — get alternative email (3 credits) or email+phone (18 credits)
4. If alumni search needed → RocketReach Universal Person Search with previous_employer (3 credits) → Universal Person Lookup (3 credits)
5. Always check email grade before sending — only A/A- are safe
```

**Note:** Per the Waterfall Enrichment Principle in `provider-selection/SKILL.md`, do NOT chain providers manually for email-coverage waterfall — that's BetterContact's job. Step 3 above is for *targeted retry* (Apollo missed → try RocketReach for THIS person), not bulk waterfall.

## API Quirks Summary

1. **Async lookups** — always check `status` field, implement polling or webhooks
2. **Free text employer matching is inconsistent** — pass multiple name variations as array
3. **Company Exports are separate credits** — purchased through sales, not from person credit pool
4. **`company_industry_tags` is deprecated** — use `company_industry` instead
5. **UI vs API results can differ** — UI uses NLP facets the API doesn't replicate exactly
6. **Bulk lookups require webhooks** — min 10, max 100 profiles per request
7. **Chrome extension is more reliable than web UI** — for manual lookups, use the extension on LinkedIn
