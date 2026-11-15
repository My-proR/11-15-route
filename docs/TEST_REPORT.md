# Test report (observed results, build sandbox: 2 vCPU, local Postgres 16, no Docker)

| Suite | Result |
|---|---|
| API pytest (smoke, pooling, tenant isolation + roles, dispatch, leave, SOS GPS, live tracking, notifications, import/reports/realtime, boarding code + trip QR, driver availability, continuity, fleet, invite links + Google-style first sign-in, language, auto-dispatch, per cab-day billing + suspension, JWT issuer/config) | 125 passed |
| `ruff check app tests` | clean |
| `pip-audit -r requirements.txt` | no known vulnerabilities |
| Web vitest (api, forms, sos, ui, boarding code, trip QR, i18n, join page, install prompt, WhatsApp link) | 39 passed |
| Web `tsc --noEmit` / `npm run build` / `npm audit --omit=dev` | OK, 0 production vulnerabilities after upgrading zod 3→4 and @hookform/resolvers 3→5 (map chunk ~1.08 MB lazy); `npm audit` still lists a moderate advisory in vitest (dev-only test runner), not shipped |
| Validation package | 4 passed |
| Playwright e2e vs real API + Vite (incl. Routes & fleet, driver duty switch, Google button + Tamil switch, PWA manifest/icons, invalid invite, Invite links page, Billing) | 15 passed (13 desktop + mobile-viewport run) |
| Mobile `tsc --noEmit` + vitest (16: share gating, throttle, payload, no background location (reads `app.config.ts`), credential + QR input rules, invite-link/OAuth-callback parsing, Tamil strings) | clean, 16 passed |
| Mobile Android bundle: `expo export --platform android` (Metro + Hermes bytecode) and `expo prebuild --platform android` | both succeed; generated manifest checked for package, `routepilot://` scheme, verified `https` `/join` App Link, RECORD_AUDIO removed, allowBackup=false. **No APK was built and nothing was run on a phone/emulator** (the sandbox cannot download the Android SDK) |

## Load test (Locust, headless)
Config: 200 users, spawn 20/s, 90 s, 300 seeded employees, **1 uvicorn worker**, same machine as Postgres and Locust (so the generator competes for 2 vCPUs).
Mix: employee 10, dispatcher 2, driver 2.

Result (console final summary): **8,581 requests, 0 failures, ~95 req/s**, median 18 ms, p95 ~100 ms, p99 ~200 ms, max ~316 ms. Slowest median: dry-run allocate 49 ms, SOS 41 ms, reports 46 ms. Raw output: `docs/load_run_200users.txt`. (The `run_stats.csv` flushed by Locust shows 8,493 requests, still 0 failures; it was written by a periodic flush before the last second.)
An earlier 200-user attempt was discarded: Postgres in the sandbox was killed mid-run, producing 500s unrelated to the API. A prior 50-user, 60 s run: 1,383 requests, 0 failures.

Caveats: loopback network, no TLS, small data, no OSRM (provisional plans), no real notification providers. Not representative of production latency.

## Not tested
Docker/compose, real Supabase auth and the Google OAuth round trip, installing the PWA on a real phone, a Play Store/TWA build, WhatsApp opening from a real phone, Twilio, SMTP, Expo push, Web Push, OSRM, mobile devices, any cloud deployment, Windows commands.
Load test numbers predate the trip-credentials, invite, auto-dispatch and billing changes (not re-run). Billing is tested through the API and worker functions with a fixed clock (including overdue pause and payment restore), not by waiting for real month ends. Auto-dispatch is tested by calling the worker function directly with a fixed clock, not by running the worker process for a day.
