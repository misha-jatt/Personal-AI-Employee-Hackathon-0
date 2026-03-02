"""
Human-in-the-Loop Approval Manager.

Generates approval request files in /Pending_Approval for sensitive actions
that require human review before execution. Watches /Approved for completed
approvals and returns them for action.

Sensitive actions (from Company_Handbook.md §7):
- Sending emails (draft is auto, send needs approval)
- Issuing refunds (NEVER auto)
- Processing payments (NEVER auto)
- Contacting suppliers (NEVER auto)
- Deleting files (NEVER auto)
- Posting on social media (NEVER auto)
- Moving files outside vault (NEVER auto)

Workflow:
  Agent creates approval request → file lands in /Pending_Approval
  Human reviews → moves to /Approved or /Rejected
  Approval watcher detects → returns approved item for execution
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .audit_logger import log_action
from .classifier import classify
from .config import Config

logger = logging.getLogger(__name__)

# Actions that ALWAYS require human approval (Company_Handbook §7)
_SENSITIVE_ACTIONS = frozenset({
    "email_send",
    "refund",
    "payment",
    "contact_supplier",
    "delete_file",
    "social_media_post",
    "move_outside_vault",
})

# Default expiry window for approval requests (hours)
_DEFAULT_EXPIRY_HOURS = 24


def _approval_path() -> Path:
    return Config.VAULT_PATH / "Pending_Approval"


def _approved_path() -> Path:
    return Config.VAULT_PATH / "Approved"


def _rejected_path() -> Path:
    return Config.VAULT_PATH / "Rejected"


def requires_approval(action_type: str) -> bool:
    """Check if an action type requires human approval."""
    return action_type.lower() in _SENSITIVE_ACTIONS


def create_approval_request(
    action_type: str,
    actor: str,
    target: str,
    description: str = "",
    parameters: dict | None = None,
    expiry_hours: int = _DEFAULT_EXPIRY_HOURS,
) -> Path | None:
    """
    Create an approval request file in /Pending_Approval.

    Returns the path to the created file, or None if DRY_RUN is active.

    Args:
        action_type: The sensitive action (e.g. "email_send", "payment")
        actor: Who is requesting (e.g. "GmailWatcher", "process-needs-action")
        target: What the action targets (e.g. email address, order number)
        description: Human-readable description of what will happen
        parameters: Action-specific metadata dict
        expiry_hours: Hours until this request expires
    """
    if Config.DRY_RUN:
        logger.info(
            f"[DRY RUN] Would create approval request: "
            f"{action_type} on {target}"
        )
        log_action(
            action_type="approval_request_created",
            actor=actor,
            target=target,
            parameters={"action_type": action_type},
            approval_status="dry_run",
            result="dry_run_skipped",
        )
        return None

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=expiry_hours)
    request_id = uuid.uuid4().hex[:8]
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    safe_target = target.replace(" ", "_").replace("/", "_").replace("\\", "_")[:50]
    filename = f"{action_type.upper()}_{timestamp}_{safe_target}_{request_id}.md"

    params = parameters or {}

    # Classify the description for priority context
    classification = classify(description or target)

    # Build parameter lines for display
    param_lines = ""
    if params:
        param_lines = "\n".join(f"  {k}: {v}" for k, v in params.items())

    # Build frontmatter parameter block
    fm_params = ""
    if params:
        for k, v in params.items():
            fm_params += f'\n  {k}: "{v}"'

    md_content = f"""---
type: approval_request
action: "{action_type}"
actor: "{actor}"
target: "{target}"
request_id: "{request_id}"
created: "{now.isoformat()}"
expires: "{expires.isoformat()}"
category: {classification.category}
priority: {classification.priority}
status: pending
parameters:{fm_params if fm_params else " {{}}"}
---

# Approval Required: {action_type.replace("_", " ").title()}

## Request Details
- **Action**: {action_type.replace("_", " ").title()}
- **Requested by**: {actor}
- **Target**: {target}
- **Priority**: {classification.priority}
- **Category**: {classification.category}
- **Created**: {now.strftime("%Y-%m-%d %H:%M:%S")} UTC
- **Expires**: {expires.strftime("%Y-%m-%d %H:%M:%S")} UTC

## Description
{description or "No additional description provided."}

## Parameters
{param_lines or "None"}

## Risk Analysis
- **Action type**: {"NEVER auto-approve" if action_type in _SENSITIVE_ACTIONS else "Standard"}
- **Company Handbook reference**: Section 7 — Permission Boundaries
- **Reversible**: {"No" if action_type in ("payment", "refund", "delete_file") else "Partially"}

## To Approve
Move this file to `/Approved` folder.

## To Reject
Move this file to `/Rejected` folder.
"""

    pending = _approval_path()
    pending.mkdir(parents=True, exist_ok=True)
    filepath = pending / filename
    filepath.write_text(md_content, encoding="utf-8")

    log_action(
        action_type="approval_request_created",
        actor=actor,
        target=str(filepath),
        parameters={
            "request_id": request_id,
            "action": action_type,
            "action_target": target,
            "expires": expires.isoformat(),
        },
        approval_status="pending",
        result="success",
    )

    logger.info(f"Approval request created: {filename}")
    return filepath


def check_approved() -> list[Path]:
    """Return list of approved request files in /Approved."""
    approved = _approved_path()
    if not approved.exists():
        return []
    return [
        f for f in approved.iterdir()
        if f.is_file() and f.suffix == ".md" and f.name != ".gitkeep"
    ]


def check_rejected() -> list[Path]:
    """Return list of rejected request files in /Rejected."""
    rejected = _rejected_path()
    if not rejected.exists():
        return []
    return [
        f for f in rejected.iterdir()
        if f.is_file() and f.suffix == ".md" and f.name != ".gitkeep"
    ]


def check_expired() -> list[Path]:
    """Return list of expired pending requests."""
    pending = _approval_path()
    if not pending.exists():
        return []

    now = datetime.now(timezone.utc)
    expired = []
    for f in pending.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        content = f.read_text(encoding="utf-8")
        # Parse expires from frontmatter
        for line in content.splitlines():
            if line.startswith("expires:"):
                try:
                    exp_str = line.split('"')[1]
                    exp_dt = datetime.fromisoformat(exp_str)
                    if now > exp_dt:
                        expired.append(f)
                except (IndexError, ValueError):
                    pass
                break
    return expired


def complete_approval(filepath: Path, result: str = "success") -> None:
    """
    Mark an approved request as completed: move to /Done and log.
    """
    done = Config.VAULT_PATH / "Done"
    done.mkdir(parents=True, exist_ok=True)
    dest = done / filepath.name
    filepath.rename(dest)

    log_action(
        action_type="approval_executed",
        actor="approval_manager",
        target=str(dest),
        parameters={"original": str(filepath)},
        approval_status="approved",
        approved_by="human",
        result=result,
    )
    logger.info(f"Approved request executed and archived: {dest.name}")


def reject_expired(filepath: Path) -> None:
    """Move an expired request to /Rejected and log."""
    rejected = _rejected_path()
    rejected.mkdir(parents=True, exist_ok=True)
    dest = rejected / filepath.name
    filepath.rename(dest)

    log_action(
        action_type="approval_expired",
        actor="approval_manager",
        target=str(dest),
        parameters={"reason": "expired"},
        approval_status="expired",
        result="expired",
    )
    logger.info(f"Expired approval moved to Rejected: {dest.name}")
