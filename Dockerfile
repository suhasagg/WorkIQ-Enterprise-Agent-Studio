# Backend: FastAPI + LangGraph runtime + demo MCP servers (spawned as stdio subprocesses).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd -r -u 10001 -m agent
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install .
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts/start.sh ./scripts/start.sh
RUN mkdir -p /app/data/uploads && chown -R agent /app/data
USER agent
EXPOSE 8140
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8140/health/ready', timeout=2).status == 200 else 1)"
CMD ["./scripts/start.sh"]
