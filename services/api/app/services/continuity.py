"""Cab/driver continuity: prefer the cab (and its driver) that already served the same people on the same shift/direction recently.

Pure functions so the rules are unit-testable. Capacity, compliance and eligibility are applied BEFORE these preferences; continuity never overrides them.
"""
from dataclasses import dataclass

HISTORY_DAYS = 14
MAX_SPARE_SEATS = 3          # do not use a much bigger cab just because it served these people before
FAIRNESS_SLACK = 0.25        # a sticky driver is accepted unless his fairness score is >25% worse than the best candidate


@dataclass(frozen=True)
class VehicleChoice:
    vehicle_id: str
    reason: str | None       # human-readable, None when no continuity applied


def pick_vehicle(fit: list, size: int, history: dict[str, int], employees_in_group: int) -> tuple[object, str | None]:
    """fit: VehicleSpec-like objects (vehicle_id, capacity). history: vehicle_id -> number of past rides of this group's employees in it.
    Returns (spec, reason). Falls back to the smallest fitting cab when there is no usable history."""
    base = min(fit, key=lambda s: (s.capacity, s.vehicle_id))
    usable = [s for s in fit if history.get(s.vehicle_id, 0) > 0 and s.capacity <= size + MAX_SPARE_SEATS]
    if not usable:
        return base, None
    best = max(usable, key=lambda s: (history[s.vehicle_id], -s.capacity, s.vehicle_id))
    rides = history[best.vehicle_id]
    return best, f"same cab as recent trips (these passengers rode it {rides} time{'s' if rides != 1 else ''} in the last {HISTORY_DAYS} days)"


def pick_driver(ranked: list[dict], preferred: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """ranked: fairness-ranked candidates, best first (lower score is better). preferred: [(driver_id, why)] in priority order.
    A preferred driver is used if he is eligible (present in ranked) and not much less fair than the best candidate."""
    if not ranked:
        return None, None
    best = ranked[0]
    by_id = {c["driver_id"]: c for c in ranked}
    for did, why in preferred:
        c = by_id.get(did)
        if c is None:
            continue
        if did == best["driver_id"]:
            return did, f"{why} (also the fairest eligible driver)"
        if c["score"] <= best["score"] * (1 + FAIRNESS_SLACK) + 1e-9:
            return did, why
    return best["driver_id"], None
