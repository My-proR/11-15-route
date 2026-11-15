"""Dispatch orchestration: slot planning (dry-run), commit, lifecycle transitions, driver offers and replacement chains.

All functions take an RLS-enforced connection (see db.user_session) except where noted. Mutations that change capacity or
state are single atomic statements or run under row locks / DB triggers, so concurrent dispatchers cannot overbook a cab
or double-apply a transition.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..common import AppError, audit, notify, staff_user_ids
from . import continuity as cont
from .fairness import DriverStats, FairnessConfig, TripLoad, score_candidates
from .pooling import Pax, PoolingConfig, VehicleSpec, pool
from .routing import get_routing_provider

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PROPOSED", "CANCELLED"},
    "PROPOSED": {"PENDING_DRIVER_ACCEPTANCE", "CANCELLED", "DRAFT"},
    "PENDING_DRIVER_ACCEPTANCE": {"DRIVER_ACCEPTED", "CONFIRMED", "REQUIRES_REASSIGNMENT", "CANCELLED"},
    "DRIVER_ACCEPTED": {"CONFIRMED", "REQUIRES_REASSIGNMENT", "CANCELLED"},
    "CONFIRMED": {"IN_PROGRESS", "REQUIRES_REASSIGNMENT", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "REQUIRES_REASSIGNMENT": {"PENDING_DRIVER_ACCEPTANCE", "PROPOSED", "CANCELLED"},
}
EDITABLE = ("DRAFT", "PROPOSED", "REQUIRES_REASSIGNMENT")
LIVE = ("DRAFT", "PROPOSED", "PENDING_DRIVER_ACCEPTANCE", "DRIVER_ACCEPTED", "CONFIRMED", "IN_PROGRESS", "REQUIRES_REASSIGNMENT")


def _m(row) -> dict:
    return dict(row._mapping)


# ------------------------------------------------------------------------------------------------------------
# State machine
# ------------------------------------------------------------------------------------------------------------
async def transition(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID, to: str, *,
                     expected_version: int | None = None, extra_sql: str = "", params: dict | None = None) -> dict:
    """Atomic, validated transition. Fails with 409 if the current status does not permit it (also covers duplicate calls)."""
    allowed_from = [s for s, nxt in TRANSITIONS.items() if to in nxt]
    q = (f"UPDATE trip_allocations SET status = :to, version = version + 1 {extra_sql} "
         "WHERE organization_id = :o AND id = :a AND status = ANY(:from) "
         + ("AND version = :v " if expected_version is not None else "") + "RETURNING *")
    p = {"to": to, "o": org_id, "a": allocation_id, "from": allowed_from, **(params or {})}
    if expected_version is not None:
        p["v"] = expected_version
    row = (await conn.execute(text(q), p)).first()
    if row is None:
        cur = (await conn.execute(text("SELECT status, version FROM trip_allocations WHERE organization_id=:o AND id=:a"),
                                  {"o": org_id, "a": allocation_id})).first()
        if cur is None:
            raise AppError(404, "not_found", "Allocation not found")
        if expected_version is not None and cur.version != expected_version and cur.status in allowed_from:
            raise AppError(409, "version_conflict", "Allocation was changed by someone else; reload and retry",
                           {"current_version": cur.version})
        raise AppError(409, "invalid_transition", f"Cannot move allocation from {cur.status} to {to}",
                       {"current": cur.status, "allowed_next": sorted(TRANSITIONS[cur.status])})
    return _m(row)


# ------------------------------------------------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------------------------------------------------
async def get_allocation(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID, *, lock: bool = False) -> dict:
    row = (await conn.execute(text("SELECT * FROM trip_allocations WHERE organization_id=:o AND id=:a" + (" FOR UPDATE" if lock else "")),
                              {"o": org_id, "a": allocation_id})).first()
    if row is None:
        raise AppError(404, "not_found", "Allocation not found")
    return _m(row)


async def org_settings(conn: AsyncConnection, org_id: uuid.UUID) -> dict:
    row = (await conn.execute(text("SELECT * FROM organization_settings WHERE organization_id=:o"), {"o": org_id})).first()
    if row is None:
        raise AppError(404, "not_found", "Organization settings missing")
    return _m(row)


def vehicle_compliance_issue(v: dict, service_date: date) -> str | None:
    if not v["is_active"]:
        return "vehicle inactive"
    if v["availability"] != "available":
        return f"vehicle {v['availability']}"
    for col, label in (("insurance_valid_until", "insurance"), ("fitness_valid_until", "fitness certificate")):
        if v.get(col) and v[col] < service_date:
            return f"{label} expired"
    return None


async def eligible_drivers(conn: AsyncConnection, org_id: uuid.UUID, *, service_date: date, shift_id: uuid.UUID, direction: str,
                           exclude: set[uuid.UUID] | None = None, settings: dict | None = None) -> list[DriverStats]:
    """Drivers who may legally/operationally take a trip in this slot, with their recent workload for fairness."""
    settings = settings or await org_settings(conn, org_id)
    window = int(settings["fairness_window_days"])
    rows = (await conn.execute(text("""
        SELECT d.id, d.name, d.local_trip_eligible FROM drivers d
        WHERE d.organization_id = :o AND d.is_active AND d.is_available AND d.license_valid_until >= :sd
          AND (cardinality(d.eligible_shift_ids) = 0 OR :sh = ANY(d.eligible_shift_ids))
          AND NOT EXISTS (SELECT 1 FROM driver_leave l WHERE l.organization_id = d.organization_id AND l.driver_id = d.id
                          AND l.status = 'approved' AND :sd BETWEEN l.starts_on AND l.ends_on)
          AND NOT EXISTS (SELECT 1 FROM trip_allocations a WHERE a.organization_id = d.organization_id AND a.driver_id = d.id
                          AND a.service_date = :sd AND a.shift_id = :sh AND a.direction = :dir
                          AND a.status NOT IN ('CANCELLED','REQUIRES_REASSIGNMENT','COMPLETED'))
        """), {"o": org_id, "sd": service_date, "sh": shift_id, "dir": direction})).all()
    ids = [r.id for r in rows if r.id not in (exclude or set())]
    if not ids:
        return []
    stats = {r.driver_id: r for r in (await conn.execute(text("""
        SELECT a.driver_id,
               coalesce(sum(a.planned_distance_km), 0) AS km, count(*) AS trips,
               coalesce(sum(a.planned_duration_minutes), 0) AS minutes,
               coalesce(sum(a.planned_distance_km) FILTER (WHERE a.is_local_trip), 0) AS local_km,
               count(*) FILTER (WHERE a.is_local_trip AND a.service_date = :sd) AS local_today
        FROM trip_allocations a
        WHERE a.organization_id = :o AND a.driver_id = ANY(:ids) AND a.status NOT IN ('CANCELLED','REQUIRES_REASSIGNMENT')
          AND a.service_date BETWEEN :from AND :sd
        GROUP BY a.driver_id"""), {"o": org_id, "ids": ids, "sd": service_date, "from": service_date - timedelta(days=window)})).all()}
    out = []
    for r in rows:
        if r.id not in ids:
            continue
        s = stats.get(r.id)
        out.append(DriverStats(str(r.id), r.name, float(s.km) if s else 0.0, int(s.trips) if s else 0, float(s.minutes) if s else 0.0, 0.0,
                               int(s.local_today) if s else 0, bool(r.local_trip_eligible), float(s.local_km) if s else 0.0))
    return out


def fairness_config(settings: dict) -> FairnessConfig:
    return FairnessConfig(weights=settings["fairness_weights"], local_target_per_day=int(settings["local_trip_target_per_day"]),
                          local_trips_excluded_from_km=bool(settings["local_trips_excluded_from_km_fairness"]))


# ------------------------------------------------------------------------------------------------------------
# Requests for a service day
# ------------------------------------------------------------------------------------------------------------
async def generate_requests(conn: AsyncConnection, org_id: uuid.UUID, service_date: date, shift_id: uuid.UUID, direction: str) -> dict:
    """Create open transport requests for active employees on the shift (snapshotting their coordinates). Idempotent."""
    day = (await conn.execute(text("SELECT is_service_day FROM service_days WHERE organization_id=:o AND service_date=:d"),
                              {"o": org_id, "d": service_date})).first()
    if day is not None and not day.is_service_day:
        raise AppError(409, "not_a_service_day", "This date is marked as a holiday / non-service day")
    res = await conn.execute(text("""
        INSERT INTO employee_transport_requests (organization_id, employee_id, service_date, shift_id, direction, pickup_lat, pickup_lon, drop_lat, drop_lon, requested_time)
        SELECT e.organization_id, e.id, :d, :sh, :dir, e.pickup_lat, e.pickup_lon, e.drop_lat, e.drop_lon, e.preferred_pickup_time
        FROM employees e WHERE e.organization_id = :o AND e.is_active AND e.shift_id = :sh
        ON CONFLICT (organization_id, employee_id, service_date, shift_id, direction) DO NOTHING RETURNING id"""),
        {"o": org_id, "d": service_date, "sh": shift_id, "dir": direction})
    created = len(res.all())
    missing = (await conn.execute(text("""
        SELECT e.employee_code, e.name FROM employee_transport_requests r JOIN employees e ON e.organization_id = r.organization_id AND e.id = r.employee_id
        WHERE r.organization_id=:o AND r.service_date=:d AND r.shift_id=:sh AND r.direction=:dir AND r.status='open'
          AND ((:dir = 'pickup' AND r.pickup_lat IS NULL) OR (:dir = 'drop' AND r.drop_lat IS NULL))"""),
        {"o": org_id, "d": service_date, "sh": shift_id, "dir": direction})).all()
    return {"created": created, "missing_location": [{"employee_code": m.employee_code, "name": m.name} for m in missing]}


# ------------------------------------------------------------------------------------------------------------
# Planning (dry-run) and commit
# ------------------------------------------------------------------------------------------------------------
async def plan_slot(conn: AsyncConnection, org_id: uuid.UUID, *, service_date: date, shift_id: uuid.UUID, direction: str,
                    branch_id: uuid.UUID | None = None) -> dict:
    """Compute a proposed set of allocations. Persists nothing."""
    st = await org_settings(conn, org_id)
    shift = (await conn.execute(text("SELECT * FROM shifts WHERE organization_id=:o AND id=:s AND is_active"), {"o": org_id, "s": shift_id})).first()
    if shift is None:
        raise AppError(404, "not_found", "Shift not found or inactive")
    day = (await conn.execute(text("SELECT is_service_day FROM service_days WHERE organization_id=:o AND service_date=:d"), {"o": org_id, "d": service_date})).first()
    if day is not None and not day.is_service_day:
        raise AppError(409, "not_a_service_day", "This date is marked as a non-service day")

    reqs = (await conn.execute(text("""
        SELECT r.id AS request_id, r.employee_id, r.pickup_lat, r.pickup_lon, r.drop_lat, r.drop_lon, r.requested_time,
               e.gender, e.accessibility_needs, e.name, e.employee_code, e.branch_id, b.latitude AS b_lat, b.longitude AS b_lon, b.name AS branch_name
        FROM employee_transport_requests r
        JOIN employees e ON e.organization_id = r.organization_id AND e.id = r.employee_id
        LEFT JOIN locations b ON b.organization_id = e.organization_id AND b.id = e.branch_id
        WHERE r.organization_id=:o AND r.service_date=:d AND r.shift_id=:sh AND r.direction=:dir AND r.status='open'
          AND (CAST(:br AS uuid) IS NULL OR e.branch_id = :br)
        ORDER BY e.employee_code"""), {"o": org_id, "d": service_date, "sh": shift_id, "dir": direction, "br": branch_id})).all()

    vrows = [_m(v) for v in (await conn.execute(text("""
        SELECT v.* FROM vehicles v WHERE v.organization_id=:o AND v.is_active AND v.availability='available'
          AND NOT EXISTS (SELECT 1 FROM trip_allocations a WHERE a.organization_id=v.organization_id AND a.vehicle_id=v.id
                          AND a.service_date=:d AND a.shift_id=:sh AND a.direction=:dir AND a.status <> 'CANCELLED')
        ORDER BY v.capacity, v.vehicle_code"""), {"o": org_id, "d": service_date, "sh": shift_id, "dir": direction})).all()]
    vehicles, excluded_vehicles = [], []
    for v in vrows:
        issue = vehicle_compliance_issue(v, service_date)
        (excluded_vehicles.append({"vehicle_code": v["vehicle_code"], "reason": issue}) if issue else vehicles.append(v))

    start_min = shift.start_time.hour * 60 + shift.start_time.minute
    cfg_base = dict(max_detour_ratio=float(st["max_detour_ratio"]), max_trip_minutes=int(st["max_trip_minutes"]),
                    pickup_window_minutes=int(shift.pickup_window_minutes), last_drop_policy=bool(st["last_drop_policy_enabled"]))
    specs = [VehicleSpec(str(v["id"]), int(v["capacity"]), frozenset(v["accessibility_features"] or [])) for v in vehicles]

    unassigned: list[dict] = []
    by_branch: dict[Any, list] = {}
    for r in reqs:
        lat, lon = (r.pickup_lat, r.pickup_lon) if direction == "pickup" else (r.drop_lat, r.drop_lon)
        if lat is None or r.b_lat is None:
            unassigned.append({"request_id": str(r.request_id), "employee_code": r.employee_code, "name": r.name,
                               "reason": "missing_employee_location" if lat is None else "missing_branch_location"})
            continue
        by_branch.setdefault(r.branch_id, []).append(r)

    provider = get_routing_provider()
    all_routes: list[dict] = []
    est_status = "final"
    conflicts: list[dict] = []
    free_specs = list(specs)
    drivers = await eligible_drivers(conn, org_id, service_date=service_date, shift_id=shift_id, direction=direction, settings=st)
    fcfg = fairness_config(st)
    used_drivers: set[str] = set()
    used_vehicles: set[str] = set()
    # Continuity history: who rode which cab (and who drove it) on this shift/direction recently.
    emp_vehicle: dict[str, dict[str, int]] = {}
    vehicle_drivers: dict[str, dict[str, tuple[int, date]]] = {}
    for h in (await conn.execute(text("""
            SELECT tp.employee_id, a.vehicle_id, a.driver_id, count(*) AS n, max(a.service_date) AS last_date
            FROM trip_passengers tp JOIN trip_allocations a ON a.organization_id = tp.organization_id AND a.id = tp.allocation_id
            WHERE a.organization_id = :o AND a.shift_id = :sh AND a.direction = :dir AND a.service_date < :sd AND a.service_date >= :from
              AND a.status IN ('CONFIRMED','IN_PROGRESS','COMPLETED') AND tp.status <> 'removed'
            GROUP BY tp.employee_id, a.vehicle_id, a.driver_id"""),
            {"o": org_id, "sh": shift_id, "dir": direction, "sd": service_date, "from": service_date - timedelta(days=cont.HISTORY_DAYS)})).all():
        em = emp_vehicle.setdefault(str(h.employee_id), {})
        em[str(h.vehicle_id)] = em.get(str(h.vehicle_id), 0) + int(h.n)
        if h.driver_id:
            dv = vehicle_drivers.setdefault(str(h.vehicle_id), {})
            n0, l0 = dv.get(str(h.driver_id), (0, h.last_date))
            dv[str(h.driver_id)] = (n0 + int(h.n), max(l0, h.last_date))
    default_driver = {str(v["id"]): str(v["default_driver_id"]) for v in vehicles if v.get("default_driver_id")}

    for branch, rows in by_branch.items():
        anchor = (rows[0].b_lat, rows[0].b_lon)
        pax = [Pax(str(r.request_id), str(r.employee_id), (r.pickup_lat, r.pickup_lon) if direction == "pickup" else (r.drop_lat, r.drop_lon),
                   r.gender, frozenset(r.accessibility_needs or []),
                   (r.requested_time.hour * 60 + r.requested_time.minute) if r.requested_time else None) for r in rows]
        cfg = PoolingConfig(direction=direction, deadline_minute=start_min - int(shift.arrival_buffer_minutes) if direction == "pickup" else None, **cfg_base)
        result = await pool(provider, anchor, pax, cfg, [s for s in free_specs])
        if result.estimate_status != "final":
            est_status = "provisional"
        for p in result.unroutable:
            emp = next(r for r in rows if str(r.request_id) == p.request_id)
            unassigned.append({"request_id": p.request_id, "employee_code": emp.employee_code, "name": emp.name, "reason": "no_feasible_route_or_vehicle"})
        for route in sorted(result.routes, key=lambda r: -len(r.pax)):
            size = len(route.pax)
            fit = [s for s in free_specs if s.vehicle_id not in used_vehicles and s.capacity >= size and route.required_features <= s.features]
            if not fit:
                conflicts.append({"type": "no_vehicle_available", "employees": [p.employee_id for p in route.pax]})
                for p in route.pax:
                    emp = next(r for r in rows if str(r.request_id) == p.request_id)
                    unassigned.append({"request_id": p.request_id, "employee_code": emp.employee_code, "name": emp.name, "reason": "no_vehicle_available"})
                continue
            hist: dict[str, int] = {}
            for px in route.pax:
                for vid, n in emp_vehicle.get(px.employee_id, {}).items():
                    hist[vid] = hist.get(vid, 0) + n
            vehicle, v_reason = cont.pick_vehicle(fit, size, hist, size)
            used_vehicles.add(vehicle.vehicle_id)
            km = (route.distance_m or 0) / 1000
            minutes = (route.duration_s or 0) / 60
            is_local = bool(route.distance_m is not None and km <= float(st["local_trip_max_km"]))
            cands = [d for d in drivers if d.driver_id not in used_drivers]
            ranked = score_candidates(cands, TripLoad(km, minutes, None, is_local), fcfg) if cands else []
            preferred: list[tuple[str, str]] = []
            if vehicle.vehicle_id in default_driver:
                preferred.append((default_driver[vehicle.vehicle_id], "this cab's regular driver"))
            for did, (n, _last) in sorted(vehicle_drivers.get(vehicle.vehicle_id, {}).items(), key=lambda kv: (-kv[1][0], -kv[1][1].toordinal(), kv[0])):
                preferred.append((did, f"drove this cab on recent trips ({n} passenger rides in the last {cont.HISTORY_DAYS} days)"))
            driver_id, d_reason = cont.pick_driver(ranked, preferred)
            if driver_id:
                used_drivers.add(driver_id)
            else:
                conflicts.append({"type": "no_eligible_driver", "vehicle_id": vehicle.vehicle_id})
            all_routes.append({
                "vehicle_id": vehicle.vehicle_id, "driver_id": driver_id, "branch_id": str(branch) if branch else None,
                "capacity": vehicle.capacity, "passengers": size, "is_local_trip": is_local,
                "distance_km": round(km, 2) if route.distance_m is not None else None,
                "duration_minutes": round(minutes, 1) if route.duration_s is not None else None,
                "estimate_status": route.estimate_status,
                "stops": [{"request_id": p.request_id, "employee_id": p.employee_id, "order": i,
                           "eta_minutes": round(route.eta_s[i] / 60, 1) if route.eta_s[i] is not None else None,
                           "ride_minutes": round(route.ride_s[i] / 60, 1) if route.ride_s[i] is not None else None,
                           "lat": p.point[0], "lon": p.point[1]} for i, p in enumerate(route.pax)],
                "explanation": {"vehicle": v_reason or "smallest compliant vehicle that fits the group", "policy_notes": route.notes,
                                "driver_choice": d_reason or "fairest eligible driver",
                                "fairness": ranked[:5], "chosen_driver_rank": next((c["rank"] for c in ranked if c["driver_id"] == driver_id), None)},
            })
    return {"service_date": service_date.isoformat(), "shift_id": str(shift_id), "direction": direction,
            "routing_provider": provider.name, "estimate_status": est_status,
            "can_finalize": est_status == "final", "routes": all_routes, "unassigned": unassigned, "conflicts": conflicts,
            "excluded_vehicles": excluded_vehicles, "summary": {"requests": len(reqs), "assigned": sum(r["passengers"] for r in all_routes),
                                                                "unassigned": len(unassigned), "routes": len(all_routes)}}


async def commit_plan(conn: AsyncConnection, org_id: uuid.UUID, actor: uuid.UUID, plan: dict, *, replace_proposed: bool) -> list[dict]:
    """Persist a computed plan as PROPOSED allocations (never published silently: drivers are not contacted yet)."""
    sd, sh, direction = date.fromisoformat(plan["service_date"]), uuid.UUID(plan["shift_id"]), plan["direction"]
    existing = (await conn.execute(text(
        "SELECT id FROM trip_allocations WHERE organization_id=:o AND service_date=:d AND shift_id=:sh AND direction=:dir AND status IN ('DRAFT','PROPOSED')"),
        {"o": org_id, "d": sd, "sh": sh, "dir": direction})).all()
    if existing and not replace_proposed:
        raise AppError(409, "proposals_exist", "Unpublished proposals already exist for this slot; pass replace_proposed=true", {"count": len(existing)})
    for e in existing:
        await cancel_allocation(conn, org_id, actor, e.id, "replaced by a new allocation run")
    created = []
    for r in plan["routes"]:
        a = (await conn.execute(text("""
            INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, driver_id, status, capacity_snapshot,
                planned_distance_km, planned_duration_minutes, estimate_status, stop_sequence, explanation, is_local_trip, created_by)
            VALUES (:o,:sd,:sh,:dir,:v,:d,'PROPOSED',:cap,:km,:min,:es,CAST(:ss AS jsonb),CAST(:ex AS jsonb),:loc,:by) RETURNING *"""),
            {"o": org_id, "sd": sd, "sh": sh, "dir": direction, "v": uuid.UUID(r["vehicle_id"]), "d": uuid.UUID(r["driver_id"]) if r["driver_id"] else None,
             "cap": r["capacity"], "km": r["distance_km"], "min": r["duration_minutes"], "es": r["estimate_status"],
             "ss": json.dumps([{"order": s["order"], "request_id": s["request_id"], "lat": s["lat"], "lon": s["lon"]} for s in r["stops"]]),
             "ex": json.dumps(r["explanation"], default=str), "loc": r["is_local_trip"], "by": actor})).first()
        for s in r["stops"]:
            await conn.execute(text("""
                INSERT INTO trip_passengers (organization_id, allocation_id, request_id, employee_id, stop_order, eta_minutes_from_start)
                VALUES (:o,:a,:r,:e,:so,:eta)"""), {"o": org_id, "a": a.id, "r": uuid.UUID(s["request_id"]), "e": uuid.UUID(s["employee_id"]), "so": s["order"], "eta": s["eta_minutes"]})
            await conn.execute(text("UPDATE employee_transport_requests SET status='allocated' WHERE organization_id=:o AND id=:r"), {"o": org_id, "r": uuid.UUID(s["request_id"])})
        created.append(_m(a))
    await audit(conn, org_id=org_id, actor=actor, action="allocation.run_committed", entity_type="slot",
                entity_id=f"{sd}:{sh}:{direction}", details={"allocations": [str(c["id"]) for c in created], "replaced": len(existing)})
    return created


# ------------------------------------------------------------------------------------------------------------
# Cancellation, driver offers, replacement chain
# ------------------------------------------------------------------------------------------------------------
async def cancel_allocation(conn: AsyncConnection, org_id: uuid.UUID, actor: uuid.UUID | None, allocation_id: uuid.UUID, reason: str) -> dict:
    a = await transition(conn, org_id, allocation_id, "CANCELLED", extra_sql=", cancelled_reason = :why", params={"why": reason})
    await conn.execute(text("UPDATE employee_transport_requests SET status='open' WHERE organization_id=:o AND status='allocated' AND id IN "
                            "(SELECT request_id FROM trip_passengers WHERE organization_id=:o AND allocation_id=:a AND status <> 'removed')"), {"o": org_id, "a": allocation_id})
    await conn.execute(text("UPDATE trip_passengers SET status='removed' WHERE organization_id=:o AND allocation_id=:a AND status <> 'removed'"), {"o": org_id, "a": allocation_id})
    await conn.execute(text("UPDATE driver_assignments SET status='CANCELLED', responded_at=now() WHERE organization_id=:o AND allocation_id=:a AND status IN ('PENDING','ACCEPTED')"),
                       {"o": org_id, "a": allocation_id})
    await audit(conn, org_id=org_id, actor=actor, action="allocation.cancelled", entity_type="allocation", entity_id=allocation_id, details={"reason": reason})
    return a


async def validate_sendable(conn: AsyncConnection, org_id: uuid.UUID, a: dict) -> None:
    if a["estimate_status"] != "final":
        raise AppError(409, "routing_unavailable", "Road-network estimates are not final for this allocation; it cannot be sent or confirmed")
    if a["driver_id"] is None:
        raise AppError(409, "driver_required", "Assign a driver first")
    v = (await conn.execute(text("SELECT * FROM vehicles WHERE organization_id=:o AND id=:v"), {"o": org_id, "v": a["vehicle_id"]})).first()
    if (issue := vehicle_compliance_issue(_m(v), a["service_date"])):
        raise AppError(409, "vehicle_not_compliant", f"Vehicle cannot be used: {issue}")
    cnt = await conn.scalar(text("SELECT count(*) FROM trip_passengers WHERE organization_id=:o AND allocation_id=:a AND status <> 'removed'"), {"o": org_id, "a": a["id"]})
    if cnt == 0:
        raise AppError(409, "no_passengers", "Allocation has no passengers")
    if cnt > v.capacity:
        raise AppError(409, "capacity_exceeded", "Passengers exceed vehicle capacity")
    ok = await eligible_drivers(conn, org_id, service_date=a["service_date"], shift_id=a["shift_id"], direction=a["direction"])
    mine = await conn.scalar(text("""SELECT 1 FROM drivers d WHERE d.organization_id=:o AND d.id=:d AND d.is_active AND d.is_available AND d.license_valid_until >= :sd
        AND (cardinality(d.eligible_shift_ids)=0 OR :sh = ANY(d.eligible_shift_ids))
        AND NOT EXISTS (SELECT 1 FROM driver_leave l WHERE l.organization_id=d.organization_id AND l.driver_id=d.id AND l.status='approved' AND :sd BETWEEN l.starts_on AND l.ends_on)"""),
        {"o": org_id, "d": a["driver_id"], "sd": a["service_date"], "sh": a["shift_id"]})
    if not mine:
        raise AppError(409, "driver_not_eligible", "Driver is on leave, unavailable, unlicensed for the date or not eligible for this shift")
    del ok


async def offer_to_driver(conn: AsyncConnection, org_id: uuid.UUID, actor: uuid.UUID | None, a: dict, driver_id: uuid.UUID, *,
                          replacement_for_leave_id: uuid.UUID | None = None, explanation: dict | None = None) -> dict:
    """Create the PENDING assignment (driver acceptance required) and notify the driver."""
    st = await org_settings(conn, org_id)
    await conn.execute(text("UPDATE trip_allocations SET driver_id=:d WHERE organization_id=:o AND id=:a"), {"d": driver_id, "o": org_id, "a": a["id"]})
    a = {**a, "driver_id": driver_id}
    await validate_sendable(conn, org_id, a)
    await conn.execute(text("UPDATE driver_assignments SET status='CANCELLED', responded_at=now() WHERE organization_id=:o AND allocation_id=:a AND status IN ('PENDING','ACCEPTED')"),
                       {"o": org_id, "a": a["id"]})
    asg = (await conn.execute(text("""
        INSERT INTO driver_assignments (organization_id, allocation_id, driver_id, expires_at, replacement_for_leave_id, fairness_explanation)
        VALUES (:o,:a,:d, now() + make_interval(mins => :mins), :l, CAST(:fx AS jsonb)) RETURNING *"""),
        {"o": org_id, "a": a["id"], "d": driver_id, "mins": st["driver_acceptance_timeout_minutes"], "l": replacement_for_leave_id,
         "fx": json.dumps(explanation or a.get("explanation", {}), default=str)})).first()
    await transition(conn, org_id, a["id"], "PENDING_DRIVER_ACCEPTANCE")
    user = await conn.scalar(text("SELECT user_id FROM drivers WHERE organization_id=:o AND id=:d"), {"o": org_id, "d": driver_id})
    if user:
        await notify(conn, org_id=org_id, user_id=user, event_type="assignment.requested", title="New trip assignment",
                     body=f"Trip on {a['service_date']} ({a['direction']}) needs your acceptance.",
                     data={"allocation_id": str(a["id"]), "assignment_id": str(asg.id)}, dedupe_key=f"assign:{asg.id}")
    await audit(conn, org_id=org_id, actor=actor, action="assignment.offered", entity_type="allocation", entity_id=a["id"],
                details={"driver_id": str(driver_id), "replacement_for_leave_id": str(replacement_for_leave_id) if replacement_for_leave_id else None})
    return _m(asg)


async def offer_next_driver(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID, *, replacement_for_leave_id: uuid.UUID | None,
                            actor: uuid.UUID | None) -> dict | None:
    """Pick the fairest eligible driver not yet declined/expired for this allocation; none => REQUIRES_REASSIGNMENT + alert staff."""
    a = await get_allocation(conn, org_id, allocation_id, lock=True)
    tried = {r.driver_id for r in (await conn.execute(text(
        "SELECT driver_id FROM driver_assignments WHERE organization_id=:o AND allocation_id=:a AND status IN ('DECLINED','EXPIRED')"), {"o": org_id, "a": allocation_id}))}
    on_leave_driver = a["driver_id"] if replacement_for_leave_id else None
    st = await org_settings(conn, org_id)
    cands = await eligible_drivers(conn, org_id, service_date=a["service_date"], shift_id=a["shift_id"], direction=a["direction"],
                                   exclude=tried | ({on_leave_driver} if on_leave_driver else set()), settings=st)
    ranked = score_candidates(cands, TripLoad(float(a["planned_distance_km"] or 0), float(a["planned_duration_minutes"] or 0), None, a["is_local_trip"]), fairness_config(st))
    if a["status"] not in ("REQUIRES_REASSIGNMENT",):
        a = await transition(conn, org_id, allocation_id, "REQUIRES_REASSIGNMENT")
    for cand in ranked:
        try:
            return await offer_to_driver(conn, org_id, actor, a, uuid.UUID(cand["driver_id"]), replacement_for_leave_id=replacement_for_leave_id,
                                         explanation={"replacement": True, "fairness": ranked[:5], "chosen": cand})
        except AppError as exc:
            if exc.status_code != 409:
                raise
            continue
    for staff in await staff_user_ids(conn, org_id):
        await notify(conn, org_id=org_id, user_id=staff, event_type="assignment.no_replacement", critical=True,
                     title="Trip needs a driver", body=f"No eligible driver for the {a['service_date']} {a['direction']} trip. Manual reassignment required.",
                     data={"allocation_id": str(allocation_id)}, dedupe_key=f"noreplace:{allocation_id}:{a['version']}")
    return None


async def notify_passengers_confirmed(conn: AsyncConnection, org_id: uuid.UUID, a: dict) -> int:
    rows = (await conn.execute(text("""
        SELECT e.user_id FROM trip_passengers p JOIN employees e ON e.organization_id=p.organization_id AND e.id=p.employee_id
        WHERE p.organization_id=:o AND p.allocation_id=:a AND p.status <> 'removed' AND e.user_id IS NOT NULL"""), {"o": org_id, "a": a["id"]})).all()
    for r in rows:
        await notify(conn, org_id=org_id, user_id=r.user_id, event_type="trip.confirmed", title="Your cab is confirmed",
                     body=f"Your {a['direction']} for {a['service_date']} is confirmed.",
                     data={"allocation_id": str(a["id"])}, dedupe_key=f"confirmed:{a['id']}:{r.user_id}:{a['driver_id']}")
    return len(rows)


async def expire_stale_assignments(conn: AsyncConnection) -> int:
    """Worker entrypoint (system session). Expires unanswered offers and continues the chain."""
    rows = (await conn.execute(text(
        "UPDATE driver_assignments SET status='EXPIRED', responded_at=now() WHERE status='PENDING' AND expires_at < now() "
        "RETURNING organization_id, allocation_id, replacement_for_leave_id"))).all()
    for r in rows:
        a = await get_allocation(conn, r.organization_id, r.allocation_id, lock=True)
        if a["status"] == "PENDING_DRIVER_ACCEPTANCE":
            await transition(conn, r.organization_id, r.allocation_id, "REQUIRES_REASSIGNMENT")
            await offer_next_driver(conn, r.organization_id, r.allocation_id, replacement_for_leave_id=r.replacement_for_leave_id, actor=None) \
                if r.replacement_for_leave_id else await _alert_staff_unanswered(conn, r.organization_id, r.allocation_id)
    return len(rows)


async def _alert_staff_unanswered(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID) -> None:
    for staff in await staff_user_ids(conn, org_id):
        await notify(conn, org_id=org_id, user_id=staff, event_type="assignment.expired", title="Driver did not respond",
                     body="A trip assignment expired without a response and needs reassignment.", data={"allocation_id": str(allocation_id)},
                     dedupe_key=f"expired:{allocation_id}:{uuid.uuid4()}")
