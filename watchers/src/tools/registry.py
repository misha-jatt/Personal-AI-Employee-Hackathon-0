"""
Tool Registry — central access point for all tool integrations.

Tools auto-register on import. New tools just need to call register().

Usage:
    from src.tools import registry
    gmail = registry.get("gmail")
    all_tools = registry.list_tools()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base_tool import BaseTool

logger = logging.getLogger(__name__)

_registry: dict[str, BaseTool] = {}


def register(tool: BaseTool) -> None:
    """Register a tool instance by its name."""
    name = tool.name
    if name in _registry:
        logger.warning(f"Tool '{name}' already registered — overwriting")
    _registry[name] = tool
    logger.debug(f"Tool registered: {name}")


def get(name: str) -> BaseTool | None:
    """Get a registered tool by name. Returns None if not found."""
    return _registry.get(name)


def list_tools() -> list[str]:
    """Return names of all registered tools."""
    return list(_registry.keys())


def list_configured() -> list[str]:
    """Return names of tools that are currently configured."""
    return [name for name, tool in _registry.items() if tool.is_configured()]


def reset() -> None:
    """Clear the registry (used in tests)."""
    _registry.clear()
