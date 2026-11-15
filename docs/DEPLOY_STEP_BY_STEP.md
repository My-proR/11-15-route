# Deploy RoutePilot from scratch (no coding needed)

**Honest note:** nobody has followed this guide on real accounts yet. Button names on Supabase, Google, Render, Vercel and GitHub change, so if a screen looks different, look for the closest label. If a step fails, copy the exact error text and send it to your developer/Claude. Nothing here has been deployed or tested in the cloud by me; everything below was written from the code and current provider documentation.

**Time:** about 2 hours. **Cost:** free while testing; about **₹5,000–5,500 per month** for a real, always-on setup (see Step 12).

You will set up 5 things, in this order:
1. **GitHub** – stores the code
2. **Supabase** – database and login
3. **Google** – the "Continue with Google" button
4. **Render** – runs the API (the "brain") and the worker (the "night shift": auto-dispatch, notifications, invoices)
5. **Vercel** – hosts the website that drivers and riders install

Keep a Notepad file called `routepilot-secrets.txt` on your PC. Paste every password/key you create into it. Never share it, upload it or put it in GitHub.

---
## Step 0 – What you need
- A Windows PC, Chrome or Edge, an email address (a Gmail address for the first admin), a phone for verification, a debit/credit card for the paid plans.
- The file `routepilot.zip`. Right-click → **Extract All** to `C:\routepilot`.
- **Git** (https://git-scm.com/download/win, click Next on everything) and **Node.js LTS** (https://nodejs.org). Restart the PC afterwards.

## Step 1 – Put the code on GitHub
1. Create an account at https://github.com. **New repository** → name `routepilot` → **Private** → **Create**.
2. Open **Command Prompt** (Start → type `cmd`). Run these one at a time (replace YOURNAME):
```
cd C:\routepilot
git init
git add .
git commit -m "first upload"
git branch -M main
git remote add origin https://github.com/YOURNAME/routepilot.git
git push -u origin main
```
Sign in if a browser window asks. Refresh the GitHub page: you should see the files (and no `.env` file).

## Step 2 – Create the database (Supabase)
1. Go to https://supabase.com → sign in with GitHub → **New project**: name `routepilot`, a **strong database password** (save it), region **South Asia (Mumbai)**. Wait ~2 minutes.
2. Left menu **SQL Editor → New query**. Load these **four files, in this order**, each in its own new query: open the file in Notepad, Select All, Copy, paste in Supabase, click **Run**. Each should say "Success". Run each only once.
   1. `C:\routepilot\supabase\migrations\20260101000000_routepilot_core.sql`
   2. `C:\routepilot\supabase\migrations\20260101000001_driver_trip_features.sql`
   3. `C:\routepilot\supabase\migrations\20260101000002_onboarding_autodispatch.sql`
   4. `C:\routepilot\supabase\migrations\20260101000003_billing.sql`
3. New query, paste this one line, Run:
```
GRANT routepilot_app TO postgres;
```
4. Collect these values and save them in your secrets file:
   - **Project URL** and **anon (public) key**: **Project Settings → API**.
   - **JWT secret**: **Project Settings → API → JWT Settings**. If your project instead shows signing keys / a JWKS URL, use `https://YOUR-PROJECT.supabase.co/auth/v1/.well-known/jwks.json`. You need one of the two.
   - **Database connection string**: **Connect** (top bar) → **Session pooler** → copy the URI, replace `[YOUR-PASSWORD]` with your database password. It starts with `postgresql://postgres.`
   - **service_role key**: **Project Settings → API**. Very secret: only the API may use it.

> Use either these SQL files **or** the Render Blueprint's automatic migration (`infra/deployment/render.yaml`), never both on the same database.

## Step 3 – Turn on "Continue with Google"
1. Go to https://console.cloud.google.com → create a project (name `routepilot`).
2. **APIs & Services → OAuth consent screen**: choose **External**, app name `RoutePilot`, your email as support and developer contact, Save.
3. **Credentials → Create credentials → OAuth client ID → Web application**. Under **Authorized redirect URIs** add exactly: `https://YOUR-PROJECT.supabase.co/auth/v1/callback`. Create. Copy the **Client ID** and **Client secret** to your secrets file.
4. Supabase → **Authentication → Sign In / Providers → Google** → switch on, paste Client ID and Secret, Save.
5. Google may show the app as "Testing". For real users, click **Publish app** on the consent screen (basic sign-in needs no review).

## Step 4 – Make two secret codes
In Command Prompt run this twice; save the results as `REALTIME_TICKET_SECRET` and `METRICS_TOKEN`:
```
powershell -c "[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N')"
```

## Step 5 – Start the API on Render
1. https://render.com → sign up with GitHub. **New + → Web Service** → your `routepilot` repository.
2. Fill in:
   - **Name:** routepilot-api  ·  **Region:** Singapore  ·  **Root Directory:** `services/api`  ·  **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`
   - **Health Check Path:** `/health/ready`
   - **Instance type:** **Starter** (always on, about US$7/month). The free type sleeps and must not be used for drivers or SOS.
3. **Environment** → add:

| Name | Value |
|---|---|
| PYTHON_VERSION | 3.13.5 |
| APP_ENV | production |
| FORWARDED_ALLOW_IPS | `*` |
| RATE_LIMIT_PER_MINUTE | 1200 |
| DATABASE_URL | the Session pooler string from Step 2 |
| SUPABASE_URL | your Project URL |
| SUPABASE_JWT_SECRET | the JWT secret (skip if you use JWKS) |
| SUPABASE_JWKS_URL | the JWKS URL (skip if you use the JWT secret) |
| SUPABASE_SERVICE_ROLE_KEY | the service_role key |
| REALTIME_TICKET_SECRET | first code from Step 4 |
| METRICS_TOKEN | second code from Step 4 |
| CORS_ORIGINS | `https://placeholder.vercel.app` (changed in Step 8) |
| GPS_SOS_FEATURE_ENABLED | false |
| LIVE_TRACKING_FEATURE_ENABLED | false |
| NOTIFICATIONS_SEND_REAL | false |

4. **Create Web Service**. When it says **Live**, copy its address (e.g. `https://routepilot-api.onrender.com`).
5. Open `https://routepilot-api.onrender.com/health/ready`. You should see an "ok"-type message. If not, open **Logs** and send the last 20 lines to your developer.
6. Do **not** add a migration command: the tables came from Step 2. The API refuses to start in production without `SUPABASE_URL` (token issuer check) and one of the JWT variables; that is intentional.

## Step 6 – Start the worker on Render (required)
Without the worker, **auto-dispatch does not run, trip offers never expire, no notifications are sent and no invoices are issued.**
1. **New + → Background Worker** → same repository.
2. **Name** routepilot-worker · **Region** Singapore · **Root Directory** `services/api` · **Build Command** `pip install -r requirements.txt` · **Start Command** `python -m app.worker` · **Instance type** Starter.
3. Add the **same environment variables** as the API (copy the whole list, including `REALTIME_TICKET_SECRET` with the *same* value).
4. Create it. In its **Logs** you should see `worker started`.

## Step 7 – Publish the website on Vercel
1. https://vercel.com → sign up with GitHub → **Add New → Project** → import `routepilot`.
2. **Root Directory:** Edit → `apps/web`. Framework: Vite (automatic).
3. **Environment Variables:**

| Name | Value |
|---|---|
| VITE_API_BASE_URL | your Render API address from Step 5 |
| VITE_SUPABASE_URL | your Supabase Project URL |
| VITE_SUPABASE_ANON_KEY | the anon (public) key |

Never add the service_role key here.
4. **Deploy**. Copy the website address, e.g. `https://routepilot-xyz.vercel.app`.
5. Vercel's free **Hobby** plan is for non-commercial use only. Because you will charge customers, use **Pro** (about US$20/month) or another host whose terms allow commercial use.

## Step 8 – Connect the pieces
1. **Supabase → Authentication → URL Configuration:** **Site URL** = your website address. Under **Redirect URLs** add `https://routepilot-xyz.vercel.app/**` (the `/**` matters: invite links return to `/join/...`).
2. **Render → API → Environment:** change `CORS_ORIGINS` to your exact website address, no trailing slash. Do the same on the worker if you added it there. Save; Render restarts.

## Step 9 – Create yourself as Platform Admin (one time)
1. Open your website → **Continue with Google** with your Gmail. You will see "Your account is not set up yet". That is expected; it created your login.
2. Supabase → **Authentication → Users** → find your Gmail → copy its **User UID**.
3. On your PC, in Command Prompt (needs Python 3.13 from https://www.python.org; tick "Add to PATH"):
```
cd C:\routepilot\services\api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set "DATABASE_URL=PASTE-THE-SESSION-POOLER-STRING"
python ..\..\scripts\bootstrap_admin.py --user-id PASTE-UID --email yourgmail@gmail.com
```
4. Refresh the website: you now see the admin menus.

## Step 10 – Create the first customer organization
1. As Platform Admin, create an **Organization** (name, short id, time zone `Asia/Kolkata`) and give the **admin's Gmail address**. Supabase emails that person an invitation; they can ignore the email and simply press **Continue with Google** with that same Gmail (it links to the same account).
2. The organization admin adds routes, shifts, vehicles, drivers and employees (or imports employees from a CSV).
3. **Invite links** (left menu): for each driver/employee press **Invite → Share on WhatsApp**. They tap the link, sign in with Google, and stay signed in. Chrome offers **Install RoutePilot** on their phone.
4. Settings → **Auto-dispatch**: pick the cutoff time (for example 18:00). Leave **Boarding code** off until drivers are comfortable with it.
5. **Charging the customer** (optional): as Platform Admin open **Billing & invoices → Manage** for that organization, tick *Charge*, set ₹ per cab-day (50), and a *Free until* date for a pilot. Put your payment details in the API variables `BILLING_PAYMENT_INSTRUCTIONS`, `BILLING_SELLER_NAME` and `BILLING_SELLER_GSTIN`. Read `BILLING.md` first, especially what happens when an invoice is not paid, and have an accountant approve the invoice layout.

## Step 11 – Check that it works (do this before real users)
- [ ] `/health/ready` is OK; worker log shows `worker started`.
- [ ] You can sign in with Google and see the dashboard.
- [ ] Create 1 vehicle, 1 driver, 2 employees; invite the driver on WhatsApp to your own phone and accept it with a second Google account.
- [ ] Driver toggles **available**; dispatcher plans a trip; driver accepts; boarding code/QR appear.
- [ ] Employee presses **SOS**: the alert appears for admins. (GPS stays off by default; the alert still works.)
- [ ] Switch language to தமிழ், refresh: it stays Tamil.
- [ ] Phone: open the website in Chrome → **Install RoutePilot**.
- [ ] In Supabase, open **Database → Backups** and check what backups your plan includes (daily backups vs point-in-time recovery). Do not trust real customer data to a plan whose backups you have not checked.

## Step 12 – What it costs (checked Oct 2026; confirm on each pricing page)
| Item | Plan | Approx. per month |
|---|---|---|
| Supabase | Pro | US$25 |
| Render API | Starter | US$7 |
| Render worker | Starter | US$7 |
| Vercel | Pro | US$20 |
| **Total** | | **about US$59 (≈ ₹5,000–5,500) + tax** |

Free-for-testing option: Supabase Free (pauses after a week idle), Render free web service (sleeps), Vercel Hobby (non-commercial). Not suitable for real drivers.
**Not included:** road routing server (OSRM) for real route grouping (a small extra server; my own estimate is US$25/month or more, not a quoted price), SMS/WhatsApp (Twilio, pay per message; India needs DLT registration), custom domain.

## Step 13 – Things that are NOT ready yet
- **Road routing (OSRM):** without it, plans are marked "provisional": dispatchers can see them but cannot send or confirm them, and auto-dispatch leaves them as proposals. Set up OSRM and add `OSRM_BASE_URL` before relying on auto-dispatch.
- **Real SMS/WhatsApp/email:** add the Twilio/SMTP keys and set `NOTIFICATIONS_SEND_REAL=true`.
- Single API instance only. Before running two or more, add Redis (`REDIS_URL`) and test.
- Backup restore drill, load test on the final build, privacy/legal review and a security test have not been done. See `KNOWN_LIMITATIONS.md` and `TEST_REPORT.md` for exactly what was and was not tested.

## Step 14 – Updating later
```
cd C:\routepilot
git add .
git commit -m "describe change"
git push
```
Render and Vercel redeploy automatically. If an update includes a new SQL file in `supabase\migrations`, run that file in the Supabase SQL Editor **before** pushing the code that needs it.

## If something goes wrong
| Symptom | Likely cause |
|---|---|
| Website says "network error" | `VITE_API_BASE_URL` wrong, or `CORS_ORIGINS` is not exactly your website address |
| Google button: "redirect_uri_mismatch" | Step 3.3 redirect URI is not exactly `https://YOUR-PROJECT.supabase.co/auth/v1/callback` |
| After Google sign-in you land on the login page again | Step 8.1: your website address is missing from Supabase Redirect URLs (include `/**`) |
| "Your account is not set up yet" | Expected for new people: send them an invite link, or finish Step 9 for yourself |
| Invite link says "not valid" | Already used, expired (7 days) or revoked: create a new one |
| API won't start; log mentions SUPABASE_URL / JWT / REALTIME_TICKET_SECRET / CORS | A required variable from Step 5 is missing, or CORS contains `*` |
| 401 on every request after sign-in | `SUPABASE_URL` does not match the project that issued the login (token issuer check) |
| "permission denied" / role `routepilot_app` errors | The `GRANT routepilot_app TO postgres;` line (Step 2.3) was not run |
| Everyone suddenly gets "too many requests" | `FORWARDED_ALLOW_IPS=*` missing (all users look like one address), or `RATE_LIMIT_PER_MINUTE` too low for a big office |
| Auto-dispatch never runs | Worker not running or missing env vars; check the worker logs; also the organization setting must be on and plans need OSRM |
