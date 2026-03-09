"""
LinkedIn MCP Server — FastAPI server that exposes LinkedIn browser automation
as MCP-compatible endpoints using Playwright.

Endpoints:
  GET  /              — Landing page with server info
  GET  /health        — Health check
  POST /linkedin_login       — Open browser and log in to LinkedIn
  POST /linkedin_create_post — Draft a post (store in memory)
  POST /linkedin_publish_post — Publish a previously created post
  POST /linkedin_logout      — Close the browser session

Workflow:
  Orchestrator -> MCP Server -> Playwright -> LinkedIn

Runs on localhost (default port 3001).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Load env from watchers/.env
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp_linkedin_server")

# ---------------------------------------------------------------------------
# Vault paths
# ---------------------------------------------------------------------------
VAULT_PATH = Path(os.getenv("VAULT_PATH", "D:/AI_Employee_Vault"))
LOGS_PATH = VAULT_PATH / "Logs"
LINKEDIN_LOG_FILE = LOGS_PATH / "linkedin_logs.md"

# ---------------------------------------------------------------------------
# LinkedIn credentials from env
# ---------------------------------------------------------------------------
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# ---------------------------------------------------------------------------
# Browser session state
# ---------------------------------------------------------------------------
_browser = None
_context = None
_page = None
_playwright = None
_draft_post: dict[str, Any] | None = None
_logged_in: bool = False


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def _log_to_file(action: str, status: str, details: str = "") -> None:
    """Append a structured log entry to /Logs/linkedin_logs.md."""
    LOGS_PATH.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"| {now} | {action} | {status} | {details} |\n"

    if not LINKEDIN_LOG_FILE.exists():
        header = (
            "# LinkedIn Automation Logs\n\n"
            "| Timestamp | Action | Status | Details |\n"
            "|-----------|--------|--------|---------|\n"
        )
        LINKEDIN_LOG_FILE.write_text(header, encoding="utf-8")

    with open(LINKEDIN_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def _log_json(action: str, status: str, data: dict | None = None) -> None:
    """Also write a JSON log line to the daily audit log."""
    try:
        from .audit_logger import log_action
        log_action(
            action_type=f"linkedin_{action}",
            actor="mcp_linkedin_server",
            target="linkedin",
            parameters=data or {},
            result=status,
        )
    except Exception:
        pass  # Audit log is best-effort


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------
async def _ensure_playwright():
    """Import and launch Playwright (async)."""
    global _playwright, _browser, _context, _page

    if _browser is not None:
        return

    from playwright.async_api import async_playwright

    _playwright = await async_playwright().__aenter__()
    _browser = await _playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    )
    _context = await _browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    _page = await _context.new_page()
    logger.info("Playwright Chromium browser launched (headless=False).")


async def _close_browser():
    """Cleanly close browser and playwright."""
    global _browser, _context, _page, _playwright, _logged_in

    if _page:
        try:
            await _page.close()
        except Exception:
            pass
        _page = None
    if _context:
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.__aexit__(None, None, None)
        except Exception:
            pass
        _playwright = None
    _logged_in = False
    logger.info("Browser session closed.")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await _close_browser()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="LinkedIn MCP Server",
    description="MCP-compatible LinkedIn browser automation via Playwright",
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Root + health
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {
        "server": "LinkedIn MCP Server",
        "version": "0.2.0",
        "status": "running",
        "browser_active": _browser is not None,
        "logged_in": _logged_in,
        "has_draft": _draft_post is not None,
        "endpoints": {
            "POST /linkedin_login": "Open browser and log in to LinkedIn",
            "POST /linkedin_create_post": "Draft a post (text required)",
            "POST /linkedin_publish_post": "Publish the drafted post via browser",
            "POST /linkedin_logout": "Close browser session",
            "GET  /health": "Health check",
            "GET  /docs": "Interactive Swagger UI",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "browser_active": _browser is not None,
        "logged_in": _logged_in,
        "has_draft": _draft_post is not None,
    }


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


class CreatePostRequest(BaseModel):
    text: str
    source_file: str | None = None


class PublishPostRequest(BaseModel):
    confirm: bool = True


class MCPResponse(BaseModel):
    success: bool
    action: str
    message: str
    data: dict = {}


# ---------------------------------------------------------------------------
# POST /linkedin_login
# ---------------------------------------------------------------------------
@app.post("/linkedin_login", response_model=MCPResponse)
async def linkedin_login(req: LoginRequest | None = None):
    """Open Chromium and log in to LinkedIn."""
    global _logged_in

    email = (req.email if req and req.email else LINKEDIN_EMAIL)
    password = (req.password if req and req.password else LINKEDIN_PASSWORD)

    if not email or not password:
        _log_to_file("login", "ERROR", "Missing credentials")
        raise HTTPException(status_code=400, detail="Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env")

    try:
        await _ensure_playwright()
        logger.info(f"Navigating to LinkedIn login page...")

        await _page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        await _page.wait_for_timeout(2000)

        # Fill login form
        await _page.fill('#username', email)
        await _page.fill('#password', password)
        await _page.wait_for_timeout(500)

        # Click sign in
        await _page.click('button[type="submit"]')
        logger.info("Login form submitted, waiting for navigation...")

        # Wait for page to settle — could be feed, challenge, or error
        await _page.wait_for_timeout(5000)

        current_url = _page.url
        logger.info(f"Post-login URL: {current_url}")

        if "feed" in current_url or "mynetwork" in current_url or "in/" in current_url:
            _logged_in = True
            _log_to_file("login", "SUCCESS", f"Logged in as {email}")
            _log_json("login", "success", {"email": email})
            return MCPResponse(
                success=True, action="linkedin_login",
                message=f"Logged in as {email}",
                data={"url": current_url},
            )
        elif "challenge" in current_url or "checkpoint" in current_url:
            _log_to_file("login", "CHALLENGE", "Verification required — complete it in the browser")
            _log_json("login", "challenge", {"url": current_url})
            return MCPResponse(
                success=False, action="linkedin_login",
                message="Security challenge detected. Complete it in the open browser window, then call /linkedin_login again.",
                data={"url": current_url, "status": "challenge"},
            )
        else:
            # May still be logged in — check for feed elements
            feed_el = _page.locator('[data-view-name="feed-root"], .scaffold-layout__main')
            try:
                await feed_el.first.wait_for(timeout=5000)
                _logged_in = True
                _log_to_file("login", "SUCCESS", f"Logged in as {email} (detected via feed)")
                return MCPResponse(
                    success=True, action="linkedin_login",
                    message=f"Logged in as {email}",
                    data={"url": current_url},
                )
            except Exception:
                _log_to_file("login", "UNKNOWN", f"URL: {current_url}")
                return MCPResponse(
                    success=False, action="linkedin_login",
                    message=f"Login outcome unclear. Check the browser. URL: {current_url}",
                    data={"url": current_url},
                )

    except Exception as exc:
        _log_to_file("login", "ERROR", str(exc)[:200])
        _log_json("login", "error", {"error": str(exc)[:200]})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /linkedin_create_post
# ---------------------------------------------------------------------------
@app.post("/linkedin_create_post", response_model=MCPResponse)
async def linkedin_create_post(req: CreatePostRequest):
    """Draft a LinkedIn post (stored in server memory, not yet published)."""
    global _draft_post

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Post text cannot be empty")
    if len(req.text) > 3000:
        raise HTTPException(status_code=400, detail=f"Post too long ({len(req.text)} chars, max 3000)")

    _draft_post = {
        "text": req.text,
        "source_file": req.source_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _log_to_file("create_post", "DRAFTED", f"Length: {len(req.text)} chars")
    _log_json("create_post", "drafted", {"text_preview": req.text[:100], "length": len(req.text)})

    return MCPResponse(
        success=True, action="linkedin_create_post",
        message=f"Post drafted ({len(req.text)} chars). Call /linkedin_publish_post to publish.",
        data={"text_preview": req.text[:100], "char_count": len(req.text)},
    )


# ---------------------------------------------------------------------------
# POST /linkedin_publish_post
# ---------------------------------------------------------------------------
@app.post("/linkedin_publish_post", response_model=MCPResponse)
async def linkedin_publish_post(req: PublishPostRequest | None = None):
    """Publish the drafted post via Playwright browser automation."""
    global _draft_post

    if _draft_post is None:
        raise HTTPException(status_code=400, detail="No draft post. Call /linkedin_create_post first.")
    if _page is None or not _logged_in:
        raise HTTPException(status_code=400, detail="Not logged in. Call /linkedin_login first.")

    text = _draft_post["text"]

    try:
        logger.info("Navigating to LinkedIn feed to create post...")
        await _page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        await _page.wait_for_timeout(3000)

        # --- Step 1: Click "Start a post" ---
        # LinkedIn has multiple possible selectors for the post trigger
        start_selectors = [
            'button.artdeco-button:has-text("Start a post")',
            'button:has-text("Start a post")',
            '.share-box-feed-entry__trigger',
            '.share-box-feed-entry__top-bar',
            'div[data-view-name="feed-top-card"] button',
        ]
        clicked = False
        for sel in start_selectors:
            try:
                loc = _page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    clicked = True
                    logger.info(f"Clicked start-post with selector: {sel}")
                    break
            except Exception:
                continue

        if not clicked:
            raise RuntimeError("Could not find 'Start a post' button. LinkedIn UI may have changed.")

        await _page.wait_for_timeout(2000)

        # --- Step 2: Type into the post editor ---
        editor_selectors = [
            'div.ql-editor[data-placeholder]',
            'div[role="textbox"][contenteditable="true"]',
            'div.ql-editor[contenteditable="true"]',
            '.editor-content div[contenteditable="true"]',
        ]
        typed = False
        for sel in editor_selectors:
            try:
                loc = _page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    await _page.wait_for_timeout(500)
                    # Use keyboard.type for more reliable input
                    await _page.keyboard.type(text, delay=10)
                    typed = True
                    logger.info(f"Typed post content with selector: {sel}")
                    break
            except Exception:
                continue

        if not typed:
            raise RuntimeError("Could not find post editor. LinkedIn UI may have changed.")

        await _page.wait_for_timeout(1500)

        # --- Step 3: Click the "Post" button ---
        post_selectors = [
            'button.share-actions__primary-action',
            'button:has-text("Post"):not(:has-text("Repost"))',
            'button[data-control-name="share.post"]',
        ]
        posted = False
        for sel in post_selectors:
            try:
                loc = _page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    posted = True
                    logger.info(f"Clicked Post button with selector: {sel}")
                    break
            except Exception:
                continue

        if not posted:
            raise RuntimeError("Could not find Post/Submit button. LinkedIn UI may have changed.")

        # Wait for post submission
        await _page.wait_for_timeout(4000)
        logger.info("Post submitted! Waiting for confirmation...")

        _log_to_file(
            "publish_post", "SUCCESS",
            f"Published {len(text)} chars | Source: {_draft_post.get('source_file', 'N/A')}",
        )
        _log_json("publish_post", "success", {
            "text_preview": text[:100],
            "length": len(text),
            "source_file": _draft_post.get("source_file"),
        })

        result_data = {
            "text_preview": text[:100],
            "char_count": len(text),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source_file": _draft_post.get("source_file"),
        }
        _draft_post = None
        return MCPResponse(
            success=True, action="linkedin_publish_post",
            message="Post published successfully to LinkedIn.",
            data=result_data,
        )

    except Exception as exc:
        _log_to_file("publish_post", "ERROR", str(exc)[:300])
        _log_json("publish_post", "error", {"error": str(exc)[:300]})
        raise HTTPException(status_code=500, detail=f"Publish failed: {exc}")


# ---------------------------------------------------------------------------
# POST /linkedin_logout
# ---------------------------------------------------------------------------
@app.post("/linkedin_logout", response_model=MCPResponse)
async def linkedin_logout():
    """Close the browser session."""
    try:
        await _close_browser()
        _log_to_file("logout", "SUCCESS", "Browser session closed")
        _log_json("logout", "success")
        return MCPResponse(success=True, action="linkedin_logout", message="Logged out. Browser closed.")
    except Exception as exc:
        _log_to_file("logout", "ERROR", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """Start the LinkedIn MCP server."""
    import uvicorn
    port = int(os.getenv("MCP_LINKEDIN_PORT", "3001"))
    print(f"\n{'='*60}")
    print(f"  LinkedIn MCP Server starting on http://127.0.0.1:{port}")
    print(f"  Swagger docs: http://127.0.0.1:{port}/docs")
    print(f"  Vault path: {VAULT_PATH}")
    print(f"  Credentials: {'SET' if LINKEDIN_EMAIL else 'MISSING'}")
    print(f"{'='*60}\n")
    _log_to_file("server_start", "INFO", f"Port {port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
