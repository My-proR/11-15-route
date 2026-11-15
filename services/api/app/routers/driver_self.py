"""Driver self-service: on/off duty availability. Auto-dispatch and manual assignment only consider available drivers."""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip
from ..schemas import Strict

router = APIRouter(tags=["driver-self"])


class AvailabilityIn(Strict):
    available: bool


async def _state(conn: AsyncConnection, org: uuid.UUID, user: uuid.UUID) -> dict:
    d = (await conn.execute(text("SELECT id, is_available, availability_updated_at FROM drivers WHERE organization_id=:o AND user_id=:u AND is_active"), {"o": org, "u": user})).first()
    if d is None:
        raise AppError(404, "not_found", "No active driver profile for this account")
    upcoming = await conn.scalar(text("""SELECT count(*) FROM trip_allocations a WHERE a.organization_id=:o AND a.driver_id=:d AND a.service_date >= current_date
        AND a.status IN ('PENDING_DRIVER_ACCEPTANCE','DRIVER_ACCEPTED','CONFIRMED','IN_PROGRESS')"""), {"o": org, "d": d.id})
    return {"available": d.is_available, "updated_at": d.availability_updated_at, "upcoming_trips": upcoming}


@router.get("/me/availability")
async def get_availability(p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    return await _state(conn, p.org_id, p.user_id)


@router.put("/me/availability")
async def set_availability(payload: AvailabilityIn, request: Request, p: Principal = Depends(require_roles("driver")), conn: AsyncConnection = Depends(db_for)):
    cur = await _state(conn, p.org_id, p.user_id)
    if not payload.available:
        in_progress = await conn.scalar(text("""SELECT 1 FROM trip_allocations a JOIN drivers d ON d.organization_id=a.organization_id AND d.id=a.driver_id
            WHERE a.organization_id=:o AND d.user_id=:u AND a.status='IN_PROGRESS' LIMIT 1"""), {"o": p.org_id, "u": p.user_id})
        if in_progress:
            raise AppError(409, "trip_in_progress", "Finish your trip in progress before going off duty")
    if cur["available"] != payload.available:
        await conn.execute(text("SELECT rp.set_my_availability(:o,:a)"), {"o": p.org_id, "a": payload.available})
        await audit(conn, org_id=p.org_id, actor=p.user_id, action="driver.availability_changed", entity_type="driver", entity_id=p.user_id,
                    details={"available": payload.available}, ip=client_ip(request))
    return await _state(conn, p.org_id, p.user_id)
