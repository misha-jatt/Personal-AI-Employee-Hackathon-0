"""
Tests for the rule-based task classifier.

Coverage:
  - Priority: urgent / high / medium / low
  - Category: Urgent / Work / Personal / Idea
  - Due date: correct offset from today, None for low
  - Frontmatter stripping
  - Case insensitivity
  - Phrase signals (multi-word)
  - Word-boundary matching (no false positives on substrings)
  - Precedence rules (urgent beats high, idea beats personal, etc.)
  - Empty and whitespace-only input
  - Filename-only fallback content
"""

from datetime import date, timedelta

import pytest

from src.classifier import ClassificationResult, classify, _normalise, _any_match


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Priority classification
# ---------------------------------------------------------------------------

class TestPriorityUrgent:
    def test_urgent_keyword(self):
        result = classify("This is URGENT, please respond immediately.")
        assert result.priority == "high"  # urgent maps to high in public API

    def test_asap_keyword(self):
        result = classify("I need this done ASAP.")
        assert result.priority == "high"

    def test_where_is_my_order_phrase(self):
        result = classify("Hi, where is my order? It's been 2 weeks.")
        assert result.priority == "high"

    def test_not_received_phrase(self):
        result = classify("Package not received after 10 days.")
        assert result.priority == "high"

    def test_wrong_item(self):
        result = classify("I received the wrong item in my package.")
        assert result.priority == "high"

    def test_exchange_keyword(self):
        result = classify("I'd like to exchange my hoodie for a size L.")
        assert result.priority == "high"

    def test_refund_keyword(self):
        result = classify("Please process my refund for order #1234.")
        assert result.priority == "high"

    def test_invoice_overdue_phrase(self):
        result = classify("Your invoice overdue notice for $500.")
        assert result.priority == "high"


class TestPriorityHigh:
    def test_tracking_keyword(self):
        # tracking/shipment alone → medium (routine fulfilment update)
        # only urgent when paired with complaint signals
        result = classify("Can you share the tracking number for my shipment?")
        assert result.priority == "medium"

    def test_invoice_keyword(self):
        result = classify("Please send the invoice for January.")
        assert result.priority == "high"

    def test_deadline_keyword(self):
        result = classify("Reminder: project deadline is tomorrow.")
        assert result.priority == "high"

    def test_complaint_keyword(self):
        result = classify("I have a complaint about my recent order.")
        assert result.priority == "high"

    def test_due_today_phrase(self):
        result = classify("This report is due today.")
        assert result.priority == "high"

    def test_expiring_keyword(self):
        result = classify("Your subscription is expiring soon.")
        assert result.priority == "high"


class TestPriorityMedium:
    def test_work_signal_no_urgency(self):
        result = classify("New Shopify order confirmation for customer Jane Doe.")
        assert result.priority == "medium"

    def test_supplier_update(self):
        result = classify("Supplier has confirmed the shipment is being prepared.")
        assert result.priority == "medium"

    def test_business_report(self):
        result = classify("Weekly sales report attached for your review.")
        assert result.priority == "medium"

    def test_meeting_keyword(self):
        result = classify("Team meeting scheduled for next Monday.")
        assert result.priority == "medium"


class TestPriorityLow:
    def test_no_signals(self):
        result = classify("Just dropping this file here for later.")
        assert result.priority == "low"

    def test_empty_string(self):
        result = classify("")
        assert result.priority == "low"

    def test_whitespace_only(self):
        result = classify("   \n\t  ")
        assert result.priority == "low"

    def test_random_text(self):
        result = classify("Lorem ipsum dolor sit amet consectetur adipiscing.")
        assert result.priority == "low"


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

class TestCategoryUrgent:
    def test_urgent_signal_wins_over_work(self):
        result = classify("Urgent: customer refund needed for Shopify order.")
        assert result.category == "Urgent"

    def test_asap_categorised_urgent(self):
        result = classify("ASAP — wrong item delivered.")
        assert result.category == "Urgent"


class TestCategoryWork:
    def test_shopify_order(self):
        result = classify("New Shopify order #4821 received.")
        assert result.category == "Work"

    def test_default_for_unrecognised(self):
        result = classify("Some random document without signals.")
        assert result.category == "Work"

    def test_email_keyword(self):
        result = classify("Draft a reply email to the client.")
        assert result.category == "Work"

    def test_revenue_keyword(self):
        result = classify("Q1 revenue summary for the business.")
        assert result.category == "Work"


class TestCategoryPersonal:
    def test_birthday_keyword(self):
        result = classify("Reminder: mum's birthday is this Saturday.")
        assert result.category == "Personal"

    def test_health_keyword(self):
        result = classify("Doctor appointment booked for health checkup.")
        assert result.category == "Personal"

    def test_vacation_keyword(self):
        result = classify("Vacation plans for next month.")
        assert result.category == "Personal"

    def test_grocery_keyword(self):
        result = classify("Grocery list for the week.")
        assert result.category == "Personal"


class TestCategoryIdea:
    def test_idea_keyword(self):
        result = classify("New idea: add a loyalty points feature to the store.")
        assert result.category == "Idea"

    def test_brainstorm_keyword(self):
        result = classify("Let's brainstorm ways to improve checkout flow.")
        assert result.category == "Idea"

    def test_suggestion_keyword(self):
        result = classify("Suggestion from the team meeting: add live chat.")
        assert result.category == "Idea"

    def test_what_if_phrase(self):
        result = classify("What if we offered free shipping on orders over $40?")
        assert result.category == "Idea"

    def test_idea_beats_personal(self):
        # "improvement" + "home" — Idea should win
        result = classify("Idea for home improvement project.")
        assert result.category == "Idea"


# ---------------------------------------------------------------------------
# Due date
# ---------------------------------------------------------------------------

class TestDueDate:
    def test_urgent_due_today(self):
        result = classify("This is urgent, needs action now.")
        assert result.suggested_due_date == _today()

    def test_high_due_tomorrow(self):
        # Use a genuine high signal (invoice) not tracking alone
        result = classify("Please send the invoice for this month.")
        assert result.suggested_due_date == _in(1)

    def test_medium_due_in_3_days(self):
        result = classify("New Shopify order received from customer.")
        assert result.suggested_due_date == _in(3)

    def test_low_due_date_is_none(self):
        result = classify("Just a random note.")
        assert result.suggested_due_date is None

    def test_due_date_is_iso_format(self):
        result = classify("Urgent payment needed.")
        # Must match YYYY-MM-DD
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}", result.suggested_due_date)


# ---------------------------------------------------------------------------
# Frontmatter stripping
# ---------------------------------------------------------------------------

class TestFrontmatterStripping:
    def test_frontmatter_ignored_for_classification(self):
        # "priority: urgent" in frontmatter should NOT influence classification
        # body has no urgency signals
        content = """---
type: file_drop
priority: urgent
status: pending
---

New Shopify order received.
"""
        result = classify(content)
        # Body has "order" → Work/medium, not urgent
        assert result.category == "Work"
        assert result.priority == "medium"

    def test_body_signals_still_detected_after_frontmatter(self):
        content = """---
type: email
from: customer@example.com
---

Hi, where is my order? I placed it 2 weeks ago.
"""
        result = classify(content)
        assert result.priority == "high"
        assert result.category == "Urgent"


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_uppercase_urgent(self):
        assert classify("URGENT MATTER").priority == "high"

    def test_mixed_case_asap(self):
        assert classify("Please respond AsAp").priority == "high"

    def test_uppercase_tracking(self):
        # tracking alone is medium; uppercase should not change that
        assert classify("TRACKING NUMBER NEEDED").priority == "medium"

    def test_uppercase_idea(self):
        assert classify("NEW IDEA FOR STORE").category == "Idea"


# ---------------------------------------------------------------------------
# Word boundary matching (no false positives)
# ---------------------------------------------------------------------------

class TestWordBoundary:
    def test_order_in_disorder_no_match(self):
        # "disorder" contains "order" but should not trigger work signal
        # at word boundary
        result = classify("There is some disorder in the files.")
        # "disorder" — no word boundary match for "order" alone
        assert result.category == "Work"  # falls through to default Work
        # priority should be low (no clean signal)
        assert result.priority == "low"

    def test_help_in_helpful_no_urgent_match(self):
        # "helpful" should not match "help" urgency signal
        result = classify("That was really helpful, thank you!")
        # no real signals → low priority
        assert result.priority == "low"

    def test_idea_as_whole_word(self):
        result = classify("This is an idea worth exploring.")
        assert result.category == "Idea"


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------

class TestPrecedence:
    def test_urgent_beats_high(self):
        # Both urgent and high signals present — urgent wins
        result = classify("URGENT: tracking number missing for this order.")
        assert result.category == "Urgent"
        assert result.priority == "high"

    def test_urgent_beats_idea(self):
        result = classify("Urgent idea: refund process improvement.")
        assert result.category == "Urgent"

    def test_idea_beats_personal(self):
        result = classify("Idea for a family birthday gift subscription.")
        assert result.category == "Idea"


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_classification_result(self):
        result = classify("Test content.")
        assert isinstance(result, ClassificationResult)

    def test_all_fields_present(self):
        result = classify("Order received from customer.")
        assert hasattr(result, "category")
        assert hasattr(result, "priority")
        assert hasattr(result, "suggested_due_date")

    def test_priority_values_are_valid(self):
        for content in [
            "urgent matter",
            "tracking number",
            "shopify order",
            "random text",
        ]:
            result = classify(content)
            assert result.priority in ("high", "medium", "low")

    def test_category_values_are_valid(self):
        for content in [
            "urgent refund",
            "shopify order",
            "birthday party",
            "brainstorm idea",
            "random text",
        ]:
            result = classify(content)
            assert result.category in ("Urgent", "Work", "Personal", "Idea")


# ---------------------------------------------------------------------------
# Internal helpers (unit tests)
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_lowercases_text(self):
        assert _normalise("HELLO WORLD") == "hello world"

    def test_collapses_whitespace(self):
        assert _normalise("hello   \n  world") == "hello world"

    def test_strips_frontmatter(self):
        text = "---\nkey: value\n---\nBody text here."
        assert _normalise(text) == "body text here."

    def test_empty_string(self):
        assert _normalise("") == ""


class TestAnyMatch:
    def test_single_word_match(self):
        assert _any_match("the order has arrived", ["order"]) is True

    def test_phrase_match(self):
        assert _any_match("where is my order today", ["where is my order"]) is True  # noqa: E501 — wait, not in signals; use actual phrase

    def test_no_match(self):
        assert _any_match("hello world", ["urgent", "asap"]) is False

    def test_word_boundary_respected(self):
        assert _any_match("disorder is present", ["order"]) is False

    def test_phrase_not_present(self):
        assert _any_match("some other text", ["where is my order"]) is False
