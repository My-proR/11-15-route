# Architecture

```
Web (Vercel) ─┐                         ┌─ Postgres (Supabase) ── RLS
Mobile (Expo) ┼─ HTTPS ─ FastAPI API ───┤
              │     └ WebSocket (ticket)└─ Redis (rate limit, optional)
Worker ───────┴─ notification outbox ── Twilio / SMTP / Expo / Web Push
API ── OSRM (road routing; optional)
```

## Tenant isolation
- Per request: `SET LOCAL ROLE routepilot_app` + `app.user_id`. Policies call `rp.uid()` and SECURITY DEFINER helpers in schema `rp` (`is_platform_admin`, `member_orgs`, `has_role`, `is_staff`, `my_employee_ids`, `is_driver_of`, `is_passenger_on`, ...).
- `system_session()` (owner role, no RLS) is used only by the worker, migrations and scripts.
- Composite FKs `(organization_id, id)`, capacity trigger with row lock, append-only audit trigger, partial unique indexes.
- Org selected with the `X-Organization-Id` header; membership is verified by RLS, not trusted.

## Dispatch
Eligibility filter (licence/insurance/fitness validity, shift eligibility, local-trip eligibility) → cheapest-insertion pooling with permutation reordering → fairness score with human-readable explanation → proposal. Dry-run preview, then save with an idempotency key. Capacity is race-safe through the DB trigger. State machine: proposed → sent → confirmed → completed/cancelled, with leave/replacement chain and emergency reassign.
If OSRM is not configured or down, plans are marked **provisional**: no distances, and send/confirm is blocked.

## Notifications
Outbox table, worker claims rows (`FOR UPDATE SKIP LOCKED`), backoff 30 s → 1 h, max attempts, dedupe keys. Status `sent` = provider accepted; only Twilio callbacks set `delivered`.

## Realtime
Client requests a ticket (single use, 60 s JWT), opens the WebSocket; hub filters events by audience.

## Feature switches
Env AND platform flag AND org setting; see `FEATURE_SWITCHES.md`.
