"""
Structured JSON audit logger for the AI Employee.
Writes one log file per day to /Logs/YYYY-MM-DD.json.
Each entry is a JSON object appended as a line (JSON Lines format).

Uses file locking to prevent interleaved writes when multiple processes
(e.g., watcher + Claude Code) write to the same log file concurrently.

Also provides setup_logging() to configure the Python logging system with:
- Console handler (all levels)
- Rotating errors.log file handler (ERROR and above only, 5 MB x 3 backups)
"""

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

_LOG_FORMAT = "[%(asctime)s] %(name)s %(levelname)s: %(message)s"
_logging_configured = False


def setup_logging() -> None:
    """
    Configure the root logger once for the entire watcher process.

    Handlers added:
    - StreamHandler  → console, all levels (respects Config.LOG_LEVEL)
    - RotatingFileHandler → /Logs/errors.log, ERROR+ only, 5 MB x 3 backups

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _logging_configured
    if _logging_configured:
        return

    Config.LOGS_PATH.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers filter individually

    formatter = logging.Formatter(_LOG_FORMAT)

    # Console — level controlled by config (INFO by default)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating error file — ERROR and above only
    error_log = Config.LOGS_PATH / "errors.log"
    error_file = logging.handlers.RotatingFileHandler(
        error_log,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    root.addHandler(error_file)

    _logging_configured = True

# Platform-specific file locking
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        f.seek(0, 2)  # seek to end before unlock
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def log_action(
    action_type: str,
    actor: str,
    target: str,
    parameters: dict | None = None,
    approval_status: str = "auto",
    approved_by: str = "system",
    result: str = "success",
    dry_run: bool | None = None,
) -> dict:
    """
    Write a structured audit log entry with file locking.

    Returns the log entry dict for testing/chaining.
    """
    if dry_run is None:
        dry_run = Config.DRY_RUN

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "actor": actor,
        "target": target,
        "parameters": parameters or {},
        "approval_status": approval_status,
        "approved_by": approved_by,
        "result": result,
        "dry_run": dry_run,
    }

    log_file = Config.LOGS_PATH / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"

    Config.LOGS_PATH.mkdir(parents=True, exist_ok=True)

    line = json.dumps(entry) + "\n"

    with open(log_file, "a", encoding="utf-8") as f:
        try:
            _lock_file(f)
            f.write(line)
            f.flush()
        finally:
            try:
                _unlock_file(f)
            except OSError:
                pass  # unlock best-effort; file close releases lock anyway

    return entry
