"""Boarding code helpers. The code never reaches the driver role; verification runs in a SQL definer function (see alembic/sql/trip_credentials.sql)."""
import uuid

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..common import error_body


def fail(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    """Return (not raise) so the request transaction COMMITS and the failed-attempt counter persists."""
    return JSONResponse(status_code=status, content=error_body(status, {"code": code, "message": message, "details": details}))


async def requirements(conn: AsyncConnection, org_id: uuid.UUID) -> dict[str, bool]:
    r = (await conn.execute(text("SELECT boarding_code_required FROM organization_settings WHERE organization_id=:o"), {"o": org_id})).first()
    return {"boarding_code": bool(r and r.boarding_code_required)}


async def verify_boarding_code(conn: AsyncConnection, org_id: uuid.UUID, allocation_id: uuid.UUID, passenger_id: uuid.UUID, code: str) -> JSONResponse | None:
    res = await conn.scalar(text("SELECT rp.verify_boarding_code(:o,:a,:p,:c)"), {"o": org_id, "a": allocation_id, "p": passenger_id, "c": code})
    return {
        "ok": None,
        "already_boarded": None,
        "not_driver": fail(404, "not_found", "Trip not found"),
        "not_found": fail(404, "not_found", "Passenger not found"),
        "not_in_progress": fail(409, "trip_not_in_progress", "Passenger status can only change while the trip is in progress"),
        "locked": fail(429, "boarding_code_locked", "Too many wrong codes for this passenger. Wait a few minutes or ask your dispatcher to board them manually."),
        "invalid": fail(403, "boarding_code_invalid", "Incorrect boarding code"),
    }[res]
