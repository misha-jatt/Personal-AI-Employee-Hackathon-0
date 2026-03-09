"""
LinkedIn Automation Orchestrator — single entry point that runs the full pipeline.

Starts the MCP server, then continuously monitors /Needs_Action for LinkedIn
social_post tasks and executes the complete flow:

  1. Detect task file (type: social_post, platform: linkedin)
  2. Generate post content from the task body
  3. Call MCP server → Playwright opens Chromium → LinkedIn login
  4. Draft post → Publish post → Logout
  5. Move task file to /Done
  6. Log results to /Logs/linkedin_logs.md

One command to run:
    cd watchers && uv run orchestrate

This replaces the need to start MCP server and watcher separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from dotenv import load_dotenv

# Load env FIRST before any other imports
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VAULT_PATH = Path(os.getenv("VAULT_PATH", "D:/AI_Employee_Vault"))
NEEDS_ACTION = VAULT_PATH / "Needs_Action"
DONE = VAULT_PATH / "Done"
LOGS_PATH = VAULT_PATH / "Logs"
LINKEDIN_LOG = LOGS_PATH / "linkedin_logs.md"

MCP_PORT = int(os.getenv("MCP_LINKEDIN_PORT", "3001"))
MCP_BASE = f"http://127.0.0.1:{MCP_PORT}"
POLL_INTERVAL = int(os.getenv("LINKEDIN_CHECK_INTERVAL", "10"))

# Track what we've already processed this session
_processed: set[str] = set()
_shutting_down = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter into a dict."""
    fm: dict[str, str] = {}
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def _is_linkedin_task(content: str) -> bool:
    fm = _parse_frontmatter(content)
    return (
        fm.get("type", "").lower() == "social_post"
        and fm.get("platform", "").lower() == "linkedin"
    )


def _extract_post_text(content: str, fm: dict[str, str]) -> str:
    """Pull the post body from the markdown file."""
    # Look for ## Post Content section
    for pat in [
        r"##\s*Post\s*Content\s*\n(.*?)(?=\n##|\Z)",
        r"##\s*Content\s*\n(.*?)(?=\n##|\Z)",
        r"##\s*Draft\s*\n(.*?)(?=\n##|\Z)",
    ]:
        m = re.search(pat, content, re.DOTALL | re.IGNORECASE)
        if m and m.group(1).strip():
            return m.group(1).strip()

    # Fallback: everything after frontmatter, minus headers
    body = re.sub(r"^---.*?---\s*", "", content, count=1, flags=re.DOTALL)
    body = re.sub(r"^#+\s+.*$", "", body, flags=re.MULTILINE).strip()
    return body or fm.get("topic", "Business update")


def _call_mcp(endpoint: str, data: dict | None = None, method: str = "POST") -> dict:
    """HTTP call to the MCP server."""
    url = f"{MCP_BASE}{endpoint}"
    body = json.dumps(data or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode() if hasattr(e, "read") else str(e)
        return {"success": False, "error": err, "status_code": e.code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _mcp_ready() -> bool:
    try:
        r = _call_mcp("/health", method="GET")
        return r.get("status") == "ok"
    except Exception:
        return False


def _log_linkedin(action: str, status: str, details: str = ""):
    """Append to /Logs/linkedin_logs.md."""
    LOGS_PATH.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"| {now} | {action} | {status} | {details} |\n"
    if not LINKEDIN_LOG.exists():
        LINKEDIN_LOG.write_text(
            "# LinkedIn Automation Logs\n\n"
            "| Timestamp | Action | Status | Details |\n"
            "|-----------|--------|--------|---------|\n",
            encoding="utf-8",
        )
    with open(LINKEDIN_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# MCP Server — run in a background thread
# ---------------------------------------------------------------------------
def _start_mcp_server():
    """Start the FastAPI MCP server in a daemon thread."""
    import uvicorn
    from .mcp_linkedin_server import app

    logger.info(f"Starting MCP server on port {MCP_PORT}...")
    uvicorn.run(app, host="127.0.0.1", port=MCP_PORT, log_level="warning")


# ---------------------------------------------------------------------------
# Core pipeline: process one LinkedIn task
# ---------------------------------------------------------------------------
def _process_task(filepath: Path) -> bool:
    """
    Full pipeline for one task file:
      login → create_post → publish_post → logout → move to /Done
    """
    logger.info(f"{'='*60}")
    logger.info(f"PROCESSING: {filepath.name}")
    logger.info(f"{'='*60}")

    content = filepath.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)
    post_text = _extract_post_text(content, fm)

    if not post_text:
        logger.error(f"No post content found in {filepath.name}")
        _log_linkedin("process", "ERROR", f"No content in {filepath.name}")
        return False

    logger.info(f"Post text ({len(post_text)} chars): {post_text[:80]}...")

    # --- Step 1: Login ---
    logger.info("Step 1/4: Logging in to LinkedIn...")
    login_resp = _call_mcp("/linkedin_login")
    if not login_resp.get("success"):
        # Check if it's a challenge — give user time
        if login_resp.get("data", {}).get("status") == "challenge":
            logger.warning("Security challenge detected! Complete it in the browser...")
            logger.warning("Waiting 60 seconds for you to complete the challenge...")
            time.sleep(60)
            # Retry login
            login_resp = _call_mcp("/linkedin_login")
            if not login_resp.get("success"):
                logger.error(f"Login failed after challenge: {login_resp.get('message')}")
                _log_linkedin("login", "FAILED", login_resp.get("message", "unknown"))
                return False
        else:
            logger.error(f"Login failed: {login_resp.get('message') or login_resp.get('error')}")
            _log_linkedin("login", "FAILED", str(login_resp.get("error", "")))
            return False

    logger.info(f"Login: {login_resp.get('message')}")

    # --- Step 2: Create draft ---
    logger.info("Step 2/4: Creating post draft...")
    draft_resp = _call_mcp("/linkedin_create_post", {
        "text": post_text,
        "source_file": filepath.name,
    })
    if not draft_resp.get("success"):
        logger.error(f"Draft failed: {draft_resp.get('error')}")
        _log_linkedin("create_post", "FAILED", str(draft_resp.get("error", "")))
        _call_mcp("/linkedin_logout")
        return False

    logger.info(f"Draft: {draft_resp.get('message')}")

    # --- Step 3: Publish ---
    logger.info("Step 3/4: Publishing post...")
    pub_resp = _call_mcp("/linkedin_publish_post", {"confirm": True})
    if not pub_resp.get("success"):
        logger.error(f"Publish failed: {pub_resp.get('error')}")
        _log_linkedin("publish_post", "FAILED", str(pub_resp.get("error", ""))[:200])
        _call_mcp("/linkedin_logout")
        return False

    logger.info(f"Publish: {pub_resp.get('message')}")

    # --- Step 4: Logout ---
    logger.info("Step 4/4: Logging out...")
    _call_mcp("/linkedin_logout")

    # --- Move to /Done ---
    DONE.mkdir(parents=True, exist_ok=True)
    dest = DONE / filepath.name
    filepath.rename(dest)
    logger.info(f"Moved {filepath.name} → /Done")

    _log_linkedin(
        "full_pipeline", "SUCCESS",
        f"Published {len(post_text)} chars from {filepath.name}",
    )

    logger.info(f"{'='*60}")
    logger.info(f"SUCCESS — Post published from {filepath.name}")
    logger.info(f"{'='*60}")
    return True


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------
def _poll_loop():
    """Continuously scan /Needs_Action for LinkedIn tasks."""
    global _shutting_down

    logger.info(f"Polling {NEEDS_ACTION} every {POLL_INTERVAL}s for LinkedIn tasks...")

    while not _shutting_down:
        try:
            if not NEEDS_ACTION.exists():
                time.sleep(POLL_INTERVAL)
                continue

            for f in sorted(NEEDS_ACTION.iterdir()):
                if _shutting_down:
                    break
                if not f.is_file() or f.suffix != ".md":
                    continue
                if str(f) in _processed:
                    continue

                try:
                    content = f.read_text(encoding="utf-8")
                except Exception:
                    continue

                if not _is_linkedin_task(content):
                    continue

                _processed.add(str(f))
                logger.info(f"Found LinkedIn task: {f.name}")

                # Wait for MCP server to be ready
                retries = 0
                while not _mcp_ready() and retries < 15:
                    logger.info("Waiting for MCP server to be ready...")
                    time.sleep(2)
                    retries += 1

                if not _mcp_ready():
                    logger.error("MCP server not ready after 30s. Skipping.")
                    _log_linkedin("orchestrator", "ERROR", "MCP server not ready")
                    continue

                _process_task(f)

        except Exception as exc:
            logger.error(f"Poll loop error: {exc}", exc_info=True)

        time.sleep(POLL_INTERVAL)


def _shutdown(signum=None, frame=None):
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("Shutting down orchestrator...")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """
    Start the LinkedIn automation orchestrator.

    Launches MCP server in a background thread, then runs the poll loop.
    """
    global _shutting_down

    print()
    print("=" * 60)
    print("  LinkedIn Automation Orchestrator")
    print("=" * 60)
    print(f"  Vault:       {VAULT_PATH}")
    print(f"  Needs_Action: {NEEDS_ACTION}")
    print(f"  MCP Server:  http://127.0.0.1:{MCP_PORT}")
    print(f"  Poll:        every {POLL_INTERVAL}s")
    print(f"  Credentials: {'SET' if os.getenv('LINKEDIN_EMAIL') else 'MISSING!'}")
    print("=" * 60)
    print()

    # Ensure vault directories exist
    for d in ("Needs_Action", "Done", "Logs", "Pending_Approval", "Approved", "Rejected"):
        (VAULT_PATH / d).mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start MCP server in background thread
    server_thread = Thread(target=_start_mcp_server, daemon=True)
    server_thread.start()

    # Give the server time to boot
    logger.info("Waiting for MCP server to start...")
    for _ in range(20):
        if _mcp_ready():
            break
        time.sleep(1)

    if _mcp_ready():
        logger.info(f"MCP server is UP on http://127.0.0.1:{MCP_PORT}")
    else:
        logger.error("MCP server failed to start! Check for port conflicts.")
        sys.exit(1)

    # Run the poll loop (blocks until shutdown)
    _poll_loop()

    logger.info("Orchestrator stopped.")


if __name__ == "__main__":
    main()
