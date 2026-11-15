# External audit archive vs this build (2026-11-12)

The audit archive is a fork of the earlier SOS-only release candidate: live tracking removed (its migration deletes tracking data), no invite links / Google sign-in / auto-dispatch / billing / installable app, and only 4 database-free tests actually run. This build keeps tracking (your decision), has those features, and runs the full test suites.

| Audit item | Decision |
|---|---|
| Render Blueprint: API + worker, pre-deploy migration, JWT config, SOS GPS off, no stale env names | **Adopted** (`infra/deployment/render.yaml`), plus tracking off, billing, proxy and rate-limit variables. The `1c-2g` plan id was replaced with the long-standing `starter` id because Render's changelog does not confirm `1c-2g`. |
| JWT issuer validation + production guard + 4 tests | **Adopted** |
| Vercel security headers (HSTS, frame-ancestors, object-src, base-uri) | **Adopted** |
| Tests selected as `no_db` run without PostgreSQL | **Adopted** |
| Rate-limit key = client IP (not caller-controlled token suffix) | **Adopted with a caveat**: needs `FORWARDED_ALLOW_IPS=*` behind a proxy and a higher `RATE_LIMIT_PER_MINUTE` for offices sharing one address. The audit's own text says the key hashes the bearer token, which contradicts its code. |
| Boarding code primary key scoped per trip (migration 0004) | **Not needed today**: boarding rows belong to a trip-passenger row that is never moved between trips. Worth adding if passenger moves are introduced. |
| SOS-only migration (deletes tracking data, blocks writes) | **Rejected** (conflicts with keeping live tracking). |
| 14 audit/DR/incident documents | Findings absorbed above; their "not verified" items remain unverified here too (cloud deploy, restore drill, device tests, load test). |

Found while merging: suspension of an organization was already enforced by the database (paused organizations disappear from row-level security), but members then could not even see their own membership. A definer function (`rp.my_orgs()`) now lets paused users sign in, see the paused notice, and lets org admins reach Billing to resolve it.
