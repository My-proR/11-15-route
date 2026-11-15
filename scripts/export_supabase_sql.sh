#!/usr/bin/env sh
# Writes the schema SQL as timestamped Supabase CLI migrations (supabase/migrations/). One file per Alembic revision.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SQL="$ROOT/services/api/alembic/sql"
OUT="$ROOT/supabase/migrations"
cat "$SQL/0001_schema.sql" "$SQL/0002_rls.sql" > "$OUT/20260101000000_routepilot_core.sql"
cp "$SQL/driver_trip_features.sql" "$OUT/20260101000001_driver_trip_features.sql"
cp "$SQL/onboarding_autodispatch.sql" "$OUT/20260101000002_onboarding_autodispatch.sql"
cp "$SQL/billing.sql" "$OUT/20260101000003_billing.sql"
echo "wrote 4 migration files in $OUT"
