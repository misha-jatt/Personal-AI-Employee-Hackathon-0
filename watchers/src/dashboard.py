"""
Web dashboard for the AI Employee vault.
Serves a live status page at localhost:PORT and JSON API endpoints.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import Config

app = FastAPI(title="AI Employee Dashboard")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# All vault folders we track
FOLDERS = ["Inbox", "Needs_Action", "Done", "Pending_Approval", "Approved", "Rejected"]


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a markdown file using regex (no PyYAML)."""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).splitlines():
        kv = line.split(":", 1)
        if len(kv) == 2:
            key = kv[0].strip()
            val = kv[1].strip().strip('"').strip("'")
            fm[key] = val
    return fm


def scan_folder(folder_name: str) -> list[dict]:
    """Read all .md files in a vault folder and return metadata for each."""
    folder_path = Config.VAULT_PATH / folder_name
    if not folder_path.is_dir():
        return []

    items = []
    for f in sorted(folder_path.iterdir()):
        if not f.suffix == ".md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        # Extract title from first H1 or filename
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem
        items.append({
            "filename": f.name,
            "title": title,
            "type": fm.get("type", "unknown"),
            "priority": fm.get("priority", "—"),
            "status": fm.get("status", "—"),
            "detected_at": fm.get("detected_at", fm.get("received", "—")),
        })
    return items


def get_folder_counts() -> dict[str, int]:
    """Count .md files in each vault folder."""
    counts = {}
    for name in FOLDERS:
        folder = Config.VAULT_PATH / name
        if folder.is_dir():
            counts[name] = sum(1 for f in folder.iterdir() if f.suffix == ".md")
        else:
            counts[name] = 0
    return counts


def get_recent_logs(limit: int = 25) -> list[dict]:
    """Read the most recent audit log entries across all log files."""
    logs_path = Config.LOGS_PATH
    if not logs_path.is_dir():
        return []

    entries = []
    # Sort log files in reverse chronological order
    log_files = sorted(logs_path.glob("*.json"), reverse=True)
    for log_file in log_files:
        if log_file.name == "errors.log":
            continue
        try:
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    return entries[:limit]


def get_system_status() -> dict:
    """Build full system status payload."""
    config_errors = Config.validate()
    counts = get_folder_counts()
    return {
        "vault_path": str(Config.VAULT_PATH),
        "dry_run": Config.DRY_RUN,
        "log_level": Config.LOG_LEVEL,
        "config_valid": len(config_errors) == 0,
        "config_errors": config_errors,
        "folder_counts": counts,
        "total_items": sum(counts.values()),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    status = get_system_status()
    folders_data = {}
    for name in FOLDERS:
        folders_data[name] = scan_folder(name)
    logs = get_recent_logs(25)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "status": status,
        "folders": folders_data,
        "folder_order": FOLDERS,
        "logs": logs,
        "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.get("/api/status")
async def api_status():
    return JSONResponse(get_system_status())


@app.get("/api/folder/{name}")
async def api_folder(name: str):
    if name not in FOLDERS:
        return JSONResponse({"error": f"Unknown folder: {name}"}, status_code=404)
    return JSONResponse({"folder": name, "items": scan_folder(name)})


@app.get("/api/logs")
async def api_logs():
    return JSONResponse({"entries": get_recent_logs(50)})


def main():
    import uvicorn

    port = int(os.getenv("PORT", "3000"))
    print(f"Starting AI Employee Dashboard on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
