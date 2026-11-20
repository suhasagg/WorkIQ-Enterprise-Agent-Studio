#!/usr/bin/env sh
# Container entrypoint: apply migrations, then serve. Demo data is seeded by the app on startup (idempotent).
set -eu
python -m alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8140}" --proxy-headers
