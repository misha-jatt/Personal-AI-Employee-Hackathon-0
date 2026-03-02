"""
Configuration loader for AI Employee Watchers.
Reads from .env file and provides typed access to all settings.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from watchers/ directory
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


class Config:
    """Central configuration — all values sourced from environment variables."""

    # Paths
    VAULT_PATH: Path = Path(os.getenv("VAULT_PATH", "D:/AI_Employee_Vault"))
    INBOX_PATH: Path = VAULT_PATH / "Inbox"
    NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
    LOGS_PATH: Path = VAULT_PATH / "Logs"

    # Behavior
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    WATCHER_CHECK_INTERVAL: int = int(os.getenv("WATCHER_CHECK_INTERVAL", "5"))

    @classmethod
    def validate(cls) -> list[str]:
        """Return list of configuration errors. Empty list = valid."""
        errors = []
        if not cls.VAULT_PATH.exists():
            errors.append(f"VAULT_PATH does not exist: {cls.VAULT_PATH}")
        if not cls.INBOX_PATH.exists():
            errors.append(f"Inbox directory missing: {cls.INBOX_PATH}")
        if not cls.NEEDS_ACTION_PATH.exists():
            errors.append(f"Needs_Action directory missing: {cls.NEEDS_ACTION_PATH}")
        if not cls.LOGS_PATH.exists():
            errors.append(f"Logs directory missing: {cls.LOGS_PATH}")
        return errors
