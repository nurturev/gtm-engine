# Non-Standard Discovery Patterns

When the target business or persona is NOT in traditional B2B databases (Apollo, RocketReach, ZoomInfo), use creative discovery. Think: **where does this business LIVE online?**

## Decision Tree

```
Is the target in B2B databases?
├── YES → Use Apollo/RocketReach (standard path)
└── NO → What type of business?
    ├── Local/SMB (restaurants, gyms, salons, clinics)
    │   → Google Maps + Yelp + Instagram → Parallel Web enrichment
    ├── E-commerce/D2C brands
    │   → Instagram + Shopify directories + Google → Parallel Web
    ├── Tech startups (pre-Series A)
    │   → ProductHunt + Crunchbase + AngelList + GitHub
    ├── Agencies/consultancies
    │   → Clutch.co + LinkedIn company search + Google
    ├── Professional services (lawyers, accountants)
    │   → Google Maps + industry directories + LinkedIn
    └── Niche/vertical businesses
        → Industry-specific directories + Google + social platforms
```

## Platform Discovery Matrix

| Business Type | Primary | Secondary | Enrichment |
|--------------|---------|-----------|------------|
| Restaurants/cafes | Google Maps, Yelp | Instagram, TripAdvisor | Parallel Web |
| Retail/boutiques | Instagram, Google Maps | Facebook, Yelp | Parallel Web |
| Gyms/fitness | Google Maps, Instagram | ClassPass, Yelp | Parallel Web |
| Salons/beauty | Instagram, Yelp | Google Maps, Booksy | Parallel Web |
| Medical clinics | Google Maps, Healthgrades | Zocdoc, NPI database | Parallel Web |
| Professional services | Google, Clutch.co | LinkedIn company, Yelp | Apollo (if people exist) |
| E-commerce/D2C | Instagram, Shopify stores | Facebook, TikTok | Parallel Web |
| SaaS startups | G2, ProductHunt | Crunchbase, Wellfound | Apollo + PredictLeads |
| Real estate | Zillow, Realtor.com | Google Maps, LinkedIn | Parallel Web |
| Construction/trades | Google Maps, BBB | Yelp, Angi | Parallel Web |

## Creative Enrichment Pipeline

### Step 1: Discover (find businesses via Google site: operators)
```python
# Search MULTIPLE platforms — cross-referencing gives better coverage
# Yelp and Instagram are well-indexed; Google Maps site: does NOT work
nrev_google_search("site:yelp.com bakeries San Jose")
nrev_google_search("site:instagram.com bakery San Jose California")
# This gives you: business names + Yelp/Instagram URLs
```
**IMPORTANT:** `site:google.com/maps` does NOT work — Maps pages are not indexed in web search. Use Yelp + Instagram as primary discovery platforms.

### Step 2: Extract structured data (TWO approaches)

**Approach A — Parallel Web Extract (preferred when server available):**
```python
# Batch all Yelp URLs into one Parallel Web Extract call
# Yelp and Instagram BLOCK basic HTTP scraping (403 errors)
# Parallel Web handles anti-bot pages that basic fetch tools can't
nrev_scrape_page(url="https://www.yelp.com/biz/peters-bakery-san-jose-2",
                objective="Extract business name, full address, phone, website, email, hours, rating, specialties")
```

**Approach B — Web search per business (fallback, always works):**
```python
# Search each business by name for contact details
# This pulls from multiple directory listings (Yelp, Yellow Pages, Facebook, etc.)
nrev_google_search("[Business Name] [City] phone website email contact")
```
**Real-world test results (10 bakeries in San Jose):**
- Phone: 100% hit rate
- Website: 80% hit rate
- Email: 50% hit rate (email is the hardest field for local businesses)
- Instagram: 70% hit rate

### Step 3: Enrich missing fields
```python
# For businesses missing email/website, use Parallel Web Task API
# Task API does AI-powered research across multiple sources
nrev_scrape_page(url="https://www.google.com/search?q=", objective="Find contact email, website, and owner name for [Business Name] at [Address] in [City]")

# For persistent gaps, try Facebook pages (often have email)
nrev_google_search("site:facebook.com [Business Name] [City]")
```

### Step 4: Find the decision maker
```python
# For local businesses, the owner IS the decision maker
# Try LinkedIn search for the owner
nrev_google_search("site:linkedin.com/in [owner name] [business name] [city]")
# Or search Apollo if the company has a website domain
nrev_search_people(company_domains=["businessdomain.com"], provider="apollo")
```

### Step 5: Always output structured data
Every workflow MUST end with structured output (table or JSON) — never just prose. This enables downstream automation (Sheets export, CRM push, email sequences).

```
| # | Business | Address | Phone | Website | Email | Source URL | Specialty |
|---|----------|---------|-------|---------|-------|------------|-----------|
| 1 | ...      | ...     | ...   | ...     | ...   | [Yelp](...)| ...       |
```
**CRITICAL: Always include source URLs** (Yelp listing, Instagram profile, Google Maps link, etc.) — without the URL the user can't verify or take action on the data.

**Set expectations on hit rates:** For local/SMB businesses, expect ~100% phone, ~80% website, ~50% email. Suggest fallbacks for missing email: contact form URL, Facebook Messenger, Instagram DM, or phone.

## Hiring Signal Discovery Patterns

Job postings reveal strategic priorities better than any press release.

**What hiring patterns tell you:**

| Hiring Pattern | What It Signals | Outreach Angle |
|---------------|----------------|----------------|
| 5+ SDRs | Scaling outbound | They need outbound tools |
| RevOps hire | Investing in process | They need automation/ops tools |
| First VP Marketing | Building demand gen | They need marketing stack |
| Data Engineer + ML | Building data capabilities | They need data infrastructure |
| 10+ engineers | Product scaling | They need dev tools |
| Compliance/Legal | Entering regulated market | They need compliance tools |
| CSM/Support hires | Customer base growing | They need CS/support tools |

**How to find:**
```python
# Greenhouse (most tech companies)
nrev_google_search("site:boards.greenhouse.io/[company] sales OR SDR OR marketing")

# Lever
nrev_google_search("site:jobs.lever.co/[company]")

# General
nrev_google_search("[company name] careers hiring [role]")
```

## Competitor Intelligence via Social

**Track competitor sales reps' public activity:**
```python
# Find their reps
nrev_google_search("site:linkedin.com/in [competitor name] SDR OR 'account executive' OR sales")

# Find their engagement (likes, comments on prospects' posts)
nrev_google_search("site:linkedin.com/posts [rep name] commented")
```

**Identify accounts they're chasing:**
- Filter engagement for ICP-fit prospects
- Cross-reference with your target accounts
- The prospect is already in evaluation stage → enter with battlecard

**Track competitor content for market intelligence:**
```python
nrev_google_search("site:linkedin.com/posts [competitor company] launched OR announced OR introducing")
nrev_google_search("[competitor] blog [product area] site:[competitor-domain].com")
```

## Competitor Intelligence — Advanced Tools

**Tools for automated monitoring:**
- Trigify ($149-$549/mo) — tracks any profile's engagement, auto-generates leads, 52% LinkedIn reply rates reported
- PhantomBuster ($56+/mo) — LinkedIn Post Commenter/Liker Scraper, extracts all engagers from posts
- Captain Data — extract post likers/commenters filtered by ICP with boolean title filtering

## LinkedIn Inbound Engine

Comments are the new cold call. Public engagement warms leads more effectively than DMs.

**The "Golden 20" Method:**
1. Curate 20 high-value prospects
2. Like/comment on their content for 1-2 weeks before connecting
3. Comment within 60-90 minutes of post going live ("golden hour" — 30% more engagement)
4. Write 15+ word comments (2x more impactful than shorter ones)
5. Engage 3-5x per week per prospect
6. Send connection request when name feels familiar — far higher acceptance

**Volume formula:**
- 1 profile, 20-30 comments/day → 5-10 leads/week
- 8-9 profiles, coordinated → 30-40 leads/week
- 4-8% of post engagers convert to pipeline

**Network hacking:** Find 5-10 niche influencers. Be first to comment when they post. Their audience sees your comment → drives ICP profile visits.

**Finding hot leads from YOUR engagement:**
- Sales Navigator: "Lead engaged with your content" alert
- Track who likes/comments on your posts → filter by ICP → enrich → warm outreach
- These people self-selected as interested: 3-5x higher response rates

## Curated Databases for Discovery

| Source | What | Size |
|--------|------|------|
| Growth List | SaaS startups | 14,140+ companies |
| CartInsight.io | Shopify stores with traffic/tech data | 392,849+ stores |
| 1800DTC.com | D2C brands curated | 1,500+ brands |
| GetLatka | SaaS revenue data | Thousands |
| Y Combinator directory | YC-backed startups | 5,000+ |
| TopStartups.io | Startup database | Thousands |
| NPI/NPPES | US healthcare providers | 9M+ records |

## Healthcare-Specific Discovery

The NPI (National Provider Identifier) database is the authoritative federal source:
- Free download: https://download.cms.gov/nppes/NPI_Files.html
- Free API: https://npiregistry.cms.hhs.gov/
- **Limitation:** Raw NPI has NO phone, NO email, NO practice size, NO tech data
- Enrich with CarePrecise or Parallel Web for contact details

## Apify Actors for Scraping at Scale

| Actor | What It Scrapes | Best For |
|-------|----------------|----------|
| Google Maps Scraper (Compass) | Name, address, phone, website, email, ratings, hours, social | Local business discovery |
| Google Maps Email Scraper | Visits business websites to extract emails + social links | Email finding for local |
| Instagram Scraper | Profiles, posts, places, hashtags, bio, website, followers | D2C/local brand discovery |
| LinkedIn Profile Scraper | Experience, education, certifications, skills (no cookies needed) | People enrichment |
| LinkedIn Post Keyword Scraper | Posts by keyword with reactions, author details (no login) | Content/engagement monitoring |
| Yelp Business Scraper | Business details + crawls associated websites for emails | Local business + contact |
| Contact Details Scraper | Crawls any website for emails, phones, social links | General enrichment |
