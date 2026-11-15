"""Master data: locations, shifts, routes, service days, employees, drivers, vehicles (+ one-day route overrides, fleet board)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip, page_params, page_response
from ..crud import Entity, build_router
from ..schemas import (DriverIn, DriverPatch, EmployeeIn, EmployeePatch, LocationIn, LocationPatch, RouteIn, RouteOverrideIn, ServiceDayIn,
                       ShiftIn, ShiftPatch, VehicleIn, VehiclePatch)
from ..services.dispatch import LIVE, vehicle_compliance_issue

ALL = ("org_admin", "dispatcher", "driver", "employee")
STAFF = ("org_admin", "dispatcher")

router = APIRouter()

router.include_router(build_router(Entity(
    "locations", "location", LocationIn, LocationPatch, {"name": "name", "kind": "kind", "created": "created_at"}, "name ASC",
    ["name", "address"], {"kind": "kind"}, read_roles=ALL), "/locations", "locations"))
router.include_router(build_router(Entity(
    "shifts", "shift", ShiftIn, ShiftPatch, {"name": "name", "start": "start_time"}, "start_time ASC", ["name"], {}, read_roles=ALL), "/shifts", "shifts"))
router.include_router(build_router(Entity(
    "employees", "employee", EmployeeIn, EmployeePatch,
    {"name": "name", "code": "employee_code", "department": "department", "created": "created_at"}, "name ASC",
    ["name", "employee_code", "work_email", "department"], {"shift_id": "shift_id", "branch_id": "branch_id", "department": "department"}), "/employees", "employees"))
router.include_router(build_router(Entity(
    "drivers", "driver", DriverIn, DriverPatch, {"name": "name", "code": "driver_code", "license": "license_valid_until"}, "name ASC",
    ["name", "driver_code", "license_number"], {}), "/drivers", "drivers"))
router.include_router(build_router(Entity(
    "vehicles", "vehicle", VehicleIn, VehiclePatch, {"code": "vehicle_code", "registration": "registration_number", "capacity": "capacity"}, "vehicle_code ASC",
    ["vehicle_code", "registration_number", "vehicle_type"], {"availability": "availability"}), "/vehicles", "vehicles"))


# ---- routes with ordered stops ------------------------------------------------------------------------
async def _route_with_stops(conn: AsyncConnection, org, rid) -> dict:
    r = (await conn.execute(text("SELECT * FROM routes WHERE organization_id=:o AND id=:i"), {"o": org, "i": rid})).first()
    if r is None:
        raise AppError(404, "not_found", "Route not found")
    stops = (await conn.execute(text(
        "SELECT s.sequence, l.id AS location_id, l.name, l.latitude, l.longitude FROM route_stops s "
        "JOIN locations l ON l.organization_id = s.organization_id AND l.id = s.location_id WHERE s.organization_id=:o AND s.route_id=:i ORDER BY s.sequence"),
        {"o": org, "i": rid})).all()
    return {**dict(r._mapping), "stops": [dict(s._mapping) for s in stops]}


async def _write_stops(conn, org, rid, stops: list[uuid.UUID]) -> None:
    await conn.execute(text("DELETE FROM route_stops WHERE organization_id=:o AND route_id=:r"), {"o": org, "r": rid})
    for i, loc in enumerate(stops):
        await conn.execute(text("INSERT INTO route_stops (organization_id, route_id, location_id, sequence) VALUES (:o,:r,:l,:s)"), {"o": org, "r": rid, "l": loc, "s": i})


@router.get("/routes", tags=["routes"])
async def list_routes(p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for), q: str | None = None,
                      page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "q": f"%{(q or '').replace('%', '')[:80]}%"}
    total = await conn.scalar(text("SELECT count(*) FROM routes WHERE organization_id=:o AND (code ILIKE :q OR name ILIKE :q)"), params)
    rows = (await conn.execute(text(
        "SELECT r.*, (SELECT count(*) FROM route_stops s WHERE s.organization_id=r.organization_id AND s.route_id=r.id) AS stop_count "
        "FROM routes r WHERE r.organization_id=:o AND (r.code ILIKE :q OR r.name ILIKE :q) ORDER BY r.code LIMIT :l OFFSET :off"), {**params, "l": size, "off": off})).all()
    return page_response([dict(r._mapping) for r in rows], total, page, size)


@router.get("/routes/{route_id}", tags=["routes"])
async def get_route(route_id: uuid.UUID, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    return await _route_with_stops(conn, p.org_id, route_id)


@router.post("/routes", status_code=201, tags=["routes"])
async def create_route(payload: RouteIn, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text("INSERT INTO routes (organization_id, code, name, branch_id, shift_id, is_active) VALUES (:o,:c,:n,:b,:s,:a) RETURNING id"),
                            {"o": p.org_id, "c": payload.code, "n": payload.name, "b": payload.branch_id, "s": payload.shift_id, "a": payload.is_active})).first()
    await _write_stops(conn, p.org_id, r.id, payload.stops)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="route.created", entity_type="route", entity_id=r.id, ip=client_ip(request))
    return await _route_with_stops(conn, p.org_id, r.id)


@router.put("/routes/{route_id}", tags=["routes"])
async def replace_route(route_id: uuid.UUID, payload: RouteIn, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE routes SET code=:c, name=:n, branch_id=:b, shift_id=:s, is_active=:a WHERE organization_id=:o AND id=:i"),
                            {"o": p.org_id, "i": route_id, "c": payload.code, "n": payload.name, "b": payload.branch_id, "s": payload.shift_id, "a": payload.is_active})).rowcount
    if not n:
        raise AppError(404, "not_found", "Route not found")
    await _write_stops(conn, p.org_id, route_id, payload.stops)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="route.updated", entity_type="route", entity_id=route_id, ip=client_ip(request))
    return await _route_with_stops(conn, p.org_id, route_id)


@router.delete("/routes/{route_id}", status_code=204, tags=["routes"])
async def delete_route(route_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE routes SET is_active=false WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": route_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "Route not found")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="route.deactivated", entity_type="route", entity_id=route_id, ip=client_ip(request))


# ---- service days (holidays / exceptions) -------------------------------------------------------------------
@router.get("/service-days", tags=["service-days"])
async def list_service_days(p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for), start: date | None = None, end: date | None = None):
    rows = (await conn.execute(text("SELECT * FROM service_days WHERE organization_id=:o AND service_date BETWEEN coalesce(:s, '1900-01-01') AND coalesce(:e, '2999-12-31') ORDER BY service_date LIMIT 500"),
                               {"o": p.org_id, "s": start, "e": end})).all()
    return [dict(r._mapping) for r in rows]


@router.put("/service-days", tags=["service-days"])
async def upsert_service_day(payload: ServiceDayIn, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text(
        "INSERT INTO service_days (organization_id, service_date, is_service_day, note) VALUES (:o,:d,:s,:n) "
        "ON CONFLICT (organization_id, service_date) DO UPDATE SET is_service_day=EXCLUDED.is_service_day, note=EXCLUDED.note RETURNING *"),
        {"o": p.org_id, "d": payload.service_date, "s": payload.is_service_day, "n": payload.note})).first()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="service_day.set", entity_type="service_day", entity_id=payload.service_date,
                details={"is_service_day": payload.is_service_day}, ip=client_ip(request))
    return dict(r._mapping)


# ---- one-day vehicle reroute (never touches the vehicle's recurring default route) ----------------------------
@router.post("/vehicles/{vehicle_id}/route-override", status_code=201, tags=["vehicles"])
async def override_vehicle_route(vehicle_id: uuid.UUID, payload: RouteOverrideIn, request: Request,
                                 p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    v = (await conn.execute(text("SELECT * FROM vehicles WHERE organization_id=:o AND id=:v"), {"o": p.org_id, "v": vehicle_id})).first()
    if v is None:
        raise AppError(404, "not_found", "Vehicle not found")
    if (issue := vehicle_compliance_issue(dict(v._mapping), payload.service_date)):
        raise AppError(409, "vehicle_not_available", f"Vehicle cannot be reassigned: {issue}")
    # "Assigned but empty" is NOT free: any live allocation that day blocks the reroute.
    busy = await conn.scalar(text("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND vehicle_id=:v AND service_date=:d AND status = ANY(:live)"),
                             {"o": p.org_id, "v": vehicle_id, "d": payload.service_date, "live": list(LIVE)})
    if busy:
        raise AppError(409, "vehicle_not_free", "Vehicle has live allocations on that date (including assigned-but-empty cabs)", {"allocations": busy})
    route_ok = await conn.scalar(text("SELECT 1 FROM routes WHERE organization_id=:o AND id=:r AND is_active"), {"o": p.org_id, "r": payload.to_route_id})
    if not route_ok:
        raise AppError(404, "not_found", "Target route not found or inactive")
    exists = await conn.scalar(text("SELECT 1 FROM route_overrides WHERE organization_id=:o AND vehicle_id=:v AND service_date=:d AND active"), {"o": p.org_id, "v": vehicle_id, "d": payload.service_date})
    if exists:
        raise AppError(409, "override_exists", "An override already exists for this vehicle and date")
    r = (await conn.execute(text(
        "INSERT INTO route_overrides (organization_id, vehicle_id, service_date, from_route_id, to_route_id, reason, actor_user_id) "
        "VALUES (:o,:v,:d,:f,:t,:why,:a) RETURNING *"),
        {"o": p.org_id, "v": vehicle_id, "d": payload.service_date, "f": v.default_route_id, "t": payload.to_route_id, "why": payload.reason, "a": p.user_id})).first()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="vehicle.route_override", entity_type="vehicle", entity_id=vehicle_id,
                details={"service_date": payload.service_date, "from": v.default_route_id, "to": payload.to_route_id, "reason": payload.reason}, ip=client_ip(request))
    return dict(r._mapping)


@router.delete("/vehicles/{vehicle_id}/route-override/{service_date}", status_code=204, tags=["vehicles"])
async def clear_override(vehicle_id: uuid.UUID, service_date: date, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE route_overrides SET active=false WHERE organization_id=:o AND vehicle_id=:v AND service_date=:d AND active"),
                            {"o": p.org_id, "v": vehicle_id, "d": service_date})).rowcount
    if not n:
        raise AppError(404, "not_found", "No active override")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="vehicle.route_override_cleared", entity_type="vehicle", entity_id=vehicle_id,
                details={"service_date": service_date}, ip=client_ip(request))


# ---- fleet board: cab status per service date ----------------------------------------------------------------------
@router.get("/vehicles-board", tags=["vehicles"])
async def fleet_board(service_date: date, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    rows = (await conn.execute(text("""
        SELECT v.id, v.vehicle_code, v.registration_number, v.capacity, v.availability, v.is_active, v.default_route_id,
               o.to_route_id AS override_route_id,
               coalesce((SELECT max(c) FROM (SELECT count(p.id) AS c FROM trip_allocations a
                         LEFT JOIN trip_passengers p ON p.organization_id=a.organization_id AND p.allocation_id=a.id AND p.status <> 'removed'
                         WHERE a.organization_id=v.organization_id AND a.vehicle_id=v.id AND a.service_date=:d AND a.status <> 'CANCELLED' GROUP BY a.id) x), 0) AS max_filled,
               (SELECT count(*) FROM trip_allocations a WHERE a.organization_id=v.organization_id AND a.vehicle_id=v.id AND a.service_date=:d AND a.status <> 'CANCELLED') AS allocations,
               (SELECT count(*) FROM trip_allocations a WHERE a.organization_id=v.organization_id AND a.vehicle_id=v.id AND a.service_date=:d AND a.status = 'PENDING_DRIVER_ACCEPTANCE') AS pending
        FROM vehicles v LEFT JOIN route_overrides o ON o.organization_id=v.organization_id AND o.vehicle_id=v.id AND o.service_date=:d AND o.active
        WHERE v.organization_id=:o ORDER BY v.vehicle_code"""), {"o": p.org_id, "d": service_date})).all()
    out = []
    for r in rows:
        if not r.is_active or r.availability == "unavailable":
            status = "unavailable"
        elif r.availability == "maintenance":
            status = "under_maintenance"
        elif r.pending:
            status = "pending_driver_acceptance"
        elif r.allocations == 0:
            status = "available"
        elif r.max_filled == 0:
            status = "assigned_empty"          # assigned but empty is NOT free
        elif r.max_filled >= r.capacity:
            status = "full"
        else:
            status = "partially_filled"
        out.append({"vehicle_id": r.id, "vehicle_code": r.vehicle_code, "registration_number": r.registration_number, "capacity": r.capacity,
                    "status": status, "max_passengers": r.max_filled, "allocations": r.allocations,
                    "default_route_id": r.default_route_id, "override_route_id": r.override_route_id})
    return {"service_date": service_date, "vehicles": out}
