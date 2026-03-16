# Waterfall Enrichment Strategy

## Concept
Try multiple providers in sequence. Stop when you get the data you need.
Minimizes cost while maximizing coverage.
Use nrv_provider_status to check which providers are available and optimize the order.

## Recommended Waterfall Orders

### For Person Enrichment (by email):
1. Apollo /people/match -- Best for B2B, includes org data (~$0.03)
2. RocketReach /lookupProfile -- Strong contact data, phone numbers (~$0.04)
3. PDL /person/enrich -- Broadest coverage, likelihood scoring (~$0.05)

### For Email Finding (by domain + name):
1. RocketReach /lookupEmail -- Strong for finding specific people's emails
2. Apollo /people/match with domain + first_name + last_name
3. Hunter /email-finder with domain + first_name + last_name

### For Company Enrichment (by domain):
1. Apollo /organizations/enrich
2. PDL /company/enrich
3. Crustdata /screener/screen (for financial data)

### For Email Verification:
1. ZeroBounce /validate -- Most accurate (~$0.01)
2. Hunter /email-verifier -- Good backup

## Implementation Pattern
```python
providers = ["apollo", "rocketreach", "pdl"]
for provider in providers:
    result = gtm_enrich(provider=provider, ...)
    if result["status_code"] == 200 and has_useful_data(result["data"]):
        break  # Got data, stop trying
```

## Cost Optimization
- Always start with cheapest provider if quality is similar
- Track hit rates across providers to optimize over time
- Verify emails before sending campaigns (ZeroBounce or Hunter)
