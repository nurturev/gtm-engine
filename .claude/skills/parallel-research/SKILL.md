---
name: parallel-research
description: Parallel AI web-research API reference for deep company research, market analysis, and AI-driven web intelligence via the gtm_enrich proxy. Use when the user wants AI-powered research on a company, industry, or market segment, or asks to "research", "deep dive", or "analyze" a target.
---

# Parallel AI Web Research via GTM Engine

## Overview
Parallel AI provides AI-powered web research capabilities.
All calls go through gtm_enrich with provider="parallel".
Base URL: https://api.parallel.ai/v1 (handled by proxy)

## Use Cases for GTM
- Deep company research: Ask Parallel to research a company's products, culture, recent news
- Market analysis: Research a market segment or industry
- Competitive intelligence: Compare competitors
- Account planning: Build detailed profiles of target accounts
- Personalization research: Find talking points for outreach

## Example Workflows

### Research a Company
  gtm_enrich(provider="parallel", endpoint="/research",
             data={"query": "What does Acme Corp do? Recent funding, products, key people."})

### Find Talking Points for Outreach
  gtm_enrich(provider="parallel", endpoint="/research",
             data={"query": "Recent news about Stripe. Any new product launches or exec changes in the last 3 months?"})

## Tips
- Parallel works best for open-ended research questions
- Combine with Apollo/RocketReach for structured contact data
- Use for account research before outreach to personalize messaging

## Cost: ~$0.02/research query
