# Feature checklist (Done = implemented and covered by an automated test; Partial = implemented, not fully verified)

| Area | Status |
|---|---|
| Multi-tenant RLS, roles, audit, idempotency | Done (isolation tests) |
| Master data CRUD (employees, drivers, vehicles, locations, shifts) | Done |
| Employee CSV/XLSX import: preview, duplicates, no overwrite | Done |
| Dispatch: pooling, capacity race-safety, fairness, dry-run, send/confirm/cancel | Done |
| Leave and replacement chain, emergency reassign | Done |
| SOS (alert never blocked by GPS), GPS switches | Done |
| Live vehicle tracking (default off, 3 layers) | Done (API/DB tests; map UI e2e not covered) |
| Notifications outbox, retries, delivery log | Done with fake providers; real providers unverified |
| Reports, audit log viewer, org settings, platform flags | Done |
| Driver availability (on/off duty) | Done (API + web e2e + mobile code) |
| Auto-assignment continuity (same cab / regular driver, fairness slack) | Done (unit + integration tests) |
| Admin Routes & fleet screen (routes -> cabs/drivers -> trips, assign driver) | Done (3 API tests, 1 e2e) |
| Google sign-in, WhatsApp invite links (single-use, expiring, revocable), stay signed in | Done on API/web; real Google/Supabase round trip not tested |
| Tamil + English switch (rider/driver screens), saved on profile | Done (Tamil wording needs native review) |
| Installable PWA (manifest, icons, service worker, install banner); TWA guide for Play Store | Done for PWA; TWA not built |
| Auto-dispatch at cutoff time (continuity, offers, staff summary, never overwrites planned slots) | Done on API/worker/web settings; not run as a long-lived worker |
| Per cab-day billing (default ₹50 + GST, free pilot, monthly invoices, payment recording, pause for overdue invoices, printable invoice) | Done on API/worker/web; no payment gateway; accountant review pending |
| Security hardening from the external audit: JWT issuer check, production config guard, IP-keyed rate limit, website security headers, Render Blueprint with API + worker | Done in code/config; not deployed |
| Passenger boarding code (optional), trip QR (driver shows, passenger scans), manual board | Done on API/web/mobile code; camera + QR rendering not device-tested |
| Realtime WebSocket | Done (API test) |
| Web app | Done (unit + e2e) |
| Mobile app | Partial (typecheck + 6 unit tests incl. no-background-GPS checks; device run pending, see MOBILE_DEVICE_CHECKLIST.md) |
| Load tests | Done (see report) |
| Docker / CI / deployment | Written, unverified |
