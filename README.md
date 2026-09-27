# Warden

**A refund-support AI agent whose every action goes through a code harness: an identity card, permission and rule checks, human approval for risky refunds, and a full audit log.**

![Demo](docs/demo.gif)
<!-- TODO: record the /chat and /approver pages side by side and save as docs/demo.gif -->

## Architecture

```
 /chat page (customer)                      /approver page (manager)
        |                                        |          ^
        v                                        |          |
 +--------------------------------------------------------------------+
 |                          FastAPI backend                           |
 |                                                                    |
 |  POST /chat --> LangGraph agent                                    |
 |                   |                                                |
 |             [agent node]  model picks a tool                       |
 |                   |                                                |
 |             [harness node]                                         |
 |               1. tool in agent.yaml allowed list?                  |
 |               2. rules: R2, R3, then R1 (plain Python)             |
 |                   |             |               |                  |
 |                 allow         block       needs_approval           |
 |                   |             |               |                  |
 |               run tool     tell agent     create ticket,           |
 |                   |         it's blocked   agent tells customer,   |
 |                   |             |          [approval node] pauses  |
 |                   |             |          with interrupt()         |
 |                   |             |               |                  |
 |                   +------> audit_log <----------+                  |
 |                                                                    |
 |  POST /tickets/{id}/approve --> resume from checkpoint,            |
 |                                 re-check R2 + permission, run tool |
 |  POST /tickets/{id}/deny    --> resume, tell agent it was denied   |
 +--------------------------------------------------------------------+
                                  |
                               SQLite
             (orders, tickets, audit_log, outbox, checkpoints)
```

## How it works

**Identity.** The agent has an ID card in [`backend/config/agent.yaml`](backend/config/agent.yaml): name `RefundBot-01`, role, supervisor `ops-manager`, and the tools it may use. Every audit row carries the agent's name, and the approver page shows the card.

**Harness.** The model never runs a tool directly. It asks, and the harness node in [`backend/agent.py`](backend/agent.py) calls `check(tool, args)` in [`backend/harness.py`](backend/harness.py) first. The result is `allowed`, `blocked` or `needs_approval`. The model's output is only a request; the decision is ordinary Python.

**Rules.** [`backend/config/rules.yaml`](backend/config/rules.yaml) names the rules; the checks are plain Python, not an LLM:

| Rule | Check | Outcome |
|---|---|---|
| Permission | tool is in `allowed_tools` | block |
| R2 | refund + already refunded > order total | block |
| R3 | email recipient is not the order's customer | block |
| R1 | refund > $200 | needs approval |

They run in that order, so a $5000 refund on a $120 order is blocked (R2) instead of being sent for approval (R1). `RULES_ENABLED=false` turns every check into "allowed" (still logged), for comparing the agent with and without rules in code.

**Approval.** On `needs_approval` the harness creates a ticket (the refund's `reason` becomes the agent's reason) and tells the agent, which tells the customer. Then an approval node pauses the run with LangGraph `interrupt()`. The paused run lives in the SQLite checkpointer, so it survives a server restart. A manager approves or denies on `/approver`, which resumes the run; the agent then tells the customer the outcome.

**Re-check on approval.** Data can change while a ticket waits (another refund on the same order, for example). When a ticket is approved, the harness re-runs the blocking checks (permission and R2) against current data. If they now fail, the tool doesn't run, the ticket becomes `blocked_on_approval`, and the agent is told why.

**Audit log.** Every attempt is written to `audit_log` with the agent, conversation, order, tool, arguments, outcome, rule and detail. Outcomes are `allowed`, `blocked`, `needs_approval`, `approved`, `denied`, `executed` (the tool ran and succeeded) and `failed` (the tool ran but returned an error, such as a missing reason, so nothing changed).

## Known limits

- **No customer login.** Anyone in the chat can ask about any order number. R3 stops email going to anyone but the order's customer, but nothing checks that the person chatting *is* that customer.
- **Single agent.** One agent identity and one approver role (`ops-manager`); no per-approver accounts, so the audit log records *that* a ticket was approved, not *who* approved it.
- **R3 needs an order in context.** `send_email` has no order argument, so R3 checks the recipient against the order the conversation is about (the last `order_id` a tool used). An email before any order has come up is blocked.
- **One pending approval per conversation.** A second over-$200 refund in the same conversation is blocked until the first ticket is decided.
- **Fake email.** `send_email` writes to an `outbox` table; nothing is sent.

## How to run

Needs Python 3.11+, Node 20+, and a model. The default is `qwen3:8b` running locally in [Ollama](https://ollama.com) (no API key, no rate limits; about 6.3 GB of memory). Any model LangChain's `init_chat_model` supports also works, such as Groq.

```bash
# backend
cd warden
uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt
ollama pull qwen3:8b       # local model (skip if you use a hosted one)
cp .env.example .env        # add API keys only for hosted models
.venv/bin/uvicorn --app-dir backend app:app --port 8000

# frontend (second terminal)
cd warden/frontend && npm install && npm run dev
```

Open http://localhost:5173/chat and http://localhost:5173/approver side by side. "Reset demo" on the approver page restores the seed data.

```bash
.venv/bin/python -m pytest                    # tests (use a scripted fake model, no API key needed)
.venv/bin/python scripts/chat_cli.py          # chat in the terminal
```

`.env` settings:

| Variable | Default | Meaning |
|---|---|---|
| `MODEL` | `ollama:qwen3:8b` | any `provider:model` for `init_chat_model`; Ollama models run with an 8K context and thinking off |
| `FALLBACK_MODEL` | *(empty)* | backup model used when `MODEL` errors, such as `google_genai:gemini-flash-latest` |
| `RULES_ENABLED` | `true` | `false` = harness allows everything (still logs) |
| `PROMPT_MODE` | `permissive` | `policy` = the three rules also written into the prompt as words |

## Project layout

```
backend/
  app.py          FastAPI routes
  agent.py        LangGraph graph: agent, harness and approval nodes
  harness.py      permission + rule checks, returns a decision
  tools.py        lookup_order, issue_refund, send_email
  db.py           SQLite setup, seed data, queries
  config/         agent.yaml (ID card), rules.yaml
frontend/         React + Vite + Tailwind: /chat and /approver
scripts/          chat_cli.py
tests/            pytest suite
```
