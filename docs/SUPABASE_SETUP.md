# Supabase setup
1. Create a project (choose a region near users, e.g. Mumbai `ap-south-1`).
2. Apply `supabase/migrations/20260101000000_routepilot_core.sql` then `20260101000001_driver_trip_features.sql`, then `20260101000002_onboarding_autodispatch.sql`, then `20260101000003_billing.sql` (SQL editor or `supabase db push`). It creates tables, the `routepilot_app` role, `rp` helper functions and RLS policies. Do **not** run `seed_dev.py`.
3. Grant: the migration creates `routepilot_app` (NOLOGIN, NOBYPASSRLS) but does not grant it to your login role. Run once: `GRANT routepilot_app TO postgres;` (or the role used in `DATABASE_URL`) so the API can `SET LOCAL ROLE routepilot_app`.
4. Auth: enable email, set Site URL/redirects to your web origin. API env: `SUPABASE_URL`, and either `SUPABASE_JWT_SECRET` or `SUPABASE_JWKS_URL`.
5. Create the first platform admin: `python scripts/bootstrap_admin.py --email you@example.com --invite you@example.com` (or `--user-id <uuid>` for an existing user) (needs `SUPABASE_SERVICE_ROLE_KEY` for invite; keep it server-side).
6. Use the pooled connection string for `DATABASE_URL` (if you use the transaction pooler and see prepared-statement errors, disable asyncpg's statement cache; this was not configured or tested) and the direct string for `MIGRATION_DATABASE_URL`.
Not tested against a real Supabase project in the build environment.


Google sign-in and invite-link setup: see `EASY_ONBOARDING.md`.
