# Cost estimate (India) — approximate, verify current pricing

Prices change; figures below are rough planning numbers in INR/month and are **not** quotes.

| Stage | Setup | Approx. monthly |
|---|---|---|
| Pilot (1 org, <100 employees) | Supabase free, Vercel hobby, Render free API (sleeps) | ~₹0; SMS extra |
| Small prod (<1,000 employees) | Supabase Pro (~US$25), small API VPS/instance (~₹500–1,500), Vercel free/pro | ~₹3,000–5,000 |
| Growing (several orgs) | Add Redis, separate worker, OSRM on a VPS (~₹1,500–4,000), monitoring | ~₹8,000–15,000 |

Variable costs: SMS/WhatsApp via Twilio (per message; DLT registration needed for Indian SMS), email SMTP, map tiles. **Live tracking and SOS GPS default off** to avoid write volume and tile usage. Self-hosted OSRM needs India OSM extract (several GB RAM). Load test (200 simulated users, 1 API worker, 2 vCPU) suggests one small instance handles pilot-scale traffic, but see `TEST_REPORT.md` for limits.
