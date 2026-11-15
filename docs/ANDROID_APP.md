# RoutePilot Android app (`apps/mobile`)

A native Expo / React Native app for **drivers and employees** (dispatchers and admins use the web console).
It does what the web app does for those roles: trips, accept/decline, start/complete, boarding code + QR, duty switch,
ride requests, SOS, alerts, organization switch — plus, new in this build:

| Feature | How it works |
|---|---|
| **Google sign-in** | Opens the system browser (Chrome Custom Tab) with Supabase OAuth + PKCE and returns to `routepilot://auth/callback`. Not a WebView (Google blocks those). |
| **Stay signed in** | Session is stored in the Android Keystore (expo-secure-store) and refreshed automatically. |
| **WhatsApp invite links** | `https://YOUR-WEB-HOST/join/<token>` opens the app (Android App Links) or `routepilot://join/<token>`. A "paste invite link" box on the sign-in screen is the fallback. Sign in with Google → account linked → trips appear. |
| **Tamil + English** | Device language is used first; switch on the sign-in screen or Account. The choice is saved to the server profile too. |
| **Paused organization** | If billing or an admin pauses the organization, the app shows a clear "access paused" screen instead of errors. |

The app is **not built here**: this sandbox cannot download the Android SDK. Build it with EAS (Expo's cloud build, free tier is enough) — no Android Studio needed.

## One-time setup (Windows CMD)
1. Create a free account at https://expo.dev.
2. Pick your **package name** (reverse-domain, e.g. `in.yourcompany.routepilot`). It can never change after the first Play Store upload.
3. Edit `apps/mobile/eas.json` → `build.base.env`: set `EXPO_PUBLIC_API_BASE_URL`, `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_ANON_KEY` (the **anon** key only — never the service-role key), `ANDROID_PACKAGE`, and `APP_LINK_HOST` (your web host without `https://`, e.g. `routepilot.vercel.app`).
4. In **Supabase → Authentication → URL Configuration → Redirect URLs** add: `routepilot://auth/callback`
5. Install tools and link the project:
```
cd apps\mobile
npm ci
npm install -g eas-cli
eas login
eas init
```
`eas init` prints a project id; put it in `eas.json` env as `EAS_PROJECT_ID` (needed for push notifications).

## Build an installable APK (to test on your own phone)
```
cd apps\mobile
eas build --platform android --profile preview
```
When it finishes EAS prints a link/QR to the `.apk`. Open it on the phone and allow "install unknown apps". The first build asks to create a keystore — say **yes** and let EAS keep it (`eas credentials` can download a backup; losing it means you cannot update the Play Store app).

## Publish on the Play Store
1. Play Console developer account (one-time US$25), create the app with the **same package name**.
2. `eas build --platform android --profile production` → produces an `.aab`.
3. Upload it to the **Internal testing** track first (`eas submit --platform android --profile production` once you have a Play service-account key, or upload by hand).
4. Fill in the Data safety form honestly: collects email, name, phone (optional), location (only if your org enables it, foreground only), push token. Add your privacy-policy URL.
5. New personal Play accounts must run a closed test with 12+ testers for 14 days before production access — plan for this.

## Make WhatsApp links open the app (App Links)
1. Set `APP_LINK_HOST` (above) and rebuild.
2. Get the signing certificate fingerprint: `eas credentials` → Android → show keystore → copy **SHA-256**. (If you use Play App Signing, use the SHA-256 of the *app signing key* shown in Play Console → App integrity.)
3. Publish `apps/web/public/.well-known/assetlinks.json` (Vercel serves it from the web app):
```json
[{"relation":["delegate_permission/common.handle_all_urls"],
  "target":{"namespace":"android_app","package_name":"in.yourcompany.routepilot",
            "sha256_cert_fingerprints":["AA:BB:...:FF"]}}]
```
4. Check it opens: `https://YOUR-WEB-HOST/.well-known/assetlinks.json` must return that JSON. Until it does (or if the app is not installed) the link simply opens the website, which works too.

## Push notifications
Needs the EAS project id (above). Android also needs FCM credentials: Firebase console → create project → add the Android app (same package) → download `google-services.json`, then `eas credentials` → Android → upload an FCM V1 service-account key. Without this, in-app alerts still work; push is simply off.

## Run locally against your backend
```
cd apps\mobile
set EXPO_PUBLIC_API_BASE_URL=http://YOUR-PC-LAN-IP:8000
set EXPO_PUBLIC_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
set EXPO_PUBLIC_SUPABASE_ANON_KEY=YOUR-ANON-KEY
npx expo start
```
(Google sign-in and App Links need a real build; Expo Go cannot use the `routepilot://` redirect reliably.)

## Checks done here vs not done
Done: TypeScript clean, 16 unit tests, Android JS bundle compiled with Hermes, native project generated (`expo prebuild`) with the manifest inspected, `expo-doctor` clean apart from checks that need the internet.
**Not done:** an actual APK build, running on a phone or emulator, real Google/Supabase sign-in, App Link verification, push delivery. Run `docs/MOBILE_DEVICE_CHECKLIST.md` on a real phone before giving it to staff.

## Alternative: PWA / TWA
The installable web app still works with no build at all (open the site in Chrome → Install). A Bubblewrap TWA is the lighter Play Store route if you do not need native features (see git history of this file or `docs/EASY_ONBOARDING.md`).
