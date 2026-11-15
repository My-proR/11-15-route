# RoutePilot

Multi-tenant employee transportation platform: employee transport requests, dispatch with road routing and pooling, driver/vehicle management, leave and replacement chain, SOS, optional live vehicle tracking, notifications, reports and audit.

| Part | Path | Stack |
|---|---|---|
| Web app | `apps/web` | React 19, TS strict, Vite, Tailwind 4, TanStack Query, Zustand, RHF + Zod |
| Android app | `apps/mobile` | Expo + expo-router; Google sign-in, invite links, Tamil; build with EAS (`docs/ANDROID_APP.md`) |
| API | `services/api` | FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, PostgreSQL, Redis (optional) |
| Worker | `services/worker` | Notification outbox runner (`python -m app.worker`) |
| DB | `supabase/` | Migrations (exported from Alembic) and seed |
| Tests | `tests/` | Playwright e2e, Locust load; unit/integration live beside each app |

Security model: every API request runs in a transaction as a non-bypass DB role (`routepilot_app`) with Postgres Row Level Security. Tenant isolation is enforced in the database, not only in handlers. See `docs/ARCHITECTURE.md`.

## Quick start (Linux/macOS)
```bash
cp .env.example .env            # fill placeholders
docker compose up -d db redis   # or: make up (whole stack)
cd services/api && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && alembic upgrade head
python ../../scripts/seed_dev.py --employees 20      # development only
uvicorn app.main:app --reload
cd ../../apps/web && npm ci && npm run dev
```
Windows commands are in `docs/LOCAL_DEV.md`.

## Docs
`docs/ARCHITECTURE.md` · `LOCAL_DEV.md` · `SUPABASE_SETUP.md` · `DEPLOY_STEP_BY_STEP.md` (beginner) · `DEPLOYMENT.md` · `FEATURE_SWITCHES.md` (SOS GPS + live tracking) · `TRIP_VERIFICATION.md` (boarding code + trip QR) · `EASY_ONBOARDING.md` (Google sign-in, invite links, Tamil, auto-dispatch) · `ANDROID_APP.md` · `BILLING.md` · `AUDIT_COMPARISON.md` · `COST_INDIA.md` · `PRIVACY_RETENTION.md` · `TEST_REPORT.md` · `FEATURE_CHECKLIST.md` · `KNOWN_LIMITATIONS.md`. Also `SECURITY.md`, `CONTRIBUTING.md`.

**Read `docs/KNOWN_LIMITATIONS.md` before deploying.** Docker Compose, real Supabase, Twilio, Expo push, Web Push, OSRM and mobile devices were not exercised in the build environment.
