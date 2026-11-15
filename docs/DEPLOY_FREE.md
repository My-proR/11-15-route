> No Oracle card? Use docs/DEPLOY_FREE_NO_CARD.md (Render free, no card) instead of the VM steps below.

# Deploy RoutePilot for ₹0 (free tiers)

**Stack:** website → Cloudflare Pages · database + login → Supabase Free · API + worker → one free Oracle Cloud VM · HTTPS address → DuckDNS (free).
Free-tier terms change; each step names the page to confirm. Checked Nov 2026.

## Why this combination (and what I rejected)
| Part | Free choice | Why / limits |
|---|---|---|
| Website | **Cloudflare Pages** | Free tier allows commercial use, unlimited bandwidth, 500 builds/month. **Vercel Hobby is not allowed for commercial use**, so it is not used. |
| Database + login | **Supabase Free** | 500 MB database, 50,000 monthly users, 5 GB/month traffic, 2 projects. Pauses after 1 week of inactivity (the worker touches the database every few seconds, so it will not pause), **no automatic backups** (see Step 9). |
| API + worker | **Oracle Cloud "Always Free" VM** | Always on, so the worker (auto-dispatch, invoices, notifications) really runs. Needs a credit/debit card for identity check; you are not charged if you stay on Always Free resources. |
| Not used: Render free | | Web service sleeps after 15 min (first request takes ~1 min — bad for SOS) and there is no free background worker, so auto-dispatch and billing would never run. |

Realistic ceiling: a few hundred riders, one small city. Move to paid (see DEPLOY_STEP_BY_STEP.md) when you have paying customers who depend on it.

## Step 1 – Code on GitHub
Private repo, then from the project folder in CMD:
```
git init
git add .
git commit -m "RoutePilot"
git branch -M main
git remote add origin https://github.com/YOURNAME/routepilot.git
git push -u origin main
```
`.env.prod` is ignored; never commit secrets.

## Step 2 – Supabase (database + login)
1. https://supabase.com → New project. Name `routepilot`, region **Mumbai (ap-south-1)**, choose a strong database password and **save it**.
2. Project → **Connect** → *Session pooler* → copy the URI (port 5432). Replace `[YOUR-PASSWORD]` with your password. This is `DATABASE_URL`.
3. Project Settings → API: copy **Project URL**, the **anon/publishable** key, and the **service_role** key (server only!). JWKS URL is `PROJECT-URL/auth/v1/.well-known/jwks.json`.

## Step 3 – "Continue with Google"
1. https://console.cloud.google.com → new project → *APIs & Services* → OAuth consent screen (External, app name RoutePilot, your email) → Credentials → **Create OAuth client ID → Web application**.
2. Authorized redirect URI: `https://PROJECTREF.supabase.co/auth/v1/callback`. Copy the Client ID and Secret.
3. Supabase → Authentication → Providers → **Google** → enable, paste both.
4. Supabase → Authentication → URL Configuration: *Site URL* = your Cloudflare Pages address (Step 6); *Redirect URLs* add `https://YOUR-SITE.pages.dev/**` and `routepilot://auth/callback` (for the Android app). Come back and finish this after Step 6.

## Step 4 – Free Oracle VM
1. https://www.oracle.com/cloud/free/ → sign up. Pick the **home region carefully (Mumbai or Hyderabad)**; it cannot be changed. A card is used for verification.
2. Compute → Instances → Create. Image **Ubuntu 24.04**. Shape: *Ampere A1 Flex* with **2 OCPU / 12 GB** (inside the Always Free allowance) — if it says "out of capacity" retry later or try the other availability domain; as a fallback use *VM.Standard.E2.1.Micro* (1 GB, slow but enough for light use).
3. Download the SSH key it offers. Note the **public IP**.
4. Networking → your VCN → Security List → add **Ingress rules** for TCP **80** and **443** from `0.0.0.0/0`.
5. Oracle may reclaim Always Free VMs it sees as idle for a week; a running worker usually counts as activity, but keep an eye on it.

## Step 5 – Free HTTPS address
1. https://www.duckdns.org → sign in with Google → create a name, e.g. `routepilot-api` → set its IP to the VM's public IP.
2. Your API address is `routepilot-api.duckdns.org`.

## Step 6 – Website on Cloudflare Pages
1. https://dash.cloudflare.com → Workers & Pages → Create → **Pages → Connect to Git** → pick the repo.
2. Framework: Vite. **Root directory** `apps/web`, build command `npm run build`, output `dist`.
3. Environment variables (Production):
   `VITE_API_BASE_URL=https://routepilot-api.duckdns.org`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and `NODE_VERSION=22`.
4. Deploy. You get `https://something.pages.dev`. Put it into Supabase *Site URL* (Step 3.4) and into `CORS_ORIGINS` (Step 7).

## Step 7 – API + worker on the VM
In CMD on your PC (replace paths/IP):
```
ssh -i C:\Users\ragul\Downloads\ssh-key.key ubuntu@VM-IP
```
On the VM:
```
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save
exit
```
(The iptables lines matter: Oracle's Ubuntu image blocks ports even after the Security List is open.) Log in again, then:
```
git clone https://github.com/YOURNAME/routepilot.git
cd routepilot
cp .env.prod.example .env.prod
nano .env.prod
```
Fill every value (database URL, Supabase URL/JWKS/service key, `API_DOMAIN`, `CORS_ORIGINS`). Generate two secrets with `openssl rand -hex 32` for `REALTIME_TICKET_SECRET` and `METRICS_TOKEN`. Keep `NOTIFICATIONS_SEND_REAL=false` until you add Twilio/SMTP. Then:
```
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml logs -f migrate api
```
`migrate` creates all tables once and exits. When the API logs "Application startup complete", open `https://routepilot-api.duckdns.org/health/live` — it should say ok. (Caddy fetches the HTTPS certificate on first request; allow a minute.)

## Step 8 – First admin and first customer
Follow **Step 9 and Step 10 of `DEPLOY_STEP_BY_STEP.md`** (sign in once with Google, run `bootstrap_admin.py` with your user id, then create the organization and send invite links on WhatsApp). Do not skip the *Check that it works* list (its Step 11).

## Step 9 – Backups (free plan has none — do this)
Supabase free does not back up for you. Weekly from your PC (install PostgreSQL client tools, then CMD):
```
pg_dump "DATABASE_URL" -Fc -f routepilot_%date:~-4%%date:~4,2%%date:~7,2%.dump
```
Keep copies somewhere outside the VM (Google Drive). Test a restore once.

## Step 10 – Android app
Follow `docs/ANDROID_APP.md` using these values: `EXPO_PUBLIC_API_BASE_URL=https://routepilot-api.duckdns.org`, `APP_LINK_HOST=YOUR-SITE.pages.dev`. EAS cloud builds are free for limited builds per month; Play Store needs a one-time US$25 (not free).

## Updating later
```
cd routepilot && git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```
The website redeploys itself on every `git push`.

## What is still not free
Play Store fee (US$25 once), a real domain (~₹600–900/year, optional), SMS/WhatsApp (Twilio), maps/routing server (OSRM needs ~2–4 GB RAM; the Oracle 12 GB VM can host it, but I have not set that up here), and GST invoices need an accountant.

## Not verified
None of this was run on real Oracle/Supabase/Cloudflare accounts from here. `docker-compose.prod.yml` was written but not started in the sandbox (no Docker). Free-tier limits come from third-party summaries dated 2026; confirm on the pricing pages. Alembic via the Supabase *session pooler* has not been tested; if `migrate` fails on a permission error, use the direct connection string in `DATABASE_URL` for that first run (Supabase shows it under *Connect → Direct*; it may need IPv6, which Oracle supports only if enabled — otherwise run migrations from your PC with `alembic upgrade head`).
