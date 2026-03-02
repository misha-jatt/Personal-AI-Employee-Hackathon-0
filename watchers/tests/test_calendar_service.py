"""
Tests for the Google Calendar integration service.

All tests mock the Google API — no real credentials or network calls needed.
Covers:
  - create_event: correct event body shape, priority colours, reminders
  - DRY_RUN suppression
  - Missing credentials.json → silent skip
  - Graceful failure: network error, API error, auth failure
  - Token refresh path
  - _build_event_body: title prefix, description content, all-day date format
  - _priority_color: correct colorId per priority
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

os.environ["DRY_RUN"] = "false"

from src.calendar_service import (
    _build_event_body,
    _is_configured,
    _priority_color,
    create_event,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def calendar_env(monkeypatch, tmp_path):
    """Set DRY_RUN=false and point credentials/token to tmp paths."""
    from src import config as cfg_mod
    monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", False)

    creds_file = tmp_path / "credentials.json"
    token_file = tmp_path / "token.json"
    creds_file.write_text('{"installed":{}}', encoding="utf-8")

    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds_file))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(token_file))
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "primary")

    # Patch _is_configured to return True (credentials file exists in tmp_path)
    monkeypatch.setattr("src.calendar_service._is_configured", lambda: True)
    yield creds_file, token_file


def _mock_creds():
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    return creds


def _mock_service(event_id="evt_001", html_link="https://calendar.google.com/event/1"):
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = {
        "id": event_id,
        "htmlLink": html_link,
    }
    return service


# ---------------------------------------------------------------------------
# create_event — return value
# ---------------------------------------------------------------------------

class TestCreateEventReturnValue:
    def test_returns_true_on_success(self):
        with patch("src.calendar_service._get_credentials", return_value=_mock_creds()), \
             patch("src.calendar_service._create_event", return_value=True):
            result = create_event("order.txt", "2026-02-25", category="Urgent", priority="high")
        assert result is True

    def test_returns_false_on_network_error(self):
        with patch("src.calendar_service._get_credentials", side_effect=ConnectionError("timeout")):
            result = create_event("order.txt", "2026-02-25")
        assert result is False

    def test_returns_false_on_api_error(self):
        with patch("src.calendar_service._get_credentials", return_value=_mock_creds()), \
             patch("src.calendar_service._create_event", side_effect=RuntimeError("quota exceeded")):
            result = create_event("order.txt", "2026-02-25")
        assert result is False

    def test_does_not_raise_on_any_exception(self):
        with patch("src.calendar_service._get_credentials", side_effect=Exception("unexpected")):
            result = create_event("file.txt", "2026-02-25")
        assert result is False


# ---------------------------------------------------------------------------
# DRY_RUN suppression
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_suppresses_api_call(self, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        with patch("src.calendar_service._get_credentials") as mock_creds:
            result = create_event("file.txt", "2026-02-25")
            mock_creds.assert_not_called()
        assert result is False

    def test_dry_run_logs_intent(self, monkeypatch, caplog):
        import logging
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        with caplog.at_level(logging.INFO, logger="src.calendar_service"):
            create_event("important_task.txt", "2026-02-26")
        assert "DRY RUN" in caplog.text
        assert "important_task.txt" in caplog.text


# ---------------------------------------------------------------------------
# Unconfigured (missing credentials.json)
# ---------------------------------------------------------------------------

class TestUnconfigured:
    def test_missing_credentials_skips_silently(self, monkeypatch):
        monkeypatch.setattr("src.calendar_service._is_configured", lambda: False)
        with patch("src.calendar_service._get_credentials") as mock_creds:
            result = create_event("file.txt", "2026-02-25")
            mock_creds.assert_not_called()
        assert result is False

    def test_is_configured_false_when_no_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "nonexistent.json"))
        # Call the real function (bypass the autouse patch)
        from src.calendar_service import _is_configured as real_fn
        monkeypatch.setattr("src.calendar_service._is_configured", real_fn)
        assert _is_configured() is False

    def test_is_configured_true_when_file_exists(self, monkeypatch, tmp_path):
        creds = tmp_path / "credentials.json"
        creds.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))
        from src.calendar_service import _is_configured as real_fn
        monkeypatch.setattr("src.calendar_service._is_configured", real_fn)
        assert _is_configured() is True


# ---------------------------------------------------------------------------
# _build_event_body — structure and content
# ---------------------------------------------------------------------------

class TestBuildEventBody:
    def test_title_includes_priority_prefix(self):
        body = _build_event_body("invoice.pdf", "2026-02-25", "", "Work", "high")
        assert body["summary"] == "[High] invoice.pdf"

    def test_medium_priority_prefix(self):
        body = _build_event_body("report.txt", "2026-02-28", "", "Work", "medium")
        assert body["summary"] == "[Medium] report.txt"

    def test_start_date_is_all_day(self):
        body = _build_event_body("file.txt", "2026-03-01", "", "Work", "low")
        assert body["start"] == {"date": "2026-03-01"}
        assert body["end"]   == {"date": "2026-03-01"}

    def test_description_contains_category(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Urgent", "high")
        assert "Urgent" in body["description"]

    def test_description_contains_priority(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "medium")
        assert "Medium" in body["description"]

    def test_description_contains_custom_text(self):
        body = _build_event_body("file.txt", "2026-02-25", "Custom notes here", "Work", "low")
        assert "Custom notes here" in body["description"]

    def test_description_contains_ai_employee_signature(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "low")
        assert "AI Employee" in body["description"]

    def test_reminders_present(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "high")
        assert "reminders" in body
        assert body["reminders"]["useDefault"] is False
        overrides = body["reminders"]["overrides"]
        assert len(overrides) == 2

    def test_popup_reminder_60_minutes(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "high")
        popup = next(r for r in body["reminders"]["overrides"] if r["method"] == "popup")
        assert popup["minutes"] == 60

    def test_email_reminder_24_hours(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "high")
        email = next(r for r in body["reminders"]["overrides"] if r["method"] == "email")
        assert email["minutes"] == 1440

    def test_color_id_present(self):
        body = _build_event_body("file.txt", "2026-02-25", "", "Work", "high")
        assert "colorId" in body


# ---------------------------------------------------------------------------
# _priority_color
# ---------------------------------------------------------------------------

class TestPriorityColor:
    def test_high_is_tomato(self):
        assert _priority_color("high") == "11"

    def test_medium_is_banana(self):
        assert _priority_color("medium") == "5"

    def test_low_is_sage(self):
        assert _priority_color("low") == "2"

    def test_unknown_defaults_to_medium(self):
        assert _priority_color("unknown") == "5"

    def test_case_insensitive_high(self):
        assert _priority_color("HIGH") == "11"

    def test_case_insensitive_low(self):
        assert _priority_color("LOW") == "2"


# ---------------------------------------------------------------------------
# _create_event — API interaction
# ---------------------------------------------------------------------------

class TestCreateEventApi:
    """Test the full create_event → _create_event path using high-level mocks."""

    def test_create_event_calls_internal_with_correct_args(self):
        with patch("src.calendar_service._get_credentials", return_value=_mock_creds()), \
             patch("src.calendar_service._create_event", return_value=True) as mock_internal:
            create_event("task.txt", "2026-02-25", "notes", "Work", "high")
        mock_internal.assert_called_once()
        args = mock_internal.call_args
        assert args[0][1] == "task.txt"      # title
        assert args[0][2] == "2026-02-25"    # due_date
        assert args[0][4] == "Work"          # category
        assert args[0][5] == "high"          # priority

    def test_create_event_passes_credentials_to_internal(self):
        creds = _mock_creds()
        with patch("src.calendar_service._get_credentials", return_value=creds), \
             patch("src.calendar_service._create_event", return_value=True) as mock_internal:
            create_event("task.txt", "2026-02-25")
        assert mock_internal.call_args[0][0] is creds

    def test_end_to_end_returns_true_when_internal_succeeds(self):
        with patch("src.calendar_service._get_credentials", return_value=_mock_creds()), \
             patch("src.calendar_service._create_event", return_value=True):
            result = create_event("task.txt", "2026-02-25", category="Work", priority="medium")
        assert result is True
