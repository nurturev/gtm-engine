# Composio Tool Connections — Dynamic Action Discovery

## Architecture

- **No hardcoded action names or parameters.** Everything is discovered at runtime.
- Actions and schemas come from Composio's API, proxied through the nrev-lite server.
- If Composio changes action names or params, Claude discovers the changes automatically.

## Workflow (MUST follow this order)

### Step 1: Check what's connected
```
nrev_list_connections()
→ Returns: [{app_id: "gmail", status: "ACTIVE"}, ...]
```
If the needed app isn't connected → tell the user to connect it via the dashboard.

### Step 2: Discover available actions
```
nrev_list_actions(app_id="gmail")
→ Returns: [{name: "GMAIL_SEND_EMAIL", display_name: "Send Email", description: "..."}, ...]
```

### Step 3: Get the EXACT parameter schema (NON-OPTIONAL)
```
nrev_get_action_schema(action_name="GMAIL_SEND_EMAIL")
→ Returns: {parameters: {recipient_email: {type: "string", required: true}, ...}}
```

**THIS STEP IS CRITICAL.** Do NOT skip it. Do NOT guess parameter names. Examples of why:
- Google Docs uses `text_to_insert`, NOT `text`
- Google Docs uses `insertion_index`, NOT `index`
- Google Docs uses `markdown_text`, NOT `content` or `markdown_content`
- Google Sheets `ranges` must be an `array`, NOT a `string`
- Google Sheets search `query` uses Google Drive query syntax: `name contains 'budget'`

### Step 4: Execute with correct params
```
nrev_execute_action(app_id="gmail", action="GMAIL_SEND_EMAIL", params={...})
```

## Available Apps (15)

| app_id | Name | Category |
|--------|------|----------|
| slack | Slack | communication |
| gmail | Gmail | communication |
| google_sheets | Google Sheets | data |
| google_docs | Google Docs | data |
| airtable | Airtable | data |
| google_drive | Google Drive | data |
| hubspot | HubSpot | crm |
| salesforce | Salesforce | crm |
| attio | Attio | crm |
| linear | Linear | project |
| notion | Notion | project |
| clickup | ClickUp | project |
| asana | Asana | project |
| google_calendar | Google Calendar | calendar |
| calendly | Calendly | calendar |

## Example Flow

User says: "Send an email to jane@acme.com about the meeting"

1. `nrev_list_connections()` → gmail is ACTIVE
2. `nrev_list_actions(app_id="gmail")` → sees GMAIL_SEND_EMAIL
3. `nrev_get_action_schema(action_name="GMAIL_SEND_EMAIL")` → learns exact params
4. `nrev_execute_action(app_id="gmail", action="GMAIL_SEND_EMAIL", params={...})`

## Connecting an App

**Dashboard:** `http://localhost:8000/console/{tenant_id}?tab=connections`
**CLI:** `nrev-lite connect <app_id>` (opens browser for OAuth)

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `"No active connection for 'X'"` | App not connected | Connect via dashboard or CLI |
| `"Following fields are missing: {X}"` | Wrong param names | **You skipped Step 3.** Call `nrev_get_action_schema` |
| `"Tool X not found"` | Invalid action name | **You skipped Step 2.** Call `nrev_list_actions` |
| `"Unknown app: 'X'"` | Invalid app_id | Use one of the 15 app_ids listed above |
| `"Invalid request data"` with type error | Wrong param type | Check schema — array vs string, etc. |
| `"Connected account missing v2 identifier"` | Composio issue | Disconnect and reconnect |

## Key Principle

The schema is the source of truth. When in doubt, call `nrev_get_action_schema`.
