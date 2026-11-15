#!/usr/bin/env bash
# Runs the browser end-to-end suite against a REAL API + Postgres + the Vite dev server (dev-token sign-in is available only in dev builds).
# Needs: a reachable Postgres (default postgresql://postgres@localhost:5433), Node, Python deps installed, Playwright's Chromium.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG="${E2E_PG_URL:-postgresql://postgres@localhost:5433}"
export DATABASE_URL="$PG/rp_e2e" APP_ENV=development SUPABASE_JWT_SECRET="e2e-secret-e2e-secret-e2e-secret-12" CORS_ORIGINS="http://localhost:5173" \
  RATE_LIMIT_PER_MINUTE=100000 RATE_LIMIT_SENSITIVE_PER_MINUTE=100000 NOTIFICATIONS_SEND_REAL=false
PY="${PYTHON:-python}"
psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS rp_e2e WITH (FORCE)" -c "CREATE DATABASE rp_e2e"
( cd "$ROOT/services/api" && alembic upgrade head >/dev/null && $PY "$ROOT/scripts/seed_dev.py" --json "$ROOT/tests/e2e/.seed.json" >/dev/null )
( cd "$ROOT/services/api" && $PY -m uvicorn app.main:app --port 8099 >"$ROOT/tests/e2e/api.log" 2>&1 & echo $! >"$ROOT/tests/e2e/.api.pid" )
( cd "$ROOT/apps/web" && VITE_API_BASE_URL=http://localhost:8099 VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_ANON_KEY=e2e-placeholder npx vite --port 5173 >"$ROOT/tests/e2e/web.log" 2>&1 & echo $! >"$ROOT/tests/e2e/.web.pid" )
trap 'kill $(cat "$ROOT/tests/e2e/.api.pid" "$ROOT/tests/e2e/.web.pid") 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -sf localhost:8099/health/ready >/dev/null && curl -sf localhost:5173 >/dev/null && break; sleep 1; done
cd "$ROOT/tests/e2e" && npx playwright test "$@"
