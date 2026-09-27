"""Chat with the agent in the terminal.

    python scripts/chat_cli.py              # new conversation
    python scripts/chat_cli.py my-thread    # continue a conversation
"""

import os
import sys
import uuid

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import agent  # noqa: E402
import db  # noqa: E402


def main():
    thread_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())[:8]
    db.init_db()
    graph = agent.build_graph()
    print(f"Thread: {thread_id}   (Ctrl+C or 'quit' to exit)\n")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if message.lower() in ("quit", "exit"):
            break
        if not message:
            continue
        result = agent.run_turn(graph, thread_id, message)
        for action in result["actions"]:
            print(f"  [ran {action['tool']} {action['args']}]")
        print(f"Agent: {result['reply']}")
        print(f"  (status: {result['status']})\n")


if __name__ == "__main__":
    main()
