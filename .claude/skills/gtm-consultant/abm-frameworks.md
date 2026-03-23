# Account-Based Marketing/Sales Frameworks

87% of marketers report ABM delivers higher ROI than any other marketing approach.

## ITSMA's Three Types of ABM

### Type 1: Strategic ABM (One-to-One)
- Median accounts targeted: 13
- Spend per account: $59K/year + salaries
- Dedicated senior marketer per 1-3 accounts
- Fully customized marketing plans per account
- 91% report higher ROI vs traditional

### Type 2: ABM Lite (One-to-Few)
- Median accounts targeted: 50
- Spend: ~$4K/account/year
- Groups of 5-10 accounts sharing attributes
- Campaigns for clusters using commonalities
- Balance of personalization and scalability

### Type 3: Programmatic ABM (One-to-Many)
- Median accounts targeted: 725
- One marketer across hundreds of accounts
- Technology-enabled targeting and personalization
- 76% report greater ROI vs traditional

## Tiered Budget Allocation
- **Tier 1 (50-60% budget):** Personalized web experiences, account-based ads, coordinated sales plays for best-fit accounts
- **Tier 2 (25-30%):** Scalable semi-personalized campaigns (LinkedIn, programmatic)
- **Tier 3 (10-20%):** Light awareness through retargeting and content syndication

## The Dark Funnel (6sense)

70% of the buyer journey is anonymous research. Only 3% of website visitors self-identify.

**Buying stages:**
- Anonymous research (70% of journey) — the "Dark Funnel"
- Active evaluation — vendor shortlisting underway
- Decision — 84% of deals won by first vendor contacted

**Implication:** You must influence buyers BEFORE they reach out. Deliver relevant content during their anonymous research phase.

## ABM with nrev-lite

**Account identification:**
- `nrev_google_search` to find companies matching ICP criteria (industry awards, conference speakers, job postings)
- `nrev_enrich_company` for firmographic and technographic validation
- `nrev_search_patterns(use_case="company_research")` for discovery queries

**Contact mapping:**
- `nrev_enrich_person` to find and enrich key stakeholders at target accounts
- Map the buying committee: Champion, Economic Buyer, Technical Evaluator, End Users

**Engagement orchestration:**
- `nrev_execute_action` to push contacts to CRM (HubSpot/Salesforce)
- `nrev_execute_action` to send personalized messages via Slack/Gmail
- `nrev_execute_action` to log activities in project management tools

## TOPO's Account-Based Everything (ABE)
Coordination of personalized marketing, sales development, sales, and CS to drive engagement with targeted accounts.

Key components:
1. ICP Definition — common attributes of best customers
2. Custom Messaging — account intelligence used to describe offering in resonant terms
3. Sales Development — high-value, no-obligation offers (free audits) over generic meeting requests
4. Full lifecycle — marketing through sales and into CS/account management
