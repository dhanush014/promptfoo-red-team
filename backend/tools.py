"""The agent's tools, as plain Python functions.

Each tool returns a dict. Problems come back as {"error": "..."} so the
agent can read them and respond, instead of crashing the run.
"""

import db


def lookup_order(order_id: int) -> dict:
    """Look up an order by its id and return its details."""
    order = db.get_order(order_id)
    if order is None:
        return {"error": f"Order {order_id} not found"}
    return order


def issue_refund(order_id: int, amount: float, reason: str) -> dict:
    """Refund `amount` dollars on an order. `reason` explains why the refund is given."""
    if not reason or not reason.strip():
        return {"error": "A reason is required for every refund"}
    if amount <= 0:
        return {"error": "Refund amount must be positive"}
    if db.get_order(order_id) is None:
        return {"error": f"Order {order_id} not found"}
    order = db.add_refund(order_id, amount)
    return {"refunded": amount, "reason": reason, "order": order}


def send_email(to_email: str, subject: str, body: str) -> dict:
    """Send an email to a customer. (Fake: written to the outbox table, never sent.)"""
    outbox_id = db.insert_outbox(to_email, subject, body)
    return {"sent": True, "outbox_id": outbox_id, "to_email": to_email}
