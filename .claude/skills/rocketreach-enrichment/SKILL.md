---
name: rocketreach-enrichment
description: RocketReach API reference for contact lookup, email finding, and people search through the gtm_enrich proxy. Use when the user wants direct dials, mobile numbers, or as a fallback after Apollo when person enrichment misses.
---

# RocketReach Enrichment via GTM Engine

## Overview
RocketReach provides contact lookup, email finding, and people search.
All calls go through gtm_enrich with provider="rocketreach".
Base URL: https://api.rocketreach.co/v2/api (handled by proxy)

## Endpoints

### GET /lookupProfile -- Look up a person
Query params:
- email (string) -- Email to look up
- linkedin_url (string) -- LinkedIn URL
- name (string) -- Full name (combine with company for best results)
- current_employer (string) -- Company name

Example:
  gtm_enrich(provider="rocketreach", method="GET", endpoint="/lookupProfile",
             params={"email": "jane@acme.com"})

Response shape:
  { "id": 123, "status": "complete", "name": "Jane Smith",
    "current_title": "VP Sales", "current_employer": "Acme Corp",
    "emails": [{"email": "jane@acme.com", "smtp_valid": "valid"}],
    "phones": [{"number": "+1415...", "type": "professional"}],
    "linkedin_url": "...", "city": "San Francisco", "region": "California" }

### GET /search -- Search for people
Query params:
- name (string) -- Person name
- current_title (array) -- Job titles
- current_employer (string) -- Company name
- company_domain (string) -- Company domain
- location (array) -- Locations
- start (int) -- Pagination offset [default: 1]
- page_size (int) -- Results per page [default: 10, max: 100]

### GET /lookupEmail -- Find email for a person
Query params:
- name (string) -- Full name
- current_employer (string) -- Company name

## Cost: ~$0.04/lookup
