"""SOS incidents. An alert is ALWAYS accepted; coordinates are stored only when the effective SOS GPS switch is on.

effective_sos_gps = GPS_SOS_FEATURE_ENABLED (env) AND platform flag AND organization_settings.sos_gps_enabled.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip, enforce_rate_limit, notify, page_params, page_response, staff_user_ids
from ..config import get_settings
from ..flags import feature_states
from ..schemas import SosCreate, SosLocation, SosResolve
from ..services.realtime import hub

router = APIRouter(prefix="/sos", tags=["sos"])
STAFF = ("org_admin", "dispatcher")
POOR_ACCURACY_M = 100.0


def _d(r) -> dict:
    return dict(r._mapping)


async def _evaluate_location(conn: AsyncConnection, p: Principal, incident_id: uuid.UUID, loc: SosLocation | None) -> tuple[str, dict]:
    """Returns (location_status, info). Persists coordinates only when every switch allows it and the fix is valid and fresh."""
    s = get_settings()
    if loc is None:
        return "not_requested", {}
    states = await feature_states(conn, p.org_id)
    if not states["sos_gps"].effective:
        return "disabled", {"stored": False, "blocked_by": states["sos_gps"].blocked_by}   # coordinates are discarded, never persisted
    if loc.status in ("denied", "unavailable"):
        return loc.status, {"stored": False}
    now = datetime.now(timezone.utc)
    captured = loc.captured_at if loc.captured_at.tzinfo else loc.captured_at.replace(tzinfo=timezone.utc)
    if captured > now + timedelta(seconds=60):
        raise AppError(422, "invalid_timestamp", "captured_at is in the future")
    if now - captured > timedelta(seconds=s.sos_location_max_age_seconds):
        return "unavailable", {"stored": False, "reason": "stale_fix"}
    retention = await conn.scalar(text("SELECT sos_retention_days FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})
    try:
        await conn.execute(text("SELECT rp.upsert_sos_location(:o,:i,:la,:lo,:ac,:ca,:d)"),
                           {"o": p.org_id, "i": incident_id, "la": loc.latitude, "lo": loc.longitude, "ac": loc.accuracy_m, "ca": captured, "d": retention})
    except DBAPIError:
        raise
    return "obtained", {"stored": True, "accuracy_m": loc.accuracy_m, "accuracy_poor": bool(loc.accuracy_m is not None and loc.accuracy_m > POOR_ACCURACY_M)}


async def _announce(conn: AsyncConnection, p: Principal, incident: dict, status: str) -> None:
    for staff in await staff_user_ids(conn, p.org_id):
        await notify(conn, org_id=p.org_id, user_id=staff, event_type="sos.raised", critical=True, title="SOS alert",
                     body=f"SOS raised by {p.full_name or p.email} ({p.role}).", data={"incident_id": str(incident["id"]), "location_status": status},
                     dedupe_key=f"sos:{incident['id']}:{staff}")
    await hub.publish(p.org_id, "sos.created", {"incident_id": str(incident["id"]), "raised_by": str(p.user_id), "role": p.role, "location_status": status, "created_at": incident["created_at"]})


@router.post("", status_code=201)
async def raise_sos(payload: SosCreate, request: Request, idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
                    p: Principal = Depends(require_roles("employee", "driver")), conn: AsyncConnection = Depends(db_for)):
    """Creates the incident immediately. An optional inline `location` is evaluated against the switches; absent/denied/disabled
    location never blocks the alert. A retry with the same Idempotency-Key returns the original incident (no duplicate alert)."""
    await enforce_rate_limit(request, scope="sos", limit=get_settings().rate_limit_sensitive_per_minute)
    existing = (await conn.execute(text("SELECT * FROM sos_incidents WHERE organization_id=:o AND raised_by_user_id=:u AND idempotency_key=:k"),
                                   {"o": p.org_id, "u": p.user_id, "k": idempotency_key})).first()
    if existing:
        return {**_public(_d(existing)), "duplicate": True}
    if payload.allocation_id:
        ok = await conn.scalar(text("SELECT 1 FROM trip_allocations WHERE organization_id=:o AND id=:a"), {"o": p.org_id, "a": payload.allocation_id})   # RLS: only trips the user is on
        if not ok:
            raise AppError(404, "not_found", "Trip not found")
    row = (await conn.execute(text("""INSERT INTO sos_incidents (organization_id, raised_by_user_id, raised_by_role, allocation_id, idempotency_key, message)
        VALUES (:o,:u,:r,:a,:k,:m) ON CONFLICT (organization_id, raised_by_user_id, idempotency_key) DO NOTHING RETURNING *"""),
                               {"o": p.org_id, "u": p.user_id, "r": p.role, "a": payload.allocation_id, "k": idempotency_key, "m": payload.message})).first()
    if row is None:    # lost a race with a concurrent identical request
        row = (await conn.execute(text("SELECT * FROM sos_incidents WHERE organization_id=:o AND raised_by_user_id=:u AND idempotency_key=:k"),
                                  {"o": p.org_id, "u": p.user_id, "k": idempotency_key})).first()
        return {**_public(_d(row)), "duplicate": True}
    inc = _d(row)
    status, info = await _evaluate_location(conn, p, inc["id"], payload.location)
    await conn.execute(text("UPDATE sos_incidents SET location_status=:s WHERE organization_id=:o AND id=:i"), {"s": status, "o": p.org_id, "i": inc["id"]})
    inc["location_status"] = status
    await _announce(conn, p, inc, status)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.raised", entity_type="sos_incident", entity_id=inc["id"], details={"location_status": status}, ip=client_ip(request))
    return {**_public(inc), "location": info, "duplicate": False}


@router.put("/{incident_id}/location")
async def attach_location(incident_id: uuid.UUID, loc: SosLocation, request: Request, p: Principal = Depends(require_roles("employee", "driver")),
                          conn: AsyncConnection = Depends(db_for)):
    """Second step: the client alerts first, then sends the fix (or denied/unavailable) when the device returns it."""
    inc = (await conn.execute(text("SELECT * FROM sos_incidents WHERE organization_id=:o AND id=:i AND raised_by_user_id=:u FOR UPDATE"), {"o": p.org_id, "i": incident_id, "u": p.user_id})).first()
    if inc is None:
        raise AppError(404, "not_found", "Incident not found")
    if inc.status == "resolved":
        raise AppError(409, "incident_resolved", "Location sharing stopped: the incident is resolved")
    status, info = await _evaluate_location(conn, p, incident_id, loc)
    await conn.execute(text("UPDATE sos_incidents SET location_status=:s WHERE organization_id=:o AND id=:i"), {"s": status, "o": p.org_id, "i": incident_id})
    await hub.publish(p.org_id, "sos.updated", {"incident_id": str(incident_id), "location_status": status})
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.location_submitted", entity_type="sos_incident", entity_id=incident_id, details={"location_status": status}, ip=client_ip(request))
    return {"incident_id": incident_id, "location_status": status, "location": info}


def _public(inc: dict) -> dict:
    keys = ("id", "status", "location_status", "message", "raised_by_role", "allocation_id", "created_at", "acknowledged_at", "resolved_at")
    return {k: inc.get(k) for k in keys}


@router.get("")
async def list_incidents(status: str | None = None, p: Principal = Depends(require_roles(*STAFF, "employee", "driver")), conn: AsyncConnection = Depends(db_for),
                         page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    """Lists incidents WITHOUT coordinates. Non-staff see only incidents they raised / (drivers) on their own trips (RLS)."""
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "st": status}
    cond = "i.organization_id=:o AND (CAST(:st AS text) IS NULL OR i.status=:st)"
    total = await conn.scalar(text(f"SELECT count(*) FROM sos_incidents i WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT i.*, up.full_name AS raised_by_name FROM sos_incidents i LEFT JOIN user_profiles up ON up.user_id=i.raised_by_user_id WHERE {cond} ORDER BY i.created_at DESC LIMIT :l OFFSET :f"),
                               {**params, "l": size, "f": off})).all()
    items = []
    for r in rows:
        d = {**_public(_d(r))}
        if p.role in STAFF:
            d["raised_by_name"] = r.raised_by_name
        items.append(d)
    return page_response(items, total, page, size)


@router.get("/{incident_id}")
async def get_incident(incident_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """Staff-only detail including coordinates. Every read of coordinates is audit-logged."""
    inc = (await conn.execute(text("SELECT * FROM sos_incidents WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": incident_id})).first()
    if inc is None:
        raise AppError(404, "not_found", "Incident not found")
    loc = (await conn.execute(text("SELECT latitude, longitude, accuracy_m, captured_at, purge_after FROM sos_incident_locations WHERE organization_id=:o AND incident_id=:i"),
                              {"o": p.org_id, "i": incident_id})).first()
    out = {**_public(_d(inc)), "location": None}
    if loc:
        out["location"] = {**_d(loc), "accuracy_poor": bool(loc.accuracy_m is not None and loc.accuracy_m > POOR_ACCURACY_M)}
        await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.location_viewed", entity_type="sos_incident", entity_id=incident_id, ip=client_ip(request))
    return out


@router.post("/{incident_id}/acknowledge")
async def acknowledge(incident_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF, "driver")), conn: AsyncConnection = Depends(db_for)):
    row = (await conn.execute(text("UPDATE sos_incidents SET status='acknowledged', acknowledged_by=:u, acknowledged_at=now() WHERE organization_id=:o AND id=:i AND status='open' RETURNING *"),
                              {"o": p.org_id, "i": incident_id, "u": p.user_id})).first()
    if row is None:
        cur = await conn.scalar(text("SELECT status FROM sos_incidents WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": incident_id})
        if cur is None:
            raise AppError(404, "not_found", "Incident not found")
        raise AppError(409, "invalid_transition", f"Incident is already {cur}")
    await notify(conn, org_id=p.org_id, user_id=row.raised_by_user_id, event_type="sos.acknowledged", critical=True, title="Help is on the way",
                 body="Your SOS was acknowledged.", data={"incident_id": str(incident_id)}, dedupe_key=f"sos-ack:{incident_id}")
    await hub.publish(p.org_id, "sos.updated", {"incident_id": str(incident_id), "status": "acknowledged"}, user_ids=[row.raised_by_user_id])
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.acknowledged", entity_type="sos_incident", entity_id=incident_id, ip=client_ip(request))
    return _public(_d(row))


@router.post("/{incident_id}/resolve")
async def resolve(incident_id: uuid.UUID, payload: SosResolve, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    row = (await conn.execute(text("UPDATE sos_incidents SET status='resolved', resolved_by=:u, resolved_at=now(), resolution_note=:n WHERE organization_id=:o AND id=:i AND status IN ('open','acknowledged') RETURNING *"),
                              {"o": p.org_id, "i": incident_id, "u": p.user_id, "n": payload.note})).first()
    if row is None:
        raise AppError(409, "invalid_transition", "Incident not found or already resolved")
    await hub.publish(p.org_id, "sos.updated", {"incident_id": str(incident_id), "status": "resolved"}, user_ids=[row.raised_by_user_id])
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.resolved", entity_type="sos_incident", entity_id=incident_id, ip=client_ip(request))
    return _public(_d(row))


@router.delete("/{incident_id}/location", status_code=204)
async def delete_location(incident_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    """Safe deletion: removes coordinates early (retention purge also does this automatically). The incident record remains."""
    n = (await conn.execute(text("DELETE FROM sos_incident_locations WHERE organization_id=:o AND incident_id=:i"), {"o": p.org_id, "i": incident_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "No stored location for this incident")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="sos.location_deleted", entity_type="sos_incident", entity_id=incident_id, ip=client_ip(request))
