"""
Tests for the HITL Approval Manager.

All tests use tmp_path — no real vault directories touched.
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["DRY_RUN"] = "false"

from src.config import Config
from src.approval_manager import (
    _SENSITIVE_ACTIONS,
    check_approved,
    check_expired,
    check_rejected,
    complete_approval,
    create_approval_request,
    reject_expired,
    requires_approval,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_vault(tmp_path):
    for d in ("Pending_Approval", "Approved", "Rejected", "Done", "Logs",
              "Inbox", "Needs_Action"):
        (tmp_path / d).mkdir(exist_ok=True)
    Config.VAULT_PATH = tmp_path
    Config.INBOX_PATH = tmp_path / "Inbox"
    Config.NEEDS_ACTION_PATH = tmp_path / "Needs_Action"
    Config.LOGS_PATH = tmp_path / "Logs"
    Config.DRY_RUN = False


@pytest.fixture(autouse=True)
def approval_env(monkeypatch, tmp_path):
    from src import config as cfg_mod
    _setup_vault(tmp_path)
    monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", False)
    monkeypatch.setattr(cfg_mod.Config, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(cfg_mod.Config, "LOGS_PATH", tmp_path / "Logs")
    yield tmp_path


# ---------------------------------------------------------------------------
# requires_approval
# ---------------------------------------------------------------------------

class TestRequiresApproval:
    def test_email_send_requires(self):
        assert requires_approval("email_send") is True

    def test_payment_requires(self):
        assert requires_approval("payment") is True

    def test_refund_requires(self):
        assert requires_approval("refund") is True

    def test_contact_supplier_requires(self):
        assert requires_approval("contact_supplier") is True

    def test_delete_file_requires(self):
        assert requires_approval("delete_file") is True

    def test_social_media_requires(self):
        assert requires_approval("social_media_post") is True

    def test_read_file_does_not_require(self):
        assert requires_approval("read_file") is False

    def test_create_file_does_not_require(self):
        assert requires_approval("create_file") is False

    def test_case_insensitive(self):
        assert requires_approval("EMAIL_SEND") is True


# ---------------------------------------------------------------------------
# create_approval_request
# ---------------------------------------------------------------------------

class TestCreateApprovalRequest:
    def test_creates_file_in_pending(self, tmp_path):
        path = create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="customer@example.com",
            description="Send order confirmation email",
        )
        assert path is not None
        assert path.exists()
        assert path.parent == tmp_path / "Pending_Approval"

    def test_filename_contains_action_type(self, tmp_path):
        path = create_approval_request(
            action_type="payment",
            actor="TestAgent",
            target="Invoice #123",
        )
        assert "PAYMENT_" in path.name

    def test_md_contains_frontmatter(self, tmp_path):
        path = create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="bob@test.com",
            description="Draft response to order inquiry",
        )
        content = path.read_text(encoding="utf-8")
        assert 'type: approval_request' in content
        assert 'action: "email_send"' in content
        assert 'actor: "TestAgent"' in content
        assert 'target: "bob@test.com"' in content
        assert "status: pending" in content

    def test_md_contains_description(self, tmp_path):
        path = create_approval_request(
            action_type="refund",
            actor="TestAgent",
            target="Order #456",
            description="Customer requested refund for defective item",
        )
        content = path.read_text(encoding="utf-8")
        assert "Customer requested refund for defective item" in content

    def test_md_contains_risk_analysis(self, tmp_path):
        path = create_approval_request(
            action_type="payment",
            actor="TestAgent",
            target="Supplier Co",
        )
        content = path.read_text(encoding="utf-8")
        assert "Risk Analysis" in content
        assert "NEVER auto-approve" in content

    def test_md_contains_approval_instructions(self, tmp_path):
        path = create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="test@test.com",
        )
        content = path.read_text(encoding="utf-8")
        assert "/Approved" in content
        assert "/Rejected" in content

    def test_md_contains_expiry(self, tmp_path):
        path = create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="test@test.com",
            expiry_hours=48,
        )
        content = path.read_text(encoding="utf-8")
        assert "expires:" in content

    def test_parameters_included(self, tmp_path):
        path = create_approval_request(
            action_type="payment",
            actor="TestAgent",
            target="Supplier",
            parameters={"amount": "$500.00", "reference": "INV-123"},
        )
        content = path.read_text(encoding="utf-8")
        assert "$500.00" in content
        assert "INV-123" in content

    def test_dry_run_returns_none(self, tmp_path, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        result = create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="test@test.com",
        )
        assert result is None

    def test_dry_run_creates_no_file(self, tmp_path, monkeypatch):
        from src import config as cfg_mod
        monkeypatch.setattr(cfg_mod.Config, "DRY_RUN", True)
        create_approval_request(
            action_type="email_send",
            actor="TestAgent",
            target="test@test.com",
        )
        pending = tmp_path / "Pending_Approval"
        assert len(list(pending.glob("*.md"))) == 0

    def test_classification_in_frontmatter(self, tmp_path):
        path = create_approval_request(
            action_type="refund",
            actor="TestAgent",
            target="Customer refund",
            description="urgent refund for wrong item shipped",
        )
        content = path.read_text(encoding="utf-8")
        assert "priority:" in content
        assert "category:" in content


# ---------------------------------------------------------------------------
# check_approved / check_rejected
# ---------------------------------------------------------------------------

class TestCheckApproved:
    def test_returns_approved_files(self, tmp_path):
        approved = tmp_path / "Approved"
        (approved / "REQ_001.md").write_text("test", encoding="utf-8")
        result = check_approved()
        assert len(result) == 1
        assert result[0].name == "REQ_001.md"

    def test_ignores_non_md(self, tmp_path):
        approved = tmp_path / "Approved"
        (approved / "notes.txt").write_text("test", encoding="utf-8")
        assert len(check_approved()) == 0

    def test_empty_when_no_files(self, tmp_path):
        assert check_approved() == []

    def test_returns_rejected_files(self, tmp_path):
        rejected = tmp_path / "Rejected"
        (rejected / "REQ_002.md").write_text("test", encoding="utf-8")
        result = check_rejected()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# check_expired
# ---------------------------------------------------------------------------

class TestCheckExpired:
    def test_detects_expired_request(self, tmp_path):
        pending = tmp_path / "Pending_Approval"
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        content = f'---\nexpires: "{past}"\nstatus: pending\n---\nTest'
        (pending / "OLD_REQ.md").write_text(content, encoding="utf-8")
        result = check_expired()
        assert len(result) == 1

    def test_ignores_non_expired(self, tmp_path):
        pending = tmp_path / "Pending_Approval"
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        content = f'---\nexpires: "{future}"\nstatus: pending\n---\nTest'
        (pending / "FRESH_REQ.md").write_text(content, encoding="utf-8")
        assert len(check_expired()) == 0


# ---------------------------------------------------------------------------
# complete_approval
# ---------------------------------------------------------------------------

class TestCompleteApproval:
    def test_moves_to_done(self, tmp_path):
        approved = tmp_path / "Approved"
        f = approved / "REQ_done.md"
        f.write_text("approved content", encoding="utf-8")
        complete_approval(f)
        assert not f.exists()
        assert (tmp_path / "Done" / "REQ_done.md").exists()


# ---------------------------------------------------------------------------
# reject_expired
# ---------------------------------------------------------------------------

class TestRejectExpired:
    def test_moves_to_rejected(self, tmp_path):
        pending = tmp_path / "Pending_Approval"
        f = pending / "OLD.md"
        f.write_text("expired", encoding="utf-8")
        reject_expired(f)
        assert not f.exists()
        assert (tmp_path / "Rejected" / "OLD.md").exists()
