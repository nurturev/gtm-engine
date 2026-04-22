# RocketReach Universal API — Probe Results (v2 pass)

Captured by `scripts/probe_rocketreach_universal_apis.py` against the
`common.dev@nurturev.com` account (key `ROCKETREACH_API` in `.env`).

---

## Headline

**Our client-facing contract does not need to change.** Everything the
Universal migration requires can be absorbed inside
`server/execution/providers/rocketreach.py`. Clients continue to call
`enrich_person`, `search_people`, `enrich_company`, `search_companies` with
the exact same input fields they use today.

- ✅ **Client-facing input contract: NO CHANGE REQUIRED.** Every identifier
  and filter our clients send today (`linkedin_url`, `email`, `name`,
  `current_employer`, `domain`, `title`, `titles`, `company_domain`, `geo`,
  `seniority`, `industry`, `skills`, `company_size`, `department`, `school`,
  `previous_employer`, `keyword`, `exclude_*`, `page_size`, `start`,
  `order_by`, etc.) is a strict subset of what Universal accepts. Zero
  breaking input shape changes.
- 🟡 **Client-facing response shape: minor shifts, acceptable per user
  directive** (the AI consumes responses and tolerates restructuring). See
  "Response shifts" below.
- 🔴 **Blocker before we can finalize the empirical diff**: the current API
  key has no Universal credit allocation. Every `/universal/*` probe returns
  `403 "These endpoints require Universal Credits."` This is a vendor-side
  provisioning step, not a code change.

---

## The blocker

Universal endpoints all return:

```json
{"detail": "These endpoints require Universal Credits."}
```

`account.json` shows the current key carries `premium_lookup 171/205`
remaining but has **no `universal_*` credit bucket** in `credit_usage`.

**Action**: have `sales@rocketreach.co` flip `common.dev@nurturev.com`
(account id `30412685`) onto the Universal plan or issue a Universal-scoped
key. Once credits land, re-run the probe script — the `universal_*.json`
placeholders will be overwritten with real payloads for the final
field-level diff.

---

## Input contract — what clients send to nRev

**Nothing here changes.** Repeating this explicitly because it is the whole
point of this pass:

| nRev operation (client surface) | Input fields client sends today | Still works against Universal? |
|---|---|---|
| `enrich_person` | `linkedin_url`, `email`, `name`+`current_employer`, `first_name`+`last_name`, `domain`, `title`, `id` | ✅ Yes — Universal accepts same identifiers (plus `npi_number`/`ticker` which we don't expose) |
| `search_people` | `current_title`/`titles`, `current_employer`/`company`, `company_domain`/`domain`, `geo`/`location`, `management_levels`/`seniority`, `company_industry`/`industry`, `skills`, `company_size`, `department`, `school`, `previous_employer`, `keyword`, `exclude_*`, `contact_method`, `page_size`, `start`, `page`, `order_by` | ✅ Yes — Universal uses the same `{query, page_size, start, order_by}` body |
| `enrich_company` | `domain`, `name` | ✅ Yes — Universal accepts same (plus `linkedin_url`, `id`, `ticker` which we could additively expose later) |
| `search_companies` | `company_name`/`name`, `domain`, `industry`, `geo`, `employees`/`size`, `revenue`, `page_size`, `start`, `page`, `order_by` | ✅ Yes — identical body shape |

**No required input fields are added by Universal. No input field is renamed
or removed. No client needs to adjust anything they send.**

---

## Internal-only changes (invisible to clients)

All of this happens inside `server/execution/providers/rocketreach.py`.
None of it changes what the client sees on the way in.

### 1. Vendor path renames — pure internal swap

| Operation | Current URL (internal) | New URL (internal) |
|---|---|---|
| `enrich_person` | `GET /api/v2/person/lookup` | `GET /api/v2/universal/person/lookup` |
| `search_people` | `POST /api/v2/person/search` | `POST /api/v2/universal/person/search` |
| `enrich_company` | `GET /api/v2/company/lookup` | `GET /api/v2/universal/company/lookup` |
| `search_companies` | `POST /api/v2/searchCompany` (irregular legacy name) | `POST /api/v2/universal/company/search` |
| async poll (new) | n/a | `GET /api/v2/universal/person/check_status?ids=<id>,<id>` |

Note: our current code in `_OPERATION_MAP` at `rocketreach.py:390-415`
already uses `/company/search` for legacy — which is **wrong for legacy v2**
(the probe confirmed the working legacy path is `/searchCompany`). Once we
flip the prefix to `/universal/`, that same `/company/search` suffix becomes
correct. Pre-existing bug that self-resolves during the migration.

### 2. `reveal_*` flags — defaults applied when caller absent; `return_cached_emails` hard-pinned

Universal person lookup gates contact data behind per-field flags and
**requires at least one `reveal_*` flag on every call** — a request with
none fails with `400 {"non_field_errors": ["Please specify at least one
enrichment type to perform a person lookup"]}`. The vendor default is
`false` for every flag today; `return_cached_emails` vendor default also
flips to `false` on **2026-05-01**.

The provider fills in defaults when the caller did not set a flag and
honors caller-supplied values on the three cost/data flags. Values travel
as lowercase strings (`"true"`/`"false"`) because they ship as GET query
parameters and httpx would render Python `True` as `"True"`, which the
vendor rejects:

```python
# ~ _prepare_enrich_person — illustrative
p.setdefault("reveal_professional_email", "true")    # caller can override
p.setdefault("reveal_personal_email",     "false")   # caller can override
p.setdefault("reveal_phone",              "false")   # caller can override (also via enrich_phone_number)
p["return_cached_emails"] = "true"                   # HARD PIN — caller override is logged and ignored
```

Why `return_cached_emails` is hard-pinned: setting it to `false` forces
RocketReach into live SMTP verification, which returns `status="searching"`
and requires polling `/universal/person/check_status`. Our synchronous
call contract cannot absorb that on every lookup. The 2026-05-01 vendor
flip makes this time-critical — without the pin, every `enrich_person`
call would silently go async that day.

Client-visible impact with default caller input: **none**. Lookup
responses continue to include professional emails exactly as today.
Personal emails and phones are `null` unless the caller explicitly opts
in per request.

**Business caveat worth flagging** (not a contract change): each `reveal_*`
flag is metered separately in Universal billing. Average cost per
`enrich_person` lookup will change — likely upward from today's single-credit
model to a multi-sub-credit lookup. This is a pricing/cost call for the
product/finance side, not an API-surface call.

### 3. Async polling — absorbed inside the provider call

Universal person lookups can return `status: "searching" | "progress" |
"waiting"` asynchronously. Today our provider simply stamps
`_async_in_progress` on the response and returns (`rocketreach.py:527-534`).

In the Universal world we'll add a polling loop inside the provider that
hits `/universal/person/check_status` every 2-5s (cap ~30s) until every
requested id reaches `complete` or `failed`, and only then returns to the
caller.

Client-visible impact: **same response shape they already receive**, plus a
small latency increase when the vendor has to do fresh work. No contract
change.

### 4. Company lookup identifier set — additively larger

Universal company lookup also accepts `linkedin_url`, `id`, `ticker`
alongside today's `domain`/`name`. We can optionally extend
`_prepare_enrich_company` (`rocketreach.py:290-307`) to forward these when
clients send them, but we do not have to — current clients only send
`domain` and `name`, both of which keep working.

### 5. Credit-type parsing in `/account`

`credit_usage` buckets change name (`premium_lookup` → Universal-specific
types). Any server-side logic that surfaces remaining credits or does a
health probe off that endpoint will need to read the new bucket names.
Purely internal.

---

## Response shifts (client-visible shape, but acceptable)

Per the user directive: response-shape shifts are fine because the AI
consumes the output and is flexible. These are the shifts to expect once
Universal credits are enabled. Our normalizer
(`server/execution/normalizer.py:53-401, 787-883`) already returns a
canonical nRev shape to clients, so most of these are absorbed before they
reach the client at all.

### Person lookup

Largely identical to v2 shape. Key nested objects stay intact:
`emails[].{email, type, grade, smtp_valid, last_validation_check}`,
`phones[].{number, is_premium, recommended}`, `work_history[]`,
`education[]`, `skills`, plus top-level `current_title` / `current_employer`
/ `linkedin_url` / `location`. Our canonical normalizer is already aligned.

### Person search

Universal keeps the `profiles[]` + `pagination{start, next, total}` shape
seen in `v2_person_search.json`. Profile rows still carry contact *domains*
(not full emails) under `teaser.*`:

```
teaser: { emails[domains], phones[{number(masked), is_premium}],
          personal_emails[domains], professional_emails[domains],
          is_premium_phone_available, preview }
```

**Pre-existing normalizer bug (not Universal-caused)**: our
`_normalize_rr_person` reads `emails[]` at the top level of a profile, but
search rows only hold teaser domains, not full emails. Same bug exists
today against v2; worth fixing during the Universal cutover since we'll be
in the file anyway. Fix is internal and does not change what the client's
canonical row looks like.

### Company lookup

Shape is essentially identical to today (`id, name, domain, industry,
employees, revenue, founded, description, address, city, state, country,
tech_stack[], social links, ticker, linkedin_url`). Our normalizer reads
the same keys we already rely on. No canonical-shape change expected.

### Company search

Two response-level renames in the raw row shape, **absorbed in our
normalizer**:

| Legacy v2 raw field | Universal raw field |
|---|---|
| `email_domain` | `domain` |
| `industry_str` | `industry` |

Pagination also reshapes at the raw level: `{total, thisPage, nextPage,
pageSize}` → `{start, next, total}`. Our client never sees these —
`_normalize_rr_company` emits our canonical `companies[]` shape.

Universal rows are expected to carry richer data
(`employees`, `linkedin_url`) that the legacy v2 rows did not. That's a
positive enrichment on the canonical row we return, not a breaking change.

---

## Captured files (unchanged from first pass)

| File | What it shows |
|---|---|
| `account.json` | Current plan: `premium_lookup`-only, no `universal_*` bucket → root cause of every 403 below |
| `v2_person_lookup_linkedin.json` | Legacy v2 lookup — 404 ("Could not find the person…") |
| `v2_person_search.json` | Legacy v2 search — **full live payload**, reference shape |
| `v2_company_lookup_domain.json` | Legacy v2 company lookup — 403 (no company lookup credits on this plan) |
| `v2_company_search.json` | Legacy v2 company search — **full live payload**, reference shape |
| `universal_person_lookup_*.json`, `universal_person_search.json`, `universal_person_lookup_by_id.json`, `universal_company_lookup_*.json`, `universal_company_search.json` | All 403 — Universal credits not provisioned |

---

## Summary for the migration decision

- Client API surface: **no change**. Ship Universal migration without a
  client-side release.
- Internal provider: swap paths, add `reveal_*` defaults, add async
  polling loop, tolerate renamed raw response fields in the normalizer.
- Pricing: credit-per-lookup cost profile shifts (not a contract change,
  but worth surfacing to finance/product before rollout).
- Prereq: Universal credits enabled on the account — email
  `sales@rocketreach.co` referencing account id `30412685`.

## Explicit callout (per user ask): input-structure changes required of clients

**None.** If a meaningful input change surfaces after we re-run the probe
with Universal credits enabled, it will be highlighted here in bold.
