"""harness.check(): permission first, then R2, R3, R1. Plain Python, no model."""

from harness import check
from tools import issue_refund

# Seed data used here:
#   order 3: priya@example.com, total $120
#   order 4: liam@example.com,  total $350


def test_lookup_is_allowed():
    assert check("lookup_order", {"order_id": 3})["outcome"] == "allowed"


def test_small_refund_is_allowed():
    result = check("issue_refund", {"order_id": 3, "amount": 50, "reason": "broken"})
    assert result == {"outcome": "allowed", "rule": None, "detail": "passed all checks"}


def test_unknown_tool_is_blocked():
    result = check("delete_records", {"order_id": 3})
    assert result["outcome"] == "blocked"
    assert result["rule"] == "PERMISSION"


def test_r1_refund_over_200_needs_approval():
    result = check("issue_refund", {"order_id": 4, "amount": 300, "reason": "damaged"})
    assert result["outcome"] == "needs_approval"
    assert result["rule"] == "R1"


def test_r1_refund_of_exactly_200_is_allowed():
    assert check("issue_refund", {"order_id": 4, "amount": 200, "reason": "damaged"})["outcome"] == "allowed"


def test_r2_refund_over_order_total_is_blocked():
    result = check("issue_refund", {"order_id": 3, "amount": 150, "reason": "broken"})
    assert result["outcome"] == "blocked"
    assert result["rule"] == "R2"


def test_r2_is_checked_before_r1():
    # $5000 is over $200 (R1) AND over the total (R2). R2 wins: blocked, not approval.
    result = check("issue_refund", {"order_id": 3, "amount": 5000, "reason": "broken"})
    assert result["outcome"] == "blocked"
    assert result["rule"] == "R2"


def test_r2_counts_earlier_refunds():
    issue_refund(3, 100, "broken")
    result = check("issue_refund", {"order_id": 3, "amount": 30, "reason": "more"})
    assert result["rule"] == "R2"
    assert check("issue_refund", {"order_id": 3, "amount": 20, "reason": "more"})["outcome"] == "allowed"


def test_r3_email_to_the_orders_customer_is_allowed():
    args = {"to_email": "priya@example.com", "subject": "Refund", "body": "Done"}
    assert check("send_email", args, order_id=3)["outcome"] == "allowed"


def test_r3_email_to_someone_else_is_blocked():
    args = {"to_email": "x@evil.com", "subject": "Order data", "body": "..."}
    result = check("send_email", args, order_id=3)
    assert result["outcome"] == "blocked"
    assert result["rule"] == "R3"


def test_r3_email_with_no_order_is_blocked():
    args = {"to_email": "priya@example.com", "subject": "Hi", "body": "..."}
    assert check("send_email", args)["rule"] == "R3"


def test_rules_disabled_allows_everything(monkeypatch):
    monkeypatch.setenv("RULES_ENABLED", "false")
    assert check("issue_refund", {"order_id": 3, "amount": 5000, "reason": "x"})["outcome"] == "allowed"
    assert check("issue_refund", {"order_id": 4, "amount": 300, "reason": "x"})["outcome"] == "allowed"
    assert check("send_email", {"to_email": "x@evil.com", "subject": "", "body": ""})["outcome"] == "allowed"
    assert check("delete_records", {})["outcome"] == "allowed"
