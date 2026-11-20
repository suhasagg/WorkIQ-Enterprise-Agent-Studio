# Test report

Environment: Ubuntu Linux, Python 3.12.16, Node 22.23, PostgreSQL 16 with pgvector (`pgvector/pgvector:pg16`),
Chromium (Playwright headless shell). Deterministic local model and hash embeddings; no network calls to model providers.

## Summary

| Suite | Command | Tests | Result |
|---|---|---|---|
| Backend unit + integration | `pytest -q` | 63 | 63 passed |
| Backend lint | `ruff check app tests` | | clean |
| Frontend components | `npm test` (Vitest) | 16 | 16 passed |
| Frontend lint and types | `npm run lint`, `npm run typecheck` | | clean |
| Frontend production build | `npm run build` | | succeeded |
| End-to-end, local stack | `npx playwright test` | 8 | 8 passed |
| End-to-end, Docker Compose stack | `docker compose up --build`, then `npx playwright test` | 8 | 8 passed |
| Screenshot tour | `npm run screenshots` | 1 (42 screenshots) | passed |

The GitHub Actions workflow in `.github/workflows/ci.yml` runs the same suites. It was written for this change and
has not yet run on GitHub at the time of writing.

## Backend (`tests/`)

The integration tests do not mock the system under test. A dedicated `workiq_test` database is created and migrated
for the session, the FastAPI lifespan is started (which spawns the four MCP stdio servers and compiles the graph with
the PostgreSQL checkpointer), and requests go through the ASGI app.

| Area | Tests |
|---|---|
| Policy, security, redaction (unit) | read allowed, write needs approval, approved write allowed, blocked path denied even when approved, viewer cannot write, unknown tool denied, server annotations, secret in payload denied, external recipient is high risk, disabled tool, password hash, JWT scopes from role, redaction, parameter hash |
| Planner, analytics, meeting extraction (unit) | intent classification, plans per workflow, question decomposition, anomaly detection, correlation, transcript parsing, due-date resolution, deterministic approval ids |
| Retrieval (unit) | hash embeddings, tokenizer, Markdown parsing and heading chunking, window overlap, injection scan, Recall / MRR / NDCG / citation correctness |
| Auth and RBAC | health, login, 401 / 403 paths, scope enforcement on the server |
| Graph | topology is the compiled LangGraph graph with subgraphs and conditional edges |
| Workflows | knowledge research with citations; unanswerable question abstains; WBR pauses, mixed reject / approve, resumes, executes and reports; edit and request-changes with a second approval round; reject everything executes nothing |
| Durability | resume after a simulated process restart (compiled graph and tasks dropped); recovery of a run left `running` without duplicate side effects; idempotent replay; approval bound to exact parameters |
| Failure handling | retry then replan on retrieval failure; tool retry on a transient fault; cancel; partial failure reported; pause and resume |
| Streaming and chat | SSE replay from a sequence number; conversation flow and ownership |
| MCP | registry and discovery; governed tool console; per-workspace disable; workspace isolation; health and unreachable server; legacy `/v1` API |
| Knowledge | ACL and workspace isolation; search modes and highlights; upload, index, search, duplicate detection, delete; PDF ingestion with page numbers; prompt-injection quarantine; secrets never persisted (run row, events, checkpoints) |
| Evaluation, observability, audit | the three datasets pass their gates and compare with a baseline; dashboard figures match the database; audit chain detects tampering |

## Frontend components (`web/src/test/`)

Formatting helpers; API client error handling; loading, error (with retry), empty and data states; status labels;
timeline rendering and verbose mode; graph node-state derivation; report rendering with citations, guardrails and
scores; workflow launcher (request body, read-only role, backend error surfaced); login error; Approval Center
(comment required to reject, invalid JSON rejected for edits, edited parameters sent, controls hidden without the scope).

## End-to-end (`web/e2e/app.spec.ts`)

1. Navigation reaches all eight screens; the theme toggles and persists.
2. Weekly Business Review: live timeline, graph paused at the approval gate, reject one action with a required comment, approve the rest, run resumes and completes, report shows executed and rejected actions, citation click highlights the source, checkpoints are listed.
3. Failure simulation: retry and replan are visible and the run completes.
4. Chat: a question is answered with sources and a link to the execution trace.
5. Knowledge: upload, indexing progress, hybrid search finds the new document with highlights, delete.
6. MCP hub: a read tool runs in the console, a write is held for approval, a restricted document is hidden from the operator.
7. Evaluation: the safety dataset runs and passes its gate.
8. Viewer role is read-only; an API error (missing run) is shown in the UI.

## Defects found by testing and fixed

| Defect | Found by | Fix |
|---|---|---|
| Secrets in a request reached graph checkpoints | integration test | redact before the run row and graph state are created |
| Free-form policy question was classified as an operational workflow and proposed actions | agent evaluation | question / policy detection in the planner |
| Unanswerable question produced unrelated findings | agent evaluation | term-coverage grading and abstention |
| "…and who owns them?" made the agent abstain | Playwright chat test | ignore pronouns and filler in coverage; light stem matching; new evaluation case |
| Client crash when leaving the chat page | Playwright navigation test | effect returned a non-function cleanup |
| Approval detail overflowed the page | screenshot review | grid column `minmax(0, 1fr)` |
| Login returned 500 in Docker (empty `JWT_SECRET` from Compose overrode the default) | Playwright on the Compose stack | empty environment variables fall back to defaults |
| Citation correctness scored 0 in evaluations | evaluation run | pass evidence passages as a list |

## Not tested

- OpenAI model and embeddings, GitHub remote MCP, OTLP export, LangSmith: no credentials or endpoints were available.
- Load, soak and multi-replica behaviour.
- Browsers other than Chromium.
- Screen-reader testing. Controls have labels and roles and are keyboard reachable, but no audit tool was run.

## Reproduce

```bash
make db install migrate
make test && make lint
make api      # terminal 1
make web      # terminal 2
make e2e
```
