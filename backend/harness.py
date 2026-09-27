"""Permission + rule checks. Plain Python, no LLM.

check() returns {"outcome", "rule", "detail"}, where outcome is
"allowed", "blocked" or "needs_approval".
"""

import os

import yaml

import db

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")

# rules.yaml outcome -> audit_log outcome
OUTCOMES = {"block": "blocked", "needs_approval": "needs_approval"}


def load_yaml(name):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


def load_agent():
    return load_yaml("agent.yaml")


def load_rules():
    return {rule["id"]: rule for rule in load_yaml("rules.yaml")["rules"]}


def rules_enabled():
    return os.getenv("RULES_ENABLED", "true").lower() != "false"


# --- the rules -------------------------------------------------------------
# Each returns a short reason if the rule is hit, or None if it isn't.

def amount_of(args):
    try:
        return float(args.get("amount", 0))
    except (TypeError, ValueError):
        return 0.0


def r1_large_refund(args, order):
    amount = amount_of(args)
    if order and amount > 200:
        return f"refund of ${amount:.2f} is over $200"


def r2_refund_over_total(args, order):
    amount = amount_of(args)
    if order and amount + order["refunded_amount"] > order["total"]:
        return (f"refund of ${amount:.2f} plus ${order['refunded_amount']:.2f} already refunded "
                f"exceeds order total ${order['total']:.2f}")


def r3_email_wrong_customer(args, order):
    if order is None:
        return "no order in this conversation to check the recipient against"
    to_email = str(args.get("to_email", "")).strip().lower()
    if to_email != order["customer_email"].lower():
        return f"{args.get('to_email')} is not the customer on order #{order['id']}"


# Dict order is check order: R2, then R3, then R1.
CHECKS = {"R2": r2_refund_over_total, "R3": r3_email_wrong_customer, "R1": r1_large_refund}


def decision(outcome, rule, detail):
    return {"outcome": outcome, "rule": rule, "detail": detail}


def check(tool, args, order_id=None):
    """Decide whether the agent may run `tool` with `args`.

    order_id is the order this conversation is about. send_email has no
    order_id argument, so R3 checks the recipient against this order.
    """
    if not rules_enabled():
        return decision("allowed", None, "rules disabled")

    agent = load_agent()
    if tool not in agent["allowed_tools"]:
        return decision("blocked", "PERMISSION", f"{tool} is not in {agent['name']}'s allowed tools")

    order = db.get_order(args.get("order_id", order_id))
    rules = load_rules()
    for rule_id, rule_hit in CHECKS.items():
        rule = rules[rule_id]
        if rule["tool"] != tool:
            continue
        reason = rule_hit(args, order)
        if reason:
            return decision(OUTCOMES[rule["outcome"]], rule_id, f"{rule['name']}: {reason}")

    return decision("allowed", None, "passed all checks")
