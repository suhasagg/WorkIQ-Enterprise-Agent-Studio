<div align="center">

# Work IQ Enterprise Agent Studio

**A multi-agent platform for business operations, built on LangGraph and the Model Context Protocol.**

A supervisor plans the work, specialist agents research, analyse and draft, every write stops for human approval,
and the whole run is checkpointed in PostgreSQL so it survives a restart.

[Features](#features) · [Screens](#product-tour) · [Architecture](#architecture) · [Quick start](#quick-start) · [Testing](#testing-and-evaluation) · [Docs](#documentation)

![Command Center](docs/screenshots/01-command-center.png)

</div>

---

## Why this exists

Most agent demos stop at a chat box calling one tool. Real operational work needs more than that: plans that can be
inspected, actions that cannot run without a person agreeing, answers that cite their sources, and a record of what
happened. This project implements those parts end to end as one working application:

| | |
|---|---|
| **Orchestration** | A real LangGraph `StateGraph`: supervisor, three agent subgraphs, conditional routing, bounded retries and replanning. |
| **Durability** | `AsyncPostgresSaver` checkpoints after every step. Runs pause at `interrupt()` and resume after approval, also across a process restart. |
| **Tools** | The official MCP SDK client talking to MCP servers over stdio and streamable HTTP, behind one governed gateway. |
| **Knowledge** | PDF, Markdown and text ingestion into PostgreSQL + pgvector, hybrid retrieval, reranking, citations and role-based filtering. |
| **Governance** | JWT auth, four roles, server-side policy on every tool call, approvals bound to the exact parameters, hash-chained audit log. |
| **Evidence** | Persisted events, tool and model telemetry, Prometheus metrics, OpenTelemetry spans, and three evaluation datasets with quality gates. |

> **Honest scope.** The default configuration is a self-contained demo: a deterministic local model (no LLM API calls),
> local hash embeddings, and four **simulated** MCP servers backed by synthetic data for a fictional company.
> They speak the real MCP protocol, but they are not live Microsoft 365, Jira or Zendesk tenants. See
> [What is real and what is simulated](#what-is-real-and-what-is-simulated).

## Features

- **Four business workflows**, each a path through the same graph:
  Weekly Executive Business Review, Customer Support Escalation, Enterprise Knowledge Research and Meeting-to-Execution.
- **Free-form requests**: the supervisor classifies intent and builds the plan.
- **Human-in-the-loop approvals**: approve, reject, request changes (the agent revises and asks again) or edit the parameters yourself.
- **Idempotent side effects**: each action has a stable idempotency key, so a retry or recovery never writes twice.
- **Live execution**: server-sent events with resume-from-sequence, shown as a timeline and on the graph.
- **Failure handling**: step retries, replanning with a fallback strategy, optional steps that degrade to a partial report, cancel, pause, resume and retry.
- **Grounded answers**: every claim carries a citation, is checked against its source passage by the evaluator, and the agent abstains when the documents do not contain an answer.
- **Prompt-injection isolation**: instructions found inside documents are quarantined and reported, never followed.
- **Multi-tenant**: every table is scoped by workspace; documents can be restricted to roles.
- **Dark and light themes, responsive layout, keyboard-accessible controls.**

## Product tour

All screenshots are produced by [`web/screenshots/capture.spec.ts`](web/screenshots/capture.spec.ts), which drives the
real application: it starts workflows, approves and rejects actions, uploads a document and runs evaluations.
Nothing is mocked. The full set of 42 is in [`docs/screenshots`](docs/screenshots).

### Workflow Studio: the graph as it runs

The compiled LangGraph topology with live node state. Here a Weekly Business Review is paused at the approval gate.
Clicking an agent opens its subgraph.

| Paused at the approval interrupt | Research agent subgraph |
|---|---|
| ![Workflow Studio](docs/screenshots/02-langgraph-workflow.png) | ![Research subgraph](docs/screenshots/12-research-subgraph.png) |

### Live execution and the executive report

Each line of the timeline is a persisted graph event streamed over SSE. The finished report has a KPI scorecard,
detected anomalies, cited causes, the outcome of every proposed action and the evidence behind each claim.

| Execution timeline | Executive report |
|---|---|
| ![Timeline](docs/screenshots/09-workflow-execution.png) | ![Executive report](docs/screenshots/10-executive-report.png) |

### Human Approval Center

Every state-changing tool call stops here with its rationale, the policy reason, the evidence and the exact
parameters. The approval is bound to a hash of those parameters, so it cannot be reused for a different call.

| Reviewing a proposed action | Editing parameters before approving |
|---|---|
| ![Approval Center](docs/screenshots/04-human-approval.png) | ![Edit parameters](docs/screenshots/14-approval-edit-parameters.png) |

### Multi-Agent Chat

Ask a question; the answer comes back with numbered citations and quality checks. Requests that need writes show
the agents working and wait for approval.

| Grounded answer with sources | Agents working, waiting for approval |
|---|---|
| ![Chat](docs/screenshots/03-multi-agent-chat.png) | ![Chat live](docs/screenshots/19-chat-live-agents-awaiting-approval.png) |

### MCP Integration Hub

Servers, their discovered tools and schemas, risk level, required roles, health and call statistics. The tool console
invokes a tool through the same gateway the agents use: reads run, writes are held for approval.

| Server and discovered tools | Write held by policy |
|---|---|
| ![MCP hub](docs/screenshots/05-mcp-integrations.png) | ![Write held](docs/screenshots/21-tool-console-write-held-for-approval.png) |

### Enterprise Knowledge Studio

Upload a document and watch it get parsed, chunked and embedded. The retrieval playground shows vector rank,
keyword rank and rerank score for each passage, with the matched terms highlighted.

| Upload and hybrid search | Document chunks |
|---|---|
| ![Knowledge Studio](docs/screenshots/06-knowledge-studio.png) | ![Chunks](docs/screenshots/24-document-chunks.png) |

### Observability and Evaluation

| Observability dashboard | Evaluation run compared with the baseline |
|---|---|
| ![Observability](docs/screenshots/07-observability.png) | ![Evaluation](docs/screenshots/28-experiment-compared-with-baseline.png) |

### Reliability and safety

| Retry, then replan after a retrieval outage | Prompt injection in a document, quarantined |
|---|---|
| ![Retry and replan](docs/screenshots/18-failure-retry-and-replan.png) | ![Prompt injection](docs/screenshots/26-prompt-injection-quarantined.png) |

| Checkpointed state in PostgreSQL | Dark theme |
|---|---|
| ![Checkpoints](docs/screenshots/16-checkpointed-state.png) | ![Dark theme](docs/screenshots/31-dark-workflow-studio-completed-run.png) |

## Architecture

```mermaid
flowchart LR
  subgraph Web["Next.js 14 · React · TypeScript · Tailwind"]
    UI["8 screens<br/>SSE timeline · React Flow graph"]
  end
  subgraph API["FastAPI"]
    AUTH["JWT auth · RBAC · rate limit"]
    RUN["Run API · SSE · approvals"]
    RUNNER["Durable runner<br/>recover · cancel · retry"]
  end
  subgraph Graph["LangGraph StateGraph"]
    SUP["Supervisor / planner"]
    RES["Research subgraph<br/>plan → retrieve → grade → extract"]
    ANA["Analytics subgraph<br/>KPIs · tickets · correlation"]
    TASK["Task subgraph<br/>gather → extract → propose"]
    COMM["Communication"]
    COMP["Compliance"]
    GATE{{"Approval gate<br/>interrupt()"}}
    EXEC["Execute actions<br/>idempotent"]
    SYN["Synthesizer"]
    EVAL["Evaluator"]
  end
  subgraph MCP["MCP gateway · official SDK client"]
    GW["schema · policy · RBAC · approval hash<br/>retry · timeout · telemetry"]
    S1["Work IQ M365<br/>(simulated)"]
    S2["Tracker<br/>(simulated)"]
    S3["Helpdesk<br/>(simulated)"]
    S4["Metrics<br/>(simulated)"]
    S5["Remote MCP<br/>streamable HTTP"]
  end
  PG[("PostgreSQL 16 + pgvector<br/>checkpoints · runs · events · approvals<br/>chunks · audit · telemetry · evals")]

  UI -->|"/api (same-origin proxy)"| AUTH --> RUN --> RUNNER --> SUP
  SUP --> RES & ANA & TASK & COMM
  RES & ANA & TASK & COMM --> SUP
  SUP --> COMP --> GATE --> EXEC --> SYN --> EVAL
  EVAL -.->|revise| SYN
  RES & ANA & TASK & EXEC --> GW
  GW --> S1 & S2 & S3 & S4 & S5
  RUNNER <-->|checkpoint| PG
  RES -->|hybrid retrieval| PG
  GW --> PG
```

| Layer | Technology |
|---|---|
| Orchestration | LangGraph 1.x `StateGraph`, nested subgraphs, `interrupt()` / `Command(resume=…)`, `AsyncPostgresSaver` |
| Models | Deterministic local model by default; OpenAI through `langchain-openai` when `OPENAI_API_KEY` is set |
| Tools | `mcp` Python SDK (client sessions, stdio and streamable HTTP), FastMCP demo servers |
| Backend | FastAPI, psycopg 3 async pool, Alembic, structlog, Prometheus client, OpenTelemetry SDK |
| Data | PostgreSQL 16, pgvector (HNSW, cosine), full-text search (`tsvector` + GIN) |
| Frontend | Next.js 14 App Router, React 18, TypeScript, Tailwind CSS, SWR, React Flow, Recharts |
| Tests | pytest (real database, real MCP servers, real graph), Vitest + Testing Library, Playwright |

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

### Docker

```bash
docker compose up --build
# open http://localhost:3000 and click a demo account
```

This starts PostgreSQL with pgvector, the API (migrations run on start) and the web app. On first start the API seeds
two synthetic workspaces and runs a few real workflows and baseline evaluations so the screens are not empty.

### Local development

Requirements: Python 3.12, Node 22, Docker (for PostgreSQL).

```bash
cp .env.example .env
make db            # PostgreSQL + pgvector on :5432
make install       # Python venv (uv) and npm packages
make migrate
make api           # http://localhost:8140  (API docs at /docs)
make web           # http://localhost:3000  (in a second terminal)
```

### Demo accounts

The password for all demo accounts is `workiq-demo` (shown on the sign-in page in demo mode).

| Account | Role | Can |
|---|---|---|
| `admin@meridian.example` | admin | everything, including connectors and the audit log |
| `approver@meridian.example` | approver | run workflows and decide approvals |
| `operator@meridian.example` | operator | run workflows, upload documents, run evaluations |
| `viewer@meridian.example` | viewer | read only |
| `admin@globex.example` | admin | a second workspace, to show tenant isolation |

A five-minute walkthrough is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Testing and evaluation

| Suite | What it covers | Result |
|---|---|---|
| `pytest` (63 tests) | Policy, auth, planner, RAG, and integration tests on a real PostgreSQL database with real MCP stdio servers and the compiled graph: approval pause and resume, edit and request-changes, resume after a simulated restart, recovery without duplicate side effects, idempotency, retries, replanning, partial failure, MCP governance, ACL, upload, PDF, prompt injection, secrets, audit chain | 63 passed |
| Vitest (16 tests) | Components, API client, launcher, approval form validation, loading / error / empty states | 16 passed |
| Playwright (8 tests) | Full workflows in a browser: run → pause → reject and approve → resume → report; failure recovery; chat; upload and retrieval; MCP console; evaluation; read-only role | 8 passed |

| Evaluation dataset | Cases | Result on the seeded demo |
|---|---|---|
| Retrieval quality | 12 | Recall@5 1.00 · MRR 0.96 · NDCG@5 0.98 |
| Agent workflows | 8 | task success 1.00 · groundedness 1.00 · citation correctness 1.00 |
| Safety and guardrails | 8 | 8 / 8 passed |

These numbers come from a small synthetic corpus and a deterministic model, so they show that the pipeline and the
gates work; they are not a benchmark of model quality. Details: [docs/TEST_REPORT.md](docs/TEST_REPORT.md) and
[docs/EVALUATION_REPORT.md](docs/EVALUATION_REPORT.md).

```bash
make test          # pytest + vitest
make lint          # ruff, eslint, tsc
make e2e           # Playwright, against a running stack
make screenshots   # regenerate docs/screenshots
```

## Project layout

```
app/
  api/        REST + SSE routers (auth, runs, approvals, mcp, knowledge, observability, evals, legacy /v1)
  graph/      state, planner, nodes, research / analytics / task subgraphs, runner, event bus
  mcp/        connection manager, governed gateway, simulated MCP servers
  rag/        ingestion, embeddings, hybrid retrieval, metrics
  evals/      datasets and the evaluation runner
  seed/       synthetic workspaces, documents, KPIs, tickets, demo history
  policy.py · security.py · audit.py · redaction.py · telemetry.py · metrics.py
alembic/      database migrations
tests/        pytest unit and integration tests
web/          Next.js app, Vitest tests, Playwright e2e and the screenshot tour
docs/         architecture, security, deployment, reports, screenshots
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Feature matrix](docs/FEATURE_MATRIX.md): each requirement, where it is implemented and how it was verified
- [Demo script](docs/DEMO_SCRIPT.md)
- [Test report](docs/TEST_REPORT.md) · [Evaluation report](docs/EVALUATION_REPORT.md)
- [Security](docs/SECURITY.md) · [Deployment](docs/DEPLOYMENT.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## Origin

This project grew out of a small Work IQ MCP workplace-agent prototype. Its ten-tool Work IQ vocabulary
(`fetch`, `create_entity`, `update_entity`, `delete_entity`, `do_action`, `call_function`, `ask`, `list_agents`,
`get_schema`, `search_paths`), its path-blocking policy, its hash-chained audit log and its `/v1/*` endpoints are kept.

Work IQ and Microsoft 365 are Microsoft products. This is an independent project and is not affiliated with or
endorsed by Microsoft.
