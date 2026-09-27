"""FastAPI routes."""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import agent  # noqa: E402
import db  # noqa: E402

db.init_db()
graph = agent.build_graph()

app = FastAPI(title="Warden")


class ChatRequest(BaseModel):
    thread_id: str
    message: str


@app.post("/chat")
def chat(req: ChatRequest):
    return agent.run_turn(graph, req.thread_id, req.message)
