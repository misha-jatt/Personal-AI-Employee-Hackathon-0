"""
Tests for the Slack notification service.

All tests mock the Slack WebClient so no real API calls are made.
Covers:
  - notify_task_processed: correct message shape, priority emoji, due date
  - notify_error: correct message shape
  - DRY_RUN suppression
  - Unconfigured / placeholder token → silent skip
  - Graceful failure: network error, API ok=false, rate limit
  - Return value contract (True/False)
"""

import os

import pytest

os.environ["DRY_RUN"] = "false"
os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-token"
os.environ["SLACK_CHANNEL_ID"] = "C123456"

from unittest.mock import MagicMock, patch

from src.slack_service import notify_error, notify_task_processed, _is_configured


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_dry_run(monkeypatch):
    """Ensure DRY_RUN is false for each test."""
    from src import config as cfg_mod
    monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123456")
    # Also patch _is_configured globally so send-path tests aren't blocked
    # by whatever placeholder values are in the real .env file.
    monkeypatch.setattr("src.slack_service._is_configured", lambda: True)
    yield


def _ok_response():
    """Simulate a successful Slack API response."""
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None: {"ok": True, "ts": "1234567890.000001"}.get(key, default)
    return mock


def _err_response(error="channel_not_found"):
    """Simulate a Slack API error response."""
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None: {"ok": False, "error": error}.get(key, default)
    return mock


# ---------------------------------------------------------------------------
# notify_task_processed — message content
# ---------------------------------------------------------------------------

class TestNotifyTaskProcessed:
    def test_returns_true_on_success(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            result = notify_task_processed("order.txt", "Urgent", "high", "2026-02-25", 512)
        assert result is True

    def test_message_contains_filename(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("invoice_jan.pdf", "Work", "medium", "2026-02-28", 1024)
            call_kwargs = mock_client.return_value.chat_postMessage.call_args.kwargs
        assert "invoice_jan.pdf" in call_kwargs["text"]

    def test_message_contains_category(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Personal", "low", None, 256)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "Personal" in text

    def test_message_contains_priority(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "medium", "2026-02-28", 256)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "Medium" in text

    def test_high_priority_uses_red_emoji(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Urgent", "high", "2026-02-25", 100)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert ":red_circle:" in text

    def test_medium_priority_uses_yellow_emoji(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "medium", "2026-02-28", 100)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert ":yellow_circle:" in text

    def test_low_priority_uses_white_emoji(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "low", None, 100)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert ":white_circle:" in text

    def test_due_date_shown_when_present(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "high", "2026-02-26", 100)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "2026-02-26" in text

    def test_due_date_dash_when_none(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "low", None, 100)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "—" in text

    def test_size_bytes_included(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "medium", None, 204800)
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "204,800" in text

    def test_correct_channel_used(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_task_processed("file.txt", "Work", "low", None, 10)
            call_kwargs = mock_client.return_value.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C123456"


# ---------------------------------------------------------------------------
# notify_error — message content
# ---------------------------------------------------------------------------

class TestNotifyError:
    def test_returns_true_on_success(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            result = notify_error("FileSystemWatcher", "Copy failed", "broken.txt")
        assert result is True

    def test_message_contains_actor(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_error("FileSystemWatcher", "Copy failed", "broken.txt")
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "FileSystemWatcher" in text

    def test_message_contains_error(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_error("FileSystemWatcher", "Disk full", "large_file.zip")
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "Disk full" in text

    def test_message_contains_target(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_error("FileSystemWatcher", "IOError", "target_file.txt")
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert "target_file.txt" in text

    def test_target_optional(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            result = notify_error("FileSystemWatcher", "Unexpected crash")
        assert result is True

    def test_error_emoji_present(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _ok_response()
            notify_error("FileSystemWatcher", "Something broke")
            text = mock_client.return_value.chat_postMessage.call_args.kwargs["text"]
        assert ":rotating_light:" in text


# ---------------------------------------------------------------------------
# DRY_RUN suppression
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_task_processed_suppressed_in_dry_run(self, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        with patch("src.slack_service._client") as mock_client:
            result = notify_task_processed("file.txt", "Work", "low", None, 10)
            mock_client.assert_not_called()
        assert result is False

    def test_error_suppressed_in_dry_run(self, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        with patch("src.slack_service._client") as mock_client:
            result = notify_error("FileSystemWatcher", "Test error")
            mock_client.assert_not_called()
        assert result is False


# ---------------------------------------------------------------------------
# Unconfigured / placeholder tokens
# ---------------------------------------------------------------------------

class TestUnconfigured:
    """These tests exercise the real _is_configured() — override the autouse patch."""

    def test_missing_token_skips_silently(self, monkeypatch):
        from src import slack_service as ss
        monkeypatch.setattr(ss, "_is_configured", ss.__dict__["_is_configured"].__wrapped__
                            if hasattr(ss.__dict__.get("_is_configured", None), "__wrapped__")
                            else lambda: False)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "")
        monkeypatch.setattr("src.slack_service._is_configured", lambda: _is_configured())
        with patch("src.slack_service._client") as mock_client:
            result = notify_task_processed("file.txt", "Work", "low", None, 10)
            mock_client.assert_not_called()
        assert result is False

    def test_missing_channel_skips_silently(self, monkeypatch):
        monkeypatch.setenv("SLACK_CHANNEL_ID", "")
        monkeypatch.setattr("src.slack_service._is_configured", lambda: _is_configured())
        with patch("src.slack_service._client") as mock_client:
            result = notify_task_processed("file.txt", "Work", "low", None, 10)
            mock_client.assert_not_called()
        assert result is False

    def test_placeholder_token_skips(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-your-bot-token-here")
        monkeypatch.setattr("src.slack_service._is_configured", lambda: _is_configured())
        with patch("src.slack_service._client") as mock_client:
            result = notify_task_processed("file.txt", "Work", "low", None, 10)
            mock_client.assert_not_called()
        assert result is False

    def test_is_configured_true_with_real_values(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-real-token")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C123456")
        assert _is_configured() is True

    def test_is_configured_false_without_token(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "")
        assert _is_configured() is False

    def test_is_configured_false_without_channel(self, monkeypatch):
        monkeypatch.setenv("SLACK_CHANNEL_ID", "")
        assert _is_configured() is False


# ---------------------------------------------------------------------------
# Graceful failure
# ---------------------------------------------------------------------------

class TestGracefulFailure:
    def test_network_error_returns_false(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.side_effect = ConnectionError("timeout")
            result = notify_task_processed("file.txt", "Work", "high", "2026-02-26", 100)
        assert result is False

    def test_api_ok_false_returns_false(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.return_value = _err_response("channel_not_found")
            result = notify_task_processed("file.txt", "Work", "high", "2026-02-26", 100)
        assert result is False

    def test_exception_does_not_propagate(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.side_effect = RuntimeError("unexpected")
            # Must not raise
            result = notify_task_processed("file.txt", "Work", "medium", None, 50)
        assert result is False

    def test_error_notify_survives_network_failure(self):
        with patch("src.slack_service._client") as mock_client:
            mock_client.return_value.chat_postMessage.side_effect = OSError("connection refused")
            result = notify_error("FileSystemWatcher", "Test", "file.txt")
        assert result is False
