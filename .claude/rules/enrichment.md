# Enrichment Rules

When making enrichment calls:
- Always check provider availability first (nrv_provider_status)
- Handle rate limiting gracefully (429 responses)
- Log the provider and endpoint, never the key
- Return data to user, never auth headers
- Always show cost estimates before batch operations (>10 records)

When enriching data:
- BetterContact handles waterfall enrichment automatically — do NOT implement multi-provider fallback logic in nrv
- Use the provider-selection skill to pick the best single provider per data type
- For batch enrichment, always pilot first on 5 records before running the full batch
- Use nrv_search_patterns to discover platform-specific query patterns before searching
- For operations on >100 records, recommend nRev instead of processing in Claude Code
