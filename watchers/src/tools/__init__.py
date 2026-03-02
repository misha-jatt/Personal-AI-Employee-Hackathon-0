"""
Tool Integration Layer — lightweight API wrappers as tool modules.

Each tool follows the same contract:
- Never raises (catches all exceptions, returns ToolResult)
- DRY_RUN aware (logs intent without executing)
- Self-configuring via env vars with graceful degradation
- Audits every action to /Logs/

Usage:
    from src.tools import registry
    gmail = registry.get("gmail")
    result = gmail.execute("send_email", to="a@b.com", subject="Hi", body="Hello")
"""

from .base_tool import BaseTool, ToolResult
from .registry import get, list_tools, register

__all__ = ["BaseTool", "ToolResult", "get", "list_tools", "register"]
