"""Dispatch: requests, allocation runs, manual edits, driver acceptance, lifecycle, leave & replacement, self-service trips."""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import (AppError, audit, client_ip, idempotency_store, idempotent, notify, page_params, page_response, staff_user_ids)
from ..schemas import (AllocateIn, AllocationPatch, DriverResponseIn, LeaveDecision, LeaveIn, MyRequestIn, PassengerAdd, ReasonIn, RequestsGenerateIn)
from ..services import dispatch as svc
from ..services import trip_credentials as tc
from ..services.routing import get_routing_provider

router = APIRouter()
STAFF = ("org_admin", "dispatcher")


class ReassignIn(BaseModel):
    driver_id: uuid.UUID
    emergency: bool = False


def _d(r) -> dict:
    return dict(r._mapping)


async def _my_driver_ids(conn: AsyncConnection, p: Principal) -> list[uuid.UUID]:
    return [r.id for r in await conn.execute(text("SELECT id FROM drivers WHERE organization_id=:o AND user_id=:u"), {"o": p.org_id, "u": p.user_id})]


async def recompute_route(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID) -> None:
    """Re-estimate distance/duration after a manual change. Failure => provisional (blocks send/confirm)."""
    a = await svc.get_allocation(conn, org_id, allocation_id)
    stops = (await conn.execute(text("""
        SELECT p.id, CASE :dir WHEN 'pickup' THEN r.pickup_lat ELSE r.drop_lat END AS lat, CASE :dir WHEN 'pickup' THEN r.pickup_lon ELSE r.drop_lon END AS lon,
               b.latitude AS b_lat, b.longitude AS b_lon
        FROM trip_passengers p JOIN employee_transport_requests r ON r.organization_id=p.organization_id AND r.id=p.request_id
        JOIN employees e ON e.organization_id=p.organization_id AND e.id=p.employee_id
        LEFT JOIN locations b ON b.organization_id=e.organization_id AND b.id=e.branch_id
        WHERE p.organization_id=:o AND p.allocation_id=:a AND p.status <> 'removed' ORDER BY p.stop_order"""), {"o": org_id, "a": allocation_id, "dir": a["direction"]})).all()
    if not stops or any(s.lat is None or s.b_lat is None for s in stops):
        await conn.execute(text("UPDATE trip_allocations SET estimate_status='provisional', planned_distance_km=NULL, planned_duration_minutes=NULL WHERE organization_id=:o AND id=:a"), {"o": org_id, "a": allocation_id})
        return
    anchor = (stops[0].b_lat, stops[0].b_lon)
    pts = [(s.lat, s.lon) for s in stops]
    path = [*pts, anchor] if a["direction"] == "pickup" else [anchor, *pts]
    route = await get_routing_provider().route(path)
    if route is None:
        await conn.execute(text("UPDATE trip_allocations SET estimate_status='provisional', planned_distance_km=NULL, planned_duration_minutes=NULL WHERE organization_id=:o AND id=:a"), {"o": org_id, "a": allocation_id})
    else:
        await conn.execute(text("UPDATE trip_allocations SET estimate_status='final', planned_distance_km=:d, planned_duration_minutes=:t WHERE organization_id=:o AND id=:a"),
                           {"d": round(route.distance_m / 1000, 2), "t": round(route.duration_s / 60, 1), "o": org_id, "a": allocation_id})


# =============================================================================================================
# Requests and allocation runs
# =============================================================================================================
@router.post("/dispatch/requests/generate", tags=["dispatch"])
async def generate(payload: RequestsGenerateIn, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    res = await svc.generate_requests(conn, p.org_id, payload.service_date, payload.shift_id, payload.direction)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="requests.generated", entity_type="slot", entity_id=f"{payload.service_date}:{payload.shift_id}:{payload.direction}",
                details={"created": res["created"]}, ip=client_ip(request))
    return res


@router.get("/dispatch/requests", tags=["dispatch"])
async def list_requests(service_date: date, shift_id: uuid.UUID | None = None, direction: str | None = None, status: str | None = None,
                        p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for),
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "d": service_date, "sh": shift_id, "dir": direction, "st": status}
    cond = ("r.organization_id=:o AND r.service_date=:d AND (CAST(:sh AS uuid) IS NULL OR r.shift_id=:sh) "
            "AND (CAST(:dir AS text) IS NULL OR r.direction=:dir) AND (CAST(:st AS text) IS NULL OR r.status=:st)")
    total = await conn.scalar(text(f"SELECT count(*) FROM employee_transport_requests r WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT r.*, e.name, e.employee_code FROM employee_transport_requests r JOIN employees e ON e.organization_id=r.organization_id AND e.id=r.employee_id "
                                    f"WHERE {cond} ORDER BY e.employee_code LIMIT :l OFFSET :f"), {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)


@router.post("/dispatch/allocate", tags=["dispatch"])
async def allocate(payload: AllocateIn, request: Request, idempotency_key: str | None = Header(default=None),
                   p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """dry_run=true (default) computes and returns a proposal without persisting anything. dry_run=false persists PROPOSED allocations."""
    if payload.generate_requests:
        await svc.generate_requests(conn, p.org_id, payload.service_date, payload.shift_id, payload.direction)
    if payload.dry_run:
        plan = await svc.plan_slot(conn, p.org_id, service_date=payload.service_date, shift_id=payload.shift_id, direction=payload.direction, branch_id=payload.branch_id)
        return {"dry_run": True, "plan": plan, "allocations": []}
    cached = await idempotent(conn, org_id=p.org_id, user_id=p.user_id, scope="allocate", key=idempotency_key, payload=payload.model_dump(mode="json"))
    if cached is not None:
        return cached
    plan = await svc.plan_slot(conn, p.org_id, service_date=payload.service_date, shift_id=payload.shift_id, direction=payload.direction, branch_id=payload.branch_id)
    created = await svc.commit_plan(conn, p.org_id, p.user_id, plan, replace_proposed=payload.replace_proposed)
    result = {"dry_run": False, "plan": plan, "allocations": [{"id": str(c["id"]), "status": c["status"], "vehicle_id": str(c["vehicle_id"]),
                                                              "driver_id": str(c["driver_id"]) if c["driver_id"] else None} for c in created]}
    await idempotency_store(conn, org_id=p.org_id, user_id=p.user_id, scope="allocate", key=idempotency_key, response=result)
    return result


@router.get("/dispatch/exceptions", tags=["dispatch"])
async def exceptions(service_date: date | None = None, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    d = service_date or date.today()
    rows = (await conn.execute(text("""
        SELECT a.id, a.service_date, a.direction, a.status, a.estimate_status, v.vehicle_code,
          CASE WHEN a.status='REQUIRES_REASSIGNMENT' THEN 'requires_reassignment'
               WHEN a.estimate_status <> 'final' THEN 'estimate_not_final'
               WHEN a.driver_id IS NULL THEN 'no_driver' END AS issue
        FROM trip_allocations a JOIN vehicles v ON v.organization_id=a.organization_id AND v.id=a.vehicle_id
        WHERE a.organization_id=:o AND a.service_date >= :d AND a.status NOT IN ('CANCELLED','COMPLETED')
          AND (a.status='REQUIRES_REASSIGNMENT' OR a.estimate_status <> 'final' OR a.driver_id IS NULL) ORDER BY a.service_date LIMIT 200"""), {"o": p.org_id, "d": d})).all()
    open_requests = await conn.scalar(text("SELECT count(*) FROM employee_transport_requests WHERE organization_id=:o AND service_date >= :d AND status='open'"), {"o": p.org_id, "d": d})
    declined = (await conn.execute(text("SELECT allocation_id, driver_id, responded_at FROM driver_assignments WHERE organization_id=:o AND status IN ('DECLINED','EXPIRED') AND responded_at > now() - interval '2 days' ORDER BY responded_at DESC LIMIT 50"), {"o": p.org_id})).all()
    return {"allocations": [_d(r) for r in rows], "open_requests": open_requests, "recent_declines_or_expiries": [_d(r) for r in declined]}


# =============================================================================================================
# Allocations
# =============================================================================================================
@router.get("/allocations", tags=["allocations"])
async def list_allocations(service_date: date | None = None, status: str | None = None, direction: str | None = None, shift_id: uuid.UUID | None = None,
                           p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for),
                           page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "d": service_date, "st": status, "dir": direction, "sh": shift_id}
    cond = ("a.organization_id=:o AND (CAST(:d AS date) IS NULL OR a.service_date=:d) AND (CAST(:st AS text) IS NULL OR a.status=:st) "
            "AND (CAST(:dir AS text) IS NULL OR a.direction=:dir) AND (CAST(:sh AS uuid) IS NULL OR a.shift_id=:sh)")
    total = await conn.scalar(text(f"SELECT count(*) FROM trip_allocations a WHERE {cond}"), params)
    rows = (await conn.execute(text(f"""
        SELECT a.*, v.vehicle_code, v.registration_number, dr.name AS driver_name,
               (SELECT count(*) FROM trip_passengers tp WHERE tp.organization_id=a.organization_id AND tp.allocation_id=a.id AND tp.status <> 'removed') AS passenger_count
        FROM trip_allocations a JOIN vehicles v ON v.organization_id=a.organization_id AND v.id=a.vehicle_id
        LEFT JOIN drivers dr ON dr.organization_id=a.organization_id AND dr.id=a.driver_id
        WHERE {cond} ORDER BY a.service_date DESC, a.created_at DESC LIMIT :l OFFSET :f"""), {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)


@router.get("/allocations/{allocation_id}", tags=["allocations"])
async def get_allocation(allocation_id: uuid.UUID, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id)
    manifest = (await conn.execute(text("SELECT * FROM rp.trip_manifest(:o,:a)"), {"o": p.org_id, "a": allocation_id})).all()
    assignments = (await conn.execute(text("SELECT asg.*, d.name AS driver_name FROM driver_assignments asg JOIN drivers d ON d.organization_id=asg.organization_id AND d.id=asg.driver_id "
                                           "WHERE asg.organization_id=:o AND asg.allocation_id=:a ORDER BY asg.requested_at"), {"o": p.org_id, "a": allocation_id})).all()
    return {**a, "passengers": [_d(m) for m in manifest], "assignments": [_d(x) for x in assignments]}


@router.patch("/allocations/{allocation_id}", tags=["allocations"])
async def edit_allocation(allocation_id: uuid.UUID, payload: AllocationPatch, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] not in svc.EDITABLE:
        raise AppError(409, "not_editable", f"Allocation in {a['status']} cannot be edited; cancel or reassign instead")
    if a["version"] != payload.expected_version:
        raise AppError(409, "version_conflict", "Allocation was changed by someone else; reload and retry", {"current_version": a["version"]})
    changes: dict = {}
    if payload.vehicle_id and payload.vehicle_id != a["vehicle_id"]:
        v = (await conn.execute(text("SELECT * FROM vehicles WHERE organization_id=:o AND id=:v"), {"o": p.org_id, "v": payload.vehicle_id})).first()
        if v is None:
            raise AppError(404, "not_found", "Vehicle not found")
        if (issue := svc.vehicle_compliance_issue(_d(v), a["service_date"])):
            raise AppError(409, "vehicle_not_compliant", f"Vehicle cannot be used: {issue}")
        cnt = await conn.scalar(text("SELECT count(*) FROM trip_passengers WHERE organization_id=:o AND allocation_id=:a AND status <> 'removed'"), {"o": p.org_id, "a": allocation_id})
        if cnt > v.capacity:
            raise AppError(409, "capacity_exceeded", f"Vehicle seats {v.capacity} but allocation has {cnt} passengers")
        await conn.execute(text("UPDATE trip_allocations SET vehicle_id=:v, capacity_snapshot=:c WHERE organization_id=:o AND id=:a"), {"v": payload.vehicle_id, "c": v.capacity, "o": p.org_id, "a": allocation_id})
        changes["vehicle_id"] = payload.vehicle_id
    if payload.driver_id and payload.driver_id != a["driver_id"]:
        ok = await svc.eligible_drivers(conn, p.org_id, service_date=a["service_date"], shift_id=a["shift_id"], direction=a["direction"])
        if str(payload.driver_id) not in {d.driver_id for d in ok}:
            raise AppError(409, "driver_not_eligible", "Driver is unavailable, on leave, unlicensed for the date, not eligible for the shift, or already assigned in this slot")
        await conn.execute(text("UPDATE trip_allocations SET driver_id=:d WHERE organization_id=:o AND id=:a"), {"d": payload.driver_id, "o": p.org_id, "a": allocation_id})
        changes["driver_id"] = payload.driver_id
    if not changes:
        raise AppError(422, "no_changes", "Nothing to change")
    await conn.execute(text("UPDATE trip_allocations SET version=version+1 WHERE organization_id=:o AND id=:a"), {"o": p.org_id, "a": allocation_id})
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="allocation.edited", entity_type="allocation", entity_id=allocation_id, details=changes, ip=client_ip(request))
    return await svc.get_allocation(conn, p.org_id, allocation_id)


@router.post("/allocations/{allocation_id}/passengers", status_code=201, tags=["allocations"])
async def add_passenger(allocation_id: uuid.UUID, payload: PassengerAdd, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] not in svc.EDITABLE:
        raise AppError(409, "not_editable", f"Allocation in {a['status']} cannot be edited")
    if payload.expected_version is not None and payload.expected_version != a["version"]:
        raise AppError(409, "version_conflict", "Allocation was changed by someone else", {"current_version": a["version"]})
    r = (await conn.execute(text("""SELECT r.*, e.accessibility_needs FROM employee_transport_requests r JOIN employees e ON e.organization_id=r.organization_id AND e.id=r.employee_id
                                    WHERE r.organization_id=:o AND r.id=:r"""), {"o": p.org_id, "r": payload.request_id})).first()
    if r is None or r.status != "open" or r.service_date != a["service_date"] or r.shift_id != a["shift_id"] or r.direction != a["direction"]:
        raise AppError(409, "request_not_available", "Request is not open for this allocation's date, shift and direction")
    v = (await conn.execute(text("SELECT accessibility_features FROM vehicles WHERE organization_id=:o AND id=:v"), {"o": p.org_id, "v": a["vehicle_id"]})).first()
    if not set(r.accessibility_needs or []) <= set(v.accessibility_features or []):
        raise AppError(409, "accessibility_mismatch", "Vehicle does not support this employee's accessibility needs")
    order = payload.stop_order if payload.stop_order is not None else (await conn.scalar(text("SELECT coalesce(max(stop_order)+1,0) FROM trip_passengers WHERE organization_id=:o AND allocation_id=:a AND status <> 'removed'"), {"o": p.org_id, "a": allocation_id}))
    row = (await conn.execute(text("INSERT INTO trip_passengers (organization_id, allocation_id, request_id, employee_id, stop_order) VALUES (:o,:a,:r,:e,:so) RETURNING id"),
                              {"o": p.org_id, "a": allocation_id, "r": payload.request_id, "e": r.employee_id, "so": order})).first()   # capacity trigger enforces seats
    await conn.execute(text("UPDATE employee_transport_requests SET status='allocated' WHERE organization_id=:o AND id=:r"), {"o": p.org_id, "r": payload.request_id})
    await conn.execute(text("UPDATE trip_allocations SET version=version+1 WHERE organization_id=:o AND id=:a"), {"o": p.org_id, "a": allocation_id})
    await recompute_route(conn, p.org_id, allocation_id)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="allocation.passenger_added", entity_type="allocation", entity_id=allocation_id, details={"request_id": payload.request_id}, ip=client_ip(request))
    return {"passenger_id": row.id}


@router.delete("/allocations/{allocation_id}/passengers/{passenger_id}", status_code=204, tags=["allocations"])
async def remove_passenger(allocation_id: uuid.UUID, passenger_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] not in svc.EDITABLE:
        raise AppError(409, "not_editable", f"Allocation in {a['status']} cannot be edited")
    row = (await conn.execute(text("UPDATE trip_passengers SET status='removed' WHERE organization_id=:o AND allocation_id=:a AND id=:i AND status <> 'removed' RETURNING request_id"),
                              {"o": p.org_id, "a": allocation_id, "i": passenger_id})).first()
    if row is None:
        raise AppError(404, "not_found", "Passenger not on this allocation")
    await conn.execute(text("UPDATE employee_transport_requests SET status='open' WHERE organization_id=:o AND id=:r"), {"o": p.org_id, "r": row.request_id})
    await conn.execute(text("UPDATE trip_allocations SET version=version+1 WHERE organization_id=:o AND id=:a"), {"o": p.org_id, "a": allocation_id})
    await recompute_route(conn, p.org_id, allocation_id)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="allocation.passenger_removed", entity_type="allocation", entity_id=allocation_id, details={"passenger_id": passenger_id}, ip=client_ip(request))


@router.post("/allocations/{allocation_id}/send", tags=["allocations"])
async def send_to_driver(allocation_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] == "PENDING_DRIVER_ACCEPTANCE":     # idempotent: no duplicate assignment/notification
        return {**a, "already_sent": True}
    if a["status"] not in ("PROPOSED", "REQUIRES_REASSIGNMENT"):
        raise AppError(409, "invalid_transition", f"Cannot send an allocation in {a['status']}")
    if a["driver_id"] is None:
        raise AppError(409, "driver_required", "Assign a driver first")
    refused = await conn.scalar(text("SELECT 1 FROM driver_assignments WHERE organization_id=:o AND allocation_id=:a AND driver_id=:d AND status IN ('DECLINED','EXPIRED')"),
                                {"o": p.org_id, "a": allocation_id, "d": a["driver_id"]})
    if refused:
        raise AppError(409, "driver_declined", "This driver declined or did not respond; choose another driver with reassign-driver")
    asg = await svc.offer_to_driver(conn, p.org_id, p.user_id, a, a["driver_id"])
    return {**await svc.get_allocation(conn, p.org_id, allocation_id), "assignment": _d_asg(asg)}


def _d_asg(asg: dict) -> dict:
    return {k: asg[k] for k in ("id", "driver_id", "status", "expires_at")}


@router.get("/allocations/{allocation_id}/eligible-drivers", tags=["allocations"])
async def eligible_driver_search(allocation_id: uuid.UUID, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """Replacement-driver search: eligible drivers (leave, licence, shift, slot conflicts applied), fairest first, with score explanations."""
    from ..services.fairness import TripLoad, score_candidates
    a = await svc.get_allocation(conn, p.org_id, allocation_id)
    st = await svc.org_settings(conn, p.org_id)
    refused = {r.driver_id for r in await conn.execute(text("SELECT driver_id FROM driver_assignments WHERE organization_id=:o AND allocation_id=:a AND status IN ('DECLINED','EXPIRED')"), {"o": p.org_id, "a": allocation_id})}
    cands = await svc.eligible_drivers(conn, p.org_id, service_date=a["service_date"], shift_id=a["shift_id"], direction=a["direction"], exclude=refused, settings=st)
    ranked = score_candidates(cands, TripLoad(float(a["planned_distance_km"] or 0), float(a["planned_duration_minutes"] or 0), None, a["is_local_trip"]), svc.fairness_config(st))
    return {"allocation_id": allocation_id, "candidates": ranked}


@router.post("/allocations/{allocation_id}/reassign-driver", tags=["allocations"])
async def reassign_driver(allocation_id: uuid.UUID, payload: ReassignIn, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] in ("PENDING_DRIVER_ACCEPTANCE", "DRIVER_ACCEPTED", "CONFIRMED"):
        a = await svc.transition(conn, p.org_id, allocation_id, "REQUIRES_REASSIGNMENT")
    elif a["status"] == "PROPOSED":
        pass
    elif a["status"] != "REQUIRES_REASSIGNMENT":
        raise AppError(409, "invalid_transition", f"Cannot reassign an allocation in {a['status']}")
    ok = await svc.eligible_drivers(conn, p.org_id, service_date=a["service_date"], shift_id=a["shift_id"], direction=a["direction"])
    if str(payload.driver_id) not in {d.driver_id for d in ok}:
        raise AppError(409, "driver_not_eligible", "Driver is not eligible for this slot")
    if a["status"] == "PROPOSED":
        a = {**a}
    asg = await svc.offer_to_driver(conn, p.org_id, p.user_id, a, payload.driver_id)
    if payload.emergency:
        await conn.execute(text("UPDATE driver_assignments SET expires_at = now() + interval '5 minutes' WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": asg["id"]})
        user = await conn.scalar(text("SELECT user_id FROM drivers WHERE organization_id=:o AND id=:d"), {"o": p.org_id, "d": payload.driver_id})
        if user:
            await notify(conn, org_id=p.org_id, user_id=user, event_type="assignment.emergency", critical=True, title="EMERGENCY dispatch",
                         body="Urgent trip assignment — respond within 5 minutes.", data={"allocation_id": str(allocation_id)}, dedupe_key=f"emerg:{asg['id']}")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="allocation.driver_reassigned", entity_type="allocation", entity_id=allocation_id,
                details={"driver_id": payload.driver_id, "emergency": payload.emergency}, ip=client_ip(request))
    return await svc.get_allocation(conn, p.org_id, allocation_id)


@router.post("/allocations/{allocation_id}/confirm", tags=["allocations"])
async def confirm(allocation_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["status"] == "CONFIRMED":
        return {**a, "already_confirmed": True}
    if a["status"] != "DRIVER_ACCEPTED":
        raise AppError(409, "driver_acceptance_required", f"Allocation must be DRIVER_ACCEPTED to confirm (currently {a['status']})")
    await svc.validate_sendable(conn, p.org_id, a)
    a = await svc.transition(conn, p.org_id, allocation_id, "CONFIRMED")
    n = await svc.notify_passengers_confirmed(conn, p.org_id, a)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="allocation.confirmed", entity_type="allocation", entity_id=allocation_id, details={"notified": n}, ip=client_ip(request))
    return a


@router.post("/allocations/{allocation_id}/cancel", tags=["allocations"])
async def cancel(allocation_id: uuid.UUID, payload: ReasonIn, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    a = await svc.cancel_allocation(conn, p.org_id, p.user_id, allocation_id, payload.reason)
    return a


# =============================================================================================================
# Driver workflow
# =============================================================================================================
@router.get("/trips/mine", tags=["trips"])
async def my_trips(p: Principal = Depends(require_roles("driver", "employee")), conn: AsyncConnection = Depends(db_for),
                   start: date | None = None, end: date | None = None):
    start = start or date.today() - timedelta(days=1)
    end = end or start + timedelta(days=8)
    rows = (await conn.execute(text("""
        SELECT a.id, a.service_date, a.direction, a.status, a.estimate_status, a.planned_distance_km, a.planned_duration_minutes, a.shift_id, s.name AS shift_name, s.start_time, s.end_time
        FROM trip_allocations a JOIN shifts s ON s.organization_id=a.organization_id AND s.id=a.shift_id
        WHERE a.organization_id=:o AND a.service_date BETWEEN :s AND :e AND a.status NOT IN ('DRAFT','PROPOSED','CANCELLED') ORDER BY a.service_date, s.start_time"""),
        {"o": p.org_id, "s": start, "e": end})).all()
    out = []
    req = await tc.requirements(conn, p.org_id)
    for r in rows:
        item = _d(r)
        item["requires_boarding_code"] = req["boarding_code"]
        crew = (await conn.execute(text("SELECT * FROM rp.trip_crew(:o,:a)"), {"o": p.org_id, "a": r.id})).first()
        item["crew"] = _d(crew) if crew else None
        if p.role == "driver":
            item["passengers"] = [_d(m) for m in (await conn.execute(text("SELECT * FROM rp.trip_manifest(:o,:a)"), {"o": p.org_id, "a": r.id})).all()]
            asg = (await conn.execute(text("SELECT asg.id, asg.status, asg.expires_at FROM driver_assignments asg WHERE asg.organization_id=:o AND asg.allocation_id=:a "
                                           "AND asg.driver_id = ANY(:mine) ORDER BY asg.requested_at DESC LIMIT 1"), {"o": p.org_id, "a": r.id, "mine": await _my_driver_ids(conn, p)})).first()
            item["assignment"] = _d(asg) if asg else None
        out.append(item)
    return out


@router.post("/driver-assignments/{assignment_id}/respond", tags=["driver-assignments"])
async def respond(assignment_id: uuid.UUID, payload: DriverResponseIn, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    mine = await _my_driver_ids(conn, p)
    asg = (await conn.execute(text("SELECT * FROM driver_assignments WHERE organization_id=:o AND id=:i AND driver_id = ANY(:m) FOR UPDATE"), {"o": p.org_id, "i": assignment_id, "m": mine})).first()
    if asg is None:
        raise AppError(404, "not_found", "Assignment not found")
    wanted = "ACCEPTED" if payload.accept else "DECLINED"
    if asg.status == wanted:                       # idempotent repeat
        return {"assignment_id": asg.id, "status": asg.status, "already_recorded": True}
    if asg.status != "PENDING":
        raise AppError(409, "assignment_closed", f"Assignment is {asg.status} and can no longer be answered")
    expired = await conn.scalar(text("SELECT expires_at < now() FROM driver_assignments WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": assignment_id})
    if expired:
        raise AppError(409, "assignment_expired", "This assignment request has expired")
    await conn.execute(text("UPDATE driver_assignments SET status=:s, responded_at=now(), response_reason=:r WHERE organization_id=:o AND id=:i"),
                       {"s": wanted, "r": payload.reason, "o": p.org_id, "i": assignment_id})
    st = await svc.org_settings(conn, p.org_id)
    if payload.accept:
        a = await svc.transition(conn, p.org_id, asg.allocation_id, "DRIVER_ACCEPTED")
        if not st["require_dispatcher_confirmation"]:
            await svc.validate_sendable(conn, p.org_id, a)
            a = await svc.transition(conn, p.org_id, asg.allocation_id, "CONFIRMED")
            await svc.notify_passengers_confirmed(conn, p.org_id, a)
        for staff in await staff_user_ids(conn, p.org_id):
            await notify(conn, org_id=p.org_id, user_id=staff, event_type="assignment.accepted", title="Driver accepted",
                         body=f"{p.full_name or 'A driver'} accepted a trip.", data={"allocation_id": str(asg.allocation_id)}, dedupe_key=f"accepted:{asg.id}")
    else:
        await svc.transition(conn, p.org_id, asg.allocation_id, "REQUIRES_REASSIGNMENT")
        for staff in await staff_user_ids(conn, p.org_id):
            await notify(conn, org_id=p.org_id, user_id=staff, event_type="assignment.declined", critical=True, title="Driver declined",
                         body="A driver declined a trip; reassignment needed.", data={"allocation_id": str(asg.allocation_id)}, dedupe_key=f"declined:{asg.id}")
        if asg.replacement_for_leave_id:
            await svc.offer_next_driver(conn, p.org_id, asg.allocation_id, replacement_for_leave_id=asg.replacement_for_leave_id, actor=p.user_id)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action=f"assignment.{wanted.lower()}", entity_type="allocation", entity_id=asg.allocation_id, ip=client_ip(request))
    return {"assignment_id": asg.id, "status": wanted}


async def _own_allocation(conn: AsyncConnection, p: Principal, allocation_id: uuid.UUID) -> dict:
    a = await svc.get_allocation(conn, p.org_id, allocation_id, lock=True)
    if a["driver_id"] not in await _my_driver_ids(conn, p):
        raise AppError(404, "not_found", "Allocation not found")
    return a


@router.post("/allocations/{allocation_id}/start", tags=["trips"])
async def start_trip(allocation_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    await _own_allocation(conn, p, allocation_id)
    a = await svc.transition(conn, p.org_id, allocation_id, "IN_PROGRESS", extra_sql=", started_at = now()")
    await conn.execute(text("SELECT rp.ensure_boarding_codes(:o,:a)"), {"o": p.org_id, "a": allocation_id})
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="trip.started", entity_type="allocation", entity_id=allocation_id, ip=client_ip(request))
    return a


@router.post("/allocations/{allocation_id}/complete", tags=["trips"])
async def complete_trip(allocation_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    await _own_allocation(conn, p, allocation_id)
    a = await svc.transition(conn, p.org_id, allocation_id, "COMPLETED", extra_sql=", completed_at = now()")
    await conn.execute(text("DELETE FROM vehicle_last_position WHERE organization_id=:o AND allocation_id=:a"), {"o": p.org_id, "a": allocation_id})   # sharing stops with the trip
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="trip.completed", entity_type="allocation", entity_id=allocation_id, ip=client_ip(request))
    return a


class PaxStatus(BaseModel):
    status: str = Field(pattern="^(boarded|dropped|no_show)$")
    code: str | None = Field(None, pattern=r"^\d{4}$", description="Passenger's 4-digit boarding code (required to mark boarded when the organization enabled it)")


@router.post("/allocations/{allocation_id}/passengers/{passenger_id}/status", tags=["trips"])
async def passenger_status(allocation_id: uuid.UUID, passenger_id: uuid.UUID, payload: PaxStatus, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    a = await _own_allocation(conn, p, allocation_id)
    if a["status"] != "IN_PROGRESS":
        raise AppError(409, "trip_not_in_progress", "Passenger status can only change while the trip is in progress")
    if payload.status == "boarded":
        req = await tc.requirements(conn, p.org_id)
        if req["boarding_code"] and not payload.code:
            return tc.fail(422, "boarding_code_required", "Ask the passenger for their 4-digit boarding code")
        if payload.code:
            bad = await tc.verify_boarding_code(conn, p.org_id, allocation_id, passenger_id, payload.code)
            if bad is not None:
                await audit(conn, org_id=p.org_id, actor=p.user_id, action="trip.boarding_code_failed", entity_type="trip_passenger", entity_id=passenger_id, ip=client_ip(request))
                return bad
            return {"passenger_id": passenger_id, "status": "boarded", "verified": True}
    n = (await conn.execute(text("UPDATE trip_passengers SET status=:s WHERE organization_id=:o AND allocation_id=:a AND id=:i AND status <> 'removed'"),
                            {"s": payload.status, "o": p.org_id, "a": allocation_id, "i": passenger_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "Passenger not found")
    return {"passenger_id": passenger_id, "status": payload.status}


# =============================================================================================================
# Driver leave and replacement
# =============================================================================================================
@router.post("/driver-leave", status_code=201, tags=["driver-leave"])
async def request_leave(payload: LeaveIn, request: Request, p: Principal = Depends(require_roles("driver", *STAFF)), conn: AsyncConnection = Depends(db_for)):
    if p.role == "driver":
        mine = await _my_driver_ids(conn, p)
        if not mine or (payload.driver_id and payload.driver_id not in mine):
            raise AppError(403, "forbidden", "Drivers can only request their own leave")
        driver_id = mine[0]
    else:
        if not payload.driver_id:
            raise AppError(422, "driver_required", "driver_id is required")
        driver_id = payload.driver_id
    overlap = await conn.scalar(text("SELECT 1 FROM driver_leave WHERE organization_id=:o AND driver_id=:d AND status IN ('requested','approved') AND starts_on <= :e AND ends_on >= :s"),
                                {"o": p.org_id, "d": driver_id, "s": payload.starts_on, "e": payload.ends_on})
    if overlap:
        raise AppError(409, "leave_overlap", "An active leave request already overlaps these dates")
    r = (await conn.execute(text("INSERT INTO driver_leave (organization_id, driver_id, starts_on, ends_on, reason) VALUES (:o,:d,:s,:e,:r) RETURNING *"),
                            {"o": p.org_id, "d": driver_id, "s": payload.starts_on, "e": payload.ends_on, "r": payload.reason})).first()
    for staff in await staff_user_ids(conn, p.org_id):
        await notify(conn, org_id=p.org_id, user_id=staff, event_type="leave.requested", title="Driver leave requested",
                     body=f"Leave {payload.starts_on} → {payload.ends_on}", data={"leave_id": str(r.id)}, dedupe_key=f"leave-req:{r.id}")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="leave.requested", entity_type="driver_leave", entity_id=r.id, ip=client_ip(request))
    return _d(r)


@router.get("/driver-leave", tags=["driver-leave"])
async def list_leave(status: str | None = None, p: Principal = Depends(require_roles("driver", *STAFF)), conn: AsyncConnection = Depends(db_for),
                     page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "st": status}
    cond = "l.organization_id=:o AND (CAST(:st AS text) IS NULL OR l.status=:st)"
    total = await conn.scalar(text(f"SELECT count(*) FROM driver_leave l WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT l.*, d.name AS driver_name FROM driver_leave l JOIN drivers d ON d.organization_id=l.organization_id AND d.id=l.driver_id WHERE {cond} ORDER BY l.starts_on DESC LIMIT :l OFFSET :f"),
                               {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)


@router.post("/driver-leave/{leave_id}/decision", tags=["driver-leave"])
async def decide_leave(leave_id: uuid.UUID, payload: LeaveDecision, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    lv = (await conn.execute(text("SELECT * FROM driver_leave WHERE organization_id=:o AND id=:i FOR UPDATE"), {"o": p.org_id, "i": leave_id})).first()
    if lv is None:
        raise AppError(404, "not_found", "Leave request not found")
    if lv.status != "requested":
        raise AppError(409, "already_decided", f"Leave is already {lv.status}")
    new = "approved" if payload.approve else "rejected"
    await conn.execute(text("UPDATE driver_leave SET status=:s, decided_by=:u, decided_at=now() WHERE organization_id=:o AND id=:i"), {"s": new, "u": p.user_id, "o": p.org_id, "i": leave_id})
    outcomes, manual = [], []
    if payload.approve:
        affected = (await conn.execute(text("""SELECT id, status FROM trip_allocations WHERE organization_id=:o AND driver_id=:d AND service_date BETWEEN :s AND :e
                                                AND status IN ('PROPOSED','PENDING_DRIVER_ACCEPTANCE','DRIVER_ACCEPTED','CONFIRMED','IN_PROGRESS') ORDER BY service_date"""),
                                       {"o": p.org_id, "d": lv.driver_id, "s": lv.starts_on, "e": lv.ends_on})).all()
        for a in affected:
            if a.status == "IN_PROGRESS":
                manual.append(str(a.id))
                continue
            if a.status != "REQUIRES_REASSIGNMENT":
                await conn.execute(text("UPDATE driver_assignments SET status='CANCELLED', responded_at=now() WHERE organization_id=:o AND allocation_id=:a AND status IN ('PENDING','ACCEPTED')"), {"o": p.org_id, "a": a.id})
                if a.status != "PROPOSED":
                    await svc.transition(conn, p.org_id, a.id, "REQUIRES_REASSIGNMENT")
                else:
                    await conn.execute(text("UPDATE trip_allocations SET status='REQUIRES_REASSIGNMENT', version=version+1 WHERE organization_id=:o AND id=:a"), {"o": p.org_id, "a": a.id})
            offered = await svc.offer_next_driver(conn, p.org_id, a.id, replacement_for_leave_id=leave_id, actor=p.user_id)
            outcomes.append({"allocation_id": str(a.id), "outcome": "offered" if offered else "no_replacement_available",
                             "driver_id": str(offered["driver_id"]) if offered else None})
    user = await conn.scalar(text("SELECT user_id FROM drivers WHERE organization_id=:o AND id=:d"), {"o": p.org_id, "d": lv.driver_id})
    if user:
        await notify(conn, org_id=p.org_id, user_id=user, event_type=f"leave.{new}", title=f"Leave {new}", body=f"Your leave {lv.starts_on} → {lv.ends_on} was {new}.", data={"leave_id": str(leave_id)}, dedupe_key=f"leave-dec:{leave_id}")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action=f"leave.{new}", entity_type="driver_leave", entity_id=leave_id,
                details={"affected": len(outcomes), "manual_attention": manual}, ip=client_ip(request))
    return {"leave_id": leave_id, "status": new, "affected_allocations": outcomes, "requires_manual_attention": manual}


@router.post("/driver-leave/{leave_id}/cancel", tags=["driver-leave"])
async def cancel_leave(leave_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("driver", *STAFF)), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text("UPDATE driver_leave SET status='cancelled' WHERE organization_id=:o AND id=:i AND status IN ('requested','approved') RETURNING id"), {"o": p.org_id, "i": leave_id})).first()
    if r is None:
        raise AppError(404, "not_found", "Leave request not found or not cancellable")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="leave.cancelled", entity_type="driver_leave", entity_id=leave_id, ip=client_ip(request))
    return {"leave_id": leave_id, "status": "cancelled"}


# =============================================================================================================
# Employee self-service requests
# =============================================================================================================
@router.get("/me/transport-requests", tags=["me"])
async def my_requests(p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    rows = (await conn.execute(text("SELECT * FROM employee_transport_requests WHERE organization_id=:o AND service_date >= current_date - 1 ORDER BY service_date, direction LIMIT 100"), {"o": p.org_id})).all()
    return [_d(r) for r in rows]


@router.post("/me/transport-requests", status_code=201, tags=["me"])
async def create_my_request(payload: MyRequestIn, p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    emp = (await conn.execute(text("SELECT * FROM employees WHERE organization_id=:o AND user_id=:u AND is_active"), {"o": p.org_id, "u": p.user_id})).first()
    if emp is None:
        raise AppError(409, "no_employee_record", "No active employee record is linked to your account")
    shift = payload.shift_id or emp.shift_id
    if shift is None:
        raise AppError(422, "shift_required", "No shift on your profile; choose a shift")
    if payload.service_date < date.today():
        raise AppError(422, "past_date", "Cannot request transport for a past date")
    r = (await conn.execute(text("""INSERT INTO employee_transport_requests (organization_id, employee_id, service_date, shift_id, direction, pickup_lat, pickup_lon, drop_lat, drop_lon, requested_time)
        VALUES (:o,:e,:d,:s,:dir,:pl,:pn,:dl,:dn,:t) ON CONFLICT (organization_id, employee_id, service_date, shift_id, direction) DO NOTHING RETURNING *"""),
        {"o": p.org_id, "e": emp.id, "d": payload.service_date, "s": shift, "dir": payload.direction, "pl": emp.pickup_lat, "pn": emp.pickup_lon, "dl": emp.drop_lat, "dn": emp.drop_lon,
         "t": payload.requested_time or emp.preferred_pickup_time})).first()
    if r is None:
        raise AppError(409, "request_exists", "You already have a request for that date, shift and direction")
    return _d(r)


@router.delete("/me/transport-requests/{request_id}", status_code=204, tags=["me"])
async def cancel_my_request(request_id: uuid.UUID, p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE employee_transport_requests SET status='cancelled' WHERE organization_id=:o AND id=:i AND status='open'"), {"o": p.org_id, "i": request_id})).rowcount
    if not n:
        raise AppError(409, "not_cancellable", "Only open requests can be cancelled here; contact your dispatcher")
