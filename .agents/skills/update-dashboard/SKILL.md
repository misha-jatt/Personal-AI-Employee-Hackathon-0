---
name: update-dashboard
description: Refresh Dashboard.md with current folder counts, system status, and recent activity from audit logs.
version: "0.1.0"
---

# Update Dashboard

Recalculate all metrics and rewrite `Dashboard.md` with the current vault state.

---

## Triggers

Use this skill when:
- "update dashboard"
- "refresh status"
- "show vault status"
- "what's the current state"
- After processing any items (called by other skills)

---

## Quick Reference

| Step | Action | Output |
|------|--------|--------|
| 1. Count | Count `.md` files in each workflow folder | Folder metrics |
| 2. Activity | Read today's log from `/Logs/` | Recent actions list |
| 3. Alerts | Check for stale items or anomalies | Alert messages |
| 4. Write | Rewrite `Dashboard.md` with fresh data | Updated dashboard |

---

## Process

### Step 1: Count Folder Contents

For each workflow folder, count the `.md` files (excluding `.gitkeep`):

```
Folders to scan:
  /Inbox
  /Needs_Action
  /Done
```

For each folder, also find the **oldest item** by reading the `detected_at` or `created` frontmatter field.

### Step 2: Gather Recent Activity

Read today's log file at `/Logs/YYYY-MM-DD.json`.

Extract the last 10 entries and format them as a bullet list:

```markdown
- [2026-02-23 10:30] FileSystemWatcher: Processed invoice.pdf → Needs_Action
- [2026-02-23 10:31] process-needs-action: Triaged invoice.pdf → Done (high)
```

If no log file exists for today, show: "No activity today."

### Step 3: Generate Alerts

Check for these conditions:

| Condition | Alert |
|-----------|-------|
| Item in `/Needs_Action` older than 24 hours | "Stale item: [name] has been pending for [N] hours" |
| More than 10 items in any queue | "Queue overflow: [folder] has [N] items" |
| No log entries in past 24 hours | "System may be idle — no recent activity" |

### Step 4: Write Dashboard.md

Rewrite `Dashboard.md` with this exact structure:

```markdown
---
title: AI Employee Dashboard
last_updated: <current ISO timestamp>
version: "0.1.0"
tier: bronze
---

# AI Employee Dashboard

## System Status

| Component         | Status    | Last Check           |
|-------------------|-----------|----------------------|
| FileSystem Watcher | `STATUS` | <timestamp or —>     |
| Claude Code        | `READY`  | <current timestamp>  |
| Dry-Run Mode       | `ON/OFF` | —                    |

## Queue Summary

| Folder             | Count | Oldest Item          |
|--------------------|-------|----------------------|
| Inbox              | N     | <date or —>          |
| Needs_Action       | N     | <date or —>          |
| Done               | N     | <date or —>          |

## Recent Activity

- [timestamp] action description
- ...

## Alerts

- alert message (or "No alerts.")

---
*Auto-updated by the `update-dashboard` Agent Skill.*
```

---

## Safety

- Dashboard.md is the ONLY file this skill writes to (single-writer rule)
- Never delete or modify files in workflow folders — only read and count
- Always include `last_updated` timestamp so humans know when data is fresh
