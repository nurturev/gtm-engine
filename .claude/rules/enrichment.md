# Enrichment Rules

When making enrichment calls:
- Always check provider availability first (nrv_provider_status)
- Handle rate limiting gracefully (429 responses)
- Log the provider and endpoint, never the key
- Return data to user, never auth headers
- Always show cost estimates before batch operations (>10 records)

When building waterfall enrichment:
- Try cheapest provider first when quality is comparable
- Stop as soon as sufficient data is obtained
- Track which provider returned data for performance analytics
- Report total cost and hit rate after completion
- Use nrv_search_patterns to discover platform-specific query patterns before searching
