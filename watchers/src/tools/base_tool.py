"""
Base class for all tool integrations.

Every tool module (Gmail, LinkedIn, Slack, etc.) inherits from BaseTool
and implements the same contract:

1. is_configured() → bool
2. execute(action, **params) → ToolResult
3. list_actions() → list[str]

Design:
- Never raises — all errors returned as ToolResult(success=False)
- DRY_RUN aware at the base level
- Audit logging built in
- HITL integration: sensitive actions routed through approval_manager
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..audit_logger import log_action
from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Standardised return type for all tool actions."""
    success: bool
    action: str
    data: dict = field(default_factory=dict)
    error: str | None = None
    dry_run: bool = False

    def __bool__(self) -> bool:
        return self.success


class BaseTool(ABC):
    """
    Abstract base for tool integrations.

    Subclasses implement:
        - name: str property
        - _is_configured() → bool
        - _execute(action, **params) → ToolResult
        - list_actions() → list[str]
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool identifier (e.g. 'gmail', 'linkedin')."""
        ...

    @abstractmethod
    def _is_configured(self) -> bool:
        """Check if required credentials/env vars are present."""
        ...

    @abstractmethod
    def _execute(self, action: str, **params: Any) -> ToolResult:
        """
        Execute the action. Called only after DRY_RUN and config checks pass.
        Must return ToolResult, never raise.
        """
        ...

    @abstractmethod
    def list_actions(self) -> list[str]:
        """Return list of supported action names."""
        ...

    def is_configured(self) -> bool:
        """Public config check (delegates to subclass)."""
        return self._is_configured()

    def execute(self, action: str, **params: Any) -> ToolResult:
        """
        Execute a tool action with all safety checks.

        Order: validate action → DRY_RUN check → config check → execute → log
        """
        # Validate action name
        if action not in self.list_actions():
            return ToolResult(
                success=False,
                action=action,
                error=f"Unknown action '{action}'. Available: {self.list_actions()}",
            )

        # DRY_RUN gate
        if Config.DRY_RUN:
            logger.info(f"[DRY RUN] {self.name}.{action}({params})")
            log_action(
                action_type=f"tool_{self.name}_{action}",
                actor=f"tool:{self.name}",
                target=params.get("target", action),
                parameters=params,
                result="dry_run_skipped",
            )
            return ToolResult(
                success=False, action=action, dry_run=True,
                data={"params": params},
            )

        # Config gate
        if not self._is_configured():
            logger.debug(f"{self.name} not configured — skipping {action}")
            return ToolResult(
                success=False, action=action,
                error=f"{self.name} not configured",
            )

        # Execute
        try:
            result = self._execute(action, **params)
        except Exception as exc:
            logger.warning(
                f"{self.name}.{action} failed (non-fatal): {exc}"
            )
            result = ToolResult(
                success=False, action=action, error=str(exc),
            )

        # Audit log
        log_action(
            action_type=f"tool_{self.name}_{action}",
            actor=f"tool:{self.name}",
            target=params.get("target", action),
            parameters={**params, "result_data": result.data},
            result="success" if result.success else "error",
        )

        return result
