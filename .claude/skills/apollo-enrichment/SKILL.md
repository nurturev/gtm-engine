---
name: apollo-enrichment
description: Apollo.io API reference for people search, person/organization enrichment, and email finding through the gtm_enrich proxy. Use when the user wants to enrich a person or company, find a work email, search Apollo for prospects, or specifically mentions Apollo.io.
---

# Apollo.io Enrichment via GTM Engine

## Overview
Apollo provides people search, enrichment, email finding, and organization data.
All calls go through gtm_enrich with provider="apollo".
Base URL: https://api.apollo.io/api/v1 (handled by proxy)

## Endpoints

### POST /people/match -- Enrich a single person
Parameters:
- email (string) -- Email to match
- first_name (string) -- Optional, improves match
- last_name (string) -- Optional
- organization_name (string) -- Optional
- domain (string) -- Company domain
- linkedin_url (string) -- LinkedIn profile URL

Example:
  gtm_enrich(provider="apollo", endpoint="/people/match",
             data={"email": "jane@acme.com"})

Response shape:
  { "person": { "id": "...", "first_name": "...", "last_name": "...",
    "title": "...", "email": "...", "organization": { ... },
    "phone_numbers": [...], "linkedin_url": "..." } }

### POST /mixed_people/search -- Search for people
Parameters:
- person_titles (array) -- Job titles to match ["VP Sales", "CRO"]
- organization_domains (array) -- Company domains ["acme.com"]
- person_locations (array) -- Locations ["San Francisco"]
- organization_num_employees_ranges (array) -- ["51,200"]
- q_organization_keyword_tags (array) -- Industry keywords ["SaaS", "fintech"]
- page (int) -- Pagination [default: 1]
- per_page (int) -- Results per page [default: 25, max: 100]

### POST /organizations/enrich -- Enrich a company
Parameters:
- domain (string) -- Company domain

### POST /mixed_companies/search -- Search companies
Parameters:
- organization_domains (array)
- organization_locations (array)
- organization_num_employees_ranges (array)

## Error Handling
- 401: Key invalid or expired. Tell user to re-add via gtm add-key apollo
- 422: Invalid parameters. Check required fields.
- 429: Rate limited. Apollo allows ~300 req/min on standard plans.

## Cost: ~$0.03/enrichment
