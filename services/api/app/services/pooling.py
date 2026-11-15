"""Employee pooling: groups employees into capacity- and time-feasible shared routes.

Pure functions over a road-network matrix (index 0 = anchor/office, 1..n = employees). No DB, no I/O except the
injected RoutingProvider, so the algorithm is unit-testable.

pickup  direction: employees -> anchor. Ride time of an employee = time from their stop to the anchor.
drop    direction: anchor -> employees. Ride time of an employee = time from the anchor to their stop.

Feasibility per employee: ride <= max(direct * max_detour_ratio, direct + 10 min); and route total <= max_trip_minutes.
When road estimates are unavailable the result is marked `provisional` (geographic grouping only, no distances) and
callers must refuse to finalize it.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from math import atan2, degrees
from typing import Sequence

from .routing import Matrix, Point, RoutingProvider, haversine_km

MIN_DETOUR_SLACK_S = 600
EXACT_ORDER_LIMIT = 7
MAX_MATRIX_POINTS = 60


@dataclass(frozen=True)
class Pax:
    request_id: str
    employee_id: str
    point: Point
    gender: str | None = None
    needs: frozenset[str] = frozenset()
    requested_minute: int | None = None     # preferred pickup, minutes after midnight (pickup direction only)


@dataclass(frozen=True)
class VehicleSpec:
    vehicle_id: str
    capacity: int
    features: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PoolingConfig:
    direction: str                          # 'pickup' | 'drop'
    max_detour_ratio: float = 1.5
    max_trip_minutes: int = 90
    pickup_window_minutes: int = 30
    deadline_minute: int | None = None      # pickup: arrival deadline at anchor, minutes after midnight
    last_drop_policy: bool = False


@dataclass
class PlannedRoute:
    pax: list[Pax]
    distance_m: float | None
    duration_s: float | None
    eta_s: list[float | None]               # offset from route start to each pax stop
    ride_s: list[float | None]
    estimate_status: str                    # final | provisional
    required_features: frozenset[str] = frozenset()
    notes: dict = field(default_factory=dict)


@dataclass
class PoolingResult:
    routes: list[PlannedRoute]
    unroutable: list[Pax]
    estimate_status: str
    reason: str | None = None


# ----------------------------------------------------------------------------------------------------------------
def _bearing(a: Point, b: Point) -> float:
    return (degrees(atan2(b[1] - a[1], b[0] - a[0])) + 360) % 360


def partition(pax: Sequence[Pax], anchor: Point, max_n: int = MAX_MATRIX_POINTS) -> list[list[Pax]]:
    """Split a big roster into angular sectors around the anchor so each matrix call stays small."""
    ordered = sorted(pax, key=lambda p: (_bearing(anchor, p.point), haversine_km(anchor, p.point)))
    return [ordered[i:i + max_n] for i in range(0, len(ordered), max_n)]


class _Eval:
    """Route evaluator bound to one matrix."""
    def __init__(self, m: Matrix, cfg: PoolingConfig, n_by_index: dict[int, Pax]):
        self.m, self.cfg, self.pax = m, cfg, n_by_index

    def run(self, order: Sequence[int]):
        d, t = self.m.distances_m, self.m.durations_s
        path = [*order, 0] if self.cfg.direction == "pickup" else [0, *order]
        legs_t = [t[path[i]][path[i + 1]] for i in range(len(path) - 1)]
        total_t = sum(legs_t)
        total_d = sum(d[path[i]][path[i + 1]] for i in range(len(path) - 1))
        if total_t > self.cfg.max_trip_minutes * 60:
            return None
        eta: dict[int, float] = {}
        ride: dict[int, float] = {}
        if self.cfg.direction == "pickup":
            cum = 0.0
            for i, idx in enumerate(order):
                eta[idx] = cum
                ride[idx] = total_t - cum
                cum += legs_t[i]
        else:
            cum = 0.0
            for i, idx in enumerate(order):
                cum += legs_t[i]
                eta[idx] = cum
                ride[idx] = cum
        for idx in order:
            direct = t[idx][0] if self.cfg.direction == "pickup" else t[0][idx]
            if ride[idx] > max(direct * self.cfg.max_detour_ratio, direct + MIN_DETOUR_SLACK_S):
                return None
            p = self.pax[idx]
            if self.cfg.direction == "pickup" and self.cfg.deadline_minute is not None and p.requested_minute is not None:
                pickup_minute = self.cfg.deadline_minute - ride[idx] / 60.0
                if abs(pickup_minute - p.requested_minute) > self.cfg.pickup_window_minutes:
                    return None
        return total_d, total_t, eta, ride


def _best_order(ev: _Eval, idxs: list[int], cfg: PoolingConfig) -> tuple[list[int], dict] | None:
    """Exact ordering for small routes; honours the optional last-drop policy as a soft preference."""
    notes: dict = {}
    if len(idxs) <= EXACT_ORDER_LIMIT:
        feasible = []
        for perm in itertools.permutations(idxs):
            r = ev.run(perm)
            if r:
                feasible.append((r[1], r[0], list(perm)))
        if not feasible:
            return None
        feasible.sort()
        best = feasible[0]
        chosen = best
        if cfg.direction == "drop" and cfg.last_drop_policy:
            genders = {ev.pax[i].gender for i in idxs}
            if "male" in genders and genders - {"male"}:
                male_last = [f for f in feasible if ev.pax[f[2][-1]].gender == "male"]
                if best[2] and ev.pax[best[2][-1]].gender == "male":
                    notes["last_drop_policy"] = "already_satisfied"
                elif male_last and male_last[0][0] <= best[0] * 1.15:
                    chosen = male_last[0]
                    notes["last_drop_policy"] = "applied"
                else:
                    notes["last_drop_policy"] = "skipped_infeasible_or_costly"
            else:
                notes["last_drop_policy"] = "not_applicable"
        return chosen[2], notes
    order = list(idxs)
    return (order, notes) if ev.run(order) else None


def build_routes(m: Matrix, pax: Sequence[Pax], cfg: PoolingConfig, vehicles: Sequence[VehicleSpec]) -> PoolingResult:
    """Cheapest-insertion construction followed by exact re-ordering of each (small) route."""
    by_idx = {i + 1: p for i, p in enumerate(pax)}
    ev = _Eval(m, cfg, by_idx)
    t = m.durations_s

    def max_cap(required: frozenset[str]) -> int:
        caps = [v.capacity for v in vehicles if required <= v.features]
        return max(caps, default=0)

    def direct(i: int) -> float:
        return t[i][0] if cfg.direction == "pickup" else t[0][i]

    remaining = sorted(by_idx, key=lambda i: (-direct(i), i))
    routes: list[PlannedRoute] = []
    unroutable: list[Pax] = []

    while remaining:
        seed = remaining.pop(0)
        need = by_idx[seed].needs
        if max_cap(need) < 1 or not ev.run([seed]):
            unroutable.append(by_idx[seed])
            continue
        order = [seed]
        while True:
            best = None
            for cand in remaining:
                req = need | by_idx[cand].needs
                if len(order) + 1 > max_cap(frozenset(req)):
                    continue
                for pos in range(len(order) + 1):
                    trial = order[:pos] + [cand] + order[pos:]
                    r = ev.run(trial)
                    if r and (best is None or r[1] < best[0]):
                        best = (r[1], cand, trial, req)
            if best is None:
                break
            _, cand, order, req = best
            need = frozenset(req)
            remaining.remove(cand)
        refined = _best_order(ev, order, cfg)
        final_order, notes = refined if refined else (order, {})
        r = ev.run(final_order)
        assert r is not None
        dist, dur, eta, ride = r
        routes.append(PlannedRoute(
            pax=[by_idx[i] for i in final_order], distance_m=dist, duration_s=dur,
            eta_s=[eta[i] for i in final_order], ride_s=[ride[i] for i in final_order],
            estimate_status="final", required_features=need, notes=notes))
    return PoolingResult(routes, unroutable, "final")


def provisional_routes(pax: Sequence[Pax], anchor: Point, vehicles: Sequence[VehicleSpec], radius_km: float = 3.0) -> PoolingResult:
    """Fallback when no road matrix is available: nearest-neighbour grouping by straight-line proximity.
    Every route is marked provisional with no distance/duration; finalization must be refused downstream."""
    remaining = sorted(pax, key=lambda p: -haversine_km(anchor, p.point))
    routes: list[PlannedRoute] = []
    unroutable: list[Pax] = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        need = set(seed.needs)
        cap = max((v.capacity for v in vehicles if need <= v.features), default=0)
        if cap < 1:
            unroutable.append(seed)
            continue
        for cand in sorted(list(remaining), key=lambda p: haversine_km(seed.point, p.point)):
            req = need | cand.needs
            cap_c = max((v.capacity for v in vehicles if req <= v.features), default=0)
            if len(group) + 1 <= cap_c and haversine_km(seed.point, cand.point) <= radius_km:
                group.append(cand)
                need = set(req)
                remaining.remove(cand)
        routes.append(PlannedRoute(group, None, None, [None] * len(group), [None] * len(group), "provisional", frozenset(need),
                                   {"warning": "road estimates unavailable; geographic grouping only"}))
    return PoolingResult(routes, unroutable, "provisional", "routing_unavailable")


async def pool(provider: RoutingProvider, anchor: Point, pax: Sequence[Pax], cfg: PoolingConfig, vehicles: Sequence[VehicleSpec]) -> PoolingResult:
    """Pool a whole roster. Any failed matrix call marks the entire result provisional (no mixed guarantees)."""
    if not pax:
        return PoolingResult([], [], "final")
    routes: list[PlannedRoute] = []
    unroutable: list[Pax] = []
    for chunk in partition(pax, anchor):
        m = await provider.matrix([anchor, *[p.point for p in chunk]])
        if m is None:
            return provisional_routes(pax, anchor, vehicles)
        res = build_routes(m, chunk, cfg, vehicles)
        routes += res.routes
        unroutable += res.unroutable
    return PoolingResult(routes, unroutable, "final")
