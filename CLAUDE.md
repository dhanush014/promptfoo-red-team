# Warden: Governed Refund Agent

## What this is
A customer support AI agent ("RefundBot-01") that handles refund requests.
Every action the agent tries goes through a harness that checks it against
the agent's permissions and a rulebook. Risky actions pause and wait for a
human approver. Every attempt is written to an audit log.

Goal: a simple, demoable portfolio project showing agent governance:
agent identity, access controls, approval flows, and audit logging.

Keep it simple. Do not add features that are not in this file.

## Architecture

```
 /chat page (customer)                 /approver page (manager)
        |                                   |        ^
        v                                   |        |
 +--------------------------------------------------------------+
 |                       FastAPI backend                        |
 |                                                              |
 |   POST /chat --> LangGraph agent                             |
 |                    |                                         |
 |              [agent node] model picks a tool                 |
 |                    |                                         |
 |              [harness node]                                  |
 |                1. tool in agent.yaml allowed list?           |
 |                2. check rules.yaml                           |
 |                    |           |              |              |
 |                  allow       block      needs_approval       |
 |                    |           |              |              |
 |               run tool     tell agent    create ticket,      |
 |                    |       it's blocked  pause (interrupt)   |
 |                    |           |              |              |
 |                    +-----> audit_log <--------+              |
 |                                                              |
 |   POST /tickets/{id}/approve --> resume paused agent         |
 +--------------------------------------------------------------+
                           |
                        SQLite
          (orders, tickets, audit_log, outbox, checkpoints)
```

## Stack
- Python 3.11+, FastAPI, LangGraph, SQLite
- LLM via LangChain `init_chat_model`, model set by env var `MODEL`
- LangGraph SQLite checkpointer so paused runs survive between requests
- Frontend: React + Vite + Tailwind, two routes: `/chat` and `/approver`
- Tests: pytest

## Folder layout

```
warden/
  backend/
    app.py            FastAPI routes
    agent.py          LangGraph graph (agent node + harness node)
    harness.py        permission + rule checks, returns a decision
    tools.py          lookup_order, issue_refund, send_email
    db.py             SQLite setup, seed data, queries
    config/
      agent.yaml
      rules.yaml
  frontend/           React app
  tests/
  .env.example
  README.md
```

## Data model (SQLite)

```
orders     id, customer_email, item, total, refunded_amount, status
tickets    id, thread_id, order_id, tool, args_json, amount,
           rule, agent_reason, status (pending/approved/denied),
           created_at, decided_at
audit_log  id, time, agent_name, thread_id, order_id, tool,
           args_json, outcome, rule, detail
           outcome = allowed / blocked / needs_approval /
                     approved / denied / executed
outbox     id, time, to_email, subject, body   (fake email, never sent)
```

Seed 10 fake orders with totals between $40 and $900.

## Tools
- `lookup_order(order_id)` returns order details
- `issue_refund(order_id, amount, reason)` updates refunded_amount.
  `reason` is required, it becomes the "agent's reason" on tickets.
- `send_email(to_email, subject, body)` writes to outbox table only

## Agent identity (config/agent.yaml)

```yaml
name: RefundBot-01
role: Customer refund support
supervisor: ops-manager
allowed_tools:
  - lookup_order
  - issue_refund
  - send_email
not_allowed:
  - delete records
  - export customer data
```

## Rules (config/rules.yaml)

```yaml
rules:
  - id: R1
    name: Large refund needs approval
    tool: issue_refund
    check: amount > 200
    outcome: needs_approval
  - id: R2
    name: Refund cannot exceed order total
    tool: issue_refund
    check: amount + refunded_amount > total
    outcome: block
  - id: R3
    name: Email only the order's customer
    tool: send_email
    check: to_email != order customer_email
    outcome: block
```

Checks are plain Python in harness.py, not evaluated by an LLM.
Check order: permission first, then R2, then R3, then R1.
Env flag `RULES_ENABLED=true/false`. When false, harness allows everything
(still logs). This is used for the before/after attack test.

## API

```
POST /chat                  {thread_id, message}
                            -> {reply, status, actions}
                            status = done / waiting_approval
                            actions = tools that actually ran this turn
GET  /chat/{thread_id}      -> messages + status (frontend polls this)
GET  /agent                 -> agent.yaml contents
GET  /tickets?status=pending
GET  /tickets/{id}          -> ticket + that thread's audit steps
POST /tickets/{id}/approve  -> resumes agent, runs the tool
POST /tickets/{id}/deny     -> resumes agent, tells it the refund was denied
GET  /audit                 -> full audit log, newest first
```

## UI

### /chat (customer)
```
+---------------------------------------+
|  Support Chat                         |
|---------------------------------------|
|  You: I want a refund for order #1042 |
|                                       |
|  Agent: Your order was $350. Refunds  |
|  over $200 need a manager's OK. I've  |
|  sent it for approval.                |
|                                       |
|  [ Waiting for approval... ]          |
|                                       |
|  Agent: Approved! $350 refunded.      |
|---------------------------------------|
|  [ type a message...          ] Send  |
+---------------------------------------+
```
- While status is waiting_approval: disable input, show waiting banner,
  poll GET /chat/{thread_id} every 2 seconds.
- "New conversation" button creates a new thread_id.

### /approver (manager)
```
+-------------------------------------------------------------+
|  Warden Approvals                     [ Tickets | Activity ]|
|-------------------------------------------------------------|
|  +-------------------------------+                          |
|  | Digital Employee: RefundBot-01|                          |
|  | Supervisor: ops-manager       |                          |
|  | Can use: lookup_order,        |                          |
|  |   issue_refund, send_email    |                          |
|  +-------------------------------+                          |
|                                                             |
|  #1042   $350   Damaged item    [Details] [Approve] [Deny]  |
|  #1051   $890   Wrong size      [Details] [Approve] [Deny]  |
+-------------------------------------------------------------+
```

Details popup:
```
+----------------------------------------+
|  Ticket #1042                          |
|----------------------------------------|
|  Customer: jane@mail.com               |
|  Order total: $350                     |
|  Refund asked: $350                    |
|  Rule triggered: R1 over $200          |
|  Agent's reason: "Item damaged"        |
|                                        |
|  What the agent did:                   |
|   1. lookup_order       allowed        |
|   2. issue_refund $350  paused         |
|                                        |
|         [ Approve ]   [ Deny ]         |
+----------------------------------------+
```

Activity tab:
```
+-------------------------------------------------------------+
|  Time   Agent         Order  Action              Outcome    |
|  10:04  RefundBot-01  #1042  refund $350         approved   |
|  09:51  RefundBot-01  #1038  email x@evil.com    BLOCKED R3 |
|  09:47  RefundBot-01  #1035  refund $5000        BLOCKED R2 |
+-------------------------------------------------------------+
```
- Blocked rows in red, approval rows in amber, allowed in gray.
- Tickets list refreshes every 3 seconds.

## Build order
- [x] 1. Database + seed data + 3 tools
- [x] 2. Agent with tools, no rules, POST /chat
- [x] 3. agent.yaml + rules.yaml + harness checks + audit log
- [x] 4. Approval pause/resume + tickets API
- [ ] 5. Both frontend pages
- [ ] 6. Attack test file + README with before/after numbers

## Working rules for Claude Code
- Do only the steps asked for in the current prompt. Stop after.
- After each step: run the tests, show the output, and tick the box above.
- Explain in plain language what you built and how to run it.
- Keep code simple and readable. No extra abstractions.
- Never put real API keys in code. Use .env.
