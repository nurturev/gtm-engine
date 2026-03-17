# Parallel Web Systems — Tool Quirks & Best Practices

## When to Use Parallel Web

Parallel Web is your **non-standard enrichment powerhouse**. Use it when:
- Target is NOT in Apollo/RocketReach (local businesses, D2C brands, niche companies)
- You need to scrape a specific web page for structured data
- You need AI-powered research answers (company context, challenges, market analysis)
- You need to extract data from Instagram, Yelp, Google Maps listings
- You need batch processing of 10+ URLs

Do NOT use when:
- Simple person enrichment (Apollo is cheaper at $0.03 vs Parallel's per-request cost)
- Simple company enrichment (Apollo/RocketReach have structured data)
- You just need an email address (use RocketReach or Hunter)

## API Operations

### 1. scrape_page — Extract from URLs
```python
nrv_scrape_page(url="https://www.yelp.com/biz/some-restaurant", objective="Extract business name, phone, email, website, address, hours")
```
**Quirk:** Always provide an `objective` parameter describing what you want extracted. Without it, you get raw page content.

### 2. search_web — AI-Powered Research
```python
nrv_parallel_research("What are the biggest challenges facing [company name] in 2026?")
```
**Best for:** Open-ended research questions where you need synthesized answers, not raw URLs.
**Cost:** ~$0.02/query

### 3. extract_structured — Task API for Complex Extraction
**Use for:** Multi-step extraction, form-like data from complex pages.
**Rate limit:** 2,000 req/min

### 4. batch_extract — Bulk Processing
**Use when:** Processing 10+ URLs
**Quirk:** Auto-batches with concurrent execution (default 20 concurrent). Don't manually loop — use the batch endpoint.

## Rate Limits
- Search/Extract: 600 req/min
- Tasks: 2,000 req/min

## Pricing
- 20K free requests
- Search: $0.004-$0.009/request
- Tasks: $5-$2,400 per 1K (varies by complexity)

## Best Practices

1. **Always set an objective** — tells the AI what to extract
2. **Use batch for 10+ URLs** — much faster than sequential
3. **Combine with Google discovery** — Google finds URLs, Parallel extracts data
4. **Cache-friendly** — set `cache_age` parameter to reuse recent extractions
5. **Great for enriching Instagram/Yelp/Maps** — extracts structured business data from listing pages that APIs don't cover
