# RocketReach API — Tool Quirks & Best Practices

## Pricing

All 4 endpoints cost **3 credits ($0.03)** per call.
If phone numbers are requested (`reveal_phone: true`): **18 credits** per call.

## Critical Gotchas

### 1. `previous_employer` is FREE TEXT, not a domain
```json
// WRONG
"previous_employer": ["mindtickle.com"]

// CORRECT
"previous_employer": ["mindtickle"]
// Better — cast a wide net with OR logic:
"previous_employer": ["mindtickle", "MindTickle", "Mind Tickle"]
```
Free-text with fuzzy/NLP matching against LinkedIn-sourced company names. Multiple values use **OR logic**. Always try multiple variations.

**For exact match:** Wrap in escaped double quotes: `["\"IBM\""]` — excludes subsidiaries like "IBM UK".

**Most reliable alternative:** Look up the RocketReach company ID first via Company Search, then use `previous_company_id`.

### 2. `current_employer` is free text — AVOID for search
**Never search people by company name.** Use `company_domain` instead:
```json
// WRONG — unreliable free-text matching
"current_employer": ["Salesforce"]

// CORRECT — domain-based, exact match
"company_domain": ["salesforce.com"]
```
If you only have a company name, **first find the domain** via Company Search or Company Lookup, then use `company_domain` for the person search.

**Exception:** `previous_employer` and `company_competitors` accept company names because there's no domain alternative for those filters.

### 3. Multi-department queries — single request
Multiple departments can be searched in ONE query — no need for separate API calls:
```json
// ONE call, not two
"department": ["Sales", "Marketing"]
```
Same applies to titles, locations, industries, and all other array filters. Multiple values use OR logic within the same filter.

### 4. Title Keywords + Include Past Titles toggle
- `current_title` — matches current title only
- `current_or_previous_title` — matches current OR past titles
- The node uses a boolean `include_past_titles` toggle to switch between these two API fields

### 5. Lookups can be ASYNC
Initial lookup may return `status: "searching"` or `"progress"`. Check the `status` field and poll if needed.
`return_cached_emails` (default true) returns cached emails immediately while async lookup continues.

### 6. Authentication is `Api-Key` header
```
Api-Key: your_api_key_here
```
NOT `Authorization: Bearer`. NOT a query parameter.

### 7. Pagination is 1-indexed, max 10,000
`start`: 1-indexed page number. `page_size`: max 100. Cannot paginate beyond 10,000 results.

### 8. Company Revenue empty-field handling
- Both empty: don't send the parameter
- Only min empty: send as 0
- Only max empty: send as 1000000000000

### 9. Company Size — special max value
If `100001+` is selected, send max value as `10000000`.

### 10. Department Growth format
Structured string: `min_pct-max_pct::Department,TimeRange`
- TimeRange values: `six_months`, `one_year`
- Example: `5-30::Engineering,six_months`
- Negative growth: `-10--20::Sales,one_year`

### 11. Boolean Logic
- **Same filter, multiple values = OR**: `current_title: ["CEO", "CTO"]` matches either
- **Different filters = AND**: title + location + industry must ALL match
- **Exclude with `-` prefix**: `current_title: ["Engineer", "-Senior"]`
- **Exact match**: `["\"IBM\""]`

## Title Keyword Expansion

Expand horizontally (related functions), NOT vertically (seniority). Seniority goes in `management_levels`, not in title keywords.
- "Marketing" → brand, growth, demand generation, content, digital marketing, performance marketing
- NOT "Director of Marketing", "VP Marketing"
- Omit noisy generic terms: "operations", "system" for IT roles; "business" for sales roles

## School Name Expansion (Alumni Search)

ALWAYS generate multiple variants for every school:
- Common abbreviation: `IIT KGP`
- Abbreviation + city: `IIT Kharagpur`
- Full name + city: `Indian Institute of Technology Kharagpur`
- Full name, comma, city: `Indian Institute of Technology, Kharagpur`

Never send a single school name variant.

## Best Lookup Identifiers (Ranked)
1. **LinkedIn URL** — ~99% match
2. **Email** — ~87% match
3. **Name + current_employer** — good match
4. **RocketReach ID** — 100% (from prior search)
5. **Name alone** — poor, may return wrong person

## The Alumni Search Superpower

RocketReach's `previous_employer` filter is unique — Apollo cannot do this.

### Use Cases
1. **Champion tracking** — find people who left a customer company
2. **Competitor alumni** — people who left a competitor
3. **Network leverage** — "We work with [previous company]..."
4. **Recent departures** — combine with `job_change_range_days`

### Previous Employer Name Variations Strategy
1. Pass multiple variations as array — OR logic handles the rest
2. Use `previous_company_id` for reliable matching
3. Use exact match `["\"IBM\""]` for precision
4. Check results and iterate — broaden variations if too few results
