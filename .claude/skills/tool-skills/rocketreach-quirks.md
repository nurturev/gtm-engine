# RocketReach API — Tool Quirks & Best Practices

## Critical Gotchas (Read Before ANY API Call)

### 1. `previous_employer` is FREE TEXT, not a domain
```json
// WRONG ❌
"previous_employer": "mindtickle.com"

// CORRECT ✅
"previous_employer": "mindtickle"
// Also try: "MindTickle", "Mindtickle"
```
This is a **free-text field** that matches against how the company name appears in LinkedIn/RocketReach records. It is NOT a domain lookup. Company name casing and formatting vary — try multiple variations:
- `"yellow.ai"` vs `"Yellow.ai"` vs `"Yellow AI"` vs `"Yellow"`
- `"mindtickle"` vs `"MindTickle"` vs `"Mind Tickle"`
- `"hubspot"` vs `"HubSpot"`

**Strategy:** Search with the most common/canonical name first. If low results, try variations. The field does partial matching but is case-sensitive in some cases.

### 2. `current_employer` is also free text (same rules)
```json
// WRONG ❌
"current_employer": "salesforce.com"

// CORRECT ✅
"current_employer": "Salesforce"
```

### 3. Search vs Lookup — different endpoints, different costs
| Endpoint | What It Returns | Cost |
|----------|----------------|------|
| `/search` | List of people matching filters (names, titles, IDs) | Credits per result viewed |
| `/lookupProfile` | Full profile with emails + phones | 1 lookup credit |
| `/lookupEmail` | Just the email | 1 lookup credit |

**Key difference from Apollo:** RocketReach search results DO include some contact data (emails with validity status), unlike Apollo where search is free but returns no contact info.

### 4. Authentication is via `Api-Key` header
```
Api-Key: your_api_key_here
```
NOT `Authorization: Bearer`. NOT a query parameter. The header name is literally `Api-Key`.

### 5. Search pagination starts at 1, not 0
```json
// First page
"start": 1, "page_size": 100

// Second page
"start": 2, "page_size": 100
```
The `start` parameter is a **page number** (1-indexed), NOT an offset. Max `page_size` is 100.

### 6. Title search uses array syntax
```json
// Single title
"current_title": ["VP Sales"]

// Multiple titles (OR logic)
"current_title": ["VP Sales", "Vice President of Sales", "Head of Sales"]
```
Always pass as an array even for a single title.

### 7. Location format is flexible but best as "City, State" or "Country"
```json
"location": ["San Francisco, California"]
// or
"location": ["United States"]
// or
"location": ["California"]
```

## Endpoints Quick Reference

| Endpoint | Method | Path | Best Use |
|----------|--------|------|----------|
| Search People | GET | `/v2/api/search` | Find people by filters |
| Lookup Profile | GET | `/v2/api/lookupProfile` | Full profile by email/LinkedIn/name |
| Lookup Email | GET | `/v2/api/lookupEmail` | Just email by name+company |
| Lookup Company | GET | `/v2/api/lookupCompany` | Company info by domain/name |
| Check Status | GET | `/v2/api/checkStatus` | Check lookup completion |
| Account Info | GET | `/v2/api/account` | Check credits remaining |

## Search Filters

### Person-level
| Parameter | Type | Notes |
|-----------|------|-------|
| `name` | string | Full name search |
| `current_title` | string[] | OR logic across titles |
| `current_employer` | string | **Free text** — company name, NOT domain |
| `previous_employer` | string | **Free text** — alumni search! This is RocketReach's killer feature |
| `location` | string[] | "City, State" or "Country" |
| `keyword` | string | Free-text keyword across profile |
| `industry` | string[] | Industry classification |

### Company-level
| Parameter | Type | Notes |
|-----------|------|-------|
| `company_domain` | string | Alternative to `current_employer` — use actual domain here |
| `company_size` | string | Employee range (e.g., "51-200") |

### Pagination
| Parameter | Type | Notes |
|-----------|------|-------|
| `start` | int | Page number (1-indexed!) |
| `page_size` | int | 1-100 max |

## The Alumni Search Superpower

RocketReach's `previous_employer` filter is **unique** — Apollo and most other providers can't do this. Use cases:
1. **Champion tracking** — Find people who left a customer company (they know your product, may bring it to their new company)
2. **Competitor alumni** — People who left a competitor may be frustrated with that product
3. **Network leverage** — "We work with [previous company], and since you were there..."

### Alumni Search Pattern
```
Step 1: Search with previous_employer
  nrv_enrich(provider="rocketreach", endpoint="/search",
    params={"previous_employer": "mindtickle", "current_title": ["VP Sales", "Head of Sales"]})

Step 2: Filter results for current companies in your ICP

Step 3: Lookup profiles for email/phone
  nrv_enrich(provider="rocketreach", endpoint="/lookupProfile",
    params={"id": 12345})  // or use email/linkedin_url
```

### Previous Employer Name Variations Strategy
Since `previous_employer` is free text, follow this pattern:
1. Try the **most recognizable brand name** first: `"Salesforce"`, `"HubSpot"`, `"Google"`
2. If results are low, try **variations**: `"salesforce.com"`, `"Salesforce, Inc."`, `"SFDC"`
3. For startups with unusual names, try **both with and without domain suffix**: `"yellow.ai"` AND `"Yellow AI"`
4. The field does **substring matching** in some cases — `"mind"` may match `"MindTickle"` but results will be noisy

## Rate Limits

| Plan | Lookups/Month | Search Results/Month |
|------|--------------|---------------------|
| Essentials ($53/mo) | 80 | 200 |
| Pro ($179/mo) | 200 | 1,000 |
| Ultimate ($359/mo) | 500 | 2,500 |

**API access requires Pro plan or higher.** The Essentials plan has very limited API access.

Per-minute rate limits: ~50 requests/minute (varies by plan).

## Credit Costs

| Action | Credits |
|--------|---------|
| Search (viewing results) | 1 per profile viewed |
| Lookup (email) | 1 |
| Lookup (phone) | 1 (included with email) |
| Company lookup | 1 |
| Bulk lookup | 1 per person |

**Key difference from Apollo:** RocketReach includes phone numbers in the same lookup credit. Apollo charges 8 credits for phone numbers separately.

## Accuracy Reality Check

| Data Point | Strength |
|------------|----------|
| Professional emails | Strong — 85-90% accuracy for US companies |
| Personal emails | Available but less reliable |
| Phone numbers | Better than Apollo — included free, ~70% accuracy |
| Previous employer data | **Best in class** — LinkedIn-sourced, very reliable |
| International data | Moderate — better than Apollo for some regions |
| Small company coverage | Weaker — fewer records for <20 employee companies |

## Response Shape — Lookup Profile

```json
{
  "id": 123456,
  "status": "complete",
  "name": "Jane Smith",
  "first_name": "Jane",
  "last_name": "Smith",
  "current_title": "VP Sales",
  "current_employer": "Acme Corp",
  "city": "San Francisco",
  "region": "California",
  "country_code": "US",
  "linkedin_url": "https://www.linkedin.com/in/janesmith",
  "emails": [
    {"email": "jane@acme.com", "smtp_valid": "valid", "type": "professional"},
    {"email": "jane.smith@gmail.com", "smtp_valid": "valid", "type": "personal"}
  ],
  "phones": [
    {"number": "+14155550123", "type": "professional"},
    {"number": "+14155550456", "type": "mobile"}
  ],
  "current_employer_domain": "acme.com",
  "previous_employers": ["OldCo", "StartupXYZ"]
}
```

**Note:** `status` can be `"complete"`, `"searching"`, or `"failed"`. If `"searching"`, poll `/checkStatus` with the ID.

## When RocketReach Beats Apollo

| Scenario | Why RocketReach Wins |
|----------|---------------------|
| Alumni/previous employer search | **Only provider with this filter** |
| Phone numbers needed | Included free (Apollo charges 8 credits) |
| Single-credit full profile | Email + phone in one lookup |
| Quick email verification | `smtp_valid` field in response |

## When Apollo Beats RocketReach

| Scenario | Why Apollo Wins |
|----------|----------------|
| Bulk search (free) | Apollo search is free; RocketReach costs credits |
| Company firmographics | Better company data (funding, tech stack, etc.) |
| Large-scale prospecting | 50K result ceiling vs RocketReach's smaller limits |
| Technology-based filtering | Apollo has 1,500+ technology UIDs |

## Best Practice: Combined Waterfall

```
1. Apollo search (FREE) — find people, get Apollo IDs
2. Apollo enrich — get email (1 credit)
3. If Apollo email bounces → RocketReach lookup — get alternative email + phone (1 credit)
4. If alumni search needed → RocketReach search with previous_employer
```
