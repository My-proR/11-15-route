# Trips: availability, assignment, boarding verification

## Who gets a trip (auto-assignment with continuity)
1. **Drivers mark themselves available / off duty** in the app (web "My trips" or mobile). Only available, licensed, non-leave drivers who are free for the slot are considered. A driver cannot go off duty during a trip in progress; already-assigned trips stay assigned (the app warns).
2. **Auto-dispatch** groups passengers by road route and drop/pickup location (capacity, detour and compliance limits first), then picks the cab and driver:
   - **Same cab:** the cab that carried these passengers on the same shift/direction in the last 14 days (never a cab with more than 3 spare seats).
   - **Same driver:** the cab's regular driver (set on the vehicle), else the driver who most recently drove that cab, **if eligible and not more than 25% less fair** than the fairest candidate. Otherwise fairness decides. The plan explains each choice ("same cab as recent trips…", "this cab's regular driver").
3. Driver gets an offer, **accepts or declines**, dispatcher confirms (existing flow), then trip details appear for driver and passengers.
4. **Admin / dispatcher can override:** **Routes & fleet** screen (staff menu) lists each route, its cabs and drivers, and the trips for a date. "Assign driver / Change driver" shows eligible drivers fairest-first and sends them an offer.
   *A cab belongs to a route through its default route on the vehicle (or the one-day route override). The auto-pooler groups people by geography, not by the route master data.*

## Boarding verification (both ways always available once the trip is in progress)
- **Per-passenger 4-digit code** (employee app shows it; driver types it). Mandatory only if the organization turns on **Settings → Boarding verification**. 5 wrong codes → that passenger is locked 10 minutes.
- **Trip QR:** the driver taps **Show trip QR** (only while the trip is in progress). The QR is a signed token that expires in 60 seconds and refreshes every ~45 s. The passenger taps **Scan trip QR to board** and is marked boarded. Only passengers assigned to that trip can board with it. QR boarding counts even when codes are mandatory.
- **Manual board:** dispatcher/admin can board a passenger with a mandatory, audited reason.
- "Dropped" and "no show" need nothing.

There is **no start PIN**: drivers get the trip by accepting it, so no extra start credential is required.

## Security notes
- Drivers cannot read passenger codes (row level security + SECURITY DEFINER checks); tests prove this, including across tenants.
- Failed attempts are committed even though the request returns an error, so the lockout cannot be dodged.
- A passenger could in theory photograph a QR and send it to a friend who is assigned to the same trip within 60 seconds. The short life, per-trip scope and audit trail limit this; it proves presence only loosely.

## Not verified
Camera scanning (mobile `expo-camera`, web `BarcodeDetector`) and QR rendering have not been run on real devices. See `MOBILE_DEVICE_CHECKLIST.md`.
