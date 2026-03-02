"""
Rule-based task classifier for the AI Employee.

Classifies markdown content into:
  - category : Work | Personal | Urgent | Idea
  - priority  : high | medium | low
  - suggested_due_date : ISO date string or None

Rules are derived from Company_Handbook.md §3 (Priority Classification).
No external API is required — classification is instant, offline, and free.

DRY_RUN aware: when DRY_RUN=true the classifier still runs (read-only),
but the caller is responsible for not writing results to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Signal tables  (order matters — first match wins within each group)
# ---------------------------------------------------------------------------

_URGENT_SIGNALS: list[str] = [
    "urgent", "asap", "emergency", "critical", "immediately",
    "not received", "where is my order", "still no tracking",
    "wrong item", "wrong size", "exchange", "refund",
    "payment failed", "invoice overdue",
]

_HIGH_SIGNALS: list[str] = [
    "invoice", "payment", "complaint", "support", "help",
    "deadline", "due today", "due tomorrow", "expiring",
]

_WORK_SIGNALS: list[str] = [
    "order", "invoice", "shipment", "tracking", "customer",
    "supplier", "shopify", "amazon", "fulfillment", "payment",
    "client", "project", "meeting", "report", "proposal",
    "email", "linkedin", "business", "revenue", "sales",
]

_PERSONAL_SIGNALS: list[str] = [
    "personal", "family", "birthday", "appointment", "health",
    "grocery", "vacation", "home", "friend", "hobby",
]

_IDEA_SIGNALS: list[str] = [
    "idea", "concept", "brainstorm", "what if", "maybe we",
    "suggestion", "feature", "improvement", "consider",
]

# Due-date offsets by priority (calendar days from today)
# "low" is intentionally absent — no due date assigned for low-priority items
_DUE_DATE_OFFSET: dict[str, int] = {
    "urgent": 0,   # today
    "high":   1,   # tomorrow
    "medium": 3,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    category: str          # Work | Personal | Urgent | Idea
    priority: str          # high | medium | low
    suggested_due_date: str | None  # ISO date or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(content: str) -> ClassificationResult:
    """
    Classify markdown content.

    Args:
        content: Raw markdown text (frontmatter + body).

    Returns:
        ClassificationResult with category, priority, suggested_due_date.
    """
    normalised = _normalise(content)

    priority_tier = _detect_priority_tier(normalised)
    category      = _detect_category(normalised)
    due_date      = _suggest_due_date(priority_tier)

    # Map internal tier to public priority label
    priority = "high" if priority_tier in ("urgent", "high") else priority_tier

    return ClassificationResult(
        category=category,
        priority=priority,
        suggested_due_date=due_date,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip frontmatter delimiters."""
    text = re.sub(r"^---.*?---", "", text, flags=re.DOTALL)
    return " ".join(text.lower().split())


def _detect_priority_tier(text: str) -> str:
    """Return 'urgent', 'high', 'medium', or 'low'."""
    if _any_match(text, _URGENT_SIGNALS):
        return "urgent"
    if _any_match(text, _HIGH_SIGNALS):
        return "high"
    # Files with recognisable work content default to medium
    if _any_match(text, _WORK_SIGNALS):
        return "medium"
    return "low"


def _detect_category(text: str) -> str:
    """Return 'Urgent', 'Work', 'Personal', or 'Idea'."""
    if _any_match(text, _URGENT_SIGNALS):
        return "Urgent"
    if _any_match(text, _IDEA_SIGNALS):
        return "Idea"
    if _any_match(text, _PERSONAL_SIGNALS):
        return "Personal"
    return "Work"  # default for business vault


def _suggest_due_date(priority_tier: str) -> str | None:
    """Return ISO date string based on priority, or None for low priority."""
    offset = _DUE_DATE_OFFSET.get(priority_tier)
    if offset is None:
        return None
    return (date.today() + timedelta(days=offset)).isoformat()


def _any_match(text: str, signals: list[str]) -> bool:
    """Return True if any signal phrase appears as a word/phrase in text."""
    for signal in signals:
        # Use word-boundary match for single words, substring for phrases
        if " " in signal:
            if signal in text:
                return True
        else:
            if re.search(rf"\b{re.escape(signal)}\b", text):
                return True
    return False
