"""Admin fleet view: routes -> cabs (and their drivers) -> trips for a date, so staff can see coverage and assign drivers.

A cab belongs to a route through vehicles.default_route_id, or the one-day route_override for that date (never modified here).
Assigning a driver reuses the standard offer/accept/confirm flow (POST /allocations/{id}/reassign-driver)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError

router = APIRouter(prefix="/fleet", tags=["fleet"])
STAFF = ("org_admin", "dispatcher")
OPEN_STATES = ("PROPOSED", "PENDING_DRIVER_ACCEPTANCE", "DRIVER_ACCEPTED", "CONFIRMED", "IN_PROGRESS", "REQUIRES_REASSIGNMENT", "COMPLETED")

_CABS = """
  SELECT v.id, v.vehicle_code, v.registration_number, v.capacity, v.availability, v.is_active, v.default_driver_id,
         coalesce(o.to_route_id, v.default_route_id) AS route_id, (o.id IS NOT NULL) AS overridden_today,
         d.name AS default_driver_name, d.phone AS default_driver_phone, d.is_available AS default_driver_available, d.is_active AS default_driver_active
  FROM vehicles v
  LEFT JOIN route_overrides o ON o.organization_id = v.organization_id AND o.vehicle_id = v.id AND o.service_date = :d AND o.active
  LEFT JOIN drivers d ON d.organization_id = v.organization_id AND d.id = v.default_driver_id
  WHERE v.organization_id = :o AND v.is_active"""


@router.get("/routes")
async def fleet_summary(service_date: date | None = None, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """One row per active route with coverage counts for the date, plus cabs that belong to no route."""
    d = service_date or date.today()
    routes = (await conn.execute(text("SELECT r.id, r.code, r.name, s.name AS shift_name, b.name AS branch_name FROM routes r "
                                      "LEFT JOIN shifts s ON s.organization_id=r.organization_id AND s.id=r.shift_id "
                                      "LEFT JOIN locations b ON b.organization_id=r.organization_id AND b.id=r.branch_id "
                                      "WHERE r.organization_id=:o AND r.is_active ORDER BY r.code"), {"o": p.org_id})).all()
    cabs = (await conn.execute(text(_CABS), {"o": p.org_id, "d": d})).all()
    trips = (await conn.execute(text("""SELECT a.vehicle_id, a.status, a.driver_id FROM trip_allocations a
        WHERE a.organization_id=:o AND a.service_date=:d AND a.status = ANY(:st)"""), {"o": p.org_id, "d": d, "st": list(OPEN_STATES)})).all()
    by_vehicle: dict[uuid.UUID, list] = {}
    for t in trips:
        by_vehicle.setdefault(t.vehicle_id, []).append(t)
    out = []
    for r in routes:
        mine = [c for c in cabs if c.route_id == r.id]
        tr = [t for v in mine for t in by_vehicle.get(v.id, [])]
        out.append({"id": r.id, "code": r.code, "name": r.name, "shift_name": r.shift_name, "branch_name": r.branch_name,
                    "cabs": len(mine), "cabs_available": sum(1 for c in mine if c.availability == "available"),
                    "trips": len(tr), "trips_needing_driver": sum(1 for t in tr if t.driver_id is None or t.status == "REQUIRES_REASSIGNMENT")})
    unrouted = [c for c in cabs if c.route_id is None]
    return {"service_date": d, "routes": out, "unrouted_cabs": [{"id": c.id, "vehicle_code": c.vehicle_code, "registration_number": c.registration_number,
                                                                    "capacity": c.capacity, "availability": c.availability} for c in unrouted]}


@router.get("/routes/{route_id}")
async def fleet_route(route_id: uuid.UUID, service_date: date | None = None, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    d = service_date or date.today()
    r = (await conn.execute(text("SELECT r.id, r.code, r.name, s.name AS shift_name FROM routes r LEFT JOIN shifts s ON s.organization_id=r.organization_id AND s.id=r.shift_id "
                                 "WHERE r.organization_id=:o AND r.id=:r"), {"o": p.org_id, "r": route_id})).first()
    if r is None:
        raise AppError(404, "not_found", "Route not found")
    stops = (await conn.execute(text("SELECT rs.sequence, l.name, l.address FROM route_stops rs JOIN locations l ON l.organization_id=rs.organization_id AND l.id=rs.location_id "
                                     "WHERE rs.organization_id=:o AND rs.route_id=:r ORDER BY rs.sequence"), {"o": p.org_id, "r": route_id})).all()
    cabs = [c for c in (await conn.execute(text(_CABS), {"o": p.org_id, "d": d})).all() if c.route_id == route_id]
    ids = [c.id for c in cabs]
    trips = [] if not ids else (await conn.execute(text("""
        SELECT a.id, a.vehicle_id, a.driver_id, a.status, a.direction, a.capacity_snapshot, a.estimate_status, s.name AS shift_name, s.start_time,
               dr.name AS driver_name, dr.phone AS driver_phone, dr.is_available AS driver_available,
               (SELECT count(*) FROM trip_passengers tp WHERE tp.organization_id=a.organization_id AND tp.allocation_id=a.id AND tp.status <> 'removed') AS passengers
        FROM trip_allocations a JOIN shifts s ON s.organization_id=a.organization_id AND s.id=a.shift_id
        LEFT JOIN drivers dr ON dr.organization_id=a.organization_id AND dr.id=a.driver_id
        WHERE a.organization_id=:o AND a.service_date=:d AND a.vehicle_id = ANY(:ids) AND a.status = ANY(:st)
        ORDER BY s.start_time, a.direction"""), {"o": p.org_id, "d": d, "ids": ids, "st": list(OPEN_STATES)})).all()
    return {"service_date": d, "route": {"id": r.id, "code": r.code, "name": r.name, "shift_name": r.shift_name, "stops": [dict(s._mapping) for s in stops]},
            "cabs": [{"id": c.id, "vehicle_code": c.vehicle_code, "registration_number": c.registration_number, "capacity": c.capacity, "availability": c.availability,
                      "overridden_today": c.overridden_today,
                      "default_driver": {"id": c.default_driver_id, "name": c.default_driver_name, "phone": c.default_driver_phone,
                                         "available": bool(c.default_driver_available and c.default_driver_active)} if c.default_driver_id else None,
                      "trips": [{"id": t.id, "status": t.status, "direction": t.direction, "shift_name": t.shift_name, "start_time": t.start_time, "passengers": t.passengers,
                                 "capacity": t.capacity_snapshot, "estimate_status": t.estimate_status,
                                 "driver": {"id": t.driver_id, "name": t.driver_name, "phone": t.driver_phone, "available": t.driver_available} if t.driver_id else None,
                                 "needs_driver": t.driver_id is None or t.status == "REQUIRES_REASSIGNMENT"} for t in trips if t.vehicle_id == c.id]} for c in cabs]}
