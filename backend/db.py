"""SQLite setup, seed data, and queries."""

import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "warden.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY,
    customer_email  TEXT NOT NULL,
    item            TEXT NOT NULL,
    total           REAL NOT NULL,
    refunded_amount REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    order_id     INTEGER,
    tool         TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    amount       REAL,
    rule         TEXT,
    agent_reason TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    time       TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    thread_id  TEXT,
    order_id   INTEGER,
    tool       TEXT NOT NULL,
    args_json  TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    rule       TEXT,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS outbox (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    time     TEXT NOT NULL,
    to_email TEXT NOT NULL,
    subject  TEXT NOT NULL,
    body     TEXT NOT NULL
);
"""

# 10 fake orders, totals between $40 and $900.
SEED_ORDERS = [
    (1, "jane@example.com", "Wireless headphones", 149.00),
    (2, "omar@example.com", "Running shoes", 89.50),
    (3, "priya@example.com", "Coffee grinder", 120.00),
    (4, "liam@example.com", "Office chair", 350.00),
    (5, "sofia@example.com", "Yoga mat", 40.00),
    (6, "chen@example.com", "Standing desk", 890.00),
    (7, "amara@example.com", "Winter jacket", 215.00),
    (8, "diego@example.com", "Bluetooth speaker", 65.99),
    (9, "hana@example.com", "Espresso machine", 640.00),
    (10, "noah@example.com", "Backpack", 72.25),
]


def db_path():
    return os.getenv("WARDEN_DB", DEFAULT_DB_PATH)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables and seed orders if the orders table is empty."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO orders (id, customer_email, item, total, refunded_amount, status)"
                " VALUES (?, ?, ?, ?, 0, 'delivered')",
                SEED_ORDERS,
            )


def reset_db():
    """Wipe all data, including saved conversations (LangGraph checkpoints), and reseed. For demos."""
    tables = ("orders", "tickets", "audit_log", "outbox", "checkpoints", "writes", "sqlite_sequence")
    with connect() as conn:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in tables:
            if table in existing:
                conn.execute(f"DELETE FROM {table}")
    init_db()


# --- queries ---------------------------------------------------------------

def get_order(order_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def add_refund(order_id, amount):
    """Add amount to refunded_amount and update status. Returns the updated order."""
    order = get_order(order_id)
    new_refunded = order["refunded_amount"] + amount
    status = "refunded" if new_refunded >= order["total"] else "partially_refunded"
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET refunded_amount = ?, status = ? WHERE id = ?",
            (new_refunded, status, order_id),
        )
    return get_order(order_id)


def insert_outbox(to_email, subject, body):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO outbox (time, to_email, subject, body) VALUES (?, ?, ?, ?)",
            (now(), to_email, subject, body),
        )
        return cur.lastrowid


def list_outbox():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM outbox ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def log_audit(agent_name, thread_id, order_id, tool, args, outcome, rule=None, detail=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (time, agent_name, thread_id, order_id, tool, args_json,"
            " outcome, rule, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), agent_name, thread_id, order_id, tool, json.dumps(args), outcome, rule, detail),
        )


def list_audit(thread_id=None):
    """Audit rows, newest first. Optionally only one conversation's rows."""
    sql = "SELECT * FROM audit_log"
    params = ()
    if thread_id is not None:
        sql += " WHERE thread_id = ?"
        params = (thread_id,)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [dict(r) for r in rows]


def create_ticket(thread_id, order_id, tool, args, rule):
    """A paused action waiting for a human. The refund's reason becomes the agent's reason."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (thread_id, order_id, tool, args_json, amount, rule, agent_reason,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (thread_id, order_id, tool, json.dumps(args), args.get("amount"), rule,
             args.get("reason"), now()),
        )
        return cur.lastrowid


def get_ticket(ticket_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def list_tickets(status=None):
    sql = "SELECT * FROM tickets"
    params = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [dict(r) for r in rows]


def decide_ticket(ticket_id, status):
    """Mark a pending ticket approved/denied. Returns False if it wasn't pending."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tickets SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, now(), ticket_id),
        )
        return cur.rowcount == 1


def set_ticket_status(ticket_id, status):
    with connect() as conn:
        conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))


def list_orders():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
    return [dict(r) for r in rows]
