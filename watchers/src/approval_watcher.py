"""
Approval Watcher — monitors /Approved for human-approved requests.

Polls /Approved for files that were moved there by a human reviewer.
When found, logs the approval and moves the file to /Done.
Also sweeps /Pending_Approval for expired requests.

This is the "action" side of the HITL loop:
  Agent creates request → /Pending_Approval
  Human moves to → /Approved or /Rejected
  This watcher detects → logs + archives to /Done
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

from .approval_manager import (
    check_approved,
    check_expired,
    check_rejected,
    complete_approval,
    reject_expired,
)
from .audit_logger import log_action
from .base_watcher import BaseWatcher
from .config import Config
from .slack_service import notify_task_processed

logger = logging.getLogger(__name__)


class ApprovalWatcher(BaseWatcher):
    """
    Watches /Approved for human-approved action requests.

    Poll cycle:
    1. Check /Approved for new files → log and archive to /Done
    2. Check /Rejected for new files → log
    3. Sweep /Pending_Approval for expired requests → move to /Rejected
    """

    def __init__(self):
        super().__init__(
            check_interval=int(os.getenv("APPROVAL_CHECK_INTERVAL", "10"))
        )
        self._processed_files: set[str] = set()
        self._shutting_down = False

    def check_for_updates(self) -> list:
        """Return list of newly approved files."""
        approved = check_approved()
        return [f for f in approved if str(f) not in self._processed_files]

    def create_action_file(self, item) -> None:
        """Process an approved request: log and archive."""
        key = str(item)
        self._processed_files.add(key)

        self.logger.info(f"Approved request detected: {item.name}")

        log_action(
            action_type="approval_detected",
            actor="ApprovalWatcher",
            target=str(item),
            approval_status="approved",
            approved_by="human",
            result="success",
        )

        notify_task_processed(
            filename=item.name,
            category="Approval",
            priority="high",
            suggested_due_date=None,
            size_bytes=item.stat().st_size,
        )

        complete_approval(item)
        return None

    def _sweep_expired(self):
        """Move expired pending requests to /Rejected."""
        expired = check_expired()
        for f in expired:
            self.logger.warning(f"Expired approval request: {f.name}")
            reject_expired(f)

    def _log_rejections(self):
        """Log any newly rejected files."""
        rejected = check_rejected()
        for f in rejected:
            key = str(f)
            if key not in self._processed_files:
                self._processed_files.add(key)
                self.logger.info(f"Rejected request: {f.name}")
                log_action(
                    action_type="approval_rejected",
                    actor="ApprovalWatcher",
                    target=str(f),
                    approval_status="rejected",
                    approved_by="human",
                    result="rejected",
                )

    def _shutdown(self, signum=None, frame=None):
        if self._shutting_down:
            return
        self._shutting_down = True
        sig_name = signal.Signals(signum).name if signum else "manual"
        self.logger.info(f"Shutdown requested ({sig_name}).")
        log_action(
            action_type="watcher_shutdown",
            actor="ApprovalWatcher",
            target="watcher",
            parameters={"signal": sig_name},
            result="success",
        )

    def run(self):
        """Start polling for approvals."""
        self.logger.info(
            f"Starting Approval Watcher "
            f"(interval={self.check_interval}s, dry_run={self.dry_run})"
        )

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        while not self._shutting_down:
            try:
                # Check approved
                items = self.check_for_updates()
                if items:
                    self.logger.info(f"Found {len(items)} approved request(s)")
                for item in items:
                    if self.dry_run:
                        self._processed_files.add(str(item))
                        self.logger.info(f"[DRY RUN] Would process approval: {item.name}")
                        log_action(
                            action_type="approval_detected",
                            actor="ApprovalWatcher",
                            target=str(item),
                            result="dry_run_skipped",
                        )
                    else:
                        self.create_action_file(item)

                # Sweep expired and log rejections
                if not self.dry_run:
                    self._sweep_expired()
                    self._log_rejections()

            except Exception as e:
                self.logger.error(f"Error in approval loop: {e}", exc_info=True)
                log_action(
                    action_type="watcher_loop_error",
                    actor="ApprovalWatcher",
                    target="main_loop",
                    parameters={"error": str(e)},
                    result="error",
                )

            time.sleep(self.check_interval)

        self.logger.info(
            f"Approval Watcher stopped. "
            f"Processed {len(self._processed_files)} item(s)."
        )


def main():
    """Entry point for the approval watcher."""
    errors = Config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Ensure HITL directories exist
    for d in ("Pending_Approval", "Approved", "Rejected"):
        (Config.VAULT_PATH / d).mkdir(parents=True, exist_ok=True)

    if Config.DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE — Approvals will be detected but not executed.")
        print("=" * 60)

    watcher = ApprovalWatcher()
    watcher.run()
