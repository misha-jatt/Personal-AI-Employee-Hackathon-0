"""
Tests for the Tool Integration Layer.

Covers: BaseTool contract, ToolResult, registry, GmailTool, LinkedInTool.
All external APIs are mocked — no network calls.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Any

import pytest

os.environ["DRY_RUN"] = "false"

from src.config import Config
from src.tools.base_tool import BaseTool, ToolResult
from src.tools import registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_vault(tmp_path):
    for d in ("Inbox", "Needs_Action", "Logs", "Pending_Approval", "Approved",
              "Rejected", "Done"):
        (tmp_path / d).mkdir(exist_ok=True)
    Config.VAULT_PATH = tmp_path
    Config.INBOX_PATH = tmp_path / "Inbox"
    Config.NEEDS_ACTION_PATH = tmp_path / "Needs_Action"
    Config.LOGS_PATH = tmp_path / "Logs"
    Config.DRY_RUN = False


class FakeTool(BaseTool):
    """Minimal concrete tool for testing the base class."""

    @property
    def name(self) -> str:
        return "fake"

    def _is_configured(self) -> bool:
        return True

    def _execute(self, action: str, **params: Any) -> ToolResult:
        if action == "greet":
            return ToolResult(success=True, action="greet", data={"msg": "hello"})
        if action == "fail":
            raise RuntimeError("boom")
        return ToolResult(success=False, action=action, error="unhandled")

    def list_actions(self) -> list[str]:
        return ["greet", "fail"]


@pytest.fixture(autouse=True)
def tool_env(monkeypatch, tmp_path):
    from src import config as cfg_mod
    _setup_vault(tmp_path)
    monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", False)
    monkeypatch.setattr(cfg_mod.Config, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(cfg_mod.Config, "LOGS_PATH", tmp_path / "Logs")
    registry.reset()
    yield tmp_path


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_bool_true_on_success(self):
        assert bool(ToolResult(success=True, action="test")) is True

    def test_bool_false_on_failure(self):
        assert bool(ToolResult(success=False, action="test")) is False

    def test_default_data_is_empty_dict(self):
        r = ToolResult(success=True, action="test")
        assert r.data == {}

    def test_error_is_none_by_default(self):
        r = ToolResult(success=True, action="test")
        assert r.error is None

    def test_dry_run_flag(self):
        r = ToolResult(success=False, action="test", dry_run=True)
        assert r.dry_run is True


# ---------------------------------------------------------------------------
# BaseTool contract
# ---------------------------------------------------------------------------

class TestBaseTool:
    def test_execute_success(self, tmp_path):
        tool = FakeTool()
        result = tool.execute("greet")
        assert result.success is True
        assert result.data["msg"] == "hello"

    def test_execute_unknown_action(self, tmp_path):
        tool = FakeTool()
        result = tool.execute("nonexistent")
        assert result.success is False
        assert "Unknown action" in result.error

    def test_execute_catches_exception(self, tmp_path):
        tool = FakeTool()
        result = tool.execute("fail")
        assert result.success is False
        assert "boom" in result.error

    def test_dry_run_skips_execution(self, tmp_path, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        tool = FakeTool()
        result = tool.execute("greet")
        assert result.success is False
        assert result.dry_run is True

    def test_unconfigured_skips(self, tmp_path):
        class UnconfiguredTool(FakeTool):
            def _is_configured(self):
                return False
        tool = UnconfiguredTool()
        result = tool.execute("greet")
        assert result.success is False
        assert "not configured" in result.error


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_register_and_get(self):
        tool = FakeTool()
        registry.register(tool)
        assert registry.get("fake") is tool

    def test_list_tools(self):
        registry.register(FakeTool())
        assert "fake" in registry.list_tools()

    def test_get_unknown_returns_none(self):
        assert registry.get("nonexistent") is None

    def test_reset_clears_all(self):
        registry.register(FakeTool())
        registry.reset()
        assert registry.list_tools() == []

    def test_list_configured(self):
        registry.register(FakeTool())
        assert "fake" in registry.list_configured()

    def test_list_configured_excludes_unconfigured(self):
        class BadTool(FakeTool):
            @property
            def name(self):
                return "bad"
            def _is_configured(self):
                return False
        registry.register(BadTool())
        assert "bad" not in registry.list_configured()


# ---------------------------------------------------------------------------
# GmailTool
# ---------------------------------------------------------------------------

class TestGmailTool:
    def test_name_is_gmail(self):
        from src.tools.gmail_tool import GmailTool
        tool = GmailTool()
        assert tool.name == "gmail"

    def test_actions_list(self):
        from src.tools.gmail_tool import GmailTool
        actions = GmailTool().list_actions()
        assert "list_unread" in actions
        assert "read_email" in actions
        assert "send_email" in actions
        assert "search" in actions

    def test_unconfigured_when_no_creds(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/nonexistent/path.json")
        from src.tools.gmail_tool import GmailTool
        tool = GmailTool()
        assert tool._is_configured() is False

    def test_configured_when_creds_exist(self, tmp_path, monkeypatch):
        creds = tmp_path / "credentials.json"
        creds.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))
        from src.tools.gmail_tool import GmailTool
        tool = GmailTool()
        assert tool._is_configured() is True

    def test_send_email_routes_to_hitl(self, tmp_path, monkeypatch):
        from src.tools.gmail_tool import GmailTool
        creds = tmp_path / "credentials.json"
        creds.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))
        tool = GmailTool()
        registry.register(tool)

        result = tool.execute("send_email", to="test@example.com", subject="Hi")
        assert result.success is False
        assert "approval" in result.error.lower()

    def test_list_unread_returns_message_ids(self, tmp_path, monkeypatch):
        from src.tools.gmail_tool import GmailTool
        creds = tmp_path / "credentials.json"
        creds.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))

        tool = GmailTool()
        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}]
        }
        tool._service = mock_service

        result = tool._list_unread()
        assert result.success is True
        assert result.data["count"] == 2
        assert "m1" in result.data["message_ids"]

    def test_read_email_requires_id(self):
        from src.tools.gmail_tool import GmailTool
        tool = GmailTool()
        result = tool._read_email()
        assert result.success is False
        assert "message_id required" in result.error

    def test_search_requires_query(self):
        from src.tools.gmail_tool import GmailTool
        tool = GmailTool()
        result = tool._search()
        assert result.success is False
        assert "query required" in result.error


# ---------------------------------------------------------------------------
# LinkedInTool
# ---------------------------------------------------------------------------

class TestLinkedInTool:
    def test_name_is_linkedin(self):
        from src.tools.linkedin_tool import LinkedInTool
        assert LinkedInTool().name == "linkedin"

    def test_actions_list(self):
        from src.tools.linkedin_tool import LinkedInTool
        actions = LinkedInTool().list_actions()
        assert "create_post" in actions
        assert "get_profile" in actions
        assert "get_connections" in actions

    def test_unconfigured_without_token(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "")
        from src.tools.linkedin_tool import LinkedInTool
        assert LinkedInTool()._is_configured() is False

    def test_unconfigured_with_placeholder(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "your-token-here")
        from src.tools.linkedin_tool import LinkedInTool
        assert LinkedInTool()._is_configured() is False

    def test_configured_with_real_token(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "AQV_real_token_123")
        from src.tools.linkedin_tool import LinkedInTool
        assert LinkedInTool()._is_configured() is True

    def test_create_post_routes_to_hitl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "AQV_real_token")
        from src.tools.linkedin_tool import LinkedInTool
        tool = LinkedInTool()
        registry.register(tool)

        result = tool.execute("create_post", text="Hello LinkedIn!")
        assert result.success is False
        assert "approval" in result.error.lower()

    def test_create_post_empty_text_fails(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "AQV_real_token")
        from src.tools.linkedin_tool import LinkedInTool
        tool = LinkedInTool()
        result = tool._create_post(text="")
        assert result.success is False
        assert "text required" in result.error


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------

class TestAutoRegistration:
    def test_gmail_auto_registers_on_import(self):
        import importlib
        registry.reset()
        import src.tools.gmail_tool
        importlib.reload(src.tools.gmail_tool)
        assert "gmail" in registry.list_tools()

    def test_linkedin_auto_registers_on_import(self):
        import importlib
        registry.reset()
        import src.tools.linkedin_tool
        importlib.reload(src.tools.linkedin_tool)
        assert "linkedin" in registry.list_tools()
