---
name: linkedin-post
description: Generate and publish LinkedIn business marketing posts via MCP server with HITL approval.
version: "0.1.0"
---

# LinkedIn Post

Automate LinkedIn business marketing posts through the full pipeline: detect task, generate post, approve, publish via browser automation.

---

## Triggers

Use this skill when:
- "post to linkedin"
- "create linkedin post"
- "schedule linkedin post"
- "check linkedin tasks"
- "publish social media"

---

## Quick Reference

| Step | Action | Output |
|------|--------|--------|
| 1. Detect | Scan `/Needs_Action` for `type: social_post, platform: linkedin` | Task file found |
| 2. Generate | Create business marketing post from task content | Post text drafted |
| 3. Approve | Move to `/Pending_Approval` (if `approval_required: true`) | Approval request |
| 4. Human Review | User moves file to `/Approved` or `/Rejected` | Decision made |
| 5. Publish | Send to MCP server → Playwright → LinkedIn | Post published |
| 6. Archive | Move file to `/Done`, log to `/Logs/linkedin_logs.md` | Audit trail |

---

## Workflow

```
User drops task in /Needs_Action
          │
          ▼
  LinkedInWatcher detects file
  (type: social_post, platform: linkedin)
          │
          ▼
  Generate marketing post from content
          │
          ├─── approval_required: true ──→ /Pending_Approval
          │                                      │
          │                              Human reviews in Obsidian
          │                                      │
          │                              /Approved or /Rejected
          │                                      │
          ├─── approval_required: false ─────────┤
          │                                      │
          ▼                                      ▼
  MCP Server: /linkedin_create_post (draft)
          │
          ▼
  MCP Server: /linkedin_publish_post
  (Playwright → Browser → LinkedIn)
          │
          ▼
  Move to /Done + log results
```

---

## Task File Format

Drop a file in `/Needs_Action` with this frontmatter:

```yaml
---
type: social_post
platform: linkedin
topic: "Product Launch Announcement"
brand: "Our Company"
hashtags: "#Innovation #Business #Growth"
approval_required: true
---

## Post Content

Excited to announce our latest product launch! After months of development,
we're bringing something truly innovative to market.

Our team has been working tirelessly to deliver a solution that addresses
real customer needs. Stay tuned for the full reveal next week.

What challenges are you facing in your business? Let us know in the comments.
```

---

## MCP Server Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/linkedin_login` | POST | Open browser, log in to LinkedIn |
| `/linkedin_create_post` | POST | Draft a post (stored in memory) |
| `/linkedin_publish_post` | POST | Publish the draft via browser |
| `/linkedin_logout` | POST | Close browser session |
| `/health` | GET | Server health check |

### Starting the MCP Server

```bash
cd watchers
uv run mcp-linkedin
```

### Starting the LinkedIn Watcher

```bash
cd watchers
uv run watch-linkedin
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LINKEDIN_EMAIL` | Yes | LinkedIn login email |
| `LINKEDIN_PASSWORD` | Yes | LinkedIn login password |
| `LINKEDIN_ACCESS_TOKEN` | Optional | API token (for API-based posting) |
| `MCP_LINKEDIN_PORT` | No | MCP server port (default: 3001) |
| `MCP_LINKEDIN_URL` | No | MCP server URL (default: http://127.0.0.1:3001) |
| `LINKEDIN_CHECK_INTERVAL` | No | Watcher poll interval in seconds (default: 15) |

---

## Integration with Existing System

- **Approval Manager**: Uses the same HITL flow as email/payment approvals
- **Audit Logger**: All actions logged to `/Logs/YYYY-MM-DD.json`
- **LinkedIn Logs**: Detailed post logs in `/Logs/linkedin_logs.md`
- **Dashboard**: Updates scheduled/published post counts
- **Tool Layer**: Existing `linkedin_tool.py` handles API-based operations

---

## Safety

- **Social media posts ALWAYS require human approval** (Company Handbook §7)
- Posts go through `/Pending_Approval` before publishing
- Browser automation uses a visible (non-headless) browser for transparency
- All actions logged to both JSON audit log and `linkedin_logs.md`
- DRY_RUN mode detects tasks but does not publish
- MCP server runs on localhost only (127.0.0.1)
- Maximum post length enforced (3000 chars)
