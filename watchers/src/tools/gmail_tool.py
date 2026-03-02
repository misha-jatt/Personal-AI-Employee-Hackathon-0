"""
Gmail Tool — read and send emails via Gmail API.

Actions:
- list_unread: List unread important emails
- read_email: Read a specific email by ID
- send_email: Draft/send an email (requires HITL approval)
- search: Search emails by query

Uses the same OAuth credentials as gmail_watcher.py but with
gmail.modify scope for sending.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .base_tool import BaseTool, ToolResult
from . import registry

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Actions that require HITL approval before execution
_APPROVAL_REQUIRED = {"send_email"}


def _credentials_path() -> Path:
    return Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"))


def _token_path() -> Path:
    return Path(os.getenv("GMAIL_TOOL_TOKEN_PATH", "gmail_tool_token.json"))


class GmailTool(BaseTool):
    """Gmail API wrapper as a tool module."""

    def __init__(self):
        self._service = None

    @property
    def name(self) -> str:
        return "gmail"

    def _is_configured(self) -> bool:
        return _credentials_path().exists()

    def list_actions(self) -> list[str]:
        return ["list_unread", "read_email", "send_email", "search"]

    def _get_service(self):
        if self._service is None:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            token_path = _token_path()
            creds = None

            if token_path.exists():
                creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(_credentials_path()), _SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json(), encoding="utf-8")

            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _execute(self, action: str, **params: Any) -> ToolResult:
        handler = {
            "list_unread": self._list_unread,
            "read_email": self._read_email,
            "send_email": self._send_email,
            "search": self._search,
        }.get(action)

        if handler is None:
            return ToolResult(success=False, action=action, error="Unknown action")

        # HITL gate for sensitive actions
        if action in _APPROVAL_REQUIRED:
            from ..approval_manager import requires_approval, create_approval_request
            if requires_approval("email_send"):
                path = create_approval_request(
                    action_type="email_send",
                    actor="tool:gmail",
                    target=params.get("to", "unknown"),
                    description=f"Send email: {params.get('subject', 'No Subject')}",
                    parameters={k: str(v) for k, v in params.items()},
                )
                return ToolResult(
                    success=False, action=action,
                    data={"approval_file": str(path) if path else None},
                    error="Requires human approval — file created in /Pending_Approval",
                )

        return handler(**params)

    def _list_unread(self, query: str = "is:unread is:important", max_results: int = 10, **_) -> ToolResult:
        service = self._get_service()
        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        return ToolResult(
            success=True, action="list_unread",
            data={"count": len(messages), "message_ids": [m["id"] for m in messages]},
        )

    def _read_email(self, message_id: str = "", **_) -> ToolResult:
        if not message_id:
            return ToolResult(success=False, action="read_email", error="message_id required")

        service = self._get_service()
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {}
        for h in msg.get("payload", {}).get("headers", []):
            if h["name"].lower() in ("from", "to", "subject", "date"):
                headers[h["name"].lower()] = h["value"]

        return ToolResult(
            success=True, action="read_email",
            data={
                "id": message_id,
                "snippet": msg.get("snippet", ""),
                "headers": headers,
            },
        )

    def _send_email(self, **params) -> ToolResult:
        # This path is only reached if approval was bypassed (shouldn't happen)
        return ToolResult(
            success=False, action="send_email",
            error="send_email requires HITL approval flow",
        )

    def _search(self, query: str = "", max_results: int = 10, **_) -> ToolResult:
        if not query:
            return ToolResult(success=False, action="search", error="query required")

        service = self._get_service()
        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        return ToolResult(
            success=True, action="search",
            data={"query": query, "count": len(messages), "message_ids": [m["id"] for m in messages]},
        )


# Auto-register on import
_instance = GmailTool()
registry.register(_instance)
