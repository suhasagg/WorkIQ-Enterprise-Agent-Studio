"""Application settings. All secrets come from the environment; nothing is hard-coded for production."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "dev-only-change-me-0123456789abcdef"


class Settings(BaseSettings):
    env: str = "dev"  # dev | test | prod
    demo_mode: bool = True  # seed synthetic data, enable demo MCP servers and demo logins
    database_url: str = "postgresql://workiq:workiq@localhost:5432/workiq"
    jwt_secret: str = INSECURE_DEFAULT_SECRET
    jwt_ttl_minutes: int = 480
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"
    demo_password: str = "workiq-demo"
    seed_history: bool = True  # on first start, run real demo workflows + baseline evals so the UI is not empty

    # Models (optional). Without a key the deterministic demo model is used.
    model_provider: str = "auto"  # auto | deterministic | openai
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"
    embedding_provider: str = "auto"  # auto | hash | openai
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 384

    # MCP
    workiq_mcp_endpoint: str = ""
    workiq_token_scope: str = ""
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    github_token: str = ""
    tool_timeout_seconds: float = 20
    tool_read_retries: int = 2
    max_tool_calls_per_run: int = 40
    allow_fault_injection: bool = True

    # Orchestration
    node_timeout_seconds: float = 60
    run_timeout_seconds: float = 300
    max_step_attempts: int = 2
    max_report_revisions: int = 1
    max_action_revisions: int = 2
    demo_step_delay_ms: int = 0  # optional pacing so humans can watch the graph animate

    # Reliability / security
    rate_limit_per_minute: int = 240
    request_timeout_seconds: float = 60
    max_upload_mb: int = 15
    upload_dir: str = "./data/uploads"

    # Observability (optional)
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "work-iq-agent-studio"
    langsmith_api_key: str = ""
    langsmith_tracing: str = ""

    # Empty variables (e.g. `JWT_SECRET=` passed through by Compose) fall back to the defaults.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    @property
    def llm_enabled(self) -> bool:
        if self.model_provider == "deterministic":
            return False
        return bool(self.openai_api_key)

    @property
    def openai_embeddings_enabled(self) -> bool:
        return self.embedding_provider == "openai" and bool(self.openai_api_key)

    def validate_for_production(self) -> None:
        if self.env == "prod":
            if self.jwt_secret == INSECURE_DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must be set to a strong value (>=32 chars) when ENV=prod")
            if self.demo_mode and self.demo_password == "workiq-demo":
                raise RuntimeError("Refusing to start in prod with DEMO_MODE and the default demo password")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
