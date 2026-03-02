"""
Google Calendar integration for the AI Employee.

Creates a calendar event when a processed task has a suggested_due_date.

OAuth flow:
  - First run: opens browser for user consent, saves token to GOOGLE_TOKEN_PATH
  - Subsequent runs: loads saved token, refreshes automatically when expired
  - credentials.json: downloaded from Google Cloud Console (never committed)
  - token.json: written by this module after first auth (never committed)

Design principles:
  - Never raises — all errors caught; Calendar being down must not crash the watcher
  - DRY_RUN aware — logs the would-be event instead of creating it
  - Disabled gracefully when credentials file is absent
  - Token stored at a path outside the vault (configurable via env var)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _credentials_path() -> Path:
    """Path to credentials.json downloaded from Google Cloud Console."""
    return Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"))


def _token_path() -> Path:
    """Path where the OAuth token is persisted after first login."""
    return Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json"))


def _calendar_id() -> str:
    """Calendar to create events on. Defaults to primary."""
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _is_configured() -> bool:
    """Return True only when credentials.json exists on disk."""
    return _credentials_path().exists()


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _get_credentials():
    """
    Load or refresh OAuth credentials.

    - If token.json exists and is valid: return as-is.
    - If token.json is expired: refresh using stored refresh_token.
    - If no token.json: run browser-based consent flow and save token.

    Returns google.oauth2.credentials.Credentials or raises on failure.
    """
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

        # Persist so next run skips the browser flow
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info(f"Google OAuth token saved to {token_path}")

    return creds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_event(
    title: str,
    due_date: str,
    description: str = "",
    category: str = "Work",
    priority: str = "medium",
) -> bool:
    """
    Create a Google Calendar all-day event for the given due date.

    Args:
        title:       Event title (typically the filename or task name).
        due_date:    ISO date string YYYY-MM-DD.
        description: Optional body text shown in event details.
        category:    Task category — added to event description.
        priority:    Task priority — added to event description.

    Returns True on success, False on any failure (including DRY_RUN).
    """
    if Config.DRY_RUN:
        logger.info(
            f"[DRY RUN] Would create calendar event: '{title}' on {due_date}"
        )
        return False

    if not _is_configured():
        logger.debug("Google Calendar not configured — credentials.json missing.")
        return False

    try:
        creds = _get_credentials()
        return _create_event(creds, title, due_date, description, category, priority)
    except Exception as exc:
        logger.warning(f"Google Calendar event creation failed (non-fatal): {exc}")
        return False


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _build_event_body(
    title: str,
    due_date: str,
    description: str,
    category: str,
    priority: str,
) -> dict:
    """Build the Calendar API event resource dict."""
    priority_label = priority.capitalize()
    full_description = (
        f"Category: {category}\n"
        f"Priority: {priority_label}\n"
        + (f"\n{description}" if description else "")
        + f"\n\n— Created by AI Employee on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    ).strip()

    return {
        "summary": f"[{priority_label}] {title}",
        "description": full_description,
        # All-day event: start/end use "date" not "dateTime"
        "start": {"date": due_date},
        "end":   {"date": due_date},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup",  "minutes": 60},
                {"method": "email",  "minutes": 1440},  # 24 hours before
            ],
        },
        "colorId": _priority_color(priority),
    }


def _priority_color(priority: str) -> str:
    """Map priority to Google Calendar colorId (1–11)."""
    return {"high": "11", "medium": "5", "low": "2"}.get(priority.lower(), "5")
    # 11 = Tomato (red), 5 = Banana (yellow), 2 = Sage (green)


def _create_event(
    creds,
    title: str,
    due_date: str,
    description: str,
    category: str,
    priority: str,
) -> bool:
    """Call the Calendar API and create the event."""
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds)
    body = _build_event_body(title, due_date, description, category, priority)

    event = service.events().insert(
        calendarId=_calendar_id(),
        body=body,
    ).execute()

    event_id = event.get("id", "unknown")
    event_link = event.get("htmlLink", "")
    logger.info(f"Calendar event created: '{title}' on {due_date} (id={event_id})")
    logger.debug(f"Event link: {event_link}")
    return True
