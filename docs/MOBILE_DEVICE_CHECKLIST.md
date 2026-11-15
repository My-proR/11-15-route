# Mobile device test checklist (not yet performed — run on a real Android device)

Build: `cd apps/mobile && npm ci && npx expo run:android` (dev build; push tokens need a dev/EAS build, not Expo Go).

Automated (done): `npm test` verifies that background location is disabled in `app.json`, no background/task location API is used, and the only `watchPositionAsync` is in the in-progress-trip hook.

Manual:
1. Tracking OFF (default): driver starts a trip → no permission prompt, no `/tracking/position` calls (check API logs/metrics).
2. Admin enables env + platform flag + org setting → within ~60 s the driver app shows "Starting location sharing"; permission prompt appears only now.
3. Deny permission → trip still works, notice shown.
4. Staff map (`/tracking`) shows the vehicle moving at the configured interval; unplug network → app recovers.
5. Complete trip → sharing stops; position row removed; android status bar shows no location icon.
6. Admin turns tracking off mid-trip → driver stops reporting within ~60 s; API returns 403 for stray posts.
7. Lock screen / app backgrounded → no location updates (foreground only, by design).
8. SOS with GPS off, denied, and on: alert is always created; coordinates attached only when on and user agrees.
9. Driver duty switch: "Go off duty" → dispatcher's auto-assignment no longer offers this driver trips; "Go available" restores it.
10. Trip QR: driver (trip in progress) taps Show trip QR; employee taps Scan trip QR to board (camera permission prompt appears only now) → passenger shows as boarded for the driver/dispatcher. Try an expired QR (wait >60 s with the screen frozen) and a QR for another trip: both must be refused.
11. Boarding code: with Settings → Boarding verification on, driver cannot mark boarded without the 4-digit code.
12. Push: confirmed-trip notification arrives; tapping opens the app.
