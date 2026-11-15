"""Operational reports from real records. JSON for staff; CSV export for org admins only (audited).

Actual distance is NOT recorded by this release (no odometer/telematics source), so reports expose planned distance only and say so.
"""
import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip

router = APIRouter(prefix="/reports", tags=["reports"])
STAFF = ("org_admin", "dispatcher")

REPORTS: dict[str, str] = {
    "vehicle-utilization": """
        SELECT a.service_date, v.vehicle_code, v.registration_number, v.capacity, count(DISTINCT a.id) AS trips,
               coalesce(sum(pc.c),0) AS passengers, round(100.0 * coalesce(sum(pc.c),0) / nullif(sum(v.capacity),0), 1) AS utilization_pct
        FROM trip_allocations a JOIN vehicles v ON v.organization_id=a.organization_id AND v.id=a.vehicle_id
        LEFT JOIN LATERAL (SELECT count(*) AS c FROM trip_passengers p WHERE p.organization_id=a.organization_id AND p.allocation_id=a.id AND p.status <> 'removed') pc ON true
        WHERE a.organization_id=:o AND a.service_date BETWEEN :s AND :e AND a.status <> 'CANCELLED'
        GROUP BY a.service_date, v.vehicle_code, v.registration_number, v.capacity ORDER BY a.service_date, v.vehicle_code""",
    "passenger-utilization": """
        SELECT r.service_date, r.direction, count(*) AS requests, count(*) FILTER (WHERE r.status='allocated') AS allocated,
               count(*) FILTER (WHERE r.status='open') AS unassigned, count(*) FILTER (WHERE r.status='cancelled') AS cancelled
        FROM employee_transport_requests r WHERE r.organization_id=:o AND r.service_date BETWEEN :s AND :e GROUP BY r.service_date, r.direction ORDER BY r.service_date, r.direction""",
    "unassigned-employees": """
        SELECT r.service_date, r.direction, e.employee_code, e.name, e.department, sh.name AS shift
        FROM employee_transport_requests r JOIN employees e ON e.organization_id=r.organization_id AND e.id=r.employee_id
        JOIN shifts sh ON sh.organization_id=r.organization_id AND sh.id=r.shift_id
        WHERE r.organization_id=:o AND r.service_date BETWEEN :s AND :e AND r.status='open' ORDER BY r.service_date, e.employee_code""",
    "driver-workloads": """
        SELECT d.driver_code, d.name, count(a.id) AS trips, count(a.id) FILTER (WHERE a.status='COMPLETED') AS completed_trips,
               round(coalesce(sum(a.planned_distance_km),0)::numeric, 1) AS planned_km, round(coalesce(sum(a.planned_duration_minutes),0)::numeric, 0) AS planned_minutes,
               count(a.id) FILTER (WHERE a.is_local_trip) AS local_trips,
               count(a.id) FILTER (WHERE a.estimate_status <> 'final') AS trips_missing_distance, NULL::numeric AS actual_km
        FROM drivers d LEFT JOIN trip_allocations a ON a.organization_id=d.organization_id AND a.driver_id=d.id AND a.service_date BETWEEN :s AND :e AND a.status NOT IN ('CANCELLED','REQUIRES_REASSIGNMENT')
        WHERE d.organization_id=:o AND d.is_active GROUP BY d.driver_code, d.name ORDER BY planned_km DESC, d.name""",
    "leave-replacements": """
        SELECT l.starts_on, l.ends_on, d.name AS driver, l.status, l.reason,
               (SELECT count(*) FROM driver_assignments asg WHERE asg.organization_id=l.organization_id AND asg.replacement_for_leave_id=l.id) AS replacement_offers,
               (SELECT count(*) FROM driver_assignments asg WHERE asg.organization_id=l.organization_id AND asg.replacement_for_leave_id=l.id AND asg.status='ACCEPTED') AS replacements_accepted
        FROM driver_leave l JOIN drivers d ON d.organization_id=l.organization_id AND d.id=l.driver_id
        WHERE l.organization_id=:o AND l.starts_on <= :e AND l.ends_on >= :s ORDER BY l.starts_on""",
    "cancelled-trips": """
        SELECT a.service_date, a.direction, v.vehicle_code, a.cancelled_reason, a.updated_at AS cancelled_at
        FROM trip_allocations a JOIN vehicles v ON v.organization_id=a.organization_id AND v.id=a.vehicle_id
        WHERE a.organization_id=:o AND a.status='CANCELLED' AND a.service_date BETWEEN :s AND :e ORDER BY a.service_date""",
    "sos-incidents": """
        SELECT i.created_at, i.raised_by_role, i.status, i.location_status,
               round(extract(epoch FROM (i.acknowledged_at - i.created_at))::numeric, 0) AS seconds_to_acknowledge,
               round(extract(epoch FROM (i.resolved_at - i.created_at))::numeric, 0) AS seconds_to_resolve
        FROM sos_incidents i WHERE i.organization_id=:o AND i.created_at::date BETWEEN :s AND :e ORDER BY i.created_at DESC""",
    "notification-delivery": """
        SELECT d.channel, d.status, count(*) AS deliveries, count(*) FILTER (WHERE n.critical) AS critical, round(avg(d.attempts)::numeric, 2) AS avg_attempts
        FROM notification_deliveries d JOIN notifications n ON n.organization_id=d.organization_id AND n.id=d.notification_id
        WHERE d.organization_id=:o AND d.created_at::date BETWEEN :s AND :e GROUP BY d.channel, d.status ORDER BY d.channel, d.status""",
    "operational-exceptions": """
        SELECT a.service_date, a.direction, v.vehicle_code, a.status,
               CASE WHEN a.status='REQUIRES_REASSIGNMENT' THEN 'requires reassignment' WHEN a.estimate_status <> 'final' THEN 'route estimate not final' WHEN a.driver_id IS NULL THEN 'no driver' END AS issue
        FROM trip_allocations a JOIN vehicles v ON v.organization_id=a.organization_id AND v.id=a.vehicle_id
        WHERE a.organization_id=:o AND a.service_date BETWEEN :s AND :e AND a.status NOT IN ('CANCELLED','COMPLETED')
          AND (a.status='REQUIRES_REASSIGNMENT' OR a.estimate_status <> 'final' OR a.driver_id IS NULL) ORDER BY a.service_date""",
}
NOTES = {"driver-workloads": "Planned distance only. Actual kilometres are not recorded in this release (no odometer/telematics source), so actual_km is null.",
         "sos-incidents": "Coordinates are never included in reports."}


def _csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s     # neutralise spreadsheet formula injection


@router.get("/{name}")
async def run_report(name: str, request: Request, start: date | None = None, end: date | None = None, format: str = Query("json", pattern="^(json|csv)$"),
                     p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    sql = REPORTS.get(name)
    if sql is None:
        raise AppError(404, "not_found", f"Unknown report. Available: {sorted(REPORTS)}")
    end = end or date.today()
    start = start or end - timedelta(days=6)
    if (end - start).days > 366 or end < start:
        raise AppError(422, "bad_range", "Range must be 1..366 days")
    rows = (await conn.execute(text(sql + " LIMIT 20000"), {"o": p.org_id, "s": start, "e": end})).all()
    data = [dict(r._mapping) for r in rows]
    if format == "csv":
        if p.role != "org_admin":
            raise AppError(403, "export_forbidden", "Only organization administrators may export reports")
        buf = io.StringIO()
        if data:
            w = csv.DictWriter(buf, fieldnames=list(data[0]))
            w.writeheader()
            for r in data:
                w.writerow({k: _csv_safe(v) for k, v in r.items()})
        await audit(conn, org_id=p.org_id, actor=p.user_id, action="report.exported", entity_type="report", entity_id=name, details={"rows": len(data), "start": start, "end": end}, ip=client_ip(request))
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}-{start}-{end}.csv"'})
    return {"report": name, "start": start, "end": end, "rows": data, "row_count": len(data), "note": NOTES.get(name)}


@router.get("")
async def overview(p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    """Dashboard numbers straight from the database (today in the organization's timezone)."""
    tz = await conn.scalar(text("SELECT timezone FROM organizations WHERE id=:o"), {"o": p.org_id})
    q = lambda sql: conn.scalar(text(sql), {"o": p.org_id, "tz": tz})   # noqa: E731
    today = "(now() AT TIME ZONE :tz)::date"
    return {
        "today": await q(f"SELECT {today}"),
        "employees_active": await q("SELECT count(*) FROM employees WHERE organization_id=:o AND is_active"),
        "drivers_active": await q("SELECT count(*) FROM drivers WHERE organization_id=:o AND is_active"),
        "vehicles_active": await q("SELECT count(*) FROM vehicles WHERE organization_id=:o AND is_active"),
        "vehicles_unavailable": await q("SELECT count(*) FROM vehicles WHERE organization_id=:o AND is_active AND availability <> 'available'"),
        "trips_today": await q(f"SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND service_date={today} AND status <> 'CANCELLED'"),
        "trips_in_progress": await q("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='IN_PROGRESS'"),
        "trips_requiring_reassignment": await q("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='REQUIRES_REASSIGNMENT'"),
        "pending_driver_acceptance": await q("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='PENDING_DRIVER_ACCEPTANCE'"),
        "unassigned_requests_today": await q(f"SELECT count(*) FROM employee_transport_requests WHERE organization_id=:o AND service_date={today} AND status='open'"),
        "open_sos": await q("SELECT count(*) FROM sos_incidents WHERE organization_id=:o AND status <> 'resolved'"),
        "drivers_on_leave_today": await q(f"SELECT count(*) FROM driver_leave WHERE organization_id=:o AND status='approved' AND {today} BETWEEN starts_on AND ends_on"),
        "failed_notifications_24h": await q("SELECT count(*) FROM notification_deliveries WHERE organization_id=:o AND status='failed' AND created_at > now() - interval '24 hours'"),
    }
