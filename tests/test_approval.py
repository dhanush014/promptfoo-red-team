"""Approval pause/resume through the API, with a scripted fake model."""

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

import agent
import db
from test_agent import ScriptedModel, tool_call

build_graph = agent.build_graph  # the real one, before tests patch it

REFUND_300 = {"order_id": 4, "amount": 300, "reason": "arrived damaged"}  # order 4 total $350


def start_app(monkeypatch, responses):
    """(Re)start app.py the way uvicorn would, with a fake model and a fresh DB connection."""
    conn = sqlite3.connect(db.db_path(), check_same_thread=False)
    graph = build_graph(model=ScriptedModel(responses), checkpointer=SqliteSaver(conn))
    monkeypatch.setattr(agent, "build_graph", lambda: graph)
    import app
    return TestClient(importlib.reload(app).app)


def audit(thread_id):
    return [(r["tool"], r["outcome"], r["rule"]) for r in reversed(db.list_audit(thread_id))]


@pytest.fixture
def paused(monkeypatch):
    """A conversation paused on a $300 refund. Returns (client, ticket)."""
    client = start_app(monkeypatch, [
        tool_call("issue_refund", REFUND_300),
        AIMessage(content="Refunds over $200 need a manager's OK. I've sent it for approval."),
        AIMessage(content="Approved! $300 refunded."),       # used if approved
    ])
    res = client.post("/chat", json={"thread_id": "t1", "message": "Refund $300 on order 4"}).json()
    assert res == {
        "reply": "Refunds over $200 need a manager's OK. I've sent it for approval.",
        "status": "waiting_approval",
        "actions": [],
    }
    tickets = client.get("/tickets", params={"status": "pending"}).json()
    assert len(tickets) == 1
    return client, tickets[0]


def test_ticket_is_created_and_nothing_runs(paused):
    client, ticket = paused
    assert ticket["thread_id"] == "t1"
    assert ticket["order_id"] == 4
    assert ticket["tool"] == "issue_refund"
    assert ticket["amount"] == 300
    assert ticket["rule"] == "R1"
    assert ticket["agent_reason"] == "arrived damaged"
    assert ticket["status"] == "pending"
    assert db.get_order(4)["refunded_amount"] == 0

    detail = client.get(f"/tickets/{ticket['id']}").json()
    assert detail["order"]["customer_email"] == "liam@example.com"
    assert [(a["tool"], a["outcome"]) for a in detail["audit"]] == [("issue_refund", "needs_approval")]

    history = client.get("/chat/t1").json()
    assert history["status"] == "waiting_approval"
    assert [m["role"] for m in history["messages"]] == ["customer", "agent"]


def test_chatting_while_paused_does_not_run_the_agent(paused):
    client, _ = paused
    res = client.post("/chat", json={"thread_id": "t1", "message": "hello?"}).json()
    assert res["status"] == "waiting_approval"
    assert res["actions"] == []


def test_approve_flow(paused):
    client, ticket = paused
    res = client.post(f"/tickets/{ticket['id']}/approve").json()

    assert res["ticket"]["status"] == "approved"
    assert res["ticket"]["decided_at"] is not None
    assert res["reply"] == "Approved! $300 refunded."
    assert res["status"] == "done"
    assert res["actions"] == [{"tool": "issue_refund", "args": REFUND_300}]
    assert db.get_order(4)["refunded_amount"] == 300
    assert audit("t1") == [
        ("issue_refund", "needs_approval", "R1"),
        ("issue_refund", "approved", "R1"),
        ("issue_refund", "executed", None),
    ]

    history = client.get("/chat/t1").json()
    assert history["status"] == "done"
    assert [m["content"] for m in history["messages"]] == [
        "Refund $300 on order 4",
        "Refunds over $200 need a manager's OK. I've sent it for approval.",
        "Approved! $300 refunded.",
    ]
    assert client.get("/tickets", params={"status": "pending"}).json() == []


def test_deny_flow(monkeypatch):
    client = start_app(monkeypatch, [
        tool_call("issue_refund", REFUND_300),
        AIMessage(content="I've sent it for approval."),
        AIMessage(content="Sorry, the manager denied the refund."),
    ])
    client.post("/chat", json={"thread_id": "t1", "message": "Refund $300 on order 4"})
    ticket = client.get("/tickets").json()[0]

    res = client.post(f"/tickets/{ticket['id']}/deny").json()
    assert res["ticket"]["status"] == "denied"
    assert res["reply"] == "Sorry, the manager denied the refund."
    assert res["status"] == "done"
    assert res["actions"] == []
    assert db.get_order(4)["refunded_amount"] == 0
    assert audit("t1") == [
        ("issue_refund", "needs_approval", "R1"),
        ("issue_refund", "denied", "R1"),
    ]


def test_agent_is_told_the_decision(paused):
    client, ticket = paused
    client.post(f"/tickets/{ticket['id']}/deny")
    # GET /chat hides the notice from the customer, so check the raw graph state.
    import app
    messages = app.graph.get_state(agent.config_for("t1")).values["messages"]
    notice = messages[-2]
    assert notice.name == agent.NOTICE
    assert notice.content.startswith(f"[Warden] A manager denied ticket #{ticket['id']}")


def test_ticket_can_only_be_decided_once(paused):
    client, ticket = paused
    assert client.post(f"/tickets/{ticket['id']}/approve").status_code == 200
    assert client.post(f"/tickets/{ticket['id']}/approve").status_code == 409
    assert client.post(f"/tickets/{ticket['id']}/deny").status_code == 409
    assert db.get_order(4)["refunded_amount"] == 300  # refunded once, not twice


def test_unknown_ticket_is_404(paused):
    client, _ = paused
    assert client.get("/tickets/999").status_code == 404
    assert client.post("/tickets/999/approve").status_code == 404


def test_resume_works_after_restarting_the_app(monkeypatch):
    client = start_app(monkeypatch, [
        tool_call("issue_refund", REFUND_300),
        AIMessage(content="I've sent it for approval."),
    ])
    client.post("/chat", json={"thread_id": "t1", "message": "Refund $300 on order 4"})
    ticket_id = client.get("/tickets").json()[0]["id"]

    # "Restart": new app module, new graph object, new DB connection. Only the SQLite file is shared.
    client = start_app(monkeypatch, [AIMessage(content="Approved! $300 refunded.")])
    assert client.get("/chat/t1").json()["status"] == "waiting_approval"

    res = client.post(f"/tickets/{ticket_id}/approve").json()
    assert res["reply"] == "Approved! $300 refunded."
    assert res["status"] == "done"
    assert db.get_order(4)["refunded_amount"] == 300


def test_agent_card(paused):
    client, _ = paused
    card = client.get("/agent").json()
    assert card["name"] == "RefundBot-01"
    assert card["supervisor"] == "ops-manager"
    assert card["allowed_tools"] == ["lookup_order", "issue_refund", "send_email"]


def test_audit_is_newest_first(paused):
    client, ticket = paused
    client.post(f"/tickets/{ticket['id']}/approve")
    outcomes = [r["outcome"] for r in client.get("/audit").json()]
    assert outcomes == ["executed", "approved", "needs_approval"]


def test_approval_rechecks_r2_with_current_data(monkeypatch):
    # Order 4 total is $350. $300 waits for approval, meanwhile another
    # conversation refunds $100. Approving the $300 would now exceed the total.
    client = start_app(monkeypatch, [
        tool_call("issue_refund", REFUND_300),                                          # t1
        AIMessage(content="I've sent it for approval."),                                # t1
        tool_call("issue_refund", {"order_id": 4, "amount": 100, "reason": "scratch"}),  # t2
        AIMessage(content="Refunded $100."),                                            # t2
        AIMessage(content="Sorry, that refund can no longer be issued."),               # t1 resumed
    ])
    client.post("/chat", json={"thread_id": "t1", "message": "Refund $300 on order 4"})
    ticket_id = client.get("/tickets").json()[0]["id"]
    client.post("/chat", json={"thread_id": "t2", "message": "Refund $100 on order 4"})
    assert db.get_order(4)["refunded_amount"] == 100

    res = client.post(f"/tickets/{ticket_id}/approve").json()

    assert res["ticket"]["status"] == "blocked_on_approval"
    assert res["actions"] == []
    assert res["reply"] == "Sorry, that refund can no longer be issued."
    assert db.get_order(4)["refunded_amount"] == 100  # the $300 did not run
    assert audit("t1") == [
        ("issue_refund", "needs_approval", "R1"),
        ("issue_refund", "approved", "R1"),
        ("issue_refund", "blocked", "R2"),
    ]
    assert db.list_audit("t1")[0]["detail"].startswith("Re-checked on approval: Refund cannot exceed")

    import app
    notice = app.graph.get_state(agent.config_for("t1")).values["messages"][-2]
    assert "blocked when re-checked by R2" in notice.content


def test_ticket_detail_has_rule_name(paused):
    client, ticket = paused
    assert client.get(f"/tickets/{ticket['id']}").json()["rule_name"] == "Large refund needs approval"


def test_demo_reset_wipes_and_reseeds(paused):
    client, ticket = paused
    client.post(f"/tickets/{ticket['id']}/approve")
    assert db.get_order(4)["refunded_amount"] == 300

    assert client.post("/demo/reset").json() == {"ok": True}

    assert db.get_order(4)["refunded_amount"] == 0
    assert len(db.list_orders()) == 10
    assert client.get("/tickets").json() == []
    assert client.get("/audit").json() == []
    assert client.get("/chat/t1").json() == {"thread_id": "t1", "messages": [], "status": "done"}
