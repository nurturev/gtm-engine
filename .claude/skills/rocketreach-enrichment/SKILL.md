# RocketReach Enrichment

## Overview
RocketReach provides person lookup, people search, company lookup, and company search.
All calls go through nrev-lite MCP tools (`nrev_enrich_person`, `nrev_search_people`, `nrev_enrich_company`).

**Cost:** All endpoints cost **3 credits ($0.03)** per call. **18 credits** when phone numbers are requested.

## Endpoints (Universal API)

### 1. Universal Person Lookup
Ref: https://docs.rocketreach.co/reference/create_universal_person_lookup

Used via: `nrev_enrich_person` with `provider="rocketreach"`

**Identifiers (at least one required):**

| Parameter | Type | Notes |
|-----------|------|-------|
| `linkedin_url` | string | Most accurate (~99% match) |
| `email` | string | |
| `name` + `current_employer` | string | Both required together |
| `id` | integer | RocketReach profile ID |
| `title` | string | Optional, improves match |
| `npi_number` | integer | US healthcare professionals |

**Reveal flags (control what data is returned):**

| Parameter | Type | Cost Impact |
|-----------|------|-------------|
| `reveal_professional_email` | boolean | Included in 3 credits |
| `reveal_personal_email` | boolean | Included in 3 credits |
| `reveal_phone` | boolean | **Bumps cost to 18 credits** |
| `reveal_detailed_person_enrichment` | boolean | |
| `reveal_healthcare_enrichment` | boolean | |

**Other:**
- `return_cached_emails` — boolean, default true. Returns cached emails immediately when lookup is async.
- `webhook_id` — integer, for async delivery.

### 2. Universal Person Search
Ref: https://docs.rocketreach.co/reference/create_universal_person_search

Used via: `nrev_search_people` (auto-selects RocketReach for alumni/school/department queries)

**Pagination:** `start` (1-10000, default 1), `page_size` (1-100, default 100), `order_by` (relevance/popularity/score)

**Query filters — Employment (Current):**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Current Company(s) | `company_domain` | string | Comma-separated domains. Variables allowed. |
| Title Keywords | `current_title` | string | Use with Include Past Titles toggle. |
| Include Past Titles | switches to `current_or_previous_title` | boolean | If true, uses `current_or_previous_title` instead of `current_title` |
| Seniority | `management_levels` | multi-select | See Seniority enum below. Variables allowed. |
| Department | `department` | multi-select | See Department enum below. Variables allowed. |

**Query filters — Previous Employment:**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Previous Employer | `previous_employer` | string | Free-text, comma-separated. Variables allowed. |
| Recently Moved In? | `job_change_range_days` | single-select | Last Month, Last 3 Months, Last 6 Months, Last Year |

**Query filters — Company Attributes:**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Company Industry | `company_industry` | multi-select | See Industry enum below. Variables allowed. |
| Company Keywords | `company_industry_keywords` | string | |
| Company Size | `company_size` | multi-select | 1-10, 11-20, 21-50, 51-100, 101-200, 201-500, 501-1000, 1001-2000, 2001-5000, 5001-10000, 100001+. Variables allowed. |
| Company Funding Amount | `company_funding_min`, `company_funding_max` | min-max | Numeric. Variables allowed. |
| Company Revenue | `company_revenue` | min-max | Numeric. If min empty send 0, if max empty send 1000000000000. Variables allowed. |
| Company Competitor(s) | `company_competitors` | string | Comma-separated domains. Variables allowed. |

**Query filters — Professional:**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Years Experience | `years_experience` | min-max | Min and max years |
| Education | `school` | string | Institution name |

**Query filters — Signals:**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Department Growth | `growth` | structured | Format: `min-max::Department,TimeRange`. See Department Growth section below. |

#### Department Growth Filter

Structured input combining department, time range, and growth percentage:

- **Format:** `min_pct-max_pct::Department,TimeRange`
- **Example:** `5-30::Engineering,six_months` (5-30% growth in Engineering over 6 months)
- **Example:** `-10--20::Sales,one_year` (10-20% decline in Sales over 1 year)

**Department:** Use Department enum (see below)
**Time Range:** `six_months` or `one_year`
**Growth presets:**
- Surge: 5-25%
- Aggressive Hiring: 25-50%
- Growth Rocket: 50%+
- Slowdown: -5 to -10%
- Layoffs: -10% and above

### 3. Universal Company Lookup
Ref: https://docs.rocketreach.co/reference/create_universal_company_lookup

Used via: `nrev_enrich_company` with `provider="rocketreach"`

| Parameter | Type | Notes |
|-----------|------|-------|
| `domain` | string | Preferred identifier |
| `name` | string | Company name |
| `id` | integer | RocketReach company ID |
| `linkedin_url` | string | Company LinkedIn URL |
| `ticker` | string | Stock ticker |

### 4. Universal Company Search
Ref: https://docs.rocketreach.co/reference/create_universal_company_search

**Pagination:** `start` (1-10000, default 1), `page_size` (1-100, default 100), `order_by` (relevance/popularity/score)

**Query filters:**

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| Company Industry | `industry` | multi-select | See Industry enum. |
| Company Revenue | `revenue` | min-max | Numeric. Same empty-field rules as person search. |
| Competitor (Domains) | `competitors` | string | Comma-separated domains |
| Total Funding | `total_funding` | min-max | Numeric. |
| Employee Count | `employees` | multi-select | 1-10, 11-20, 21-50, 51-100, 101-200, 201-500, 501-1000, 1001-2000, 2001-5000, 5001-10000, 10001+ |
| Company Keywords | `industry_keywords` | string | |
| Locations | `location` | string | |
| Publicly Traded | `publicly_traded` | boolean | |
| Company Growth | `growth` | structured | Same format as person search Department Growth |

---

## Enums

### Seniority (`management_levels`)
Founder/Owner, C-Level, Vice President, Head, Director, Manager, Senior, Individual Contributor, Entry, Intern, Volunteer

### Department (`department` and `growth` filter)

**Top-level departments:** C-Suite, Product & Engineering, HR, Legal, Marketing, Health, Operations, Sales, Education, Finance

**All departments:** Executive, Founder, Product & Engineering Executive, Finance Executive, HR Executive, Legal Executive, Marketing Executive, Health Executive, Operations Executive, Sales Executive, DevOps, Graphic Design, Product Design, Web Design, Information Technology, Project Engineering, Quality Assurance, Mechanical Engineering, Electrical Engineering, Data Science, Software Development, Web Development, Information Security, Network Operations, Systems Administration, Product Management, Artificial Intelligence / Machine Learning, Digital Transformation, Accounting, Tax, Investment Management, Financial Planning & Analysis, Risk, Financial Reporting, Investor Relations, Financial Strategy, Internal Audit & Control, Recruiting, Compensation & Benefits, Learning & Development, Diversity & Inclusion, Employee & Labor Relations, Talent Management, Legal Counsel, Compliance, Contracts, Corporate Secretary, Litigation, Privacy, Paralegal, Judicial, Content Marketing, Product Marketing, Brand Management, Public Relations (PR), Event Marketing, Advertising, Customer Experience, Demand Generation, Digital Marketing, Search Engine Optimization (SEO), Social Media Marketing, Broadcasting, Editorial, Journalism, Video, Writing, Dental, Doctor, Fitness, Nursing, Therapy, Wellness, Medical Administration, Medical Education & Training, Medical Research, Clinical Operations, Logistics, Project Management, Office Operations, Customer Service / Support, Product, Call Center, Corporate Strategy, Facilities Management, Quality Management, Supply Chain, Manufacturing, Real Estate, Business Development, Customer Success, Account Management, Channel Sales, Inside Sales, Sales Enablement, Sales Operations, Administration, Professor, Teacher, Researcher

### Industry (`company_industry` / `industry`)

**Top-level industries:** Agriculture & Fishing, Business Services, Construction, Consumer Services, Education, Energy Utilities & Waste Treatment, Finance, Government & Public Services, Healthcare, Leisure & Hospitality, Law Firms & Legal Services, Manufacturing, Media & Internet, Metals & Mining, Organizations, Real Estate, Research & Technology, Retail, IT & Software, Telecommunications, Supply Chain & Logistics, Transportation

**Sub-industries:** (110+ sub-industries available — use the top-level for broad targeting, sub-industries for precision. Full list in the Industry enum CSV.)

---

## Title Keyword Expansion Rules (Apollo & RocketReach)

When a user describes a role, expand it into the title keywords people actually use. Two critical rules:

**1. Expand horizontally (related functions), not vertically (seniority).**

- "Marketing" → brand, growth, demand generation, loyalty, content, digital marketing, performance marketing
- Do NOT expand to "Director of Marketing", "VP Marketing" — that's seniority, which is a separate filter (`seniority` / `management_levels`). Adding seniority terms to keywords pollutes results.

**2. Omit noisy generic terms that attract irrelevant results.**

- For IT/security roles: omit "operations", "system" — these pull in sysadmins, IT ops people who aren't the target
- For sales roles: omit "business" — pulls in business analysts, business ops
- If a keyword is ambiguous and would match more wrong people than right, leave it out

### Example: IT and Identity & Access Management

User asks for: "IT and IAM people"

Expanded keywords:
```
IT, Information Technology, Information Security, InfoSec, Cybersecurity, Cyber Security, Cyber,
Security, Network Security, Cloud Security, Application Security, AppSec, Data Security,
Data Protection, Data Privacy, Privacy, Endpoint Security, Threat Intelligence,
Vulnerability Management, Security Operations, SOC, SIEM, Penetration Testing,
Security Architecture, Security Engineering, Zero Trust, Compliance, Risk Management,
GRC, Governance Risk and Compliance, IT Governance, IT Risk, IT Audit,
Identity, IAM, Identity and Access Management, Identity Management, Access Management,
Privileged Access, PAM, Identity Governance, IGA, Single Sign-On, SSO, MFA,
Multi-Factor Authentication, Authentication, Directory Services, Active Directory,
Azure AD, Okta, CyberArk, SailPoint, ForgeRock, Ping Identity
```

Notice: No "operations" or "system" keywords. No seniority terms mixed in.

### Applying keywords to each provider

**Apollo (Search People):** Put the full expanded list in `keywords`. Enable `include_similar_titles: true` for additional coverage.

**RocketReach:** Put the keywords in `current_title`. If the role maps to a known department, also set `department` for an additional filter layer.

---

## Alumni / School Name Expansion Rules

When searching by school/education, ALWAYS generate multiple name variants. RocketReach matches against LinkedIn-sourced school names which vary in format.

**For every school input, generate ALL of these variants:**

1. **Common abbreviation** — e.g., `IIT KGP`
2. **Abbreviation + city full form** — e.g., `IIT Kharagpur`
3. **Full institute name + city** — e.g., `Indian Institute of Technology Kharagpur`
4. **Full name with comma for location** — e.g., `Indian Institute of Technology, Kharagpur`

**Example:** User says "IIT KGP"
```json
"school": [
  "IIT KGP",
  "IIT Kharagpur",
  "Indian Institute of Technology Kharagpur",
  "Indian Institute of Technology, Kharagpur"
]
```

**Example:** User says "MIT"
```json
"school": [
  "MIT",
  "Massachusetts Institute of Technology",
  "Massachusetts Institute of Technology, Cambridge"
]
```

**Example:** User says "ISB"
```json
"school": [
  "ISB",
  "ISB Hyderabad",
  "Indian School of Business",
  "Indian School of Business, Hyderabad"
]
```

Always apply this expansion — never send a single school name variant.

---

## When to Use RocketReach Over Apollo

| Scenario | Why RocketReach |
|----------|----------------|
| Alumni/previous employer search | `previous_employer` filter — unique to RocketReach |
| School/education search | `school` filter works reliably |
| Department-level targeting | `department` filter (Apollo lacks this) |
| Recently changed jobs | `job_change_range_days` filter |
| Department growth signals | `growth` structured filter |
| Name+company lookup (no email/LinkedIn) | Only provider supporting this combo |

## When to Use Apollo Over RocketReach

| Scenario | Why Apollo |
|----------|-----------|
| Default B2B search | Larger database, faster |
| Niche role keywords | `include_similar_titles` expands variations |
| Account-based search | `company_domains` with per-row variables |
| Technology stack filtering | 1,500+ tech UIDs |
| Company firmographics | Better funding, tech stack, revenue data |
