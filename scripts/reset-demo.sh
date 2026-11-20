#!/usr/bin/env bash
# Drops and re-creates the demo database, then applies migrations. The API re-seeds demo data on its next start.
set -euo pipefail
cd "$(dirname "$0")/.."
ADMIN_URL="${DATABASE_ADMIN_URL:-postgresql://workiq:workiq@localhost:5432/postgres}"
DB="${DATABASE_NAME:-workiq}"
PY="${PYTHON:-.venv/bin/python}"
"$PY" - "$ADMIN_URL" "$DB" <<'PYEOF'
import sys, psycopg
with psycopg.connect(sys.argv[1], autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{sys.argv[2]}" WITH (FORCE)')
    c.execute(f'CREATE DATABASE "{sys.argv[2]}"')
PYEOF
"$PY" -m alembic upgrade head
echo "database '$DB' reset"
