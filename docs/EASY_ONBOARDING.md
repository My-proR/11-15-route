# Easy sign-in, invite links, Tamil/English, install on phones, auto-dispatch

## For people using the app (drivers, employees)
1. Your admin sends you a **WhatsApp link**.
2. Tap it → **Continue with Google** → done. You stay signed in on that phone.
3. Android Chrome shows **Install RoutePilot** (iPhone: Share → Add to Home Screen). It then opens like any app.
4. Switch **English / தமிழ்** at any time; the choice follows your account to other phones.

## For admins and dispatchers
- **Invite links** (left menu): lists drivers and employees that do not have an account yet. Tap **Invite** → **Share on WhatsApp** (a message with the link is prepared, with +91 added to 10-digit numbers).
- A link works **once**, expires after **7 days**, is tied to that one driver/employee record, and can be **revoked**. Only a hash is stored on the server, so the link cannot be looked up later: if lost, create a new one.
- To lock a link to one Google account, create it through the API with `"email": "name@gmail.com"` (the UI sends links without an email lock).
- Dispatchers can invite drivers and employees; only org admins can invite dispatchers (API).
- **Settings → Auto-dispatch**: pick the cutoff time and how many days ahead. At the cutoff the worker plans every shift and direction, **keeping the same cab and driver** for the same people when it can, and (optionally) sends offers to drivers. You get a notification listing anything that needs attention. Slots you already planned are never changed. It runs once per organisation per date.

## One-time setup (Google login in Supabase)
1. Google Cloud Console → *APIs & Services* → *OAuth consent screen* (External) → *Credentials* → **Create OAuth client ID → Web application**.
2. Authorised redirect URI: `https://<your-project>.supabase.co/auth/v1/callback`.
3. Supabase → *Authentication → Providers → Google* → paste Client ID and Secret → Enable.
4. Supabase → *Authentication → URL Configuration*: **Site URL** = your web address; **Redirect URLs** must include `https://<your-web-address>/**` (the `/**` is needed so `/join/<token>` links return correctly).
5. Run the third SQL file (`20260101000002_onboarding_autodispatch.sql`) once.
6. Make sure the **worker** service is running; auto-dispatch is executed by it.

Security notes: signing in with Google alone gives **no access**. A Google account that is not linked to an invite or a member record gets "your account is not set up yet". The server checks the invite token, its expiry, single use, optional email lock, the organisation status, and refuses to take over a record already linked to another account.

## Install on Android / Play Store
See `ANDROID_APP.md`.
