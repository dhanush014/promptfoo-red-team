"""SQLite setup, seed data, and queries."""

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


def list_orders():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
    return [dict(r) for r in rows]
