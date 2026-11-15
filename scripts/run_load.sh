#!/usr/bin/env bash
# Reproducible local load run. Single uvicorn worker + local Postgres on the SAME machine: numbers describe this setup only, not production.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG="${E2E_PG_URL:-postgresql://postgres@localhost:5433}"
USERS="${USERS:-50}"; SPAWN="${SPAWN:-10}"; DURATION="${DURATION:-60s}"; EMPLOYEES="${EMPLOYEES:-200}"; WORKERS="${WORKERS:-1}"
export DATABASE_URL="$PG/rp_load" APP_ENV=development SUPABASE_JWT_SECRET="load-secret-load-secret-load-secret-1" RATE_LIMIT_PER_MINUTE=1000000 RATE_LIMIT_SENSITIVE_PER_MINUTE=1000000 NOTIFICATIONS_SEND_REAL=false
PY="${PYTHON:-python}"
psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS rp_load WITH (FORCE)" -c "CREATE DATABASE rp_load"
( cd "$ROOT/services/api" && alembic upgrade head >/dev/null && $PY "$ROOT/scripts/seed_dev.py" --employees "$EMPLOYEES" --quiet --json "$ROOT/tests/load/.seed.json" >/dev/null )
( cd "$ROOT/services/api" && $PY -m uvicorn app.main:app --port 8098 --workers "$WORKERS" --log-level warning >"$ROOT/tests/load/api.log" 2>&1 & echo $! >"$ROOT/tests/load/.api.pid" )
trap 'kill "$(cat "$ROOT/tests/load/.api.pid")" 2>/dev/null; true' EXIT
for i in $(seq 1 40); do curl -sf localhost:8098/health/ready >/dev/null && break; sleep 1; done
cd "$ROOT/tests/load" && locust -f locustfile.py --headless -u "$USERS" -r "$SPAWN" -t "$DURATION" --host http://localhost:8098 --csv results/run --only-summary
