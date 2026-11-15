"""Boarding: passenger code (employee shows, driver enters), trip QR (driver shows, passenger scans) and dispatcher manual boarding.
The code is enforced only when organization_settings.boarding_code_required is on; the QR is always an allowed way to board."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip, limiter
from ..schemas import Strict
from ..services import trip_credentials as tc
from ..services import trip_qr

router = APIRouter(tags=["trip-credentials"])
STAFF = ("org_admin", "dispatcher")


class ManualBoard(Strict):
    reason: str = Field(min_length=3, max_length=300)


async def _alloc_status(conn: AsyncConnection, org: uuid.UUID, alloc: uuid.UUID) -> str:
    st = await conn.scalar(text("SELECT status FROM trip_allocations WHERE organization_id=:o AND id=:a"), {"o": org, "a": alloc})
    if st is None:
        raise AppError(404, "not_found", "Allocation not found")
    return st


@router.get("/me/boarding-code/{allocation_id}")
async def my_boarding_code(allocation_id: uuid.UUID, p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    """The employee's own 4-digit code for this trip. Show it to the driver when boarding."""
    st = await _alloc_status(conn, p.org_id, allocation_id)     # RLS: employees only see allocations they are on
    if st not in ("CONFIRMED", "IN_PROGRESS"):
        raise AppError(409, "trip_not_ready", "The boarding code is available once the trip is confirmed")
    await conn.execute(text("SELECT rp.ensure_boarding_codes(:o,:a)"), {"o": p.org_id, "a": allocation_id})
    row = (await conn.execute(text("""SELECT c.code, p.status FROM trip_boarding_codes c JOIN trip_passengers p ON p.organization_id=c.organization_id AND p.id=c.passenger_id
                                      WHERE c.organization_id=:o AND c.allocation_id=:a AND p.status <> 'removed' AND p.employee_id IN (SELECT rp.my_employee_ids(:o))"""),
                              {"o": p.org_id, "a": allocation_id})).first()
    if row is None:
        raise AppError(404, "not_found", "You are not a passenger on this trip")
    req = await tc.requirements(conn, p.org_id)
    return {"allocation_id": allocation_id, "code": row.code, "boarded": row.status == "boarded", "required": req["boarding_code"]}


@router.post("/allocations/{allocation_id}/passengers/{passenger_id}/manual-board")
async def manual_board(allocation_id: uuid.UUID, passenger_id: uuid.UUID, payload: ManualBoard, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """Dispatcher override when the employee cannot show a code (dead phone etc.). Always audited with a reason."""
    if await _alloc_status(conn, p.org_id, allocation_id) != "IN_PROGRESS":
        raise AppError(409, "trip_not_in_progress", "Passengers can only be boarded while the trip is in progress")
    n = (await conn.execute(text("UPDATE trip_passengers SET status='boarded' WHERE organization_id=:o AND allocation_id=:a AND id=:i AND status IN ('assigned','boarded')"),
                            {"o": p.org_id, "a": allocation_id, "i": passenger_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "Passenger not found or no longer on this trip")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="trip.manual_board", entity_type="trip_passenger", entity_id=passenger_id,
                details={"allocation_id": str(allocation_id), "reason": payload.reason}, ip=client_ip(request))
    return {"passenger_id": passenger_id, "status": "boarded", "manual": True}


@router.get("/allocations/{allocation_id}/trip-qr")
async def trip_qr_token(allocation_id: uuid.UUID, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    """Driver: QR payload for passengers to scan. Valid for 60 s; the app should refresh it about every 45 s."""
    ok = await conn.scalar(text("SELECT rp.is_driver_of(:o,:a)"), {"o": p.org_id, "a": allocation_id})
    if not ok:
        raise AppError(404, "not_found", "Trip not found")
    if await _alloc_status(conn, p.org_id, allocation_id) != "IN_PROGRESS":
        raise AppError(409, "trip_not_in_progress", "The trip QR is available once the trip has started")
    token, exp = trip_qr.make_token(p.org_id, allocation_id)
    return {"token": token, "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat(), "ttl_seconds": trip_qr.TTL_SECONDS}


class QrBoard(Strict):
    token: str = Field(min_length=20, max_length=600)


@router.post("/me/trips/{allocation_id}/board-by-qr")
async def board_by_qr(allocation_id: uuid.UUID, payload: QrBoard, request: Request, p: Principal = Depends(require_roles("employee")), conn: AsyncConnection = Depends(db_for)):
    """Passenger: scan the driver's trip QR to mark yourself boarded."""
    if not await limiter.hit(f"qr:{p.user_id}", 10, window=60):
        raise AppError(429, "too_many_attempts", "Too many scan attempts. Wait a minute.")
    parsed = trip_qr.read_token(payload.token)
    if isinstance(parsed, str):
        raise AppError(422, f"qr_{parsed}", {"malformed": "That is not a RoutePilot trip QR", "bad_signature": "That QR code is not valid", "expired": "That QR code expired. Ask the driver to show it again."}[parsed])
    org, alloc = parsed
    if org != p.org_id or alloc != allocation_id:
        raise AppError(403, "qr_wrong_trip", "This QR code is for a different trip")
    res = await conn.scalar(text("SELECT rp.board_self(:o,:a)"), {"o": p.org_id, "a": allocation_id})
    if res == "not_passenger":
        raise AppError(403, "not_passenger", "You are not a passenger on this trip")
    if res == "not_in_progress":
        raise AppError(409, "trip_not_in_progress", "This trip is not in progress")
    if res == "ok":
        await audit(conn, org_id=p.org_id, actor=p.user_id, action="trip.boarded_by_qr", entity_type="allocation", entity_id=allocation_id, ip=client_ip(request))
    return {"allocation_id": allocation_id, "status": "boarded", "already": res == "already_boarded"}
