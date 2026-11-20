# Feature matrix

Each requirement, where it lives and how it was checked. "pytest" refers to `tests/test_integration.py` unless noted;
"e2e" to `web/e2e/app.spec.ts`.

## Orchestration

| Requirement | Implementation | Verified by |
|---|---|---|
| LangGraph `StateGraph` with typed shared state | `app/graph/build.py`, `app/graph/state.py` | pytest `test_graph_topology_is_real_langgraph` |
| Supervisor planning and intent classification | `app/graph/planner.py`, `nodes.supervisor` | `tests/test_unit.py` planner tests; eval `agent_workflows` |
| Independently testable agent subgraphs | `research.py`, `analytics.py`, `tasks.py` | topology test; subgraph view in the UI |
| Conditional edges, bounded retry, replanning | `nodes.supervisor`, `max_step_attempts` | pytest `test_retry_and_replan_on_retrieval_failure`; e2e failure test |
| Partial failure handling | optional steps, `partial` flag in the report | pytest `test_partial_failure_is_reported_not_hidden` |
| Synthesis and self-evaluation loop | `nodes.synthesizer`, `nodes.evaluator` | workflow tests assert scores and citations |
| PostgreSQL checkpointing, thread ids | `runner.py` with `AsyncPostgresSaver` | pytest `test_resume_after_process_restart` |
| `interrupt()` approval pause and resume | `nodes.approval_gate`, `runner.decide_approval` | pytest `test_wbr_pauses_for_approval_then_resumes_and_executes`; e2e |
| Recovery after restart | `runner.recover()` in the app lifespan | pytest `test_recover_run_interrupted_mid_flight` |
| Idempotent external actions | idempotency keys in `action_executions` | pytest `test_action_execution_is_idempotent` |
| Cancel, pause, resume, retry, timeouts | `runner.py`, `/api/runs/{id}/…` | pytest `test_tool_retry_on_transient_fault` (cancel), `test_pause_and_resume` |

## MCP

| Requirement | Implementation | Verified by |
|---|---|---|
| Official SDK client, discovery, schemas | `app/mcp/manager.py` | pytest `test_mcp_registry_and_discovery` |
| At least three providers | workiq, tracker, helpdesk, metrics (simulated) | same |
| Registration of remote servers, credential isolation | `POST /api/mcp/servers`, env-var references | pytest `test_mcp_health_and_unreachable_server` |
| Tool-level permissions and policy | `app/policy.py`, `gateway.py`, per-workspace enable flag | pytest `test_mcp_tool_console_is_governed`, `test_tool_can_be_disabled_per_workspace` |
| Timeout, retry, error handling, tracing | `gateway.call` | pytest tool-retry test; `tool_executions` telemetry |
| Health monitoring | `ping` health check, status in the hub | pytest health test |
| Work IQ ten-tool model preserved | `app/mcp/servers/workiq_m365.py` | unit `test_ten_tool_vocabulary_preserved`; discovery test asserts 10 tools |
| Simulated adapters clearly labelled | "Simulated" badge, server descriptions, report badge | e2e MCP test |

## Knowledge

| Requirement | Implementation | Verified by |
|---|---|---|
| PDF, Markdown, text upload | `app/api/knowledge.py`, `rag/ingest.py` | pytest `test_upload_ingest_search_delete`, `test_pdf_ingestion`; e2e upload |
| Metadata, chunking strategies | heading and window chunkers | unit chunking tests |
| Embeddings in pgvector | `chunks.embedding vector(384)`, HNSW | search tests |
| Hybrid retrieval, filters, reranking | `rag/retrieve.py` | pytest `test_search_modes_and_highlights` |
| Citations and evidence highlighting | report citations, `highlights` | e2e report and search tests |
| ACL filtering and workspace isolation | role filter in SQL | pytest `test_document_acl_and_workspace_isolation` |
| Retrieval evaluation | `evals/datasets/retrieval_quality.json` | pytest `test_evaluation_datasets_pass_their_gates` |
| Prompt-injection isolation | sentence-level quarantine in `research.grade` | pytest `test_prompt_injection_in_document_is_quarantined`; safety eval |

## Security and governance

| Requirement | Implementation | Verified by |
|---|---|---|
| Authentication | JWT in an HTTP-only cookie or bearer header | pytest `test_health_and_auth` |
| Role-based access | `ROLE_SCOPES`, `require(scope)` | pytest `test_rbac_enforced_server_side`; e2e viewer test |
| Approval bound to exact parameters | parameter hash check in the gateway | pytest `test_approval_binds_exact_parameters` |
| Secrets never persisted | redaction before run creation | pytest `test_secrets_in_request_are_never_persisted` |
| Tamper-evident audit | `app/audit.py` hash chain | pytest `test_audit_chain_is_tamper_evident` |

## Observability and evaluation

| Requirement | Implementation | Verified by |
|---|---|---|
| Structured logs, request ids | `logging_setup.py`, middleware | manual |
| Metrics | `/metrics` (Prometheus) | pytest dashboard test |
| Traces | OpenTelemetry spans per node and tool | span counter shown in the UI; export not verified (no collector) |
| Token and cost accounting | `llm_calls` | observability API test (cost is 0 with the local model) |
| Dashboards | `/api/dashboard`, `/api/observability` | pytest `test_dashboard_and_observability_reflect_real_activity` |
| Evaluation datasets, gates, baselines, regressions | `app/evals/` | pytest evaluation test; e2e evaluation test |

## Frontend

| Screen | Route | Verified by |
|---|---|---|
| AI Operations Command Center | `/` | e2e navigation; screenshot 01 |
| LangGraph Workflow Studio | `/studio` | e2e; screenshots 02, 12 |
| Multi-Agent Chat Workspace | `/chat` | e2e chat test; screenshots 03, 19 |
| Human Approval Center | `/approvals` | e2e approval flow; Vitest approval tests; screenshots 04, 14, 15 |
| MCP Integration Hub | `/integrations` | e2e MCP test; screenshots 05, 20–22 |
| Enterprise Knowledge Studio | `/knowledge` | e2e upload test; screenshots 06, 23–25 |
| AI Observability Dashboard | `/observability` | screenshots 07, 29 |
| Evaluation Studio | `/evals` | e2e evaluation test; screenshots 08, 27, 28 |
| Dark / light theme, responsive | theme toggle, mobile drawer | e2e theme test; screenshots 30–33, 40, 41 |
| Loading, error and empty states | `Async`, `ErrorBox`, `Empty` | Vitest `Async states` |


