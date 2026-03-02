"""
Abstract base class for all AI Employee Watchers.
Defines the perception contract: check → create action file → log.
"""

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .audit_logger import log_action, setup_logging
from .config import Config


class BaseWatcher(ABC):
    """
    Base class for all Watchers (Gmail, FileSystem, WhatsApp, etc.).

    Subclasses implement:
        - check_for_updates() → list of new items
        - create_action_file(item) → Path to the .md file created
    """

    def __init__(self, vault_path: Path | None = None, check_interval: int | None = None):
        self.vault_path = vault_path or Config.VAULT_PATH
        self.needs_action = self.vault_path / "Needs_Action"
        self.check_interval = check_interval or Config.WATCHER_CHECK_INTERVAL
        self.dry_run = Config.DRY_RUN

        setup_logging()
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def check_for_updates(self) -> list:
        """Return list of new items to process."""
        ...

    @abstractmethod
    def create_action_file(self, item) -> Path:
        """Create a .md file in Needs_Action for the given item. Return file path."""
        ...

    def run(self):
        """Main poll loop. Runs until interrupted."""
        self.logger.info(
            f"Starting {self.__class__.__name__} "
            f"(interval={self.check_interval}s, dry_run={self.dry_run})"
        )

        while True:
            try:
                items = self.check_for_updates()
                for item in items:
                    if self.dry_run:
                        self.logger.info(f"[DRY RUN] Would create action file for: {item}")
                        log_action(
                            action_type="watcher_detect",
                            actor=self.__class__.__name__,
                            target=str(item),
                            result="dry_run_skipped",
                        )
                    else:
                        filepath = self.create_action_file(item)
                        self.logger.info(f"Created action file: {filepath}")
                        log_action(
                            action_type="watcher_detect",
                            actor=self.__class__.__name__,
                            target=str(filepath),
                            parameters={"source_item": str(item)},
                            result="success",
                        )
            except KeyboardInterrupt:
                self.logger.info("Watcher stopped by user.")
                break
            except Exception as e:
                self.logger.error(f"Error in watcher loop: {e}", exc_info=True)
                log_action(
                    action_type="watcher_error",
                    actor=self.__class__.__name__,
                    target="watcher_loop",
                    parameters={"error": str(e)},
                    result="error",
                )

            time.sleep(self.check_interval)
