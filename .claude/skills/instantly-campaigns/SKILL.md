---
name: instantly-campaigns
description: Instantly.ai API reference for managing cold email campaigns, leads, and inboxes through the gtm_enrich proxy. Use when the user wants to push leads into Instantly, list or update campaigns, check campaign status, or manage cold email outreach via Instantly.
---

# Instantly Email Campaigns

Base URL: https://api.instantly.ai/api/v1 (via proxy)
Auth: query param api_key (handled automatically)

## Endpoints

### GET /campaign/list -- List campaigns
  gtm_enrich(provider="instantly", method="GET", endpoint="/campaign/list")

### POST /campaign/create -- Create a new campaign
  data={"name": "Q1 Outreach", "schedule": {...}}

### POST /lead/add -- Add leads to a campaign
  data={"campaign_id": "...",
        "leads": [{"email": "...", "first_name": "...",
                    "last_name": "...", "company_name": "..."}]}

### GET /analytics/campaign/summary -- Campaign metrics
  params={"campaign_id": "..."}

### POST /lead/delete -- Remove leads
  data={"campaign_id": "...", "delete_list": ["email1@...", "email2@..."]}

## Campaign Workflow
1. Create campaign with name and schedule
2. Add verified leads (always verify first!)
3. Set sequences/steps in Instantly UI or via API
4. Monitor analytics via /analytics/campaign/summary

## Important: Always verify emails before adding to Instantly
Sending to invalid emails damages sender reputation.
