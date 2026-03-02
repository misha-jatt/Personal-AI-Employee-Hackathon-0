"""
LinkedIn Tool — post content and read profile data via LinkedIn API.

Actions:
- create_post: Create a text post (requires HITL approval)
- get_profile: Read authenticated user's profile
- get_connections: Get connection count

LinkedIn API uses OAuth 2.0 with access tokens.
See: https://learn.microsoft.com/en-us/linkedin/shared/authentication/

All posting actions route through HITL approval since Company Handbook §7
lists social media posts as NEVER auto-approve.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base_tool import BaseTool, ToolResult
from . import registry

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES = ("your-", "REPLACE", "xxx")

# All write actions require HITL approval
_APPROVAL_REQUIRED = {"create_post"}


def _access_token() -> str:
    return os.getenv("LINKEDIN_ACCESS_TOKEN", "")


def _is_placeholder(value: str) -> bool:
    return any(value.startswith(p) for p in _PLACEHOLDER_PREFIXES)


class LinkedInTool(BaseTool):
    """LinkedIn API wrapper as a tool module."""

    @property
    def name(self) -> str:
        return "linkedin"

    def _is_configured(self) -> bool:
        token = _access_token()
        return bool(token) and not _is_placeholder(token)

    def list_actions(self) -> list[str]:
        return ["create_post", "get_profile", "get_connections"]

    def _execute(self, action: str, **params: Any) -> ToolResult:
        handler = {
            "create_post": self._create_post,
            "get_profile": self._get_profile,
            "get_connections": self._get_connections,
        }.get(action)

        if handler is None:
            return ToolResult(success=False, action=action, error="Unknown action")

        # HITL gate for posting
        if action in _APPROVAL_REQUIRED:
            from ..approval_manager import requires_approval, create_approval_request
            if requires_approval("social_media_post"):
                path = create_approval_request(
                    action_type="social_media_post",
                    actor="tool:linkedin",
                    target="LinkedIn post",
                    description=f"Post to LinkedIn: {params.get('text', '')[:100]}",
                    parameters={k: str(v)[:200] for k, v in params.items()},
                )
                return ToolResult(
                    success=False, action=action,
                    data={"approval_file": str(path) if path else None},
                    error="Requires human approval — file created in /Pending_Approval",
                )

        return handler(**params)

    def _api_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def _create_post(self, text: str = "", **_) -> ToolResult:
        """Create a LinkedIn text post via the UGC Post API."""
        if not text:
            return ToolResult(success=False, action="create_post", error="text required")

        import urllib.request
        import json

        # Get user URN first
        profile = self._get_profile()
        if not profile.success:
            return ToolResult(
                success=False, action="create_post",
                error=f"Could not get profile: {profile.error}",
            )
        user_urn = profile.data.get("urn", "")

        body = json.dumps({
            "author": user_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }).encode()

        req = urllib.request.Request(
            "https://api.linkedin.com/v2/ugcPosts",
            data=body,
            headers=self._api_headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return ToolResult(
                    success=True, action="create_post",
                    data={"post_id": data.get("id", ""), "text_preview": text[:100]},
                )
        except Exception as exc:
            return ToolResult(
                success=False, action="create_post", error=str(exc),
            )

    def _get_profile(self, **_) -> ToolResult:
        """Get the authenticated user's LinkedIn profile."""
        import urllib.request
        import json

        req = urllib.request.Request(
            "https://api.linkedin.com/v2/me",
            headers=self._api_headers(),
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                first = data.get("localizedFirstName", "")
                last = data.get("localizedLastName", "")
                urn = data.get("id", "")
                return ToolResult(
                    success=True, action="get_profile",
                    data={
                        "name": f"{first} {last}".strip(),
                        "urn": f"urn:li:person:{urn}",
                        "raw_id": urn,
                    },
                )
        except Exception as exc:
            return ToolResult(
                success=False, action="get_profile", error=str(exc),
            )

    def _get_connections(self, **_) -> ToolResult:
        """Get connection count for the authenticated user."""
        import urllib.request
        import json

        req = urllib.request.Request(
            "https://api.linkedin.com/v2/connections?q=viewer&start=0&count=0",
            headers=self._api_headers(),
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                total = data.get("paging", {}).get("total", 0)
                return ToolResult(
                    success=True, action="get_connections",
                    data={"total_connections": total},
                )
        except Exception as exc:
            return ToolResult(
                success=False, action="get_connections", error=str(exc),
            )


# Auto-register on import
_instance = LinkedInTool()
registry.register(_instance)
