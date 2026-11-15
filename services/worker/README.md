# RoutePilot worker

Runs `python -m app.worker` from `services/api` (shared code). One instance is enough; several are safe (rows are claimed with `FOR UPDATE SKIP LOCKED`).

Every ~5 seconds it: expires unanswered driver assignments and offers the next replacement, delivers pending notifications
(only when `NOTIFICATIONS_SEND_REAL=true`; SMS/WhatsApp additionally need the platform flag), and hourly purges expired SOS coordinates,
tracking history, old idempotency keys and stale last-positions.

Environment: same `DATABASE_URL` (owner role, it acts across organizations — never expose it to the browser), provider credentials as in `.env.example`.
