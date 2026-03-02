---
name: tool-integration
description: Extensible tool layer wrapping external APIs (Gmail, LinkedIn, etc.) with DRY_RUN, HITL approval, and audit logging.
version: "0.1.0"
---

# Tool Integration Layer

Lightweight API wrappers as tool modules — replaces MCP protocol with a simple Python-native approach.

---

## Triggers

Use this skill when:
- "use gmail tool"
- "post to linkedin"
- "list available tools"
- "check tool status"

---

## Architecture

```
src/tools/
├── __init__.py        # Package exports + registry access
├── base_tool.py       # BaseTool ABC + ToolResult dataclass
├── registry.py        # Central tool registry (get/register/list)
├── gmail_tool.py      # Gmail: list_unread, read_email, send_email, search
└── linkedin_tool.py   # LinkedIn: create_post, get_profile, get_connections
```

---

## Quick Reference

| Tool | Actions | HITL Required |
|------|---------|:---:|
| **gmail** | list_unread, read_email, search | No |
| **gmail** | send_email | **Yes** |
| **linkedin** | get_profile, get_connections | No |
| **linkedin** | create_post | **Yes** |

---

## Usage

```python
from src.tools import registry

# Import tools to auto-register them
import src.tools.gmail_tool
import src.tools.linkedin_tool

# List available tools
registry.list_tools()        # ["gmail", "linkedin"]
registry.list_configured()   # ["gmail"]  (only configured ones)

# Execute an action
gmail = registry.get("gmail")
result = gmail.execute("list_unread", query="is:unread", max_results=5)

if result.success:
    print(result.data["count"], "unread emails")
else:
    print("Error:", result.error)

# Sensitive actions auto-route to HITL
result = gmail.execute("send_email", to="a@b.com", subject="Hi")
# result.success == False
# result.error == "Requires human approval — file created in /Pending_Approval"
```

---

## Adding a New Tool

1. Create `src/tools/my_tool.py`
2. Subclass `BaseTool`, implement: `name`, `_is_configured()`, `_execute()`, `list_actions()`
3. Auto-register at bottom: `registry.register(MyTool())`
4. Add env vars to `.env`
5. Write tests in `tests/test_tools.py`

```python
from src.tools.base_tool import BaseTool, ToolResult
from src.tools import registry

class MyTool(BaseTool):
    @property
    def name(self): return "my_tool"
    def _is_configured(self): return bool(os.getenv("MY_TOOL_KEY"))
    def list_actions(self): return ["do_thing"]
    def _execute(self, action, **params):
        return ToolResult(success=True, action=action, data={"done": True})

registry.register(MyTool())
```

---

## Safety

- **DRY_RUN**: All tools skip execution in dry-run mode (handled by BaseTool)
- **HITL**: Sensitive actions (send_email, create_post) auto-create approval requests
- **Never raises**: All exceptions caught at BaseTool level, returned as ToolResult
- **Audit trail**: Every execute() call logged to `/Logs/YYYY-MM-DD.json`
- **Graceful degradation**: Unconfigured tools return error result, never crash
