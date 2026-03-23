# Channel Strategy for B2B Prospecting

## LinkedIn (Sales Navigator)

**Boolean search:** Use AND, OR, NOT, quotes, parentheses in keyword/title fields.
Example: `(Marketing AND (Growth OR Demand OR Revenue)) AND (startup OR SaaS)`

**Spotlight filters (most underutilized):**
- Changed Jobs (Past 90 Days) — 3x more likely to buy. Transforms response rates.
- Recently Posted — active users respond more.
- Follows Your Company / Buyer Intent — already brand-aware.

**AI features (2025-2026):** Buyer Intent Signals filter uses behavioral analysis. Micro-behavior tracking for funding announcements and content engagement. Reportedly +30% conversion.

**Best practice:** Combine 3-4 filters. Save searches for weekly notifications. Automate identification, authenticate interactions.

## Cold Email

**Infrastructure (non-negotiable 2025-2026):**
- SPF, DKIM, DMARC mandatory
- Dedicated sending domain (outreach.yourcompany.com)
- 50-100 emails/mailbox/day max
- One sending address per domain
- Multiple domains to spread volume
- Target 95%+ deliverability

**Warming:** Week 1-2: 10-25/day. Week 3-4: 25-50/day. Week 5-6: scale. Keep warm at 15% of volume.

**Writing:** Under 80 words first touch. Reference triggers (hiring, funding, launches). Single CTA. 5% of senders personalize every email but get 2-3x results.

**Follow-up:** 4-7 touchpoints. 58% replies on Step 1; 42% from follow-ups. 60% come after second follow-up. Launch Monday, follow-up Wednesday.

## Communities

Cold outreach reply rates below 6%; community-sourced deals close faster at higher value. 80% of B2B buying happens in "dark social."

**Key Slack communities:**
- Marketing/Growth: RevGenius (50K+), Superpath, Demand Curve, Online Geniuses (53K+)
- SaaS/Startups: Product-led Growth (15K+), SaaS Alliance, SaaStock
- GTM/Sales: Pavilion, TEAMM (invite-only)

**Reddit:** Influences ~75% of B2B buying decisions. Key subs: r/sales, r/SaaS, r/startups, r/sysadmin, r/devops, r/ecommerce. Follow 90/10 rule (90% value, 10% promotion). Comment-first for 6-8 weeks before self-promotion. r/devops often beats r/Entrepreneur for SaaS leads.

## Events

53.9% of attendees plan more in-person in 2025. 80% see in-person as most trustworthy discovery channel.

**Playbook:** Pre-event (SDRs run email/phone/LinkedIn to book on-site meetings), on-site (booth qualification + demos/dinners), post-event (24-48 hour follow-up, multi-touch 2-3 weeks).

**Pro tip:** Small executive roundtables adjacent to big conferences > booth traffic. First-party event data improves CAC by 83%.

## Intent Data Platforms

| Platform | Type | What It Captures | Price |
|----------|------|-----------------|-------|
| Bombora | Cooperative | Content consumption across 5K+ B2B sites | $25K-$75K/yr |
| G2 | Platform-specific | Software research, vendor comparison | $10K-$36K/yr |
| TrustRadius | Platform-specific | Verified review engagement from 12M buyers | Varies |
| 6sense | AI-aggregated | Multi-source intent + buying stage | $35K-$150K+ |
| Demandbase | AI-aggregated | 650K+ intent keywords | $50K-$150K+ |

92% of B2B buyers consult review platforms before purchase. Platform-specific intent (G2, TrustRadius) is more accurate than topic-level because it captures explicit buying behavior.

## Social Selling on X/Twitter

X inboxes far less crowded since most B2B shifted budgets. 27% of Americans earning $100K+ use X.

**Signal-Bridge-Value framework:** Reference the tweet, connect to a challenge, offer something useful. Focus on pain points, milestones, tool discussions, competitor mentions.

Expected: 15-20 personalized emails/week, 2-4 meetings (15-20% reply, 50% meeting conversion).

## GitHub for Technical Buyers

100M+ developers, only 31% of recruiters use it. Advanced search (language:, location:, followers:). Evaluate contribution graphs, pinned repos, commit quality.

Referencing specific work (commits, PRs) increases response by 60%. Intent signals: opening issues, commenting on repos, downloading packages, browsing docs.

## Job Boards as Intent Signals

**URL patterns:**
- Greenhouse: `boards.greenhouse.io/{company}`
- Lever: `jobs.lever.co/{company}`
- Ashby: `jobs.ashbyhq.com/{company}`

**Signal mapping:** 5 SDR hires = scaling outbound. RevOps hire = investing in tooling. 10+ sales reps = needs sales tools.

Use `nrev_search_patterns(use_case="hiring_signals")` then `nrev_google_search` with site: filters for each ATS platform.
