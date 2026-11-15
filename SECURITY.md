# Security

Report vulnerabilities privately to the maintainer (do not open a public issue). Include steps to reproduce.

## Controls in the code
- Tenant isolation via Postgres RLS; API connects as `routepilot_app` (NOBYPASSRLS) and sets `app.user_id` per transaction. Composite `(organization_id, id)` foreign keys block cross-tenant references.
- Roles (platform admin, org admin, dispatcher, driver, employee) enforced in API dependencies and RLS policies.
- Supabase JWT verification (HS256 secret or JWKS); a `user_profiles` row is required.
- `audit_logs` is append-only (trigger). Idempotency keys on mutating calls. Rate limiting. CSV formula-injection neutralized on import/export.
- SOS and live-tracking coordinates are blocked by DB triggers when the switches are off, not just in the UI.
- Realtime WebSocket uses a single-use 60-second ticket.
- `/metrics` can be protected with `METRICS_TOKEN`.
- No secrets in the repo; `.env.example` contains placeholders only. The service-role key must never reach the web or mobile bundle.
- `pip-audit` on `requirements.txt` reported no known vulnerabilities at build time; re-run in CI.

## Operator checklist
Rotate any key ever pasted into chat/tickets; set a strong `REALTIME_TICKET_SECRET`; restrict `CORS_ORIGINS`; enable Supabase email confirmation; never run `seed_dev.py` in production (it refuses outside development).
