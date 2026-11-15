# Contributing
- API: `cd services/api && ruff check app tests && pytest   # or: make lint test` (needs Postgres; see `docs/LOCAL_DEV.md`).
- Web: `cd apps/web && npm run typecheck && npm test && npm run build`.
- SQL changes: Alembic is the source of truth. After editing, run `scripts/export_supabase_sql.sh`; CI fails if the exported file differs.
- API changes: regenerate `packages/shared-types` from the OpenAPI schema.
- Every new table needs `organization_id`, RLS policies, and a cross-tenant test.
- Do not add code paths that bypass the SOS rule: alerts are never blocked by location failure.
