# Deployment (none of these were performed or verified)

## Web → Vercel
Root `apps/web`, build `npm run build`, output `dist`. `vercel.json` has SPA rewrite and security headers. Env: `VITE_API_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. Set API `CORS_ORIGINS` to the Vercel origin.

## API + worker
- Render: `infra/deployment/render.yaml` (starting point from the earlier SOS-only project; add worker service and the new env vars). Free tier sleeps; use a paid instance for the worker.
- VPS/Docker: `docker compose up -d --build` with `infra/deployment/Caddyfile` for TLS. Run `alembic` migrations as the `migrate` service before `api`.
- Health: `/health/ready`; metrics: `/metrics` (set `METRICS_TOKEN`).
- Set `ROUTING_PROVIDER` and `OSRM_BASE_URL` for real dispatch; without it plans are provisional.
- Notifications: `NOTIFICATIONS_SEND_REAL=false` logs only; set `true` plus Twilio/SMTP/VAPID credentials to send.

## Mobile
`cd apps/mobile && npm ci && npx expo start`. Set `EXPO_PUBLIC_*`. Builds via EAS (`eas build`); requires an Expo account and Apple/Google developer accounts. Not run on a device.

## Post-deploy checks
`/health/ready` 200; login; `/me`; create org; cross-tenant read returns nothing; SOS alert works with GPS switches off; run `scripts/run_load.sh` against staging with small numbers.
