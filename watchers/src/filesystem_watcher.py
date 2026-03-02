"""
FileSystem Watcher — monitors /Inbox for new file drops.

Uses watchdog for real-time filesystem events with a periodic reconciliation
scan to catch any events missed by the OS notification layer.

Edge cases handled:
- Timestamp collisions: microsecond precision + 4-char UUID suffix
- Same-name files: dedup counter appended to dest path
- Partial writes: stability check waits until file size stops changing
- Missed events: periodic reconciliation scan every check_interval seconds
- Watchdog event types: on_created, on_modified, on_moved all handled
- Large files: configurable stability timeout with max wait
"""

import signal
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Timer

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .audit_logger import log_action
from .base_watcher import BaseWatcher
from .calendar_service import create_event
from .classifier import classify
from .config import Config
from .slack_service import notify_error, notify_task_processed

# Max retry attempts per file before quarantining it
_MAX_RETRIES = 3

# How long to wait between stability checks (seconds)
_STABILITY_POLL = 0.5
# Max time to wait for a file to stabilize (seconds)
_STABILITY_TIMEOUT = 30.0
# Delay before processing a detected file, to let writes finish (seconds)
_DEBOUNCE_DELAY = 1.0


def _wait_for_stable(path: Path, poll: float = _STABILITY_POLL, timeout: float = _STABILITY_TIMEOUT) -> bool:
    """
    Wait until a file's size stops changing, indicating the write is complete.

    Returns True if the file stabilized, False if it timed out or vanished.
    """
    elapsed = 0.0
    prev_size = -1
    while elapsed < timeout:
        if not path.exists():
            return False
        try:
            current_size = path.stat().st_size
        except OSError:
            return False

        if current_size == prev_size and current_size > 0:
            return True  # size stable for one poll interval

        prev_size = current_size
        time.sleep(poll)
        elapsed += poll

    # Timed out — accept whatever we have if file exists and has content
    return path.exists() and path.stat().st_size > 0


def _unique_dest(directory: Path, prefix: str, name: str) -> Path:
    """
    Generate a unique destination path, appending a counter if needed.

    Example: FILE_report.pdf → FILE_report.pdf
             FILE_report.pdf (exists) → FILE_report_2.pdf
             FILE_report_2.pdf (exists) → FILE_report_3.pdf
    """
    candidate = directory / f"{prefix}{name}"
    if not candidate.exists():
        return candidate

    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 2
    while True:
        candidate = directory / f"{prefix}{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class InboxHandler(FileSystemEventHandler):
    """Watchdog event handler for the /Inbox directory."""

    def __init__(self, watcher: "FileSystemWatcher"):
        super().__init__()
        self.watcher = watcher
        self._pending_timers: dict[str, Timer] = {}

    def _schedule_processing(self, path: Path):
        """Debounce: schedule processing after a short delay to coalesce rapid events."""
        key = str(path)

        # Cancel any existing timer for this path
        if key in self._pending_timers:
            self._pending_timers[key].cancel()

        def _process():
            self._pending_timers.pop(key, None)
            self.watcher._process_file(path)

        timer = Timer(_DEBOUNCE_DELAY, _process)
        timer.daemon = True
        timer.start()
        self._pending_timers[key] = timer

    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be skipped."""
        if not path.is_file():
            return True
        if path.name.startswith("."):
            return True
        return False

    def on_created(self, event):
        if event.is_directory:
            return
        src = Path(event.src_path)
        if self._should_ignore(src):
            return
        self.watcher.logger.debug(f"on_created: {src.name}")
        self._schedule_processing(src)

    def on_modified(self, event):
        """Catch files that were created empty then written to (common on Windows)."""
        if event.is_directory:
            return
        src = Path(event.src_path)
        if self._should_ignore(src):
            return
        # Only process if not already processed
        if str(src) in self.watcher._processed_paths:
            return
        self.watcher.logger.debug(f"on_modified (unprocessed): {src.name}")
        self._schedule_processing(src)

    def on_moved(self, event):
        """Catch files moved/renamed into the Inbox folder."""
        if event.is_directory:
            return
        dest = Path(event.dest_path)
        # Only care if the destination is inside our inbox
        try:
            dest.relative_to(self.watcher.inbox_path)
        except ValueError:
            return
        if self._should_ignore(dest):
            return
        self.watcher.logger.debug(f"on_moved: → {dest.name}")
        self._schedule_processing(dest)


class FileSystemWatcher(BaseWatcher):
    """
    Watches /Inbox for new files using watchdog + periodic reconciliation.

    When a file is dropped into /Inbox:
    1. Waits for the file to stabilize (finish writing)
    2. Copies it to /Needs_Action/FILE_<original_name> (deduped)
    3. Creates a metadata .md file with frontmatter (unique timestamp)
    4. Logs the action to /Logs/
    5. Removes the original from /Inbox

    Periodic reconciliation scans /Inbox every check_interval seconds
    to catch any files missed by OS-level notifications.
    """

    def __init__(self):
        super().__init__()
        self.inbox_path = Config.INBOX_PATH
        self.observer = Observer()
        self._shutting_down = False
        # Track processed paths to avoid re-processing across event types
        self._processed_paths: set[str] = set()
        # Track retry attempts per file path — quarantine after _MAX_RETRIES
        self._retry_counts: dict[str, int] = {}

    def check_for_updates(self) -> list:
        """Scan Inbox for any unprocessed files (reconciliation fallback)."""
        items = []
        if not self.inbox_path.exists():
            return items
        for f in self.inbox_path.iterdir():
            if f.is_file() and not f.name.startswith("."):
                if str(f) not in self._processed_paths:
                    items.append(f)
        return items

    def _process_file(self, item: Path):
        """
        Process a single file: stability check, dedup, copy, log.

        Thread-safe entry point called by both the event handler and reconciliation.
        """
        key = str(item)

        # Skip if already processed
        if key in self._processed_paths:
            return

        # Check file still exists (may have been cleaned up already)
        if not item.exists():
            self.watcher_log_skip(item, "file_vanished")
            return

        # Dry-run check
        if self.dry_run:
            self._processed_paths.add(key)
            self.logger.info(f"[DRY RUN] Would process: {item.name}")
            log_action(
                action_type="file_drop_detected",
                actor="FileSystemWatcher",
                target=str(item),
                result="dry_run_skipped",
            )
            return

        # Wait for file to finish writing
        self.logger.debug(f"Waiting for stable: {item.name}")
        if not _wait_for_stable(item):
            self.logger.warning(f"File not stable or vanished: {item.name}")
            log_action(
                action_type="file_drop_detected",
                actor="FileSystemWatcher",
                target=str(item),
                parameters={"error": "file_not_stable"},
                result="skipped",
            )
            return

        # Check if this file has exceeded max retries
        attempt = self._retry_counts.get(key, 0)
        if attempt >= _MAX_RETRIES:
            if key not in self._processed_paths:
                self._processed_paths.add(key)  # quarantine — stop retrying
                self.logger.error(
                    f"Quarantined {item.name} after {_MAX_RETRIES} failed attempts"
                )
                log_action(
                    action_type="file_quarantined",
                    actor="FileSystemWatcher",
                    target=str(item),
                    parameters={"attempts": _MAX_RETRIES},
                    result="quarantined",
                )
                notify_error(
                    actor="FileSystemWatcher",
                    error_message=f"Quarantined after {_MAX_RETRIES} failed attempts",
                    target=item.name,
                )
            return

        # Mark as processed before doing work (prevents re-entry from reconciliation)
        self._processed_paths.add(key)

        try:
            self.create_action_file(item)
            # Success — clear retry counter
            self._retry_counts.pop(key, None)
        except Exception as e:
            # Un-mark so reconciliation can retry
            self._processed_paths.discard(key)
            self._retry_counts[key] = attempt + 1
            remaining = _MAX_RETRIES - (attempt + 1)
            self.logger.error(
                f"Failed to process {item.name} (attempt {attempt + 1}/{_MAX_RETRIES}, "
                f"{remaining} retries left): {e}",
                exc_info=True,
            )
            log_action(
                action_type="file_drop_error",
                actor="FileSystemWatcher",
                target=str(item),
                parameters={
                    "error": str(e),
                    "attempt": attempt + 1,
                    "max_retries": _MAX_RETRIES,
                },
                result="error",
            )
            notify_error(
                actor="FileSystemWatcher",
                error_message=f"(attempt {attempt + 1}/{_MAX_RETRIES}) {e}",
                target=item.name,
            )

    def watcher_log_skip(self, item: Path, reason: str):
        """Log when a file is skipped during processing."""
        self.logger.debug(f"Skipping {item.name}: {reason}")

    def create_action_file(self, item: Path) -> Path:
        """
        Process a file drop:
        1. Copy the original file to Needs_Action (with dedup)
        2. Create a companion .md metadata file (with unique timestamp)
        3. Log the action
        4. Remove original from Inbox
        """
        now = datetime.now(timezone.utc)
        # Microsecond precision + 4-char UUID to guarantee uniqueness
        timestamp = now.strftime("%Y-%m-%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:4]
        safe_name = item.name.replace(" ", "_")

        # Copy original file — dedup if same name exists
        dest_file = _unique_dest(self.needs_action, "FILE_", safe_name)
        shutil.copy2(str(item), str(dest_file))

        # Verify copy integrity (size check)
        src_size = item.stat().st_size
        dest_size = dest_file.stat().st_size
        if src_size != dest_size:
            dest_file.unlink()
            raise IOError(
                f"Copy verification failed for {item.name}: "
                f"source={src_size} bytes, dest={dest_size} bytes"
            )

        # Classify content for priority/category/due-date
        try:
            raw_text = item.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw_text = item.name  # fall back to filename only
        classification = classify(raw_text)

        due_date_line = (
            f'suggested_due_date: "{classification.suggested_due_date}"'
            if classification.suggested_due_date
            else "suggested_due_date: null"
        )

        # Create metadata markdown — always unique due to timestamp+uuid
        md_content = f"""---
type: file_drop
source: inbox
original_name: "{item.name}"
copied_as: "{dest_file.name}"
size_bytes: {src_size}
detected_at: "{now.isoformat()}"
category: {classification.category}
priority: {classification.priority}
{due_date_line}
status: pending
---

# File Drop: {item.name}

## Classification
- **Category**: {classification.category}
- **Priority**: {classification.priority}
- **Suggested due date**: {classification.suggested_due_date or "—"}

## Details
- **Original name**: {item.name}
- **Saved as**: {dest_file.name}
- **Size**: {src_size:,} bytes
- **Detected**: {now.strftime("%Y-%m-%d %H:%M:%S")} UTC
- **Source**: /Inbox (FileSystem Watcher)

## Suggested Actions
- [ ] Review file contents
- [ ] Route to appropriate workflow
- [ ] Move to /Done when processed
"""
        md_path = self.needs_action / f"FILE_{timestamp}_{safe_name}.md"
        md_path.write_text(md_content, encoding="utf-8")

        # Log
        log_action(
            action_type="file_drop_processed",
            actor="FileSystemWatcher",
            target=str(md_path),
            parameters={
                "original_file": item.name,
                "size_bytes": src_size,
                "copied_to": str(dest_file),
                "md_file": str(md_path),
            },
            result="success",
        )

        # Remove from Inbox only after successful copy + metadata + log
        try:
            item.unlink()
        except OSError as e:
            # Non-fatal: file was processed, just couldn't clean up
            self.logger.warning(f"Could not remove {item.name} from Inbox: {e}")

        self.logger.info(f"Processed {item.name} → {md_path.name}")

        # Slack notification — non-fatal if Slack is down or unconfigured
        notify_task_processed(
            filename=item.name,
            category=classification.category,
            priority=classification.priority,
            suggested_due_date=classification.suggested_due_date,
            size_bytes=src_size,
        )

        # Google Calendar event — only when a due date was assigned
        if classification.suggested_due_date:
            create_event(
                title=item.name,
                due_date=classification.suggested_due_date,
                category=classification.category,
                priority=classification.priority,
            )

        return md_path

    def _reconciliation_scan(self):
        """
        Periodic scan to catch files missed by OS-level notifications.

        Runs every check_interval seconds as a safety net.
        """
        missed = self.check_for_updates()
        if missed:
            self.logger.info(f"Reconciliation found {len(missed)} unprocessed file(s)")
            for item in missed:
                self._process_file(item)

    def _shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler for SIGINT/SIGTERM."""
        if self._shutting_down:
            return  # prevent double-shutdown
        self._shutting_down = True

        sig_name = signal.Signals(signum).name if signum else "manual"
        self.logger.info(f"Shutdown requested ({sig_name}). Cleaning up...")

        self.observer.stop()
        log_action(
            action_type="watcher_shutdown",
            actor="FileSystemWatcher",
            target="watcher",
            parameters={
                "signal": sig_name,
                "processed_count": len(self._processed_paths),
                "pending_retries": len(self._retry_counts),
            },
            result="success",
        )

    def run(self):
        """Start watchdog observer with periodic reconciliation."""
        self.logger.info(
            f"Starting FileSystem Watcher on: {self.inbox_path} "
            f"(dry_run={self.dry_run})"
        )

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # First, process any files already in Inbox
        existing = self.check_for_updates()
        if existing:
            self.logger.info(f"Found {len(existing)} existing file(s) in Inbox")
            for item in existing:
                self._process_file(item)

        # Set up real-time watching
        handler = InboxHandler(self)
        self.observer.schedule(handler, str(self.inbox_path), recursive=False)
        self.observer.start()

        self.logger.info("Watcher is running. Press Ctrl+C to stop.")

        while not self._shutting_down:
            try:
                time.sleep(self.check_interval)
                self._reconciliation_scan()
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}", exc_info=True)
                log_action(
                    action_type="watcher_loop_error",
                    actor="FileSystemWatcher",
                    target="main_loop",
                    parameters={"error": str(e)},
                    result="error",
                )
                # Continue running — don't crash on transient errors

        self.observer.join()
        self.logger.info(
            f"Watcher stopped. Processed {len(self._processed_paths)} file(s) this session."
        )


def main():
    """Entry point for the filesystem watcher."""
    # Validate config before starting
    errors = Config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if Config.DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE — No files will be moved or created.")
        print("  Set DRY_RUN=false in .env to enable live mode.")
        print("=" * 60)

    watcher = FileSystemWatcher()
    watcher.run()
