---
name: gmail-watcher
description: Monitor Gmail for unread important emails and create action files in /Needs_Action with email metadata and classification.
version: "0.1.0"
---

# Gmail Watcher

Polls Gmail API for unread important emails, classifies them, and creates `.md` files in `/Needs_Action`.

---

## Triggers

Use this skill when:
- "check gmail"
- "monitor emails"
- "watch gmail"
- "check for new emails"
- "start gmail watcher"

---

## Quick Reference

| Step | Action | Output |
|------|--------|--------|
| 1. Poll | Query Gmail API for unread important messages | List of new emails |
| 2. Dedup | Filter out already-processed message IDs | New messages only |
| 3. Classify | Run classifier on subject + snippet | Category + priority |
| 4. Create | Write `.md` to `/Needs_Action` with frontmatter | `EMAIL_*.md` file |
| 5. Notify | Slack notification + Calendar event (if due date) | Notifications sent |
| 6. Log | Audit entry to `/Logs/YYYY-MM-DD.json` | JSON log line |

---

## Process

### Step 1: Poll Gmail

Query Gmail API with `is:unread is:important` (configurable via `GMAIL_QUERY` env var).

```
For each unread important message:
  - Check if gmail_id already processed → skip
  - Fetch full message (headers, snippet, body)
```

### Step 2: Classify

Use the rule-based classifier on `subject + snippet + body`:
- **Priority**: urgent / high / medium / low
- **Category**: Urgent / Work / Personal / Idea
- **Due date**: Based on priority offset

### Step 3: Create Action File

Write to `/Needs_Action/EMAIL_<timestamp>_<subject>.md`:

```yaml
---
type: email
source: gmail
gmail_id: "msg_id"
from: "sender@example.com"
subject: "Email Subject"
category: Work
priority: high
suggested_due_date: "2026-03-03"
status: pending
---
```

### Step 4: Log and Notify

- Audit log: `action_type: "email_processed"`
- Slack: Task processed notification
- Calendar: Event created if due date assigned

---

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `GOOGLE_CREDENTIALS_PATH` | `credentials.json` | OAuth client secrets |
| `GMAIL_TOKEN_PATH` | `gmail_token.json` | Persisted OAuth token |
| `GMAIL_CHECK_INTERVAL` | `120` | Poll interval in seconds |
| `GMAIL_QUERY` | `is:unread is:important` | Gmail search query |

---

## Running

```bash
cd watchers
uv run watch-gmail
```

---

## Safety

- **Read-only**: Uses `gmail.readonly` scope — never modifies emails
- **DRY_RUN**: Logs intent without creating files or calling APIs
- **Retry**: 3 attempts per message, then quarantine
- **Never crashes**: All API errors caught and logged
- ALWAYS log every action to `/Logs/`
