---
name: composio-connections
description: Discovery flow (list → actions → schema → execute) for user-connected apps like Gmail, Slack, HubSpot, Salesforce, Google Sheets, Calendar, Notion, Linear, ClickUp, and Instantly via Composio. Use when the user asks to send/draft/post/update via an external tool, mentions an app by name, or wants to read from or write to one of their own connected SaaS tools.
---

# Apps — Connected Tool Actions (via Composio)

## Apps vs Data Providers

**Apps** are tools the user connects via OAuth (Gmail, Slack, Zoom, CRM, outreach tools). Use Apps when you need to **read from or write to a user's own tool**. App actions are **free** (no credits).

**Data Providers** are API services for enrichment, search, scraping, and AI research. Use Data Providers when you need **bulk data or row-level processing**. These cost credits (unless BYOK).

**Rule of thumb:** "Is this the user's tool that they log into?" → Apps. "Is this a data/AI service nrev-lite calls on behalf of the user?" → Data Providers.

## When to Activate

Use this skill when:
- The user asks to interact with an external app (send email, check calendar, update CRM, send campaign, etc.)
- A GTM workflow step requires delivery to an external system (push leads to HubSpot, send results via Slack)
- The user asks "what apps are connected?" or "can I use Gmail?"

See the **Intent-to-App Mapping** in CLAUDE.md for keyword → app_id triggers.

## Architecture

- **No hardcoded action names or parameters.** Everything is discovered at runtime via Composio's API.
- If Composio changes action names or params, Claude discovers the changes automatically.
- **Composio actions are free** — no credits are charged for connected app actions.

---

## Level 1: Intent Recognition + Routing

When the user's request involves an external app:

1. **Identify the target app_id** from the user's intent (see CLAUDE.md mapping table).

2. **Check for system MCP tools first.** Look for tools named `slack_send_message`, `clickup_create_task`, `gmail_*`, etc. in your available tools. If a system MCP tool exists for that app, **use it directly** — it's faster and already authenticated.

3. **If no system MCP tool**, call `nrev_app_list()`:
   - If the app is **ACTIVE** → proceed to Level 2 (Action Discovery)
   - If the app is **not listed** → call `nrev_app_connect(app_id)` to set it up in-session (OAuth URL or API key prompt)
   - If the user asks **"what apps can I connect?"** → call `nrev_app_catalog()` to browse all 22 available apps

4. **Console for app management:** Use `nrev_open_console(tab="apps")` if the user wants to see all their connections in the dashboard.

5. **Never ask the user to set up a system MCP** — that's technical. Guide them to nrev-lite's app connection flow instead.

---

## Level 2: Action Discovery

### Step 1: List available actions
```
nrev_app_actions(app_id="gmail")
→ Returns: [{name: "GMAIL_SEND_EMAIL", display_name: "Send Email", description: "..."}, ...]
```

### Step 2: Get the EXACT parameter schema (NON-OPTIONAL)
```
nrev_app_action_schema(action_name="GMAIL_SEND_EMAIL")
→ Returns: {parameters: {recipient_email: {type: "string", required: true}, ...}}
```

**THIS STEP IS CRITICAL.** Do NOT skip it. Do NOT guess parameter names. The schema is the source of truth.

### Common Actions Quick Reference

These are **indicative names** to help you identify the right action from `nrev_app_actions` results. **Always call `nrev_app_actions` for real action names.**

| App | Common Actions (indicative) |
|-----|---------------------------|
| gmail | Send email, List emails, Read email, Create draft, Search emails |
| slack | Send message, List channels, Read channel messages, Create channel |
| microsoft_teams | Send message, List channels, Create meeting, List members |
| google_calendar | List events, Create event, Update event, Delete event, Find free slots |
| calendly | List event types, List scheduled events, Get event details |
| cal_com | List bookings, Create booking, List event types, Get availability |
| zoom | Create meeting, List meetings, Get recording, List webinars |
| fireflies | Get transcripts, Upload audio, Add to live meeting, Delete transcript |
| google_sheets | Read range, Write/update range, Create spreadsheet, Find spreadsheet |
| google_docs | Create document, Insert text, Read document, Append text |
| google_drive | List files, Upload file, Search files, Create folder |
| airtable | List records, Create record, Update record, Search records |
| hubspot | Create contact, Update contact, Create deal, List contacts, Search |
| salesforce | Create lead, Update opportunity, Query records (SOQL), Create contact |
| attio | Create record, Update record, List records, Search |
| instantly | List campaigns, Add leads, Get campaign stats, Create campaign |
| linear | Create issue, List issues, Update issue, List projects |
| notion | Create page, Update page, Query database, Search |
| clickup | Create task, Update task, List tasks, Add comment |
| asana | Create task, Update task, List tasks, Add comment |
| posthog | Capture event, Get insights, List feature flags, Query events |

---

## Level 3: Execution + Parameter Gotchas

### Execute the action
```
nrev_app_execute(app_id="gmail", action="GMAIL_SEND_EMAIL", params={...})
→ Returns: {status: "success", data: {...}} or {status: "error", error: "..."}
```

### Known Parameter Gotchas

These traps cause most failures. Always verify via `nrev_app_action_schema`, but be aware:

**Google Docs:**
- Use `text_to_insert`, NOT `text`
- Use `insertion_index`, NOT `index`
- Use `markdown_text`, NOT `content` or `markdown_content`

**Google Sheets:**
- `ranges` must be an **array**, NOT a string
- Range format: `"Sheet1!A1:B10"` (include sheet name)
- Search `query` uses Google Drive query syntax: `name contains 'budget'`
- When creating a spreadsheet, use the create action, not a Drive action

**Gmail:**
- Recipient field is typically `recipient_email`, not `to` or `email`
- Body may require specifying `is_html: true` for HTML content
- Attachments have specific format requirements — always check schema

**HubSpot / Salesforce:**
- Field names are **API names**, not display labels (e.g., `firstname` not `First Name`)
- Custom fields may have prefixes (HubSpot: property names, Salesforce: `Custom_Field__c`)
- Date formats vary — check the schema for expected format

**Google Calendar:**
- Date/time fields typically need ISO 8601 format with timezone
- Recurrence uses RRULE format — check schema for exact field name
- "All day" events vs timed events may use different fields

**General Rule:** When in doubt, call `nrev_app_action_schema`. The schema is always the source of truth.

### Response Handling

- `status: "success"` → action completed, check `data` for results
- `status: "error"` → check the `error` field:
  - "Following fields are missing" → you used wrong param names, re-check schema
  - "Token expired" / "401 Unauthorized" → user must reconnect the app in dashboard
  - "Rate limited" / "429" → wait briefly and retry, or tell user to try again
  - "Invalid request data" with type error → wrong param type (e.g., string vs array)

---

## Available Apps (22)

| app_id | Name | Category |
|--------|------|----------|
| slack | Slack | communication |
| gmail | Gmail | communication |
| microsoft_teams | Microsoft Teams | communication |
| google_sheets | Google Sheets | data |
| google_docs | Google Docs | data |
| airtable | Airtable | data |
| google_drive | Google Drive | data |
| hubspot | HubSpot | crm |
| salesforce | Salesforce | crm |
| attio | Attio | crm |
| instantly | Instantly | outreach |
| linear | Linear | project |
| notion | Notion | project |
| clickup | ClickUp | project |
| asana | Asana | project |
| google_calendar | Google Calendar | calendar |
| calendly | Calendly | calendar |
| cal_com | Cal.com | calendar |
| zoom | Zoom | meetings |
| fireflies | Fireflies.ai | meetings |
| posthog | PostHog | analytics |

---

## Example Flows

### Example 1: Send an email
User says: "Send an email to jane@acme.com about the meeting"

1. Intent: "email" → app_id: `gmail`
2. No system Gmail MCP → `nrev_app_list()` → gmail is ACTIVE
3. `nrev_app_actions(app_id="gmail")` → finds GMAIL_SEND_EMAIL
4. `nrev_app_action_schema(action_name="GMAIL_SEND_EMAIL")` → learns exact params
5. `nrev_app_execute(app_id="gmail", action="GMAIL_SEND_EMAIL", params={recipient_email: "jane@acme.com", subject: "Meeting Follow-up", body: "..."})`

### Example 2: Check today's calendar
User says: "What's on my calendar today?"

1. Intent: "calendar" → app_id: `google_calendar`
2. No system Calendar MCP → `nrev_app_list()` → google_calendar is ACTIVE
3. `nrev_app_actions(app_id="google_calendar")` → finds event listing action
4. `nrev_app_action_schema(action_name="GOOGLECALENDAR_FIND_EVENT")` → learns date params
5. `nrev_app_execute(app_id="google_calendar", action="...", params={start_date: "2026-03-24", ...})`

### Example 3: System MCP routing
User says: "Send this to the #sales channel on Slack"

1. Intent: "Slack" → app_id: `slack`
2. System Slack MCP exists (`slack_send_message` tool available) → **use system MCP directly**
3. `slack_send_message(channel_id="C...", message="...")` — no nrev-lite tools needed

---

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `"No active connection for 'X'"` | App not connected | Connect via dashboard or CLI |
| `"Following fields are missing: {X}"` | Wrong param names | **You skipped get_action_schema.** Call it. |
| `"Tool X not found"` | Invalid action name | **You skipped list_actions.** Call it. |
| `"Unknown app: 'X'"` | Invalid app_id | Use one of the 15 app_ids above |
| `"Invalid request data"` with type error | Wrong param type | Check schema — array vs string, etc. |
| `"Connected account missing v2 identifier"` | Composio issue | Disconnect and reconnect |
| `"Token expired"` / `"401"` | OAuth refresh failed | User must reconnect the app |
| `"Rate limited"` / `"429"` | Too many requests | Wait briefly and retry |

## Key Principle

The schema is the source of truth. When in doubt, call `nrev_app_action_schema`.
