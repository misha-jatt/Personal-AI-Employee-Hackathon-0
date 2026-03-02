---
name: process-needs-action
description: Triage and process items in /Needs_Action. Reads pending items, classifies priority, and moves completed items to /Done.
version: "0.1.0"
---

# Process Needs_Action

Scan the `/Needs_Action` folder, triage each item by priority, process it, and move to `/Done`.

---

## Triggers

Use this skill when:
- "process inbox"
- "check needs_action"
- "what's pending"
- "triage new items"
- "process new items"

---

## Quick Reference

| Step | Action | Output |
|------|--------|--------|
| 1. Scan | Read all `.md` files in `/Needs_Action` | List of pending items |
| 2. Parse | Extract YAML frontmatter from each file | Structured metadata |
| 3. Classify | Assign priority (high/medium/low) | Updated frontmatter |
| 4. Process | Handle the item and move to `/Done` | File moved |
| 5. Log | Write audit entry to `/Logs/YYYY-MM-DD.json` | JSON log line |

---

## Process

### Step 1: Scan /Needs_Action

Read all `.md` files in the `/Needs_Action` directory. Ignore `.gitkeep` and non-markdown files.

```
For each file in /Needs_Action/*.md:
  - Read the file
  - Parse YAML frontmatter
  - Extract: type, source, priority, status
```

### Step 2: Classify Priority

Assign priority based on these rules from `Company_Handbook.md`:

| Signal | Priority |
|--------|----------|
| Contains "urgent", "asap", "payment", "invoice" | **high** |
| From known contact / contains "request", "question" | **medium** |
| General file drop, informational | **low** |

Update the frontmatter `priority` field in the file.

### Step 3: Process and Route to Done

For each item:
1. Read the file content
2. Determine what action is needed based on type
3. Process the item
4. Move the file from `/Needs_Action` to `/Done`

### Step 4: Update Dashboard

After processing all items, invoke the `update-dashboard` skill to refresh `Dashboard.md`.

### Step 5: Log Every Action

For each item processed, write a JSON log entry:

```json
{
  "timestamp": "ISO-8601",
  "action_type": "needs_action_triage",
  "actor": "process-needs-action",
  "target": "filename.md",
  "parameters": {
    "priority": "high",
    "routed_to": "/Done"
  },
  "result": "success"
}
```

Write to `/Logs/YYYY-MM-DD.json` (one JSON object per line).

---

## Output Format

After processing, report a summary:

```markdown
## Triage Complete

| Item | Type | Priority | Routed To |
|------|------|----------|-----------|
| FILE_invoice.pdf.md | file_drop | high | /Done |
| FILE_report.csv.md | file_drop | low | /Done |

**Processed**: 2 items
**High priority**: 1
```

---

## Safety

- NEVER delete source files — move them to `/Done`
- ALWAYS log every triage decision to `/Logs/`
- Read `Company_Handbook.md` before processing
