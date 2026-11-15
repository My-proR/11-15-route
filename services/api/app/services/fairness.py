"""Configurable, explainable driver workload fairness.

Score (lower is better) for assigning a trip to a driver:
    sum_k  w_k * (driver_stat_k + trip_k) / scale_k
where scale_k is the mean of that stat across eligible drivers (>=1 to avoid divide-by-zero).
Stats: planned_km, trips, duty_minutes, deadhead_km (only when measurable).

Local-trip rule (optional): a trip no longer than `local_trip_max_km` is "local". Local-eligible drivers who have fewer
than `local_trip_target_per_day` local trips today receive a score bonus; local trips may be excluded from the km term.
The target is never forced: an infeasible or unsafe assignment is never made to hit it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {"planned_km": 1.0, "trips": 0.5, "duty_minutes": 0.3, "deadhead_km": 0.4}
LOCAL_BONUS = 1.0


@dataclass
class DriverStats:
    driver_id: str
    name: str = ""
    planned_km: float = 0.0
    trips: int = 0
    duty_minutes: float = 0.0
    deadhead_km: float = 0.0
    local_trips_today: int = 0
    local_trip_eligible: bool = False
    local_km_in_window: float = 0.0     # local-trip km already counted in planned_km


@dataclass
class TripLoad:
    km: float
    minutes: float
    deadhead_km: float | None = None
    is_local: bool = False


@dataclass
class FairnessConfig:
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    local_target_per_day: int = 0
    local_trips_excluded_from_km: bool = False


def score_candidates(drivers: list[DriverStats], trip: TripLoad, cfg: FairnessConfig) -> list[dict]:
    """Return candidates ordered best-first, each with an explanation of how the score was formed."""
    if not drivers:
        return []
    w = {**DEFAULT_WEIGHTS, **cfg.weights}

    def eff_km(d: DriverStats) -> float:
        return d.planned_km - (d.local_km_in_window if cfg.local_trips_excluded_from_km else 0.0)

    trip_km = 0.0 if (trip.is_local and cfg.local_trips_excluded_from_km) else trip.km
    stats = {
        "planned_km": [eff_km(d) for d in drivers],
        "trips": [float(d.trips) for d in drivers],
        "duty_minutes": [d.duty_minutes for d in drivers],
        "deadhead_km": [d.deadhead_km for d in drivers],
    }
    scales = {k: max(sum(v) / len(v), 1.0) for k, v in stats.items()}
    trip_vals = {"planned_km": trip_km, "trips": 1.0, "duty_minutes": trip.minutes, "deadhead_km": trip.deadhead_km or 0.0}
    out = []
    for i, d in enumerate(drivers):
        comps = {}
        for k in ("planned_km", "trips", "duty_minutes", "deadhead_km"):
            if k == "deadhead_km" and trip.deadhead_km is None:
                comps[k] = 0.0       # not measurable: contributes nothing rather than an invented value
                continue
            comps[k] = round(w[k] * (stats[k][i] + trip_vals[k]) / scales[k], 4)
        bonus = 0.0
        local_note = None
        if cfg.local_target_per_day > 0 and trip.is_local and d.local_trip_eligible:
            if d.local_trips_today < cfg.local_target_per_day:
                bonus = LOCAL_BONUS
                local_note = f"below local-trip target ({d.local_trips_today}/{cfg.local_target_per_day})"
            else:
                local_note = "local-trip target already met"
        total = round(sum(comps.values()) - bonus, 4)
        out.append({"driver_id": d.driver_id, "name": d.name, "score": total, "components": comps, "local_trip_bonus": bonus,
                    "local_trip_note": local_note, "deadhead_measured": trip.deadhead_km is not None})
    out.sort(key=lambda c: (c["score"], c["driver_id"]))
    for rank, c in enumerate(out, 1):
        c["rank"] = rank
    return out
