# Implementation plan

The starting point was a ~300-line prototype: a FastAPI app with a one-node graph, an in-memory audit log and memory,
and a fixture-only Work IQ client. There was no database, UI, retrieval or MCP transport.

## Phases

| # | Phase | Outcome | Status |
|---|---|---|---|
| 1 | Foundations | Settings, PostgreSQL schema and Alembic migration, async pool, JWT auth and roles, redaction, hash-chained audit, policy engine | Done |
| 2 | MCP | Connection manager on the official SDK, governed gateway, four demo servers, Work IQ vocabulary preserved, registration of remote servers | Done |
| 3 | Retrieval | Ingestion for PDF / Markdown / text, hash embeddings, pgvector + full-text hybrid search, RRF, reranking, ACL filter, metrics | Done |
| 4 | Graph | Typed state, planner, supervisor, three subgraphs, compliance, approval interrupt, execution, synthesis, evaluation | Done |
| 5 | Runtime | Runner with PostgreSQL checkpoints, resume, recovery, pause, cancel, retry, timeouts, event bus and SSE | Done |
| 6 | API | Routers for auth, runs, conversations, approvals, MCP, knowledge, observability, audit, evaluations; legacy `/v1` | Done |
| 7 | Evaluation | Three datasets, runner, thresholds, baselines and regression detection | Done |
| 8 | Frontend | Eight screens, live run view, graph view, approvals, tool console, upload and search, charts, themes | Done |
| 9 | Tests | pytest, Vitest, Playwright, screenshot tour | Done |
| 10 | Delivery | Dockerfiles, Compose, CI workflow, environment template, documentation | Done |

## What was kept from the prototype

- The ten Work IQ tools and the path-based policy (blocked Graph paths, write approval).
- The hash-chained audit log, now stored in PostgreSQL.
- The `/v1/run`, `/v1/discover`, `/v1/schema`, `/v1/memory` and `/v1/audit` endpoints. `/v1/run` now runs the durable
  graph; its old `approved=true` flag is ignored because writes always need a recorded approval.

## Not done

- A live LLM run. The OpenAI code path exists but there was no API key to exercise it.
- Live third-party integrations. GitHub's remote MCP server is registered but needs a token.
- Horizontal scaling of the runner (see Architecture, trade-offs).
- A recorded walkthrough video. `docs/DEMO_SCRIPT.md` and the Playwright screenshot tour are the reproducible substitute.
