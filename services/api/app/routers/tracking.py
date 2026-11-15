"""Live vehicle tracking. OFF by default (deployment env + platform flag + organization setting must all be on).

When off: the API refuses positions (403), stores nothing, and GETs report `enabled: false`. When on:
  * only the assigned driver of an IN_PROGRESS allocation may report, at most once per live_tracking_interval_seconds;
  * the latest position per vehicle is upserted (one row per vehicle); history is kept only if the organization enabled it;
  * staff see all vehicles; passengers see only their own cab and only if the organization enabled employee view;
  * sharing stops when the trip completes (position row is deleted).
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip, limiter
from ..config import get_settings
from ..flags import feature_states
from ..schemas import PositionIn
from ..services.realtime import hub

router = APIRouter(prefix="/tracking", tags=["tracking"])
STAFF = ("org_admin", "dispatcher")
MAX_STALE = timedelta(minutes=2)


def _d(r) -> dict:
    return dict(r._mapping)


@router.get("/status")
async def status(p: Principal = Depends(require_roles("org_admin", "dispatcher", "driver", "employee")), conn: AsyncConnection = Depends(db_for)):
    """What the client needs to decide whether to ask for GPS at all (drivers) or show a map (staff/employees)."""
    states = await feature_states(conn, p.org_id)
    st = (await conn.execute(text("SELECT live_tracking_interval_seconds, live_tracking_history_enabled, live_tracking_employee_view FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})).first()
    t = states["live_tracking"]
    return {"enabled": t.effective, "blocked_by": t.blocked_by, "interval_seconds": st.live_tracking_interval_seconds if t.effective else None,
            "history_enabled": bool(t.effective and st.live_tracking_history_enabled), "employee_view": bool(t.effective and st.live_tracking_employee_view)}


@router.post("/position", status_code=202)
async def report_position(payload: PositionIn, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    s = get_settings()
    states = await feature_states(conn, p.org_id)
    if not states["live_tracking"].effective:
        raise AppError(403, "live_tracking_disabled", "Live tracking is disabled; position was not stored", {"blocked_by": states["live_tracking"].blocked_by})
    now = datetime.now(timezone.utc)
    cap = payload.captured_at if payload.captured_at.tzinfo else payload.captured_at.replace(tzinfo=timezone.utc)
    if cap > now + timedelta(seconds=60):
        raise AppError(422, "invalid_timestamp", "captured_at is in the future")
    if now - cap > MAX_STALE:
        raise AppError(422, "stale_position", "Position is too old to be useful")
    cfg = (await conn.execute(text("SELECT live_tracking_interval_seconds, live_tracking_history_enabled, live_tracking_history_retention_days FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})).first()
    a = (await conn.execute(text("""SELECT a.id, a.vehicle_id, a.status FROM trip_allocations a JOIN drivers d ON d.organization_id=a.organization_id AND d.id=a.driver_id
                                    WHERE a.organization_id=:o AND a.id=:a AND d.user_id=:u"""), {"o": p.org_id, "a": payload.allocation_id, "u": p.user_id})).first()
    if a is None:
        raise AppError(404, "not_found", "Trip not found")
    if a.status != "IN_PROGRESS":
        raise AppError(409, "trip_not_in_progress", "Positions are accepted only while the trip is in progress")
    min_gap = max(cfg.live_tracking_interval_seconds, s.live_tracking_min_interval_seconds) - 2
    if not await limiter.hit(f"track:{a.vehicle_id}", 1, window=max(min_gap, 1)):
        raise AppError(429, "too_frequent", f"Report at most once every {cfg.live_tracking_interval_seconds}s", {"interval_seconds": cfg.live_tracking_interval_seconds})
    await conn.execute(text("""INSERT INTO vehicle_last_position (organization_id, vehicle_id, allocation_id, latitude, longitude, accuracy_m, speed_kmh, heading, captured_at)
        VALUES (:o,:v,:a,:la,:lo,:ac,:sp,:h,:c)
        ON CONFLICT (organization_id, vehicle_id) DO UPDATE SET allocation_id=EXCLUDED.allocation_id, latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
          accuracy_m=EXCLUDED.accuracy_m, speed_kmh=EXCLUDED.speed_kmh, heading=EXCLUDED.heading, captured_at=EXCLUDED.captured_at, received_at=now()"""),
                       {"o": p.org_id, "v": a.vehicle_id, "a": a.id, "la": payload.latitude, "lo": payload.longitude, "ac": payload.accuracy_m, "sp": payload.speed_kmh, "h": payload.heading, "c": cap})
    if cfg.live_tracking_history_enabled:
        await conn.execute(text("""INSERT INTO vehicle_location_history (organization_id, vehicle_id, allocation_id, latitude, longitude, accuracy_m, captured_at, purge_after)
            VALUES (:o,:v,:a,:la,:lo,:ac,:c, now() + make_interval(days => :d))"""),
                           {"o": p.org_id, "v": a.vehicle_id, "a": a.id, "la": payload.latitude, "lo": payload.longitude, "ac": payload.accuracy_m, "c": cap, "d": cfg.live_tracking_history_retention_days})
    event = {"vehicle_id": str(a.vehicle_id), "allocation_id": str(a.id), "latitude": payload.latitude, "longitude": payload.longitude,
             "accuracy_m": payload.accuracy_m, "speed_kmh": payload.speed_kmh, "heading": payload.heading, "captured_at": cap.isoformat()}
    users: list[uuid.UUID] = []
    if (await conn.scalar(text("SELECT live_tracking_employee_view FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})):
        users = [r.user_id for r in await conn.execute(text("SELECT e.user_id FROM trip_passengers tp JOIN employees e ON e.organization_id=tp.organization_id AND e.id=tp.employee_id "
                                                           "WHERE tp.organization_id=:o AND tp.allocation_id=:a AND tp.status <> 'removed' AND e.user_id IS NOT NULL"), {"o": p.org_id, "a": a.id})]
    await hub.publish(p.org_id, "tracking.position", event, user_ids=users)
    return {"accepted": True, "next_report_in_seconds": cfg.live_tracking_interval_seconds}


@router.get("/vehicles")
async def live_vehicles(p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """Current position of every vehicle on an in-progress trip (staff only)."""
    states = await feature_states(conn, p.org_id)
    if not states["live_tracking"].effective:
        return {"enabled": False, "blocked_by": states["live_tracking"].blocked_by, "vehicles": []}
    rows = (await conn.execute(text("""SELECT lp.vehicle_id, v.vehicle_code, v.registration_number, lp.allocation_id, lp.latitude, lp.longitude, lp.accuracy_m, lp.speed_kmh, lp.heading, lp.captured_at, lp.received_at,
                                              dr.name AS driver_name
        FROM vehicle_last_position lp JOIN vehicles v ON v.organization_id=lp.organization_id AND v.id=lp.vehicle_id
        JOIN trip_allocations a ON a.organization_id=lp.organization_id AND a.id=lp.allocation_id AND a.status='IN_PROGRESS'
        LEFT JOIN drivers dr ON dr.organization_id=a.organization_id AND dr.id=a.driver_id
        WHERE lp.organization_id=:o ORDER BY v.vehicle_code"""), {"o": p.org_id})).all()
    now = datetime.now(timezone.utc)
    return {"enabled": True, "vehicles": [{**_d(r), "stale": (now - r.captured_at) > MAX_STALE} for r in rows]}


@router.get("/vehicles/{vehicle_id}")
async def one_vehicle(vehicle_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    states = await feature_states(conn, p.org_id)
    if not states["live_tracking"].effective:
        raise AppError(403, "live_tracking_disabled", "Live tracking is disabled")
    r = (await conn.execute(text("SELECT * FROM vehicle_last_position WHERE organization_id=:o AND vehicle_id=:v"), {"o": p.org_id, "v": vehicle_id})).first()
    if r is None:
        raise AppError(404, "not_found", "No live position for this vehicle (not on an in-progress trip)")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="tracking.vehicle_viewed", entity_type="vehicle", entity_id=vehicle_id, ip=client_ip(request))
    return _d(r)


@router.get("/vehicles/{vehicle_id}/history")
async def history(vehicle_id: uuid.UUID, request: Request, hours: int = Query(6, ge=1, le=72), p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    states = await feature_states(conn, p.org_id)
    hist = await conn.scalar(text("SELECT live_tracking_history_enabled FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})
    if not states["live_tracking"].effective or not hist:
        raise AppError(403, "tracking_history_disabled", "Location history is disabled for this organization")
    rows = (await conn.execute(text("SELECT latitude, longitude, accuracy_m, captured_at FROM vehicle_location_history WHERE organization_id=:o AND vehicle_id=:v AND captured_at > now() - make_interval(hours => :h) ORDER BY captured_at LIMIT 5000"),
                               {"o": p.org_id, "v": vehicle_id, "h": hours})).all()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="tracking.history_viewed", entity_type="vehicle", entity_id=vehicle_id, details={"hours": hours}, ip=client_ip(request))
    return {"vehicle_id": vehicle_id, "points": [_d(r) for r in rows]}


@router.get("/my-trip")
async def my_trip(p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    """Passenger view of their own cab. Only when live tracking is effective AND the organization allowed employee view."""
    states = await feature_states(conn, p.org_id)
    allowed = await conn.scalar(text("SELECT live_tracking_employee_view FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})
    if not (states["live_tracking"].effective and allowed):
        return {"enabled": False, "position": None}
    r = (await conn.execute(text("""SELECT lp.latitude, lp.longitude, lp.heading, lp.captured_at FROM vehicle_last_position lp
        JOIN trip_allocations a ON a.organization_id=lp.organization_id AND a.id=lp.allocation_id AND a.status='IN_PROGRESS' WHERE lp.organization_id=:o"""), {"o": p.org_id})).first()   # RLS limits to own cab
    return {"enabled": True, "position": _d(r) if r else None}
