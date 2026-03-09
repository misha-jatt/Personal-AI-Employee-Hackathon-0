"""
LinkedIn Watcher — monitors /Needs_Action for social_post tasks.

Scans files with `type: social_post` and `platform: linkedin` in frontmatter.
When found:
  1. Generates a business marketing post from the task content
  2. If approval_required: true → moves to /Pending_Approval
  3. After human approval → moves to /Approved
  4. Sends post to MCP server for publishing

Works with the approval_watcher.py and mcp_linkedin_server.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit_logger import log_action, setup_logging
from .base_watcher import BaseWatcher
from .config import Config

logger = logging.getLogger(__name__)

# MCP server base URL
MCP_BASE_URL = os.getenv("MCP_LINKEDIN_URL", "http://127.0.0.1:3001")

# Vault paths
PENDING_APPROVAL = Config.VAULT_PATH / "Pending_Approval"
APPROVED = Config.VAULT_PATH / "Approved"
DONE = Config.VAULT_PATH / "Done"


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML-like frontmatter from a markdown file."""
    fm: dict[str, str] = {}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return fm
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm


def _is_linkedin_post_task(content: str) -> bool:
    """Check if a file is a social_post task targeting LinkedIn."""
    fm = _parse_frontmatter(content)
    task_type = fm.get("type", "").lower()
    platform = fm.get("platform", "").lower()
    return task_type == "social_post" and platform == "linkedin"


def _extract_post_content(content: str, fm: dict[str, str]) -> str:
    """
    Extract the post text from the markdown body.
    Looks for a ## Post Content or ## Content section, or uses the body.
    """
    # Try to find a dedicated post content section
    patterns = [
        r"##\s*Post\s*Content\s*\n(.*?)(?=\n##|\Z)",
        r"##\s*Content\s*\n(.*?)(?=\n##|\Z)",
        r"##\s*Draft\s*\n(.*?)(?=\n##|\Z)",
        r"##\s*Message\s*\n(.*?)(?=\n##|\Z)",
    ]
    for pat in patterns:
        m = re.search(pat, content, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()
            if text:
                return text

    # Fallback: use everything after frontmatter, stripping markdown headers
    body = re.sub(r"^---.*?---\s*", "", content, count=1, flags=re.DOTALL)
    # Remove markdown headers
    body = re.sub(r"^#+\s+.*$", "", body, flags=re.MULTILINE)
    body = body.strip()
    return body or fm.get("topic", "Business update")


def _generate_marketing_post(task_content: str, fm: dict[str, str]) -> str:
    """
    Generate a business marketing post from the task content.

    Uses the task body directly if it looks like a complete post,
    otherwise wraps it with LinkedIn-friendly formatting.
    """
    post_text = _extract_post_content(task_content, fm)

    # If the content already looks like a ready post (has enough substance)
    if len(post_text) > 50:
        return post_text

    # Build a LinkedIn post from the topic/content
    topic = fm.get("topic", "business update")
    brand = fm.get("brand", "our team")
    hashtags = fm.get("hashtags", "#Business #Growth #Innovation")

    post = (
        f"{post_text}\n\n"
        f"---\n"
        f"{hashtags}\n"
    )
    return post.strip()


def _call_mcp(endpoint: str, data: dict | None = None) -> dict:
    """Call the LinkedIn MCP server."""
    url = f"{MCP_BASE_URL}{endpoint}"
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.readable() else str(e)
        return {"success": False, "error": error_body, "status_code": e.code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _mcp_health() -> bool:
    """Check if the MCP server is running."""
    try:
        req = urllib.request.Request(f"{MCP_BASE_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status") == "ok"
    except Exception:
        return False


class LinkedInWatcher(BaseWatcher):
    """
    Watches /Needs_Action for social_post tasks with platform: linkedin.

    Processing flow:
    1. Detect social_post file with platform: linkedin
    2. Generate a marketing post from the content
    3. If approval_required: true → move to /Pending_Approval
    4. After approval → send to MCP server for publishing
    """

    def __init__(self):
        super().__init__(
            check_interval=int(os.getenv("LINKEDIN_CHECK_INTERVAL", "15"))
        )
        self._processed_files: set[str] = set()
        self._shutting_down = False

    def check_for_updates(self) -> list[Path]:
        """Return list of LinkedIn social_post files in /Needs_Action."""
        needs_action = Config.NEEDS_ACTION_PATH
        if not needs_action.exists():
            return []

        results = []
        for f in needs_action.iterdir():
            if not f.is_file() or f.suffix != ".md":
                continue
            if str(f) in self._processed_files:
                continue
            try:
                content = f.read_text(encoding="utf-8")
                if _is_linkedin_post_task(content):
                    results.append(f)
            except Exception as exc:
                self.logger.warning(f"Could not read {f.name}: {exc}")

        return results

    def create_action_file(self, item: Path) -> Path | None:
        """Process a LinkedIn post task."""
        self._processed_files.add(str(item))

        content = item.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)

        self.logger.info(f"Processing LinkedIn post task: {item.name}")

        # Generate the marketing post
        post_text = _generate_marketing_post(content, fm)
        self.logger.info(f"Generated post ({len(post_text)} chars)")

        approval_required = fm.get("approval_required", "true").lower() == "true"

        if approval_required:
            # Move to /Pending_Approval with the generated post appended
            return self._send_to_approval(item, content, fm, post_text)
        else:
            # Directly publish (rare — most social posts need approval)
            return self._publish_post(item, post_text)

    def _send_to_approval(
        self, item: Path, content: str, fm: dict, post_text: str
    ) -> Path | None:
        """Move task to /Pending_Approval with generated post content."""
        PENDING_APPROVAL.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        new_content = content.rstrip() + f"""

---

## Generated LinkedIn Post (Pending Approval)

**Generated at**: {now.strftime("%Y-%m-%d %H:%M:%S")} UTC
**Character count**: {len(post_text)}

### Post Preview:

{post_text}

---

**To Approve**: Move this file to `/Approved`
**To Reject**: Move this file to `/Rejected`
"""

        dest = PENDING_APPROVAL / item.name
        dest.write_text(new_content, encoding="utf-8")

        # Remove from Needs_Action
        item.unlink()

        log_action(
            action_type="linkedin_post_pending_approval",
            actor="LinkedInWatcher",
            target=str(dest),
            parameters={
                "source_file": item.name,
                "post_length": len(post_text),
                "topic": fm.get("topic", "N/A"),
            },
            approval_status="pending",
            result="success",
        )

        self.logger.info(f"Moved to Pending_Approval: {dest.name}")
        return dest

    def _publish_post(self, item: Path, post_text: str) -> Path | None:
        """Send post to MCP server for publishing."""
        # Check if MCP server is running
        if not _mcp_health():
            self.logger.error("MCP LinkedIn server is not running")
            log_action(
                action_type="linkedin_publish_failed",
                actor="LinkedInWatcher",
                target=item.name,
                parameters={"error": "MCP server not running"},
                result="error",
            )
            return None

        # Create the draft on MCP server
        draft_result = _call_mcp("/linkedin_create_post", {
            "text": post_text,
            "source_file": item.name,
        })

        if not draft_result.get("success"):
            self.logger.error(f"Failed to create draft: {draft_result.get('error')}")
            return None

        # Publish the draft
        publish_result = _call_mcp("/linkedin_publish_post", {"confirm": True})

        if publish_result.get("success"):
            self.logger.info(f"Post published! Moving {item.name} to /Done")

            DONE.mkdir(parents=True, exist_ok=True)
            dest = DONE / item.name
            item.rename(dest)

            log_action(
                action_type="linkedin_post_published",
                actor="LinkedInWatcher",
                target=str(dest),
                parameters={
                    "post_length": len(post_text),
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                approval_status="auto",
                result="success",
            )
            return dest
        else:
            self.logger.error(f"Failed to publish: {publish_result.get('error')}")
            log_action(
                action_type="linkedin_publish_failed",
                actor="LinkedInWatcher",
                target=item.name,
                parameters={"error": str(publish_result.get("error", "unknown"))},
                result="error",
            )
            return None

    def _shutdown(self, signum=None, frame=None):
        if self._shutting_down:
            return
        self._shutting_down = True
        sig_name = signal.Signals(signum).name if signum else "manual"
        self.logger.info(f"Shutdown requested ({sig_name}).")
        log_action(
            action_type="watcher_shutdown",
            actor="LinkedInWatcher",
            target="watcher",
            parameters={"signal": sig_name},
            result="success",
        )

    def run(self):
        """Start polling for LinkedIn post tasks."""
        self.logger.info(
            f"Starting LinkedIn Watcher "
            f"(interval={self.check_interval}s, dry_run={self.dry_run})"
        )

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        while not self._shutting_down:
            try:
                items = self.check_for_updates()
                if items:
                    self.logger.info(f"Found {len(items)} LinkedIn post task(s)")
                for item in items:
                    if self.dry_run:
                        self._processed_files.add(str(item))
                        self.logger.info(
                            f"[DRY RUN] Would process LinkedIn post: {item.name}"
                        )
                        log_action(
                            action_type="linkedin_post_detected",
                            actor="LinkedInWatcher",
                            target=str(item),
                            result="dry_run_skipped",
                        )
                    else:
                        self.create_action_file(item)
            except Exception as e:
                self.logger.error(f"Error in LinkedIn watcher loop: {e}", exc_info=True)
                log_action(
                    action_type="watcher_loop_error",
                    actor="LinkedInWatcher",
                    target="main_loop",
                    parameters={"error": str(e)},
                    result="error",
                )

            time.sleep(self.check_interval)

        self.logger.info(
            f"LinkedIn Watcher stopped. "
            f"Processed {len(self._processed_files)} item(s)."
        )


class LinkedInApprovalHandler:
    """
    Handles approved LinkedIn posts from /Approved.

    When the ApprovalWatcher detects an approved social_post file,
    this handler sends it to the MCP server for publishing.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        setup_logging()

    def handle_approved_post(self, filepath: Path) -> bool:
        """
        Process an approved LinkedIn post file.

        1. Read the file and extract the generated post text
        2. Send to MCP server for publishing
        3. Move to /Done
        """
        content = filepath.read_text(encoding="utf-8")

        # Check if this is a LinkedIn post
        if not _is_linkedin_post_task(content):
            return False

        # Extract the generated post from the approval file
        post_match = re.search(
            r"### Post Preview:\s*\n(.*?)(?=\n---|\Z)",
            content,
            re.DOTALL,
        )
        if not post_match:
            self.logger.error(f"No post preview found in {filepath.name}")
            return False

        post_text = post_match.group(1).strip()

        # Publish via MCP
        if not _mcp_health():
            self.logger.error("MCP server not running — cannot publish approved post")
            return False

        draft = _call_mcp("/linkedin_create_post", {
            "text": post_text,
            "source_file": filepath.name,
        })
        if not draft.get("success"):
            self.logger.error(f"Draft creation failed: {draft.get('error')}")
            return False

        result = _call_mcp("/linkedin_publish_post", {"confirm": True})
        if result.get("success"):
            self.logger.info(f"Approved post published: {filepath.name}")

            DONE.mkdir(parents=True, exist_ok=True)
            dest = DONE / filepath.name
            filepath.rename(dest)

            log_action(
                action_type="linkedin_post_published",
                actor="LinkedInApprovalHandler",
                target=str(dest),
                parameters={
                    "post_length": len(post_text),
                    "approved_by": "human",
                },
                approval_status="approved",
                approved_by="human",
                result="success",
            )
            return True
        else:
            self.logger.error(f"Publish failed: {result.get('error')}")
            return False


def main():
    """Entry point for the LinkedIn watcher."""
    errors = Config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Ensure directories exist
    for d in ("Pending_Approval", "Approved", "Rejected", "Done"):
        (Config.VAULT_PATH / d).mkdir(parents=True, exist_ok=True)

    if Config.DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE — LinkedIn posts will be detected but not published.")
        print("=" * 60)

    watcher = LinkedInWatcher()
    watcher.run()


if __name__ == "__main__":
    main()
