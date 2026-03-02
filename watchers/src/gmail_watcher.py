"""
Gmail Watcher — monitors unread important emails via Gmail API.

Polls Gmail for unread important messages, creates .md action files
in /Needs_Action with email metadata and classified priority.

OAuth flow reuses the same credentials.json as calendar_service but
stores a separate gmail_token.json for the gmail.readonly scope.

Design:
- Read-only: uses gmail.readonly scope, never modifies emails
- DRY_RUN aware: logs intent without API calls or file writes
- Duplicate prevention: tracks processed Gmail message IDs
- Graceful failure: API errors never crash the watcher
- Retry with quarantine: max 3 attempts per message before skipping
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audit_logger import log_action
from .base_watcher import BaseWatcher
from .calendar_service import create_event
from .classifier import classify
from .config import Config
from .slack_service import notify_error, notify_task_processed

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _credentials_path() -> Path:
    return Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"))


def _token_path() -> Path:
    return Path(os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json"))


def _gmail_query() -> str:
    return os.getenv("GMAIL_QUERY", "is:unread is:important")


def _is_configured() -> bool:
    return _credentials_path().exists()


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _get_credentials():
    """Load or refresh OAuth credentials for Gmail (readonly)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = _token_path()
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_credentials_path()), _SCOPES
            )
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info(f"Gmail OAuth token saved to {token_path}")

    return creds


def _build_service(creds):
    """Build the Gmail API service object."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def _extract_headers(payload: dict) -> dict[str, str]:
    """Extract common headers from a Gmail message payload."""
    headers = {}
    for h in payload.get("headers", []):
        name = h.get("name", "").lower()
        if name in ("from", "to", "subject", "date"):
            headers[name] = h.get("value", "")
    return headers


def _extract_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    import base64

    # Simple message
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    # Multipart — find text/plain part
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")

    return ""


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

class GmailWatcher(BaseWatcher):
    """
    Watches Gmail for unread important emails.

    Poll cycle:
    1. Query Gmail API for unread important messages
    2. Filter out already-processed IDs
    3. For each new message: fetch full content, classify, write .md, log, notify
    """

    def __init__(self):
        super().__init__(
            check_interval=int(os.getenv("GMAIL_CHECK_INTERVAL", "120"))
        )
        self._processed_ids: set[str] = set()
        self._retry_counts: dict[str, int] = {}
        self._shutting_down = False
        self._service = None

    def _get_service(self):
        """Lazy-init the Gmail service (avoids auth at import time)."""
        if self._service is None:
            creds = _get_credentials()
            self._service = _build_service(creds)
        return self._service

    def check_for_updates(self) -> list:
        """Query Gmail for unread important messages not yet processed."""
        if not _is_configured():
            return []

        try:
            service = self._get_service()
            results = service.users().messages().list(
                userId="me", q=_gmail_query()
            ).execute()
            messages = results.get("messages", [])
            return [m for m in messages if m["id"] not in self._processed_ids]
        except Exception as exc:
            self.logger.warning(f"Gmail API query failed (non-fatal): {exc}")
            return []

    def create_action_file(self, message: dict) -> Path:
        """Fetch full message, classify, and write .md to Needs_Action."""
        msg_id = message["id"]
        service = self._get_service()

        # Fetch full message
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = _extract_headers(msg.get("payload", {}))
        snippet = msg.get("snippet", "")
        body = _extract_body(msg.get("payload", {}))

        sender = headers.get("from", "Unknown")
        subject = headers.get("subject", "No Subject")
        date = headers.get("date", "")

        # Classify using snippet + subject for best signal
        classify_text = f"{subject} {snippet} {body[:500]}"
        classification = classify(classify_text)

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:4]
        safe_subject = subject.replace(" ", "_")[:60]  # cap length

        due_date_line = (
            f'suggested_due_date: "{classification.suggested_due_date}"'
            if classification.suggested_due_date
            else "suggested_due_date: null"
        )

        md_content = f"""---
type: email
source: gmail
gmail_id: "{msg_id}"
from: "{sender}"
subject: "{subject}"
received: "{date}"
detected_at: "{now.isoformat()}"
category: {classification.category}
priority: {classification.priority}
{due_date_line}
status: pending
---

# Email: {subject}

## From
{sender}

## Snippet
{snippet}

## Classification
- **Category**: {classification.category}
- **Priority**: {classification.priority}
- **Suggested due date**: {classification.suggested_due_date or "—"}

## Suggested Actions
- [ ] Review email contents
- [ ] Reply to sender
- [ ] Route to appropriate workflow
- [ ] Move to /Done when processed
"""

        md_path = self.needs_action / f"EMAIL_{timestamp}_{safe_subject}.md"
        md_path.write_text(md_content, encoding="utf-8")

        self._processed_ids.add(msg_id)

        # Audit log
        log_action(
            action_type="email_processed",
            actor="GmailWatcher",
            target=str(md_path),
            parameters={
                "gmail_id": msg_id,
                "from": sender,
                "subject": subject,
                "category": classification.category,
                "priority": classification.priority,
            },
            result="success",
        )

        self.logger.info(f"Processed email: {subject} → {md_path.name}")

        # Slack notification
        notify_task_processed(
            filename=subject,
            category=classification.category,
            priority=classification.priority,
            suggested_due_date=classification.suggested_due_date,
            size_bytes=len(snippet),
        )

        # Calendar event if due date assigned
        if classification.suggested_due_date:
            create_event(
                title=subject,
                due_date=classification.suggested_due_date,
                category=classification.category,
                priority=classification.priority,
            )

        return md_path

    def _process_message(self, message: dict):
        """Process a single message with retry logic."""
        msg_id = message["id"]

        if msg_id in self._processed_ids:
            return

        if self.dry_run:
            self._processed_ids.add(msg_id)
            self.logger.info(f"[DRY RUN] Would process email: {msg_id}")
            log_action(
                action_type="email_detected",
                actor="GmailWatcher",
                target=msg_id,
                result="dry_run_skipped",
            )
            return

        attempt = self._retry_counts.get(msg_id, 0)
        if attempt >= _MAX_RETRIES:
            if msg_id not in self._processed_ids:
                self._processed_ids.add(msg_id)
                self.logger.error(
                    f"Quarantined email {msg_id} after {_MAX_RETRIES} failed attempts"
                )
                log_action(
                    action_type="email_quarantined",
                    actor="GmailWatcher",
                    target=msg_id,
                    parameters={"attempts": _MAX_RETRIES},
                    result="quarantined",
                )
                notify_error(
                    actor="GmailWatcher",
                    error_message=f"Quarantined after {_MAX_RETRIES} failed attempts",
                    target=msg_id,
                )
            return

        try:
            self.create_action_file(message)
            self._retry_counts.pop(msg_id, None)
        except Exception as e:
            self._retry_counts[msg_id] = attempt + 1
            remaining = _MAX_RETRIES - (attempt + 1)
            self.logger.error(
                f"Failed to process email {msg_id} "
                f"(attempt {attempt + 1}/{_MAX_RETRIES}, "
                f"{remaining} retries left): {e}",
                exc_info=True,
            )
            log_action(
                action_type="email_error",
                actor="GmailWatcher",
                target=msg_id,
                parameters={
                    "error": str(e),
                    "attempt": attempt + 1,
                    "max_retries": _MAX_RETRIES,
                },
                result="error",
            )
            notify_error(
                actor="GmailWatcher",
                error_message=f"(attempt {attempt + 1}/{_MAX_RETRIES}) {e}",
                target=msg_id,
            )

    def _shutdown(self, signum=None, frame=None):
        if self._shutting_down:
            return
        self._shutting_down = True
        sig_name = signal.Signals(signum).name if signum else "manual"
        self.logger.info(f"Shutdown requested ({sig_name}). Cleaning up...")
        log_action(
            action_type="watcher_shutdown",
            actor="GmailWatcher",
            target="watcher",
            parameters={
                "signal": sig_name,
                "processed_count": len(self._processed_ids),
                "pending_retries": len(self._retry_counts),
            },
            result="success",
        )

    def run(self):
        """Start polling Gmail with graceful shutdown."""
        self.logger.info(
            f"Starting Gmail Watcher "
            f"(interval={self.check_interval}s, dry_run={self.dry_run})"
        )

        if not _is_configured():
            self.logger.warning(
                "Gmail not configured — credentials.json missing. "
                "Watcher will idle until configured."
            )

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        while not self._shutting_down:
            try:
                messages = self.check_for_updates()
                if messages:
                    self.logger.info(f"Found {len(messages)} new email(s)")
                for msg in messages:
                    self._process_message(msg)
            except Exception as e:
                self.logger.error(f"Error in Gmail poll loop: {e}", exc_info=True)
                log_action(
                    action_type="watcher_loop_error",
                    actor="GmailWatcher",
                    target="main_loop",
                    parameters={"error": str(e)},
                    result="error",
                )

            time.sleep(self.check_interval)

        self.logger.info(
            f"Gmail Watcher stopped. "
            f"Processed {len(self._processed_ids)} email(s) this session."
        )


def main():
    """Entry point for the Gmail watcher."""
    errors = Config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if Config.DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE — No files will be created.")
        print("  Set DRY_RUN=false in .env to enable live mode.")
        print("=" * 60)

    watcher = GmailWatcher()
    watcher.run()
