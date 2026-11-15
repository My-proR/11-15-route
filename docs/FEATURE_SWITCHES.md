# Feature switches: SOS GPS and live tracking

Effective state = **deployment env** AND **platform flag** AND **organization setting**. All default to **off**. The API reports `blocked_by` = `deployment` | `platform` | `organization`.

| Feature | Env var | Notes |
|---|---|---|
| SOS GPS location | `GPS_SOS_FEATURE_ENABLED` | The SOS alert is always created. If GPS is off, denied or fails, the alert is still raised without coordinates. |
| Live vehicle tracking | `LIVE_TRACKING_FEATURE_ENABLED` | Drivers report position (foreground only) at >= `LIVE_TRACKING_MIN_INTERVAL_SECONDS`. Admins/dispatchers can see any vehicle when on. Off by default to cut deployment cost (no write traffic, no map tiles). |

Enforcement is layered: UI hides controls, API rejects, and DB triggers block coordinate/location inserts when off. Turning a switch off stops new writes; retention rules in `PRIVACY_RETENTION.md` govern old rows. Mobile uses foreground location only; background location is disabled in `app.json`. The app explains why before asking for permission.
