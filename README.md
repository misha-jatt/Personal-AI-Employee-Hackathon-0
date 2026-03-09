# Personal AI Employee — "Jarvis"

A local-first autonomous AI Employee for e-commerce operations. Built with Claude Code as the reasoning engine and Obsidian as the management dashboard.

**Hackathon**: Personal AI Employee Hackathon 0
**Tier**: Bronze (Foundation)
**Business**: Physical products e-commerce (Shopify/Amazon)

## Architecture

```
┌─────────────────────────────────────────────────┐
│               EXTERNAL SOURCES                   │
│   Gmail    │    File Drops    │   (Future: more) │
└─────┬──────┴────────┬─────────┴──────────────────┘
      │               │
      ▼               ▼
┌─────────────────────────────────────────────────┐
│            PERCEPTION LAYER (Watchers)           │
│  Gmail Watcher (Python)  │  FileSystem Watcher   │
│  Monitors inbox for      │  Monitors /Inbox for   │
│  orders, support, leads  │  file drops            │
└─────────────┬────────────┴───────────┬───────────┘
              │                        │
              ▼                        ▼
┌─────────────────────────────────────────────────┐
│          OBSIDIAN VAULT (Local Memory)           │
│                                                   │
│  /Inbox          — Raw file drops                 │
│  /Needs_Action   — Watcher output for processing  │
│  /Done           — Completed items archive         │
│  /Logs           — JSON audit logs                 │
│                                                   │
│  Dashboard.md       — Live status                  │
│  Company_Handbook.md — Business rules              │
│  AGENTS.md          — AI Employee instructions     │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│          REASONING LAYER (Claude Code)           │
│                                                   │
│  "Jarvis" — Professional Executive Assistant      │
│                                                   │
│  Skills:                                          │
│   • process-needs-action  — Triage & process      │
│   • update-dashboard      — Refresh Dashboard.md  │
│   • triage-classify       — Classify by priority   │
│   • vault-reader          — Quick vault briefing   │
└─────────────────────────────────────────────────┘
```

## Bronze Tier Deliverables

| # | Requirement | Status |
|---|------------|--------|
| 1 | Obsidian vault with Dashboard.md and Company_Handbook.md | Done |
| 2 | Working Watcher scripts (FileSystem + Gmail) | FileSystem done, Gmail in progress |
| 3 | Claude Code reading from and writing to vault | Done |
| 4 | Folder structure: /Inbox, /Needs_Action, /Done | Done |
| 5 | All AI functionality as Agent Skills | 4 skills defined |
| 6 | .env usage, dry-run support, audit logging | Done |

## Folder Structure

```
AI_Employee_Vault/
├── AGENTS.md                     # AI Employee persona & instructions
├── README.md                     # This file
├── Dashboard.md                  # Live vault status
├── Company_Handbook.md           # Business rules & SOPs
├── requirements.md               # Hackathon spec (reference)
├── .env.example                  # Environment template
├── .gitignore                    # Secrets exclusion
│
├── Inbox/                        # Drop files here
├── Needs_Action/                 # Watcher output → Claude reads
├── Done/                         # Completed items archive
├── Logs/                         # JSON audit logs (YYYY-MM-DD.json)
│
├── .agents/skills/               # Agent Skill definitions
│   ├── process-needs-action/     # Triage & process items
│   ├── update-dashboard/         # Refresh Dashboard.md
│   ├── triage-classify/          # Classify items by priority (to build)
│   └── vault-reader/             # Quick vault state briefing (to build)
│
└── watchers/                     # Python perception layer
    ├── pyproject.toml            # UV project config
    ├── .env.example              # Watcher env template
    ├── src/
    │   ├── config.py             # Environment loader
    │   ├── audit_logger.py       # Structured JSON logging
    │   ├── base_watcher.py       # Abstract base class
    │   ├── filesystem_watcher.py # Monitors /Inbox for file drops
    │   └── gmail_watcher.py      # Monitors Gmail (to build)
    └── tests/
        └── test_filesystem_watcher.py
```

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Claude Code | Latest | Reasoning engine (Jarvis) |
| Obsidian | 1.10.6+ | Knowledge base & dashboard |
| Python | 3.13+ | Watcher scripts |
| UV | Latest | Python package manager |
| Git | Latest | Version control |

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/misha-jatt/Personal-AI-Employee-Hackathon-0.git
cd Personal-AI-Employee-Hackathon-0

# 2. Open as Obsidian vault
# Open Obsidian → "Open folder as vault" → select this directory

# 3. Set up the watcher
cd watchers
cp .env.example .env        # Edit with your paths
uv venv && uv pip install -e ".[dev]"

# 4. Start the FileSystem watcher
uv run watch-inbox

# 5. In another terminal, start Claude Code
cd /path/to/AI_Employee_Vault
claude

# 6. Talk to Jarvis
> process inbox
> update dashboard
> what's pending
> summarize vault
```

## Gmail Watcher Setup

The Gmail Watcher requires Google Cloud credentials. Setup guide:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the **Gmail API**
4. Create OAuth 2.0 credentials (Desktop application)
5. Download `credentials.json`
6. Add to `watchers/.env`:
   ```
   GMAIL_CREDENTIALS_PATH=/path/to/credentials.json
   ```
7. On first run, a browser window opens for OAuth consent

Detailed setup instructions will be documented when the Gmail Watcher is built.

## Security

- **Credentials**: `.env` files only, never in markdown or git
- **Dry-run**: `DRY_RUN=true` by default — logs actions without executing
- **Audit logs**: Every action logged to `/Logs/YYYY-MM-DD.json` in structured JSON
- **No auto-payments**: All payment actions require human approval
- **Local-first**: All data stays on your machine inside the Obsidian vault

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `process-needs-action` | Scan /Needs_Action, classify priority, process and move to /Done |
| `update-dashboard` | Recount all folders, read logs, rewrite Dashboard.md |
| `triage-classify` | Read items and classify by priority + type without moving them |
| `vault-reader` | Quick briefing — full vault state summary on demand |

## What's Next (Silver Tier)

- Human-in-the-loop approval workflow (/Pending_Approval → /Approved)
- Plan.md generation for multi-step tasks
- MCP server for sending emails
- LinkedIn auto-posting
- Scheduling via cron/Task Scheduler

## License

Hackathon project — Personal AI Employee Hackathon 0 by Panaversity.
