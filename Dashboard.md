---
title: AI Employee Dashboard
last_updated: 2026-03-02T18:00:00Z
version: "0.3.0"
tier: silver
---

# AI Employee Dashboard

## System Status

| Component            | Status    | Last Check           |
|----------------------|-----------|----------------------|
| FileSystem Watcher   | `READY`   | 2026-03-02 18:00 UTC |
| Gmail Watcher        | `READY`   | 2026-03-02 18:00 UTC |
| Approval Watcher     | `READY`   | 2026-03-02 18:00 UTC |
| LinkedIn Watcher     | `READY`   | 2026-03-06 UTC       |
| LinkedIn MCP Server  | `READY`   | 2026-03-06 UTC       |
| Slack Notifications  | `READY`   | 2026-03-02 18:00 UTC |
| Google Calendar      | `READY`   | 2026-03-02 18:00 UTC |
| Tool Layer (Gmail)   | `READY`   | 2026-03-02 18:00 UTC |
| Tool Layer (LinkedIn)| `READY`   | 2026-03-06 UTC       |
| Claude Code          | `READY`   | 2026-03-02 18:00 UTC |
| Dry-Run Mode         | `ON`      | —                    |

## Queue Summary

| Folder            | Count | Oldest Item                              |
|-------------------|-------|------------------------------------------|
| Inbox             | 0     | —                                        |
| Needs_Action      | 5     | FILE_2026-02-25 (test drops + orders)    |
| Done              | 1     | EMAIL_2026-02-25_order_inquiry_sarah     |
| Pending_Approval  | 0     | —                                        |
| Approved          | 0     | —                                        |
| Rejected          | 0     | —                                        |
| Plans             | 0     | —                                        |

## Watchers (4 total)

| Watcher            | Entry Point        | Poll Interval | Scope                    |
|--------------------|--------------------|---------------|--------------------------|
| FileSystem Watcher | `watch-inbox`      | 5s            | /Inbox file drops        |
| Gmail Watcher      | `watch-gmail`      | 120s           | Unread important emails  |
| Approval Watcher   | `watch-approvals`  | 10s           | /Approved folder         |
| LinkedIn Watcher   | `watch-linkedin`   | 15s           | LinkedIn social_post tasks |

## Agent Skills (8 total)

| Skill              | Purpose                                      |
|--------------------|----------------------------------------------|
| process-needs-action | Triage /Needs_Action items, classify, route |
| update-dashboard   | Refresh this dashboard                       |
| agent-md-refactor  | Refactor agent markdown files                |
| gmail-watcher      | Monitor Gmail for unread important emails    |
| hitl-approval      | HITL approval gate for sensitive actions     |
| linkedin-post      | LinkedIn post automation via MCP + Playwright |
| planning-agent     | Create structured plans in /Plans            |
| tool-integration   | Extensible API tool layer (Gmail, LinkedIn)  |

## Tool Layer (2 tools)

| Tool       | Actions                                       | HITL Required       |
|------------|-----------------------------------------------|---------------------|
| **gmail**  | list_unread, read_email, search               | No                  |
| **gmail**  | send_email                                    | Yes (approval gate) |
| **linkedin** | get_profile, get_connections                | No                  |
| **linkedin** | create_post                                 | Yes (approval gate) |

## LinkedIn Automation (MCP Server)

| Endpoint | Purpose | Status |
|----------|---------|--------|
| `/linkedin_login` | Browser login via Playwright | `READY` |
| `/linkedin_create_post` | Draft a post | `READY` |
| `/linkedin_publish_post` | Publish via browser | `READY` |
| `/linkedin_logout` | Close browser session | `READY` |

### LinkedIn Post Pipeline

| Metric | Count |
|--------|-------|
| Scheduled (Pending Approval) | 0 |
| Published | 0 |
| Rejected | 0 |

> Logs: `/Logs/linkedin_logs.md`

## Services

| Service             | Module                | Status    |
|---------------------|-----------------------|-----------|
| Slack Notifications | `slack_service.py`    | Configured |
| Google Calendar     | `calendar_service.py` | Configured |
| Audit Logger        | `audit_logger.py`     | Active    |
| Error Log (rotating)| `errors.log`         | Active    |
| Task Classifier     | `classifier.py`       | Active    |
| Approval Manager    | `approval_manager.py` | Active    |

## Test Coverage

| Test File                  | Tests | Status |
|----------------------------|-------|--------|
| test_filesystem_watcher.py | 32    | PASS   |
| test_classifier.py         | 67    | PASS   |
| test_calendar_service.py   | 29    | PASS   |
| test_slack_service.py      | 29    | PASS   |
| test_gmail_watcher.py      | 26    | PASS   |
| test_approval_manager.py   | 28    | PASS   |
| test_tools.py              | 33    | PASS   |
| **Total**                  | **244** | **ALL PASS** |

## Recent Activity

- [2026-03-06] `linkedin-post`: LinkedIn MCP server + watcher + agent skill created (Playwright automation)
- [2026-03-03] `tool-integration`: LinkedIn access token configured — tool layer now READY
- [2026-03-02 18:00] `update-dashboard`: Dashboard refresh — 244 tests passing, all systems nominal
- [2026-03-02 12:00] `update-dashboard`: Full dashboard refresh — Silver tier components complete
- [2026-03-02 11:00] `tool-integration`: Gmail + LinkedIn tool layer created (33 tests)
- [2026-03-02 10:30] `hitl-approval`: HITL approval manager + watcher created (28 tests)
- [2026-03-02 10:00] `gmail-watcher`: Gmail Watcher agent skill created (26 tests)
- [2026-02-25 19:45] `process-needs-action`: Triaged `EMAIL_2026-02-25_order_inquiry_sarah.md` → **HIGH** priority → moved to `/Done`
- [2026-02-25 14:59] `filesystem-watcher`: Live file drop test — 2 files processed end-to-end

## Alerts

> No alerts.

---
*Auto-updated by the `update-dashboard` Agent Skill.*
