"""Initial schema: identity, runs, events, approvals, MCP, RAG (pgvector), telemetry, evaluations.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "eval_results", "eval_runs", "chunks", "documents", "kpi_weekly", "demo_records", "mcp_tool_settings",
    "mcp_servers", "memory_items", "audit_events", "llm_calls", "tool_executions", "action_executions",
    "approvals", "run_events", "messages", "runs", "conversations", "users", "workspaces",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
    CREATE TABLE workspaces (
        id text PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE users (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        email text NOT NULL UNIQUE, name text NOT NULL,
        role text NOT NULL CHECK (role IN ('viewer','operator','approver','admin')),
        password_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE conversations (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id text NOT NULL, title text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE runs (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id text NOT NULL, conversation_id text, request text NOT NULL,
        workflow text, intent text, status text NOT NULL DEFAULT 'queued',
        origin text NOT NULL DEFAULT 'live', options jsonb NOT NULL DEFAULT '{}', principal jsonb NOT NULL DEFAULT '{}',
        plan jsonb, result jsonb, error text, retry_count int NOT NULL DEFAULT 0,
        tokens_in int NOT NULL DEFAULT 0, tokens_out int NOT NULL DEFAULT 0, cost_usd numeric(12,6) NOT NULL DEFAULT 0,
        tool_calls int NOT NULL DEFAULT 0, pause_requested boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX runs_ws_created ON runs(workspace_id, created_at DESC);
    CREATE INDEX runs_status ON runs(status);
    CREATE TABLE messages (
        id bigserial PRIMARY KEY, conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        workspace_id text NOT NULL, role text NOT NULL, content text NOT NULL, run_id text,
        created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX messages_conv ON messages(conversation_id, id);
    CREATE TABLE run_events (
        seq bigserial PRIMARY KEY, run_id text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        workspace_id text NOT NULL, ts timestamptz NOT NULL DEFAULT now(), type text NOT NULL,
        node text, agent text, payload jsonb NOT NULL DEFAULT '{}');
    CREATE INDEX run_events_run ON run_events(run_id, seq);
    CREATE TABLE approvals (
        id text PRIMARY KEY, run_id text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        workspace_id text NOT NULL, action_id text NOT NULL, round int NOT NULL DEFAULT 0,
        status text NOT NULL DEFAULT 'pending', risk text NOT NULL, server text NOT NULL, tool text NOT NULL,
        title text NOT NULL, rationale text, params jsonb NOT NULL, edited_params jsonb, params_hash text NOT NULL,
        evidence jsonb NOT NULL DEFAULT '[]', requesting_agent text, policy_reason text,
        decided_by text, decided_by_name text, decided_at timestamptz, comment text,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (run_id, action_id, round));
    CREATE INDEX approvals_ws_status ON approvals(workspace_id, status, created_at DESC);
    CREATE TABLE action_executions (
        idempotency_key text PRIMARY KEY, run_id text NOT NULL, workspace_id text NOT NULL, action_id text NOT NULL,
        approval_id text, server text NOT NULL, tool text NOT NULL, params jsonb NOT NULL,
        status text NOT NULL DEFAULT 'started', attempts int NOT NULL DEFAULT 1, result jsonb, error text,
        created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz);
    CREATE TABLE tool_executions (
        id bigserial PRIMARY KEY, run_id text, workspace_id text NOT NULL, actor text, server text NOT NULL,
        tool text NOT NULL, args jsonb NOT NULL DEFAULT '{}', status text NOT NULL, decision text, error text,
        latency_ms int NOT NULL DEFAULT 0, attempt int NOT NULL DEFAULT 1, node text, agent text,
        is_write boolean NOT NULL DEFAULT false, origin text NOT NULL DEFAULT 'live',
        created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX tool_exec_ws ON tool_executions(workspace_id, created_at DESC);
    CREATE INDEX tool_exec_run ON tool_executions(run_id);
    CREATE TABLE llm_calls (
        id bigserial PRIMARY KEY, run_id text, workspace_id text NOT NULL, node text, agent text,
        provider text NOT NULL, model text NOT NULL, purpose text, prompt_tokens int NOT NULL DEFAULT 0,
        completion_tokens int NOT NULL DEFAULT 0, cost_usd numeric(12,6) NOT NULL DEFAULT 0,
        latency_ms int NOT NULL DEFAULT 0, estimated boolean NOT NULL DEFAULT true,
        origin text NOT NULL DEFAULT 'live', created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX llm_calls_ws ON llm_calls(workspace_id, created_at DESC);
    CREATE TABLE audit_events (
        seq bigserial PRIMARY KEY, workspace_id text NOT NULL, ts text NOT NULL, actor text NOT NULL,
        action text NOT NULL, resource text NOT NULL DEFAULT '', decision text NOT NULL DEFAULT '', run_id text,
        detail jsonb NOT NULL DEFAULT '{}', prior_hash text NOT NULL, hash text NOT NULL);
    CREATE INDEX audit_ws ON audit_events(workspace_id, seq DESC);
    CREATE TABLE memory_items (
        id bigserial PRIMARY KEY, workspace_id text NOT NULL, layer text NOT NULL, key text NOT NULL,
        value jsonb NOT NULL, ts timestamptz NOT NULL DEFAULT now());
    CREATE INDEX memory_ws ON memory_items(workspace_id, layer, id DESC);
    CREATE TABLE mcp_servers (
        id text PRIMARY KEY, name text NOT NULL, description text NOT NULL DEFAULT '', provider text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('demo','live')), transport text NOT NULL CHECK (transport IN ('stdio','streamable_http')),
        command jsonb, url text, credential_env text, scopes jsonb NOT NULL DEFAULT '[]',
        enabled boolean NOT NULL DEFAULT true, builtin boolean NOT NULL DEFAULT false,
        created_by text, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE mcp_tool_settings (
        workspace_id text NOT NULL, server_id text NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
        tool text NOT NULL, enabled boolean NOT NULL DEFAULT true, updated_by text,
        updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (workspace_id, server_id, tool));
    CREATE TABLE demo_records (
        id text PRIMARY KEY, workspace_id text NOT NULL, provider text NOT NULL, kind text NOT NULL,
        idempotency_key text UNIQUE, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX demo_records_lookup ON demo_records(workspace_id, provider, kind);
    CREATE TABLE kpi_weekly (
        workspace_id text NOT NULL, week_start date NOT NULL, metric text NOT NULL, value double precision NOT NULL,
        target double precision, PRIMARY KEY (workspace_id, week_start, metric));
    CREATE TABLE documents (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        title text NOT NULL, filename text NOT NULL, mime text NOT NULL, source_type text NOT NULL,
        sha256 text NOT NULL, size_bytes int NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'queued',
        stage text NOT NULL DEFAULT 'queued', progress int NOT NULL DEFAULT 0, error text,
        chunk_strategy text NOT NULL DEFAULT 'auto', chunk_count int NOT NULL DEFAULT 0,
        metadata jsonb NOT NULL DEFAULT '{}', allowed_roles text[] NOT NULL DEFAULT '{}',
        untrusted_flags jsonb NOT NULL DEFAULT '[]', embedding_model text, origin text NOT NULL DEFAULT 'upload',
        created_by text, created_at timestamptz NOT NULL DEFAULT now(), indexed_at timestamptz,
        UNIQUE (workspace_id, sha256));
    CREATE TABLE chunks (
        id text PRIMARY KEY, document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        workspace_id text NOT NULL, ordinal int NOT NULL, doc_title text NOT NULL DEFAULT '', heading text, page int, content text NOT NULL,
        token_count int NOT NULL DEFAULT 0, flags jsonb NOT NULL DEFAULT '[]', embedding vector(384),
        tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', doc_title || ' ' || coalesce(heading,'') || ' ' || content)) STORED);
    CREATE INDEX chunks_doc ON chunks(document_id, ordinal);
    CREATE INDEX chunks_ws ON chunks(workspace_id);
    CREATE INDEX chunks_tsv ON chunks USING gin(tsv);
    CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
    CREATE TABLE eval_runs (
        id text PRIMARY KEY, workspace_id text NOT NULL, dataset text NOT NULL, status text NOT NULL DEFAULT 'queued',
        metrics jsonb NOT NULL DEFAULT '{}', total int NOT NULL DEFAULT 0, passed int NOT NULL DEFAULT 0,
        is_baseline boolean NOT NULL DEFAULT false, origin text NOT NULL DEFAULT 'live', error text,
        config jsonb NOT NULL DEFAULT '{}', created_by text, created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz);
    CREATE INDEX eval_runs_ws ON eval_runs(workspace_id, dataset, created_at DESC);
    CREATE TABLE eval_results (
        id bigserial PRIMARY KEY, eval_run_id text NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
        case_id text NOT NULL, name text NOT NULL DEFAULT '', passed boolean NOT NULL, scores jsonb NOT NULL DEFAULT '{}',
        detail jsonb NOT NULL DEFAULT '{}', latency_ms int NOT NULL DEFAULT 0);
    CREATE INDEX eval_results_run ON eval_results(eval_run_id);
    """)


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
