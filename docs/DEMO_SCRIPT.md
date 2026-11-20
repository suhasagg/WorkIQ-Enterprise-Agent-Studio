# Demo script (about five minutes)

Start the stack (`docker compose up --build` or `make api` + `make web`) and open http://localhost:3000.
To make the graph slow enough to watch, start the API with `DEMO_STEP_DELAY_MS=400`.

Each step lists the matching screenshot in `docs/screenshots`. The whole tour is also automated:
`make screenshots` replays it in a browser.

| Time | Do | Say | Screenshot |
|---|---|---|---|
| 0:00 | Sign in as **Jordan Blake (operator)**. | Demo mode: synthetic company, simulated MCP servers, deterministic model. Everything else is the real system. | 11, 01 |
| 0:20 | Command Center: point at runs, pending approvals, agents, MCP servers. | These numbers come from persisted runs, not fixtures. | 01 |
| 0:40 | Launch **Weekly Executive Business Review**. | One request becomes a plan: analytics, research, task and communication agents. | 09 |
| 1:00 | On the run page open **Timeline**, then **Graph**. | Each line is a persisted event streamed over SSE. The graph stopped at the approval gate: that is a LangGraph `interrupt()`, and the state is in PostgreSQL. | 09, 13 |
| 1:30 | Open **State & checkpoints**. | These are the checkpoints. If the server restarted now, the run would continue from here. | 16 |
| 1:50 | Sign out, sign in as **Priya Raman (approver)**, open **Approvals**. | Every write waits here with its reason, evidence and exact parameters. | 04 |
| 2:10 | Reject the email with a comment; approve the tracker issues. Show **Edit** on one. | The approval is bound to a hash of the parameters. An edited action is recorded as edited. | 14, 15 |
| 2:40 | Open the run again: the **Report**. Click a citation. | KPI scorecard, anomalies, causes with citations, and what happened to each action: three executed, one rejected. | 10 |
| 3:10 | **Workflow Studio**: launch Knowledge Research with **Failure simulation → Retrieval outage**. | The step fails, is retried, and the supervisor replans with a keyword-only fallback. The run still completes. | 18 |
| 3:35 | **Agent Chat**: ask "What are our major operational risks and who owns them?" | Same graph behind a chat. The answer cites its sources. Ask something the documents do not cover and it declines to answer. | 03, 37 |
| 4:00 | **MCP Integrations**: open Work IQ, then try `tracker.create_issue` in the tool console. | Tools are discovered over MCP. Reads run; a write from the console is held by policy. | 05, 21 |
| 4:20 | **Knowledge**: upload a Markdown file, search for it. | Parsed, chunked, embedded into pgvector, then found by hybrid search with highlights. | 06, 24 |
| 4:40 | Sign in as **Avery Chen (admin)**, **Evaluations**: run the retrieval dataset with *Vector only*, no rerank. | Evaluations run against the live pipeline and are compared with a baseline; this configuration is flagged as a regression. | 28 |
| 4:55 | **Observability**: latency, tools, guardrails; click **Verify chain**. | Tool and model telemetry, and a hash-chained audit log that verifies. | 07, 29 |

Other things worth showing if there is time: the research subgraph (12), prompt injection quarantined (26), the
restricted board memo visible to the admin only (25), the second workspace (42), dark theme (30) and mobile (32).
