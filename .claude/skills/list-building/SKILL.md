---
name: list-building
description: Step-by-step playbook for building targeted prospect lists from ICP criteria — defining titles, firmographics, signals, then searching, enriching, and de-duplicating. Use when the user asks to build a list, find prospects matching criteria, target accounts in an industry/geo, or run an ICP-based prospecting workflow.
---

# Prospect List Building

## Step 1: Define ICP Criteria
Gather from user:
- Target titles (VP Sales, CRO, Head of Growth)
- Company size (51-200, 201-1000)
- Industries (SaaS, FinTech)
- Locations (US, SF Bay Area)
- Technologies used (optional)

## Step 2: Search for Prospects
Apollo /mixed_people/search:
  data={"person_titles": ["VP Sales", "CRO"],
        "organization_num_employees_ranges": ["51,200"],
        "person_locations": ["United States"],
        "per_page": 100, "page": 1}

Or RocketReach /search:
  params={"current_title": ["VP Sales"],
          "company_size": "51-200",
          "location": ["United States"]}

## Step 3: Enrich Each Prospect
For each result, enrich with additional data:
- Phone numbers (RocketReach /lookupProfile)
- Company data (Apollo /organizations/enrich)
- Use Apollo/RocketReach email grades to filter low-quality addresses (standalone email verification is not integrated into nrev-lite today)

## Step 4: Score Against ICP
Score each prospect 0-100 based on:
- Title match (40 points)
- Company size match (20 points)
- Industry match (20 points)
- Location match (10 points)
- Data completeness (10 points)

## Step 5: Save & Export
**Always save to a dataset first** — this preserves data for re-use, dashboard creation, and scheduled workflow accumulation.

Primary: Save to a persistent dataset using `nrev_create_and_populate_dataset`:
- Name: descriptive (e.g., "VP Sales SaaS 51-200 Mar 2026")
- dedup_key: "email" (for contacts) or "domain" (for companies)
- columns: define all fields with types

Then optionally export from the dataset:
- Google Sheets (via Composio connection)
- CSV (via `nrev_query_dataset` or dashboard CSV export)
- HubSpot/Salesforce (via Composio connection)
- Instantly (for email campaigns)

## Cost Awareness
Show the user: "Building a list of N people will cost approximately $X.XX"
Track actual cost and report: "List complete: N people for $X.XX (Y% hit rate)"
