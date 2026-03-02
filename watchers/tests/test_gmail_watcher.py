"""
Tests for the Gmail Watcher.

All tests mock the Gmail API — no real credentials or network calls needed.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["DRY_RUN"] = "false"

from src.config import Config
from src.gmail_watcher import (
    GmailWatcher,
    _extract_body,
    _extract_headers,
    _is_configured,
    _MAX_RETRIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_config(tmp_path):
    """Redirect Config paths to a temp directory."""
    inbox = tmp_path / "Inbox"
    needs = tmp_path / "Needs_Action"
    logs = tmp_path / "Logs"
    inbox.mkdir(exist_ok=True)
    needs.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    Config.VAULT_PATH = tmp_path
    Config.INBOX_PATH = inbox
    Config.NEEDS_ACTION_PATH = needs
    Config.LOGS_PATH = logs
    Config.DRY_RUN = False
    return inbox, needs, logs


def _make_watcher(tmp_path):
    """Create a GmailWatcher with paths pointed at tmp_path."""
    _inbox, needs, _logs = _patch_config(tmp_path)
    watcher = GmailWatcher()
    watcher.needs_action = needs
    return watcher, needs


def _fake_message(msg_id="msg_001", subject="Test Subject", sender="alice@example.com",
                  snippet="Hello world", body_text="Full body text"):
    """Build a fake Gmail API message response."""
    import base64
    encoded_body = base64.urlsafe_b64encode(body_text.encode()).decode()
    return {
        "id": msg_id,
        "snippet": snippet,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 2 Mar 2026 10:00:00 +0000"},
                {"name": "To", "value": "me@example.com"},
            ],
            "body": {"data": encoded_body},
        },
    }


def _mock_service(messages_list=None, message_get=None):
    """Create a mock Gmail service."""
    service = MagicMock()
    if messages_list is not None:
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": messages_list
        }
    else:
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": []
        }
    if message_get is not None:
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = message_get
    return service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def gmail_env(monkeypatch, tmp_path):
    """Set up environment for all Gmail tests."""
    from src import config as cfg_mod
    _patch_config(tmp_path)
    monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", False)
    monkeypatch.setattr("src.gmail_watcher._is_configured", lambda: True)
    monkeypatch.setattr("src.gmail_watcher.notify_task_processed", lambda **kw: True)
    monkeypatch.setattr("src.gmail_watcher.notify_error", lambda **kw: True)
    monkeypatch.setattr("src.gmail_watcher.create_event", lambda **kw: True)
    yield tmp_path


# ---------------------------------------------------------------------------
# _extract_headers
# ---------------------------------------------------------------------------

class TestExtractHeaders:
    def test_extracts_from_and_subject(self):
        payload = {
            "headers": [
                {"name": "From", "value": "alice@test.com"},
                {"name": "Subject", "value": "Hello"},
                {"name": "X-Custom", "value": "ignored"},
            ]
        }
        h = _extract_headers(payload)
        assert h["from"] == "alice@test.com"
        assert h["subject"] == "Hello"
        assert "x-custom" not in h

    def test_empty_headers(self):
        assert _extract_headers({}) == {}

    def test_case_insensitive_matching(self):
        payload = {"headers": [{"name": "FROM", "value": "bob@test.com"}]}
        h = _extract_headers(payload)
        assert h["from"] == "bob@test.com"


# ---------------------------------------------------------------------------
# _extract_body
# ---------------------------------------------------------------------------

class TestExtractBody:
    def test_plain_text_body(self):
        import base64
        data = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": data}}
        assert _extract_body(payload) == "Hello world"

    def test_multipart_body(self):
        import base64
        data = base64.urlsafe_b64encode(b"Part text").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": data}},
                {"mimeType": "text/html", "body": {"data": data}},
            ],
        }
        assert _extract_body(payload) == "Part text"

    def test_no_body_returns_empty(self):
        assert _extract_body({"mimeType": "text/html"}) == ""


# ---------------------------------------------------------------------------
# check_for_updates
# ---------------------------------------------------------------------------

class TestCheckForUpdates:
    def test_returns_new_messages(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        service = _mock_service(messages_list=[{"id": "m1"}, {"id": "m2"}])
        watcher._service = service
        result = watcher.check_for_updates()
        assert len(result) == 2

    def test_filters_already_processed(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        service = _mock_service(messages_list=[{"id": "m1"}, {"id": "m2"}])
        watcher._service = service
        watcher._processed_ids.add("m1")
        result = watcher.check_for_updates()
        assert len(result) == 1
        assert result[0]["id"] == "m2"

    def test_returns_empty_on_api_error(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        service = MagicMock()
        service.users.return_value.messages.return_value.list.side_effect = ConnectionError("timeout")
        watcher._service = service
        result = watcher.check_for_updates()
        assert result == []

    def test_returns_empty_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.gmail_watcher._is_configured", lambda: False)
        watcher, _ = _make_watcher(tmp_path)
        assert watcher.check_for_updates() == []


# ---------------------------------------------------------------------------
# create_action_file
# ---------------------------------------------------------------------------

class TestCreateActionFile:
    def test_creates_md_file(self, tmp_path):
        watcher, needs = _make_watcher(tmp_path)
        msg = _fake_message()
        service = _mock_service(message_get=msg)
        watcher._service = service

        path = watcher.create_action_file({"id": "msg_001"})
        assert path.exists()
        assert path.suffix == ".md"
        assert path.parent == needs

    def test_md_contains_frontmatter(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message(subject="Order Inquiry")
        service = _mock_service(message_get=msg)
        watcher._service = service

        path = watcher.create_action_file({"id": "msg_001"})
        content = path.read_text(encoding="utf-8")
        assert "type: email" in content
        assert "source: gmail" in content
        assert 'gmail_id: "msg_001"' in content
        assert "Order Inquiry" in content

    def test_md_contains_classification(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message(subject="URGENT refund request")
        service = _mock_service(message_get=msg)
        watcher._service = service

        path = watcher.create_action_file({"id": "msg_002"})
        content = path.read_text(encoding="utf-8")
        assert "priority:" in content
        assert "category:" in content

    def test_marks_message_as_processed(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message()
        service = _mock_service(message_get=msg)
        watcher._service = service

        watcher.create_action_file({"id": "msg_001"})
        assert "msg_001" in watcher._processed_ids

    def test_filename_contains_email_prefix(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message(subject="My Report")
        service = _mock_service(message_get=msg)
        watcher._service = service

        path = watcher.create_action_file({"id": "msg_003"})
        assert path.name.startswith("EMAIL_")

    def test_subject_spaces_replaced(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message(subject="My Long Subject")
        service = _mock_service(message_get=msg)
        watcher._service = service

        path = watcher.create_action_file({"id": "msg_004"})
        assert " " not in path.name


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

class TestDuplicatePrevention:
    def test_process_message_skips_already_processed(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        watcher._processed_ids.add("msg_001")
        # Should not attempt API call
        watcher._service = None  # would crash if accessed
        watcher._process_message({"id": "msg_001"})  # no error

    def test_check_for_updates_excludes_processed(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        service = _mock_service(messages_list=[{"id": "m1"}, {"id": "m2"}, {"id": "m3"}])
        watcher._service = service
        watcher._processed_ids = {"m1", "m3"}
        result = watcher.check_for_updates()
        assert [m["id"] for m in result] == ["m2"]


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_create_files(self, tmp_path, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        watcher, needs = _make_watcher(tmp_path)
        watcher.dry_run = True
        watcher._process_message({"id": "msg_dry"})
        md_files = list(needs.glob("*.md"))
        assert len(md_files) == 0

    def test_dry_run_marks_as_processed(self, tmp_path, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        watcher, _ = _make_watcher(tmp_path)
        watcher.dry_run = True
        watcher._process_message({"id": "msg_dry2"})
        assert "msg_dry2" in watcher._processed_ids


# ---------------------------------------------------------------------------
# Error handling & retry
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_api_error_in_create_increments_retry(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        service = MagicMock()
        service.users.return_value.messages.return_value.get.side_effect = RuntimeError("API down")
        watcher._service = service

        watcher._process_message({"id": "msg_fail"})
        assert watcher._retry_counts["msg_fail"] == 1
        assert "msg_fail" not in watcher._processed_ids

    def test_quarantine_after_max_retries(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        watcher._retry_counts["msg_bad"] = _MAX_RETRIES

        watcher._process_message({"id": "msg_bad"})
        assert "msg_bad" in watcher._processed_ids  # quarantined

    def test_success_clears_retry_count(self, tmp_path):
        watcher, _ = _make_watcher(tmp_path)
        msg = _fake_message(msg_id="msg_retry")
        service = _mock_service(message_get=msg)
        watcher._service = service
        watcher._retry_counts["msg_retry"] = 2

        watcher._process_message({"id": "msg_retry"})
        assert "msg_retry" not in watcher._retry_counts
        assert "msg_retry" in watcher._processed_ids


# ---------------------------------------------------------------------------
# Unconfigured
# ---------------------------------------------------------------------------

class TestUnconfigured:
    def test_unconfigured_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.gmail_watcher._is_configured", lambda: False)
        watcher, _ = _make_watcher(tmp_path)
        assert watcher.check_for_updates() == []

    def test_is_configured_false_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "nope.json"))
        from src.gmail_watcher import _is_configured as real_fn
        monkeypatch.setattr("src.gmail_watcher._is_configured", real_fn)
        assert _is_configured() is False

    def test_is_configured_true_when_file_exists(self, tmp_path, monkeypatch):
        creds = tmp_path / "credentials.json"
        creds.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))
        from src.gmail_watcher import _is_configured as real_fn
        monkeypatch.setattr("src.gmail_watcher._is_configured", real_fn)
        assert _is_configured() is True
