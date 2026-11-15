# Deploy RoutePilot free, with NO card (Render + Supabase + Cloudflare)

Nothing here asks for a credit or debit card. Everything is free for testing **and** a small first production pilot, with one honest limit: **Render's free server goes to sleep after 15 minutes without visitors**, and waking takes about a minute. A free "pinger" visits it every 10 minutes to keep it awake (Step 5). If the pinger or Render has a hiccup, the first request afterwards is slow — fine for testing and a pilot, **not** for customers who rely on SOS. When you have paying customers, move the API to a paid always-on plan (about US$7/month, see `DEPLOY_STEP_BY_STEP.md`).

How it fits together: the website runs on **Cloudflare Pages**, login and database on **Supabase**, and the API plus the background worker (auto-dispatch, invoices, notifications) run in **one free Render service** (`RUN_WORKER_IN_API=true`).

Keep your `my-values.txt` open. You need: PROJECT URL, ANON KEY, SERVICE ROLE KEY, DATABASE URL.

## Step 1 – Put the project on GitHub (private)
1. Sign in at github.com (no card needed). Click **+ → New repository**, name `routepilot`, choose **Private**, click **Create**.
2. In CMD, in the project folder:
```
cd "C:\Users\ragul\Documents\routepilot (1)"
git remote add origin https://github.com/YOURNAME/routepilot.git
git branch -M main
git push -u origin main
```
(If git asks you to log in, a browser window opens; sign in.)

## Step 2 – API on Render
1. Go to **render.com** → **Get Started** → sign in **with GitHub** (no card).
2. **New + → Web Service** → pick your `routepilot` repo → **Connect**.
3. Fill the form exactly:
   - **Name:** `routepilot-api`
   - **Region:** Singapore
   - **Root Directory:** `services/api`
   - **Runtime / Language:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'"`
   - **Instance Type:** **Free**
4. Scroll to **Environment Variables → Add** and enter these (name on the left, value on the right):

| Name | Value |
|---|---|
| `PYTHON_VERSION` | `3.13.5` |
| `APP_ENV` | `production` |
| `RUN_WORKER_IN_API` | `true` |
| `FORWARDED_ALLOW_IPS` | `*` |
| `RATE_LIMIT_PER_MINUTE` | `1200` |
| `DATABASE_URL` | your DATABASE URL (Session pooler) |
| `SUPABASE_URL` | your PROJECT URL |
| `SUPABASE_JWKS_URL` | PROJECT URL followed by `/auth/v1/.well-known/jwks.json` |
| `SUPABASE_SERVICE_ROLE_KEY` | your SERVICE ROLE KEY (secret; only here) |
| `REALTIME_TICKET_SECRET` | a long random text (about 40 letters/numbers; make one at bitwarden.com/password-generator) |
| `METRICS_TOKEN` | a different long random text |
| `CORS_ORIGINS` | leave a temporary `https://example.com`; you fix it in Step 4 |
| `GPS_SOS_FEATURE_ENABLED` | `false` |
| `LIVE_TRACKING_FEATURE_ENABLED` | `false` |
| `NOTIFICATIONS_SEND_REAL` | `false` |

5. Click **Create Web Service**. Open the **Logs** tab; the first deploy takes several minutes. It is working when you see "Application startup complete" and "worker started".
6. At the top of the page copy your address, like `https://routepilot-api.onrender.com`. Put it next to `API ADDRESS` in your notepad. Open `YOURADDRESS/health/live` in the browser; it should show ok (the first visit may take a minute).

## Step 3 – Website on Cloudflare Pages
1. Go to **dash.cloudflare.com**, create a free account (no card). **Workers & Pages → Create → Pages → Connect to Git** → choose your repo.
2. Settings: **Framework preset** Vite · **Root directory** `apps/web` · **Build command** `npm run build` · **Build output directory** `dist`.
3. **Environment variables** (Production): `VITE_API_BASE_URL` = API ADDRESS · `VITE_SUPABASE_URL` = PROJECT URL · `VITE_SUPABASE_ANON_KEY` = ANON KEY · `NODE_VERSION` = `22`.
4. Click **Save and Deploy**. When finished copy your site address, like `https://routepilot.pages.dev`, into the notepad as `WEBSITE ADDRESS`.

## Step 4 – Connect the pieces
1. **Render → your service → Environment**: change `CORS_ORIGINS` to your WEBSITE ADDRESS (no slash at the end). Save; Render redeploys.
2. **Supabase → Authentication → URL Configuration**: *Site URL* = WEBSITE ADDRESS. Under *Redirect URLs* add `WEBSITE ADDRESS/**` and `routepilot://auth/callback`.
3. **Google sign-in**: follow Step 3 of `DEPLOY_FREE.md` (Google Cloud OAuth client; no card needed for this).

## Step 5 – Keep it awake (free pinger)
1. Go to **cron-job.org**, create a free account.
2. **Create cronjob**: Title `routepilot`, URL `YOUR-API-ADDRESS/health/live`, schedule **every 10 minutes**. Save.
3. Next day, open the job's history: all runs should say success.
(You can use uptimerobot.com instead, 5-minute checks.)

## Step 6 – Backups (Supabase free has none)
Follow Step 9 of `DEPLOY_FREE.md` weekly.

## Step 7 – First admin, first customer, Android app
- Admin and customer: Steps 9–10 of `DEPLOY_STEP_BY_STEP.md`.
- Android app: put API ADDRESS in `eas.json` → `EXPO_PUBLIC_API_BASE_URL` and the site name (without https://) in `APP_LINK_HOST`, then `eas build` (free, no card). Publishing on Google Play costs a one-time US$25 and is the only unavoidable cost; you can share the APK directly without it.

## Limits to remember
- 512 MB memory: fine for a small pilot; many simultaneous users need a paid plan.
- Render's free plan allows about 750 running hours a month — enough for ONE always-awake service. Don't add a second free service to the same account.
- Notification texts/WhatsApp (Twilio) and routing (OSRM) are not free, so `NOTIFICATIONS_SEND_REAL=false` and auto-dispatch suggestions need routing set up later.

## Not verified
Not run on real Render, Supabase or Cloudflare accounts from here. The embedded-worker code is unit-tested (starts and stops with the API); the migration-on-start command and Render's free-tier rules should be checked on the first deploy. Free-tier terms come from 2026 third-party summaries; confirm on render.com/pricing.
