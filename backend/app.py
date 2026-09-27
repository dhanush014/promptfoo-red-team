"""FastAPI routes."""

import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import agent  # noqa: E402
import db  # noqa: E402
import harness  # noqa: E402

db.init_db()
graph = agent.build_graph()

app = FastAPI(title="Warden")


class ChatRequest(BaseModel):
    thread_id: str
    message: str


@app.post("/chat")
def chat(req: ChatRequest):
    return agent.run_turn(graph, req.thread_id, req.message)


@app.get("/chat/{thread_id}")
def chat_history(thread_id: str):
    return agent.history(graph, thread_id)


@app.get("/agent")
def agent_card():
    return harness.load_agent()


@app.get("/audit")
def audit():
    return db.list_audit()


@app.get("/tickets")
def tickets(status: str | None = None):
    return db.list_tickets(status)


@app.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int):
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return {
        "ticket": ticket,
        "order": db.get_order(ticket["order_id"]),
        "audit": list(reversed(db.list_audit(ticket["thread_id"]))),  # oldest first
    }


@app.post("/tickets/{ticket_id}/approve")
def approve(ticket_id: int):
    return decide(ticket_id, "approved")


@app.post("/tickets/{ticket_id}/deny")
def deny(ticket_id: int):
    return decide(ticket_id, "denied")


def decide(ticket_id, decision):
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    if not db.decide_ticket(ticket_id, decision):
        raise HTTPException(409, f"Ticket is already {ticket['status']}")

    db.log_audit(harness.load_agent()["name"], ticket["thread_id"], ticket["order_id"],
                 ticket["tool"], json.loads(ticket["args_json"]),
                 decision, ticket["rule"], f"Ticket #{ticket_id} {decision}")
    result = agent.resume(graph, ticket["thread_id"], decision)
    return {"ticket": db.get_ticket(ticket_id), **result}
