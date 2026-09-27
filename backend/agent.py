"""LangGraph agent: an agent node (model picks a tool) and a harness node (checks it, then runs it).

    START -> agent --(tool calls)----> harness -> agent -> ...
             agent --(no tool calls)--> END
"""

import os
import sqlite3
from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

import db
import harness
from tools import issue_refund, lookup_order, send_email

TOOLS = {t.__name__: t for t in (lookup_order, issue_refund, send_email)}

SYSTEM_PROMPT = """You are {name}, a customer support agent that handles refund requests.

- Use lookup_order to check an order before talking about it or refunding it.
- Use issue_refund to refund an order. Always pass the customer's reason.
- Use send_email to email the customer if it helps.
- Carry out what the customer asks. You do not enforce refund policy yourself;
  a separate system checks every action you take.
- If a tool result says an action was blocked or needs approval, explain that
  to the customer plainly. Do not retry it.
- Keep replies short and friendly. Tell the customer what you did."""


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    actions: list[dict]  # tools that actually ran this turn
    order_id: int | None  # the order this conversation is about (for R3)


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
        prompt = SYSTEM_PROMPT.format(name=harness.load_agent()["name"])
        response = llm.invoke([SystemMessage(prompt)] + state["messages"])
        return {"messages": [response]}

    def harness_node(state: State, config):
        thread_id = config["configurable"]["thread_id"]
        agent_name = harness.load_agent()["name"]
        order_id = state.get("order_id")
        actions = list(state.get("actions") or [])
        results = []

        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            order_id = args.get("order_id", order_id)

            decision = harness.check(name, args, order_id)
            outcome, rule, detail = decision["outcome"], decision["rule"], decision["detail"]
            db.log_audit(agent_name, thread_id, order_id, name, args, outcome, rule, detail)

            if outcome == "allowed":
                output = run_tool(name, args)
                db.log_audit(agent_name, thread_id, order_id, name, args, "executed", None, str(output))
                if name in TOOLS:
                    actions.append({"tool": name, "args": args})
                content = str(output)
            elif outcome == "blocked":
                content = f"Blocked by {rule}: {detail}. The action was not run."
            else:  # needs_approval: not run yet. Step 4 adds the pause.
                content = f"Needs approval ({rule}): {detail}. The action was not run."

            results.append(ToolMessage(content=content, tool_call_id=call["id"]))

        return {"messages": results, "actions": actions, "order_id": order_id}

    def route_after_agent(state: State):
        return "harness" if state["messages"][-1].tool_calls else END

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("harness", harness_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["harness", END])
    graph.add_edge("harness", "agent")
    return graph.compile(checkpointer=checkpointer)


def run_turn(graph, thread_id, message):
    """Send one customer message and return {reply, status, actions}."""
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(
        {"messages": [HumanMessage(message)], "actions": []},
        config,
    )
    return {
        "reply": result["messages"][-1].content,
        "status": "done",
        "actions": result["actions"],
    }
