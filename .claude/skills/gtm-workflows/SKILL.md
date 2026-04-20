---
name: gtm-workflows
description: Catalog of pre-defined multi-step GTM workflow templates (prospect research, list building, signal monitoring, enrichment) and the dataset-first persistence rule that ensures results survive external export failures. Use when the user wants a ready-made workflow template, asks "what kind of workflows can I run?", or is about to produce structured output that should be persisted.
---

# Common GTM Workflows

## Dataset-First Principle

Every workflow that produces structured output should save results to a persistent dataset BEFORE any external export (Sheets, CRM, email). Use `nrev_create_and_populate_dataset` with an appropriate `dedup_key`. This ensures data is preserved, queryable, and dashboard-ready regardless of whether the external export succeeds.

## Workflow 1: Prospect Research
1. User provides target criteria (industry, company size, titles)
2. Search Apollo /mixed_people/search with criteria
3. Enrich top results with Apollo (or RocketReach if phone numbers are required)
4. Score against ICP
5. Save to dataset (dedup_key: "email") via `nrev_create_and_populate_dataset`
6. Export to Google Sheets (via Composio connection)

## Workflow 2: Account-Based Enrichment
1. User provides list of target domains
2. For each domain: Apollo /organizations/enrich
3. Find key contacts: Apollo /mixed_people/search per domain
4. Enrich contacts via Apollo (add RocketReach if phones are needed)
5. Save to dataset (dedup_key: "email") via `nrev_create_and_populate_dataset`
6. Push to CRM via HubSpot/Salesforce connection

## Workflow 3: Email Campaign Launch
1. Build prospect list (Workflow 1 or 2)
2. Rely on Apollo/RocketReach email quality grades to filter questionable addresses (standalone email verification is not integrated into nrev-lite today)
3. Save filtered list to dataset (dedup_key: "email")
4. Push to Instantly via the Instantly App (Composio OAuth) — `nrev_app_execute`
5. Create/activate the campaign through the same app actions
6. Monitor via the Instantly dashboard or Composio action responses

## Workflow 4: Company Research
1. Use Parallel AI for deep company research
2. Use Google Search for recent news/funding
3. Enrich company via Apollo/Crustdata
4. Find contacts at company
5. Save to dataset (dedup_key: "domain") via `nrev_create_and_populate_dataset`
6. Build personalized outreach angles

## Workflow 5: Competitive Intelligence
1. Search Google for competitor info (rapidapi_google or google_search)
2. Deep research with Parallel AI
3. Find their customers via case studies
4. Build lookalike prospect list via Apollo search
5. Save to dataset (dedup_key: "domain") via `nrev_create_and_populate_dataset`

## Human-in-the-Loop Checkpoints
- Before sending any campaign: require approval
- Before adding >100 leads: confirm with user
- Before spending >$10 on enrichment: confirm budget
