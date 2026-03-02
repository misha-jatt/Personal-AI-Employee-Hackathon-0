"""Tests for the FileSystem Watcher — including edge cases."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Override env before importing modules
os.environ["DRY_RUN"] = "false"
os.environ["LOG_LEVEL"] = "DEBUG"


def _patch_config(tmp_path):
    """Patch Config to use a temporary vault directory."""
    from src.config import Config

    inbox = tmp_path / "Inbox"
    needs_action = tmp_path / "Needs_Action"
    logs = tmp_path / "Logs"
    inbox.mkdir(exist_ok=True)
    needs_action.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)

    Config.VAULT_PATH = tmp_path
    Config.INBOX_PATH = inbox
    Config.NEEDS_ACTION_PATH = needs_action
    Config.LOGS_PATH = logs
    Config.DRY_RUN = False

    return inbox, needs_action, logs


def _make_watcher(inbox, needs_action):
    """Create a FileSystemWatcher with patched paths."""
    from src.filesystem_watcher import FileSystemWatcher

    watcher = FileSystemWatcher()
    watcher.needs_action = needs_action
    watcher.inbox_path = inbox
    return watcher


class TestConfigValidation:
    """Tests for Config.validate()."""

    def test_validates_existing_vault(self, tmp_path):
        _patch_config(tmp_path)
        from src.config import Config

        errors = Config.validate()
        assert errors == []

    def test_fails_on_missing_directories(self, tmp_path):
        from src.config import Config

        Config.VAULT_PATH = tmp_path / "nonexistent"
        Config.INBOX_PATH = tmp_path / "nonexistent" / "Inbox"
        Config.NEEDS_ACTION_PATH = tmp_path / "nonexistent" / "Needs_Action"
        Config.LOGS_PATH = tmp_path / "nonexistent" / "Logs"

        errors = Config.validate()
        assert len(errors) == 4


class TestActionFileCreation:
    """Tests for create_action_file() — core processing logic."""

    def test_creates_md_with_correct_frontmatter(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        test_file = inbox / "test_document.pdf"
        test_file.write_text("fake pdf content")

        watcher = _make_watcher(inbox, needs_action)
        result = watcher.create_action_file(test_file)

        assert result.exists()
        content = result.read_text()
        assert "type: file_drop" in content
        assert "test_document.pdf" in content
        assert "status: pending" in content

    def test_removes_original_from_inbox(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        test_file = inbox / "remove_me.txt"
        test_file.write_text("content")

        watcher = _make_watcher(inbox, needs_action)
        watcher.create_action_file(test_file)

        assert not test_file.exists(), "Original file should be removed from Inbox"

    def test_copies_file_to_needs_action(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        test_file = inbox / "data.csv"
        test_file.write_text("col1,col2\na,b")

        watcher = _make_watcher(inbox, needs_action)
        watcher.create_action_file(test_file)

        copied = needs_action / "FILE_data.csv"
        assert copied.exists()
        assert copied.read_text() == "col1,col2\na,b"

    def test_md_includes_copied_as_field(self, tmp_path):
        """Metadata should record what the file was actually saved as."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        test_file = inbox / "report.pdf"
        test_file.write_text("content")

        watcher = _make_watcher(inbox, needs_action)
        result = watcher.create_action_file(test_file)

        content = result.read_text()
        assert 'copied_as: "FILE_report.pdf"' in content


class TestTimestampUniqueness:
    """Task #1: Timestamps should never collide."""

    def test_unique_md_filenames_for_rapid_files(self, tmp_path):
        """Two files processed in rapid succession should get different .md names."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        file_a = inbox / "a.txt"
        file_a.write_text("aaa")
        result_a = watcher.create_action_file(file_a)

        file_b = inbox / "b.txt"
        file_b.write_text("bbb")
        result_b = watcher.create_action_file(file_b)

        assert result_a.name != result_b.name

    def test_timestamp_contains_microseconds(self, tmp_path):
        """Filename should contain microsecond precision."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        test_file = inbox / "test.txt"
        test_file.write_text("x")

        watcher = _make_watcher(inbox, needs_action)
        result = watcher.create_action_file(test_file)

        # Format: FILE_YYYY-MM-DD_HHMMSS_ffffff_xxxx_name.md
        # The _ count should be at least 5 (date_time_micro_uuid_name)
        parts = result.stem.split("_")
        assert len(parts) >= 6, f"Expected microsecond+uuid in filename, got: {result.name}"


class TestSameNameCollision:
    """Task #2: Dropping two files with the same name must not overwrite."""

    def test_second_file_gets_counter_suffix(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        # Process first file
        file1 = inbox / "report.pdf"
        file1.write_text("version 1")
        watcher.create_action_file(file1)

        # Process second file with same name
        file2 = inbox / "report.pdf"
        file2.write_text("version 2")
        watcher.create_action_file(file2)

        # Both should exist in Needs_Action
        copied_files = [f for f in needs_action.iterdir() if f.name.startswith("FILE_report") and not f.name.endswith(".md")]
        assert len(copied_files) == 2, f"Expected 2 files, got: {[f.name for f in copied_files]}"

        # Content should be different
        contents = sorted([f.read_text() for f in copied_files])
        assert contents == ["version 1", "version 2"]

    def test_third_file_gets_counter_3(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        for i in range(3):
            f = inbox / "data.csv"
            f.write_text(f"row{i}")
            watcher.create_action_file(f)

        copied = [f for f in needs_action.iterdir() if f.name.startswith("FILE_data") and not f.name.endswith(".md")]
        assert len(copied) == 3


class TestUniqueDestHelper:
    """Unit tests for the _unique_dest function."""

    def test_no_collision(self, tmp_path):
        from src.filesystem_watcher import _unique_dest

        result = _unique_dest(tmp_path, "FILE_", "report.pdf")
        assert result == tmp_path / "FILE_report.pdf"

    def test_first_collision(self, tmp_path):
        from src.filesystem_watcher import _unique_dest

        (tmp_path / "FILE_report.pdf").write_text("existing")
        result = _unique_dest(tmp_path, "FILE_", "report.pdf")
        assert result == tmp_path / "FILE_report_2.pdf"

    def test_multiple_collisions(self, tmp_path):
        from src.filesystem_watcher import _unique_dest

        (tmp_path / "FILE_report.pdf").write_text("v1")
        (tmp_path / "FILE_report_2.pdf").write_text("v2")
        (tmp_path / "FILE_report_3.pdf").write_text("v3")

        result = _unique_dest(tmp_path, "FILE_", "report.pdf")
        assert result == tmp_path / "FILE_report_4.pdf"

    def test_no_extension(self, tmp_path):
        from src.filesystem_watcher import _unique_dest

        (tmp_path / "FILE_README").write_text("existing")
        result = _unique_dest(tmp_path, "FILE_", "README")
        assert result == tmp_path / "FILE_README_2"


class TestFileStability:
    """Task #3: Files should only be processed after they finish writing."""

    def test_stable_file_returns_true(self, tmp_path):
        from src.filesystem_watcher import _wait_for_stable

        f = tmp_path / "stable.txt"
        f.write_text("complete content")

        assert _wait_for_stable(f, poll=0.1, timeout=2.0) is True

    def test_missing_file_returns_false(self, tmp_path):
        from src.filesystem_watcher import _wait_for_stable

        f = tmp_path / "ghost.txt"
        assert _wait_for_stable(f, poll=0.1, timeout=0.5) is False

    def test_empty_file_waits(self, tmp_path):
        """An empty file should not immediately be considered stable."""
        from src.filesystem_watcher import _wait_for_stable

        f = tmp_path / "empty.txt"
        f.write_text("")  # 0 bytes

        # Should return False because size is 0 and stays 0
        assert _wait_for_stable(f, poll=0.1, timeout=0.5) is False


class TestReconciliationScan:
    """Task #4: Periodic scan should catch missed events."""

    def test_check_for_updates_finds_unprocessed(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        # Place files directly in Inbox (simulating missed events)
        (inbox / "missed1.txt").write_text("a")
        (inbox / "missed2.txt").write_text("b")
        (inbox / ".gitkeep").write_text("")  # should be ignored

        watcher = _make_watcher(inbox, needs_action)
        found = watcher.check_for_updates()

        names = [f.name for f in found]
        assert "missed1.txt" in names
        assert "missed2.txt" in names
        assert ".gitkeep" not in names

    def test_check_for_updates_skips_already_processed(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        f = inbox / "already.txt"
        f.write_text("done")

        watcher = _make_watcher(inbox, needs_action)
        watcher._processed_paths.add(str(f))

        found = watcher.check_for_updates()
        assert len(found) == 0

    def test_reconciliation_processes_missed_files(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        (inbox / "orphan.txt").write_text("missed by watchdog")

        watcher = _make_watcher(inbox, needs_action)
        watcher._reconciliation_scan()

        # File should have been processed
        md_files = [f for f in needs_action.iterdir() if f.suffix == ".md"]
        assert len(md_files) == 1
        assert "orphan.txt" in md_files[0].read_text()


class TestDryRunMode:
    """Dry run should log but not create/move files."""

    def test_dry_run_does_not_copy_files(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)
        from src.config import Config
        Config.DRY_RUN = True

        test_file = inbox / "secret.pdf"
        test_file.write_text("sensitive")

        watcher = _make_watcher(inbox, needs_action)
        watcher.dry_run = True
        watcher._process_file(test_file)

        # Original should still exist
        assert test_file.exists()

        # Nothing should be in Needs_Action except .gitkeep
        created = [f for f in needs_action.iterdir() if f.suffix == ".md"]
        assert len(created) == 0

        # Reset
        Config.DRY_RUN = False

    def test_dry_run_logs_action(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)
        from src.config import Config
        Config.DRY_RUN = True

        test_file = inbox / "doc.txt"
        test_file.write_text("content")

        watcher = _make_watcher(inbox, needs_action)
        watcher.dry_run = True
        watcher._process_file(test_file)

        log_files = list(logs.glob("*.json"))
        assert len(log_files) == 1

        entries = log_files[0].read_text().strip().split("\n")
        parsed = json.loads(entries[0])
        assert parsed["result"] == "dry_run_skipped"

        Config.DRY_RUN = False


class TestAuditLogger:
    """Task #5: Audit log entries and file locking."""

    def test_log_entry_has_all_fields(self, tmp_path):
        _, _, logs = _patch_config(tmp_path)

        from src.audit_logger import log_action

        entry = log_action(
            action_type="test_action",
            actor="test_actor",
            target="test_target",
            parameters={"key": "value"},
            result="success",
            dry_run=False,
        )

        required_fields = [
            "timestamp", "action_type", "actor", "target",
            "parameters", "approval_status", "approved_by",
            "result", "dry_run",
        ]
        for field in required_fields:
            assert field in entry, f"Missing field: {field}"

    def test_log_appends_to_daily_file(self, tmp_path):
        _, _, logs = _patch_config(tmp_path)

        from src.audit_logger import log_action

        log_action(action_type="first", actor="a", target="t", dry_run=False)
        log_action(action_type="second", actor="b", target="t", dry_run=False)

        log_files = list(logs.glob("*.json"))
        assert len(log_files) == 1

        lines = log_files[0].read_text().strip().split("\n")
        assert len(lines) == 2

        assert json.loads(lines[0])["action_type"] == "first"
        assert json.loads(lines[1])["action_type"] == "second"

    def test_log_is_valid_jsonlines(self, tmp_path):
        _, _, logs = _patch_config(tmp_path)

        from src.audit_logger import log_action

        for i in range(10):
            log_action(action_type=f"action_{i}", actor="test", target="t", dry_run=False)

        log_file = list(logs.glob("*.json"))[0]
        lines = log_file.read_text().strip().split("\n")

        for i, line in enumerate(lines):
            try:
                json.loads(line)
            except json.JSONDecodeError:
                pytest.fail(f"Line {i} is not valid JSON: {line}")


class TestFiveSimultaneousFiles:
    """Integration: simulate dropping 5 files at once."""

    def test_five_unique_files_all_processed(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        filenames = ["invoice.pdf", "receipt.png", "contract.docx", "photo.jpg", "notes.txt"]
        for name in filenames:
            f = inbox / name
            f.write_text(f"content of {name}")
            watcher._process_file(f)

        # All 5 should produce a .md metadata file
        md_files = [f for f in needs_action.iterdir() if f.suffix == ".md"]
        assert len(md_files) == 5

        # All 5 should produce a copied file
        copied = [f for f in needs_action.iterdir() if f.suffix != ".md"]
        assert len(copied) == 5

        # Inbox should be empty (except .gitkeep)
        remaining = [f for f in inbox.iterdir() if not f.name.startswith(".")]
        assert len(remaining) == 0

    def test_five_same_name_files_no_data_loss(self, tmp_path):
        """The critical edge case: 5 files named 'report.pdf' dropped sequentially."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        for i in range(5):
            f = inbox / "report.pdf"
            f.write_text(f"version {i}")
            watcher.create_action_file(f)

        # Should have 5 distinct copied files
        copied = [f for f in needs_action.iterdir() if f.suffix == ".pdf"]
        assert len(copied) == 5, f"Expected 5 files, got: {[f.name for f in copied]}"

        # All versions should be preserved
        contents = sorted([f.read_text() for f in copied])
        expected = sorted([f"version {i}" for i in range(5)])
        assert contents == expected

    def test_five_files_all_logged(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        for i in range(5):
            f = inbox / f"file_{i}.txt"
            f.write_text(f"data {i}")
            watcher._process_file(f)

        log_file = list(logs.glob("*.json"))[0]
        entries = log_file.read_text().strip().split("\n")

        # Each file produces one log entry
        success_entries = [json.loads(e) for e in entries if json.loads(e)["result"] == "success"]
        assert len(success_entries) == 5

    def test_process_file_is_idempotent(self, tmp_path):
        """Calling _process_file twice on the same path should only process once."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        f = inbox / "once.txt"
        f.write_text("only once")

        watcher = _make_watcher(inbox, needs_action)
        watcher._process_file(f)
        watcher._process_file(f)  # second call — file already gone

        md_files = [x for x in needs_action.iterdir() if x.suffix == ".md"]
        assert len(md_files) == 1


class TestErrorRecovery:
    """Edge cases around failures during processing."""

    def test_vanished_file_does_not_crash(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        watcher = _make_watcher(inbox, needs_action)

        ghost = inbox / "vanished.txt"
        # Never created — simulate a file that vanished between detection and processing
        watcher._process_file(ghost)

        # Should not crash, and should not be in processed set
        assert str(ghost) not in watcher._processed_paths

    def test_copy_failure_allows_retry(self, tmp_path):
        """If create_action_file fails, the file should be retryable."""
        inbox, needs_action, logs = _patch_config(tmp_path)

        f = inbox / "retry_me.txt"
        f.write_text("important data")

        watcher = _make_watcher(inbox, needs_action)

        # Simulate failure by making needs_action unwritable temporarily
        with patch.object(watcher, "create_action_file", side_effect=PermissionError("disk full")):
            watcher._process_file(f)

        # File should NOT be in processed set (so reconciliation can retry)
        assert str(f) not in watcher._processed_paths

        # Now process for real
        watcher._process_file(f)

        md_files = [x for x in needs_action.iterdir() if x.suffix == ".md"]
        assert len(md_files) == 1


class TestSpacesInFilenames:
    """Files with spaces should be handled safely."""

    def test_spaces_replaced_with_underscores(self, tmp_path):
        inbox, needs_action, logs = _patch_config(tmp_path)

        f = inbox / "my report final (2).pdf"
        f.write_text("content")

        watcher = _make_watcher(inbox, needs_action)
        result = watcher.create_action_file(f)

        assert " " not in result.name
        # Original name should be preserved in the metadata
        content = result.read_text()
        assert "my report final (2).pdf" in content
