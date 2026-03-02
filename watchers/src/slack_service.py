"""
Slack notification service for the AI Employee.

Sends two types of notifications:
  - Task processed  : summary of what was classified and filed
  - Error alert     : when the watcher encounters an unrecoverable error

Design principles:
  - Never raises — all errors are caught and logged; Slack being down
    must not crash the watcher.
  - DRY_RUN aware — logs the would-be message instead of sending.
  - Disabled gracefully when SLACK_BOT_TOKEN / SLACK_CHANNEL_ID are
    missing or are placeholder values.
  - No third-party dependencies beyond slack-sdk.
"""

from __future__ import annotations

import logging
import os

from .config import Config

logger = logging.getLogger(__name__)

# Placeholder sentinel — if the user hasn't replaced the default values
# the service disables itself rather than spamming API errors.
_PLACEHOLDER_PREFIXES = ("your-", "xoxb-your")


def _token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "").strip()


def _channel() -> str:
    return os.getenv("SLACK_CHANNEL_ID", "").strip()


def _is_configured() -> bool:
    """Return True only when both env vars are set and non-placeholder."""
    tok, ch = _token(), _channel()
    if not tok or not ch:
        return False
    for prefix in _PLACEHOLDER_PREFIXES:
        if tok.startswith(prefix) or ch.startswith(prefix):
            return False
    return True


def _client():
    """Lazy-import WebClient so tests don't need a live token."""
    from slack_sdk import WebClient  # noqa: PLC0415
    return WebClient(token=_token())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify_task_processed(
    filename: str,
    category: str,
    priority: str,
    suggested_due_date: str | None,
    size_bytes: int,
) -> bool:
    """
    Send a Slack message summarising a successfully processed task.

    Returns True on success, False on any failure (including DRY_RUN).
    """
    priority_emoji = {"high": ":red_circle:", "medium": ":yellow_circle:", "low": ":white_circle:"}.get(
        priority.lower(), ":white_circle:"
    )
    due = suggested_due_date or "—"

    text = (
        f":inbox_tray: *New task filed*\n"
        f"> *File:* `{filename}`\n"
        f"> *Category:* {category}\n"
        f"> *Priority:* {priority_emoji} {priority.capitalize()}\n"
        f"> *Due:* {due}\n"
        f"> *Size:* {size_bytes:,} bytes"
    )

    return _send(text)


def notify_error(
    actor: str,
    error_message: str,
    target: str = "",
) -> bool:
    """
    Send a Slack alert for a watcher error.

    Returns True on success, False on any failure (including DRY_RUN).
    """
    target_line = f"\n> *Target:* `{target}`" if target else ""
    text = (
        f":rotating_light: *AI Employee error*\n"
        f"> *Component:* `{actor}`"
        f"{target_line}\n"
        f"> *Error:* {error_message}"
    )

    return _send(text)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _send(text: str) -> bool:
    """
    Core send routine.  Never raises.

    Returns True if the message was sent (or would be sent in DRY_RUN).
    """
    if Config.DRY_RUN:
        logger.info(f"[DRY RUN] Slack notification suppressed:\n{text}")
        return False

    if not _is_configured():
        logger.debug("Slack not configured — notification skipped.")
        return False

    try:
        response = _client().chat_postMessage(
            channel=_channel(),
            text=text,
            unfurl_links=False,
            unfurl_media=False,
        )
        if response.get("ok"):
            logger.debug(f"Slack notification sent (ts={response.get('ts')})")
            return True

        # Slack API returned ok=false
        logger.warning(f"Slack API error: {response.get('error', 'unknown')}")
        return False

    except Exception as exc:  # network down, token invalid, rate-limited, etc.
        logger.warning(f"Slack notification failed (non-fatal): {exc}")
        return False
