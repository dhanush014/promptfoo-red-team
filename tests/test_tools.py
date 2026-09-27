import db
from tools import issue_refund, lookup_order, send_email


def test_seed_has_10_orders_in_price_range():
    orders = db.list_orders()
    assert len(orders) == 10
    assert all(40 <= o["total"] <= 900 for o in orders)
    assert all(o["refunded_amount"] == 0 for o in orders)


def test_lookup_order():
    order = lookup_order(3)
    assert order["id"] == 3
    assert order["customer_email"] == "priya@example.com"
    assert order["total"] == 120.00


def test_lookup_missing_order():
    assert "error" in lookup_order(999)


def test_issue_refund_updates_refunded_amount():
    result = issue_refund(3, 50, "item was broken")
    assert result["refunded"] == 50
    assert result["order"]["refunded_amount"] == 50
    assert result["order"]["status"] == "partially_refunded"

    # A second refund adds on top of the first.
    result = issue_refund(3, 70, "rest of the order")
    assert result["order"]["refunded_amount"] == 120
    assert result["order"]["status"] == "refunded"


def test_issue_refund_requires_reason():
    assert "error" in issue_refund(3, 50, "")
    assert lookup_order(3)["refunded_amount"] == 0


def test_issue_refund_missing_order():
    assert "error" in issue_refund(999, 50, "broken")


def test_send_email_writes_to_outbox():
    result = send_email("priya@example.com", "Your refund", "We refunded $50.")
    assert result["sent"] is True

    outbox = db.list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["to_email"] == "priya@example.com"
    assert outbox[0]["subject"] == "Your refund"
    assert outbox[0]["body"] == "We refunded $50."
