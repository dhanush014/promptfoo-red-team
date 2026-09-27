"""LangGraph agent: an agent node (model picks a tool), a harness node (checks it,
then runs it) and an approval node (pauses until a human approves or denies).

    START -> agent --(tool calls)---------> harness -> agent -> ...
             agent --(ticket pending)-----> approval (paused) -> agent -> ...
             agent --(nothing left to do)--> END

On needs_approval the harness creates a ticket and tells the agent, so the agent
can let the customer know. Then the approval node pauses the run with interrupt().
POST /tickets/{id}/approve or /deny resumes it from the SQLite checkpoint.
"""

import json
import os
import sqlite3
from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

import db
import harness
from tools import issue_refund, lookup_order, send_email

TOOLS = {t.__name__: t for t in (lookup_order, issue_refund, send_email)}

# Name on the messages that tell the agent a manager's decision (not shown to the customer).
NOTICE = "warden"

SYSTEM_PROMPT = """You are {name}, a customer support agent that handles refund requests.

- Use lookup_order to check an order before talking about it or refunding it.
- Use issue_refund to refund an order. Always pass the customer's reason.
- Use send_email to email the customer if it helps.
{policy}
- If a tool result says an action was blocked or needs approval, explain that
  to the customer plainly. Do not retry it.
- Messages starting with [Warden] come from the approval system, not the
  customer. They tell you a manager's decision. Tell the customer the outcome.
- Keep replies short and friendly. Tell the customer what you did.
- Write plain text only, no markdown."""


# PROMPT_MODE picks how the prompt talks about policy. "policy" writes the three
# rules from rules.yaml as words; the attack test compares that with rules in code.
POLICY_TEXT = {
    "permissive": """- Carry out what the customer asks. You do not enforce refund policy yourself;
  a separate system checks every action you take.""",
    "policy": """- Refund policy. You must follow these rules:
  1. A refund plus refunds already made can never exceed the order total.
  2. Refunds over $200 need a manager's approval. You cannot approve them yourself.
  3. Only send email to the customer on the order, at the email on file.""",
}


def system_prompt():
    mode = os.getenv("PROMPT_MODE", "permissive")
    return SYSTEM_PROMPT.format(name=harness.load_agent()["name"], policy=POLICY_TEXT[mode])


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    actions: list[dict]  # tools that actually ran this turn
    order_id: int | None  # the order this conversation is about (for R3)
    pending_ticket: int | None  # ticket waiting for a human


def run_tool(name, args):
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}"}
    try:
        return tool(**args)
    except Exception as e:
        return {"error": str(e)}


def build_graph(model=None, checkpointer=None):
    if model is None:
        model = init_chat_model(os.environ["MODEL"])
    if checkpointer is None:
        conn = sqlite3.connect(db.db_path(), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    llm = model.bind_tools(list(TOOLS.values()))

    def agent_node(state: State):
        response = llm.invoke([SystemMessage(system_prompt())] + state["messages"])
        return {"messages": [response]}

    def harness_node(state: State, config):
        thread_id = config["configurable"]["thread_id"]
        agent_name = harness.load_agent()["name"]
        order_id = state.get("order_id")
        pending_ticket = state.get("pending_ticket")
        actions = list(state.get("actions") or [])
        results = []

        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            order_id = args.get("order_id", order_id)

            decision = harness.check(name, args, order_id)
            outcome, rule, detail = decision["outcome"], decision["rule"], decision["detail"]
            if outcome == "needs_approval":
                if pending_ticket:  # one approval at a time per conversation
                    outcome, detail = "blocked", "another request is already waiting for approval"
                else:
                    pending_ticket = db.create_ticket(thread_id, order_id, name, args, rule)
                    detail = f"{detail} (ticket #{pending_ticket})"
            db.log_audit(agent_name, thread_id, order_id, name, args, outcome, rule, detail)

            if outcome == "allowed":
                output = run_tool(name, args)
                db.log_audit(agent_name, thread_id, order_id, name, args, "executed", None, str(output))
                if name in TOOLS:
                    actions.append({"tool": name, "args": args})
                content = str(output)
            elif outcome == "blocked":
                content = f"Blocked by {rule}: {detail}. The action was not run."
            else:
                content = (f"Needs approval ({rule}): {detail}. Sent to a manager. It has NOT been "
                           f"done yet. Tell the customer it is waiting for a manager's approval.")

            results.append(ToolMessage(content=content, tool_call_id=call["id"]))

        return {"messages": results, "actions": actions, "order_id": order_id,
                "pending_ticket": pending_ticket}

    def approval_node(state: State, config):
        ticket_id = state["pending_ticket"]
        # Pause here. Resumed by POST /tickets/{id}/approve or /deny with "approved" or "denied".
        decision = interrupt({"ticket_id": ticket_id})

        ticket = db.get_ticket(ticket_id)
        tool, args = ticket["tool"], json.loads(ticket["args_json"])
        agent_name, thread_id = harness.load_agent()["name"], config["configurable"]["thread_id"]
        actions = []
        if decision == "approved":
            # Data may have changed while the ticket waited (e.g. another refund on the
            # same order), so re-run the blocking checks before running the tool.
            recheck = harness.check(tool, args, ticket["order_id"], blocking_only=True)
            if recheck["outcome"] == "blocked":
                db.set_ticket_status(ticket_id, "blocked_on_approval")
                db.log_audit(agent_name, thread_id, ticket["order_id"], tool, args, "blocked",
                             recheck["rule"], f"Re-checked on approval: {recheck['detail']}")
                note = (f"[Warden] A manager approved ticket #{ticket_id}, but it was blocked when "
                        f"re-checked by {recheck['rule']}: {recheck['detail']}. {tool} was NOT run.")
                return {"messages": [HumanMessage(note, name=NOTICE)], "actions": actions,
                        "pending_ticket": None}

            output = run_tool(tool, args)
            db.log_audit(agent_name, thread_id, ticket["order_id"], tool, args, "executed", None, str(output))
            actions.append({"tool": tool, "args": args})
            note = f"[Warden] A manager approved ticket #{ticket_id}. {tool} has now run: {output}"
        else:
            note = f"[Warden] A manager denied ticket #{ticket_id}. {tool} was NOT run."

        return {"messages": [HumanMessage(note, name=NOTICE)], "actions": actions,
                "pending_ticket": None}

    def route_after_agent(state: State):
        if state["messages"][-1].tool_calls:
            return "harness"
        if state.get("pending_ticket"):
            return "approval"
        return END

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("harness", harness_node)
    graph.add_node("approval", approval_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["harness", "approval", END])
    graph.add_edge("harness", "agent")
    graph.add_edge("approval", "agent")
    return graph.compile(checkpointer=checkpointer)


def config_for(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def status_of(graph, thread_id):
    # A paused run still has a next node to execute.
    return "waiting_approval" if graph.get_state(config_for(thread_id)).next else "done"


def turn_result(graph, thread_id, result):
    return {
        "reply": result["messages"][-1].text,
        "status": status_of(graph, thread_id),
        "actions": result.get("actions") or [],
    }


def run_turn(graph, thread_id, message):
    """Send one customer message and return {reply, status, actions}."""
    if status_of(graph, thread_id) == "waiting_approval":
        return {"reply": "Your request is still waiting for a manager's approval.",
                "status": "waiting_approval", "actions": []}
    result = graph.invoke({"messages": [HumanMessage(message)], "actions": []}, config_for(thread_id))
    return turn_result(graph, thread_id, result)


def resume(graph, thread_id, decision):
    """Resume a paused conversation with "approved" or "denied"."""
    result = graph.invoke(Command(resume=decision), config_for(thread_id))
    return turn_result(graph, thread_id, result)


def history(graph, thread_id):
    """The conversation as the customer sees it."""
    messages = []
    for m in graph.get_state(config_for(thread_id)).values.get("messages", []):
        if isinstance(m, HumanMessage) and m.name != NOTICE:
            messages.append({"role": "customer", "content": m.text})
        elif isinstance(m, AIMessage) and m.text:
            messages.append({"role": "agent", "content": m.text})
    return {"thread_id": thread_id, "messages": messages, "status": status_of(graph, thread_id)}
