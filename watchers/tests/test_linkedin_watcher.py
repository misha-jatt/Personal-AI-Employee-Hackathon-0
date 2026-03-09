"""
Tests for the LinkedIn watcher and MCP server modules.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _env_setup(tmp_path, monkeypatch):
    """Set up a temporary vault for every test."""
    vault = tmp_path / "vault"
    for d in ("Inbox", "Needs_Action", "Done", "Logs", "Pending_Approval", "Approved", "Rejected"):
        (vault / d).mkdir(parents=True)

    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MCP_LINKEDIN_URL", "http://127.0.0.1:3001")

    # Reload config
    from src.config import Config
    monkeypatch.setattr(Config, "VAULT_PATH", vault)
    monkeypatch.setattr(Config, "NEEDS_ACTION_PATH", vault / "Needs_Action")
    monkeypatch.setattr(Config, "LOGS_PATH", vault / "Logs")
    monkeypatch.setattr(Config, "DRY_RUN", False)

    yield vault


SAMPLE_TASK = textwrap.dedent("""\
    ---
    type: social_post
    platform: linkedin
    topic: "Product Launch"
    brand: "TestCorp"
    hashtags: "#Innovation #Tech"
    approval_required: true
    ---

    # LinkedIn Post Task

    ## Post Content

    We are thrilled to announce our latest innovation! After months of hard work,
    our team has created something truly remarkable. Join us on this journey
    as we redefine what's possible in the tech industry.

    Stay tuned for more updates!
""")

SAMPLE_TASK_NO_APPROVAL = textwrap.dedent("""\
    ---
    type: social_post
    platform: linkedin
    topic: "Quick Update"
    approval_required: false
    ---

    ## Post Content

    Quick business update from our team.
""")

NON_LINKEDIN_TASK = textwrap.dedent("""\
    ---
    type: social_post
    platform: twitter
    topic: "Tweet"
    ---

    Some tweet content.
""")

ORDER_TASK = textwrap.dedent("""\
    ---
    type: order
    priority: high
    ---

    Order #SH-1234 needs processing.
""")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
class TestParseFrontmatter:
    def test_basic_parse(self):
        from src.linkedin_watcher import _parse_frontmatter
        fm = _parse_frontmatter(SAMPLE_TASK)
        assert fm["type"] == "social_post"
        assert fm["platform"] == "linkedin"
        assert fm["topic"] == "Product Launch"

    def test_no_frontmatter(self):
        from src.linkedin_watcher import _parse_frontmatter
        fm = _parse_frontmatter("No frontmatter here")
        assert fm == {}

    def test_quoted_values(self):
        from src.linkedin_watcher import _parse_frontmatter
        content = '---\nkey: "quoted value"\n---\n'
        fm = _parse_frontmatter(content)
        assert fm["key"] == "quoted value"


# ---------------------------------------------------------------------------
# LinkedIn post detection
# ---------------------------------------------------------------------------
class TestIsLinkedInPostTask:
    def test_linkedin_post(self):
        from src.linkedin_watcher import _is_linkedin_post_task
        assert _is_linkedin_post_task(SAMPLE_TASK) is True

    def test_twitter_post(self):
        from src.linkedin_watcher import _is_linkedin_post_task
        assert _is_linkedin_post_task(NON_LINKEDIN_TASK) is False

    def test_order_task(self):
        from src.linkedin_watcher import _is_linkedin_post_task
        assert _is_linkedin_post_task(ORDER_TASK) is False

    def test_empty_content(self):
        from src.linkedin_watcher import _is_linkedin_post_task
        assert _is_linkedin_post_task("") is False


# ---------------------------------------------------------------------------
# Post content extraction
# ---------------------------------------------------------------------------
class TestExtractPostContent:
    def test_extract_from_section(self):
        from src.linkedin_watcher import _extract_post_content, _parse_frontmatter
        fm = _parse_frontmatter(SAMPLE_TASK)
        text = _extract_post_content(SAMPLE_TASK, fm)
        assert "thrilled" in text
        assert len(text) > 50

    def test_extract_fallback(self):
        from src.linkedin_watcher import _extract_post_content
        content = "---\ntype: social_post\nplatform: linkedin\ntopic: Test\n---\n\nJust some text here."
        fm = {"topic": "Test"}
        text = _extract_post_content(content, fm)
        assert "Just some text here" in text


# ---------------------------------------------------------------------------
# Marketing post generation
# ---------------------------------------------------------------------------
class TestGenerateMarketingPost:
    def test_generates_from_task(self):
        from src.linkedin_watcher import _generate_marketing_post, _parse_frontmatter
        fm = _parse_frontmatter(SAMPLE_TASK)
        post = _generate_marketing_post(SAMPLE_TASK, fm)
        assert len(post) > 50
        assert "thrilled" in post

    def test_short_content_gets_hashtags(self):
        from src.linkedin_watcher import _generate_marketing_post
        fm = {"topic": "Launch", "hashtags": "#Test", "brand": "Corp"}
        content = "---\ntype: social_post\nplatform: linkedin\n---\n\nShort."
        post = _generate_marketing_post(content, fm)
        assert "#Test" in post


# ---------------------------------------------------------------------------
# LinkedInWatcher
# ---------------------------------------------------------------------------
class TestLinkedInWatcher:
    def test_check_for_updates_finds_linkedin_tasks(self, _env_setup):
        vault = _env_setup
        task_file = vault / "Needs_Action" / "test_linkedin.md"
        task_file.write_text(SAMPLE_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInWatcher
        watcher = LinkedInWatcher()
        items = watcher.check_for_updates()
        assert len(items) == 1
        assert items[0].name == "test_linkedin.md"

    def test_ignores_non_linkedin_tasks(self, _env_setup):
        vault = _env_setup
        (vault / "Needs_Action" / "twitter.md").write_text(NON_LINKEDIN_TASK, encoding="utf-8")
        (vault / "Needs_Action" / "order.md").write_text(ORDER_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInWatcher
        watcher = LinkedInWatcher()
        items = watcher.check_for_updates()
        assert len(items) == 0

    def test_does_not_reprocess(self, _env_setup):
        vault = _env_setup
        task_file = vault / "Needs_Action" / "test_linkedin.md"
        task_file.write_text(SAMPLE_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInWatcher
        watcher = LinkedInWatcher()
        items1 = watcher.check_for_updates()
        assert len(items1) == 1

        # Process it
        watcher._processed_files.add(str(items1[0]))
        items2 = watcher.check_for_updates()
        assert len(items2) == 0

    def test_create_action_file_approval_required(self, _env_setup):
        vault = _env_setup
        task_file = vault / "Needs_Action" / "linkedin_post.md"
        task_file.write_text(SAMPLE_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInWatcher
        watcher = LinkedInWatcher()
        result = watcher.create_action_file(task_file)

        # Should move to Pending_Approval
        assert result is not None
        assert "Pending_Approval" in str(result)
        assert result.exists()
        assert not task_file.exists()  # Original removed

        # Check that generated post is in the file
        content = result.read_text(encoding="utf-8")
        assert "Generated LinkedIn Post" in content
        assert "Post Preview" in content

    def test_dry_run_no_publish(self, _env_setup, monkeypatch):
        vault = _env_setup
        from src.config import Config
        monkeypatch.setattr(Config, "DRY_RUN", True)

        task_file = vault / "Needs_Action" / "linkedin_post.md"
        task_file.write_text(SAMPLE_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInWatcher
        watcher = LinkedInWatcher()
        # In dry run, check_for_updates works but run() skips processing
        items = watcher.check_for_updates()
        assert len(items) == 1


# ---------------------------------------------------------------------------
# MCP Server (unit tests via FastAPI TestClient)
# ---------------------------------------------------------------------------
class TestMCPServer:
    def test_health_endpoint(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["browser_active"] is False

    def test_create_post_empty_text(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/linkedin_create_post", json={"text": ""})
        assert resp.status_code == 400

    def test_create_post_too_long(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/linkedin_create_post", json={"text": "x" * 3001})
        assert resp.status_code == 400

    def test_create_post_success(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/linkedin_create_post", json={
            "text": "Test post content for LinkedIn!",
            "source_file": "test.md",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["action"] == "linkedin_create_post"
        assert data["data"]["char_count"] == 31

    def test_publish_without_draft(self):
        from src.mcp_linkedin_server import app, _draft_post
        import src.mcp_linkedin_server as mod
        from fastapi.testclient import TestClient

        mod._draft_post = None
        client = TestClient(app)
        resp = client.post("/linkedin_publish_post", json={"confirm": True})
        assert resp.status_code == 400

    def test_login_without_credentials(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Clear env vars for this test
        with patch.dict(os.environ, {"LINKEDIN_EMAIL": "", "LINKEDIN_PASSWORD": ""}, clear=False):
            import src.mcp_linkedin_server as mod
            orig_email = mod.LINKEDIN_EMAIL
            orig_pass = mod.LINKEDIN_PASSWORD
            mod.LINKEDIN_EMAIL = ""
            mod.LINKEDIN_PASSWORD = ""
            try:
                resp = client.post("/linkedin_login", json={})
                assert resp.status_code == 400
            finally:
                mod.LINKEDIN_EMAIL = orig_email
                mod.LINKEDIN_PASSWORD = orig_pass

    def test_logout_no_browser(self):
        from src.mcp_linkedin_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/linkedin_logout")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# LinkedInApprovalHandler
# ---------------------------------------------------------------------------
class TestLinkedInApprovalHandler:
    def test_non_linkedin_file_returns_false(self, _env_setup):
        vault = _env_setup
        filepath = vault / "Approved" / "order.md"
        filepath.write_text(ORDER_TASK, encoding="utf-8")

        from src.linkedin_watcher import LinkedInApprovalHandler
        handler = LinkedInApprovalHandler()
        assert handler.handle_approved_post(filepath) is False

    def test_no_post_preview_returns_false(self, _env_setup):
        vault = _env_setup
        filepath = vault / "Approved" / "linkedin.md"
        filepath.write_text(SAMPLE_TASK, encoding="utf-8")  # no "Post Preview" section

        from src.linkedin_watcher import LinkedInApprovalHandler
        handler = LinkedInApprovalHandler()
        assert handler.handle_approved_post(filepath) is False

    @patch("src.linkedin_watcher._mcp_health", return_value=False)
    def test_mcp_not_running(self, mock_health, _env_setup):
        vault = _env_setup
        content = SAMPLE_TASK + "\n### Post Preview:\n\nHello LinkedIn!\n\n---\n"
        filepath = vault / "Approved" / "linkedin.md"
        filepath.write_text(content, encoding="utf-8")

        from src.linkedin_watcher import LinkedInApprovalHandler
        handler = LinkedInApprovalHandler()
        assert handler.handle_approved_post(filepath) is False


# ---------------------------------------------------------------------------
# MCP call helper
# ---------------------------------------------------------------------------
class TestMCPCallHelper:
    @patch("urllib.request.urlopen")
    def test_call_mcp_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from src.linkedin_watcher import _call_mcp
        result = _call_mcp("/test", {"key": "value"})
        assert result["success"] is True

    @patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_call_mcp_failure(self, mock_urlopen):
        from src.linkedin_watcher import _call_mcp
        result = _call_mcp("/test")
        assert result["success"] is False
        assert "Connection refused" in result["error"]
