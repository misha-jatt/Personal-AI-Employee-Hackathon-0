---
name: hitl-approval
description: Human-in-the-Loop approval gate for sensitive actions. Creates approval requests and watches for human decisions.
version: "0.1.0"
---

# HITL Approval

For sensitive actions (payments, emails, refunds), generate an approval request file instead of executing. Wait for human review.

---

## Triggers

Use this skill when:
- "approve action"
- "request approval"
- "check approvals"
- "pending approvals"
- "watch approvals"

---

## Quick Reference

| Step | Action | Output |
|------|--------|--------|
| 1. Gate | Check if action requires approval | Yes/No |
| 2. Create | Write approval request to `/Pending_Approval` | `<ACTION>_*.md` file |
| 3. Wait | Human reviews in Obsidian | File stays in Pending |
| 4. Detect | Approval Watcher polls `/Approved` | Approved file found |
| 5. Execute | Action proceeds with `approved_by: human` | Logged + archived |

---

## Sensitive Actions (Company Handbook §7)

| Action | Auto-Approve | Requires Human |
|--------|:---:|:---:|
| Read vault files | Yes | — |
| Create/move files | Yes | — |
| Draft email | Yes | — |
| **Send email** | — | **Always** |
| **Issue refund** | — | **Always** |
| **Process payment** | — | **Always** |
| **Contact supplier** | — | **Always** |
| **Delete file** | — | **Always** |
| **Social media post** | — | **Always** |

---

## Approval Request Format

Files created in `/Pending_Approval/<ACTION>_<timestamp>_<target>_<id>.md`:

```yaml
---
type: approval_request
action: "email_send"
actor: "process-needs-action"
target: "customer@example.com"
request_id: "a1b2c3d4"
created: "2026-03-02T10:00:00+00:00"
expires: "2026-03-03T10:00:00+00:00"
priority: high
status: pending
parameters:
  subject: "Re: Order #SH-4821"
  template: "exchange_instructions"
---
```

## Human Review

- **To Approve**: Move file to `/Approved`
- **To Reject**: Move file to `/Rejected`
- **Expired**: Auto-moved to `/Rejected` after 24 hours

---

## Integration

```python
from src.approval_manager import requires_approval, create_approval_request

# Before any sensitive action:
if requires_approval("email_send"):
    create_approval_request(
        action_type="email_send",
        actor="process-needs-action",
        target="customer@example.com",
        description="Send exchange instructions for order #SH-4821",
        parameters={"subject": "Re: Order #SH-4821"},
    )
    # Do NOT execute the action — wait for human approval
```

---

## Running the Approval Watcher

```bash
cd watchers
uv run watch-approvals
```

---

## Safety

- NEVER execute a sensitive action without an approval file in `/Approved`
- ALWAYS log every approval request, approval, and rejection
- Expired requests auto-move to `/Rejected` after 24 hours
- DRY_RUN mode logs intent without creating files
- All audit entries include `approval_status` and `approved_by` fields
