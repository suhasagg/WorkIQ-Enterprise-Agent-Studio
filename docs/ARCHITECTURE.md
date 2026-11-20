# Architecture

## Overview

```
Browser (Next.js) ──/api (same-origin proxy)──▶ FastAPI ──▶ Runner ──▶ LangGraph StateGraph
                                                   │                        │
                                                   │                        ├─▶ MCP gateway ──▶ MCP servers (stdio / HTTP)
                                                   ▼                        ▼
                                          PostgreSQL 16 + pgvector  (checkpoints, runs, events, approvals,
                                          documents, chunks, audit, tool + model telemetry, evaluations)
```

There is one stateful store, PostgreSQL. The API process holds no state that cannot be rebuilt from it.

## The graph

`app/graph/build.py` assembles a `StateGraph(AgentState)`:

```
START → intake → supervisor ─┬─▶ research   (subgraph) ─┐
                             ├─▶ analytics  (subgraph) ─┤
                             ├─▶ task       (subgraph) ─┼─▶ supervisor
                             ├─▶ communication ─────────┘
                             └─▶ compliance ─┬─▶ approval_gate ─┬─▶ execute_actions ─▶ synthesizer ─▶ evaluator ─┬─▶ finalize → END
                                             │                  ├─▶ revise_actions ─▶ compliance                 └─▶ synthesizer (one revision)
                                             │                  └─▶ synthesizer (everything rejected)
                                             ├─▶ execute_actions (nothing needs approval)
                                             └─▶ synthesizer     (no actions)
```

| Node | Role |
|---|---|
| `intake` | Redacts sensitive data from the request and records guardrail events. |
| `supervisor` | Classifies intent, builds the plan, routes to the next step, retries a failed step, replans with a fallback, marks optional steps as skipped. |
| `research` | Subgraph: `plan_queries → retrieve → grade → extract`. Hybrid retrieval, relevance and coverage grading, injection quarantine, cited findings. |
| `analytics` | Subgraph: `fetch → analyze_kpis / analyze_tickets → correlate`. Target gaps, week-over-week change, z-scores, adverse streaks, correlations. |
| `task` | Subgraph: `gather → extract → propose`. Parses meeting transcripts, resolves owners and due dates, proposes actions. |
| `communication` | Drafts summary and escalation messages as proposed actions. |
| `compliance` | Runs every proposed action through the policy engine and assigns risk. |
| `approval_gate` | Creates approval records and calls `interrupt()`. The graph stops here until a decision arrives. |
| `revise_actions` | Applies "request changes" feedback and sends the revised action back through compliance for a new approval round. |
| `execute_actions` | Executes approved actions through the MCP gateway with idempotency keys. |
| `synthesizer` | Builds the report: typed sections, numbered citations, narrative. |
| `evaluator` | Checks each cited claim against its source passage, section completeness, tool success and approval coverage. May send the draft back once. |

### State

`AgentState` (`app/graph/state.py`) is a `TypedDict`. List fields that several nodes append to (`evidence`, `findings`,
`proposed_actions`, `action_results`, `guardrails`, `errors`) use reducers, so parallel or repeated updates merge
instead of overwriting.

### Durability

- The graph is compiled with `AsyncPostgresSaver`; `thread_id` is the run id.
- `interrupt()` in `approval_gate` persists the pending state. `decide_approval` (in `app/graph/runner.py`) validates the
  decision and, once every approval of the round is decided, resumes with `Command(resume=…)`.
- Approval ids are deterministic (`sha256(run_id | action_id | round)`), so re-executing the gate after a restart
  does not create duplicates.
- External actions carry an idempotency key stored in `action_executions`; a replay returns the stored result.
- On startup `runner.recover()` finds runs left `running` or `queued` by a dead process and continues them from the
  last checkpoint. A graceful shutdown leaves in-flight runs marked `running` for exactly this reason.
- Runs can be paused (at the next step boundary), resumed, cancelled and retried. A run has a wall-clock timeout,
  each node has its own timeout, and each run has a tool-call budget.

### Events

Nodes emit events through `app/graph/events.py`. Each event is written to `run_events` with a per-run sequence number
and then announced to in-process subscribers. `GET /api/runs/{id}/stream` is a server-sent-events endpoint that reads
from the table, so a client that reconnects with `Last-Event-ID` or `?after=` loses nothing.

## MCP layer

- `app/mcp/manager.py` keeps one `ClientSession` per server (stdio subprocess or streamable HTTP), runs
  `initialize` and `list_tools`, health-checks with `ping`, and stores server metadata in `mcp_servers`.
- `app/mcp/gateway.py` is the only path to a tool. For each call it: checks the tool is enabled for the workspace,
  validates arguments against the tool's JSON Schema, asks the policy engine for a decision, verifies the approval and
  its parameter hash for writes, applies a timeout, retries read-only calls, records the call in `tool_executions`,
  writes an audit event and updates Prometheus counters.
- The workspace id is injected by the gateway, never taken from model output, and is removed from the schemas shown
  to users.
- Credentials are referenced by environment variable name. A token is only ever sent to the server it belongs to.

The four demo servers in `app/mcp/servers/` are FastMCP servers started as subprocesses. They read and write the
`demo_records` and `kpi_weekly` tables. The Work IQ server exposes the original ten-tool vocabulary.

## Retrieval

Ingestion (`app/rag/ingest.py`): parse (pypdf / Markdown / text) → extract title and metadata → chunk by heading or by
sliding window → scan for embedded instructions → embed → store in `chunks` (`vector(384)` plus a generated
`tsvector`).

Retrieval (`app/rag/retrieve.py`): pgvector cosine search (HNSW) and PostgreSQL full-text search run with the same
workspace and role filter, are fused with reciprocal rank fusion, then reranked with a deterministic lexical scorer
that also looks at the title and heading. Results carry both ranks, the scores and highlight terms.

## Data model

| Area | Tables |
|---|---|
| Identity | `workspaces`, `users` |
| Runs | `runs`, `run_events`, `conversations`, `messages`, `memory_items`, LangGraph `checkpoints*` |
| Governance | `approvals`, `action_executions`, `audit_events` |
| Tools | `mcp_servers`, `mcp_tool_settings`, `tool_executions`, `llm_calls` |
| Knowledge | `documents`, `chunks` |
| Evaluation | `eval_runs`, `eval_results` |
| Demo providers | `demo_records`, `kpi_weekly` |

Every table except the LangGraph checkpoint tables has a `workspace_id` and every query filters on it.

## Frontend

Next.js App Router with client components. `/api/*` is rewritten to the backend, so the session cookie is first-party.
Data is fetched with SWR; a run is followed with `useRun` (`web/src/lib/useRun.ts`), which reads the SSE stream with
`fetch` and resumes from the last sequence number. The graph view (`web/src/components/graph.tsx`) renders the
topology returned by `/api/graph/topology`, which is derived from the compiled graph, not drawn by hand.

## Design decisions and trade-offs

| Decision | Reason | Cost |
|---|---|---|
| Deterministic model by default | The demo runs offline, costs nothing and gives repeatable tests and evaluations. | Text is templated; it does not show LLM reasoning quality. |
| Runner inside the API process | One process to run and debug; recovery comes from checkpoints. | Single replica. Scaling out needs a job queue and a leader or lease per run. |
| PostgreSQL for everything | One dependency, transactional consistency between runs, approvals and audit. | Event fan-out is in-process; multi-replica streaming would need `LISTEN/NOTIFY` or a broker. |
| Lexical evaluator | Cheap, deterministic groundedness check. | Paraphrased claims can score low; an LLM judge would be more tolerant. |
| Approvals bound to a parameter hash | An approval cannot be replayed for different arguments. | Any edit creates a new hash and is recorded as an edit. |
