# Security

## Threat model

| Threat | Control | Where |
|---|---|---|
| An agent performs a harmful or unintended write | Every state-changing tool call needs a recorded human approval. The decision is made by the policy engine on the server, not by the model. | `app/policy.py`, `app/mcp/gateway.py`, `nodes.approval_gate` |
| An approval is reused for a different action | The approval stores a hash of the exact parameters; the gateway re-computes it at execution time and refuses a mismatch. | `gateway._verify_approval` |
| A retry or crash repeats a side effect | Idempotency key per action; a replay returns the stored result. | `action_executions`, `nodes.execute_actions` |
| Prompt injection through a document | Retrieved text is treated as data. Sentences that look like instructions are dropped, the chunk is flagged, a guardrail event is recorded, and documents never trigger tool calls. | `rag/ingest.scan_untrusted`, `research.grade` |
| Prompt injection through the request | Requests are redacted and classified; tools reachable from a request are still subject to policy and approval. | `nodes.intake`, policy |
| A user reads another tenant's data | `workspace_id` on every table and in every query. The gateway injects the workspace id into tool calls; it is never model-supplied. | all SQL, `gateway.call` |
| A user reads a restricted document | Role filter applied inside the retrieval SQL, before ranking. | `rag/retrieve.py`, `api/knowledge.py` |
| Privilege escalation through the token | The token carries the role name only; scopes are looked up on the server. | `security.decode_token` |
| Secrets leak into logs, events or checkpoints | The request is redacted before the run row is written and before it enters graph state. Tool arguments are redacted before they are stored. | `app/redaction.py`, `runner.create` |
| Secrets in connector configuration | Only the name of an environment variable is stored. The value is read at connect time and sent only to that server. | `mcp/manager.py`, `api/mcp.py` |
| Access to dangerous Microsoft Graph paths | Blocked path list (authentication methods, service principals, directory roles, OAuth permission grants) is denied even with an approval. | `policy.py` |
| Audit log tampering | Each audit event stores the hash of the previous one; `/api/audit/verify` recomputes the chain. | `app/audit.py` |
| Abuse and resource exhaustion | Per-client rate limit, upload size and type limits, tool-call budget per run, node / tool / run timeouts. | `api/deps.py`, `config.py` |
| Cross-site request forgery | Session cookie is `HttpOnly`, `SameSite=Lax`; state-changing endpoints are JSON `POST`/`PUT`/`DELETE`. CORS allows only configured origins. | `api/auth.py`, `main.py` |

## Roles

| Scope | viewer | operator | approver | admin |
|---|:-:|:-:|:-:|:-:|
| `workiq:read` (view, search, read tools) | ✓ | ✓ | ✓ | ✓ |
| `agent:run`, `workiq:write` (start workflows, propose writes) | | ✓ | ✓ | ✓ |
| `knowledge:write` (upload, delete, re-index) | | ✓ | ✓ | ✓ |
| `eval:run` | | ✓ | ✓ | ✓ |
| `approval:decide` | | | ✓ | ✓ |
| `connector:admin` (register servers, enable tools) | | | | ✓ |
| `audit:read` | | | | ✓ |

The UI hides controls a role cannot use, but enforcement is on the server (`tests/test_integration.py::test_rbac_enforced_server_side`).

## Production settings

`ENV=prod` makes the API refuse to start with the default JWT secret, or with demo mode and the default demo password.
Before exposing an instance:

- Set `JWT_SECRET` (32+ random characters) and `COOKIE_SECURE=true`; serve over HTTPS.
- Set `DEMO_MODE=false`, `SEED_HISTORY=false` and `ALLOW_FAULT_INJECTION=false`.
- Replace demo logins with your identity provider. The login endpoint is a demo convenience, not an SSO integration.
- Set `CORS_ORIGINS` to the real origin.
- Use a database role without superuser rights and enable TLS to PostgreSQL.
- Put the API behind a gateway that enforces rate limits across replicas; the built-in limiter is per process.

## Known gaps

- No SSO / OIDC integration; authentication is local accounts with bcrypt hashes.
- The injection scanner is pattern-based. It is a second line of defence behind the rule that documents cannot cause tool calls.
- Uploaded files are stored on local disk and are not virus-scanned.
- No encryption at rest beyond what the database and disk provide.

## Reporting

Please report security issues privately to the repository owner rather than in a public issue.
