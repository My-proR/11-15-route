"""Auto-dispatch at an organisation's cutoff time.

Once per organisation per service date, after the local cutoff time, the worker plans every active shift (pickup and drop) for
the target date and, when the org allows it, offers each trip to its driver. Humans stay in charge of exceptions: slots that
already contain allocations are never touched, plans without final road estimates stay PROPOSED, and staff get one summary
notification listing what needs attention. Each (org, date) is claimed exactly once via auto_dispatch_runs.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..common import AppError
from ..common import notify, staff_user_ids
from ..db import system_session
from . import dispatch as svc

log = logging.getLogger("routepilot.autodispatch")
DIRECTIONS = ("pickup", "drop")


def target_date_if_due(now_utc: datetime, tz_name: str, cutoff, days_ahead: int) -> date | None:
    """Service date to plan when the org's local clock has reached the cutoff, else None."""
    try:
        local = now_utc.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        local = now_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    if local.time().replace(tzinfo=None) < cutoff:
        return None
    return local.date() + timedelta(days=days_ahead)


def _msg(exc: AppError) -> str:
    return exc.detail.get("message", "error") if isinstance(exc.detail, dict) else str(exc.detail)


async def _plan_org(conn: AsyncConnection, org_id: uuid.UUID, service_date: date, send: bool) -> dict:
    shifts = (await conn.execute(text("SELECT id, name FROM shifts WHERE organization_id=:o AND is_active ORDER BY start_time"), {"o": org_id})).all()
    summary: dict = {"service_date": str(service_date), "slots": [], "created": 0, "offered": 0, "attention": []}
    for sh in shifts:
        for direction in DIRECTIONS:
            slot = {"shift": sh.name, "direction": direction}
            existing = await conn.scalar(text("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND service_date=:d AND shift_id=:s AND direction=:dir AND status <> 'CANCELLED'"),
                                         {"o": org_id, "d": service_date, "s": sh.id, "dir": direction})
            if existing:
                slot["skipped"] = "allocations already exist"; summary["slots"].append(slot); continue
            try:
                async with conn.begin_nested():
                    await svc.generate_requests(conn, org_id, service_date, sh.id, direction)
                    plan = await svc.plan_slot(conn, org_id, service_date=service_date, shift_id=sh.id, direction=direction)
                    if not plan["routes"]:
                        slot["skipped"] = "no passengers"
                        if plan["unassigned"]:
                            summary["attention"].append(f"{sh.name} {direction}: {len(plan['unassigned'])} passenger(s) could not be placed")
                        summary["slots"].append(slot); continue
                    created = await svc.commit_plan(conn, org_id, None, plan, replace_proposed=False)
            except AppError as exc:
                slot["skipped"] = _msg(exc)
                summary["attention"].append(f"{sh.name} {direction}: {slot['skipped']}")
                summary["slots"].append(slot); continue
            summary["created"] += len(created)
            if plan["unassigned"]:
                summary["attention"].append(f"{sh.name} {direction}: {len(plan['unassigned'])} passenger(s) not placed")
            for a in created:
                if not send:
                    continue
                if a["estimate_status"] != "final" or a["driver_id"] is None:
                    summary["attention"].append(f"{sh.name} {direction}: a trip needs a {'driver' if a['driver_id'] is None else 'road estimate'} before it can be sent")
                    continue
                try:
                    async with conn.begin_nested():
                        await svc.offer_to_driver(conn, org_id, None, a, a["driver_id"])
                    summary["offered"] += 1
                except AppError as exc:
                    summary["attention"].append(f"{sh.name} {direction}: could not send a trip ({_msg(exc)})")
            slot["trips"] = len(created); summary["slots"].append(slot)
    return summary


async def _notify_staff(conn: AsyncConnection, org_id: uuid.UUID, service_date: date, summary: dict) -> None:
    attention = summary.get("attention") or []
    body = (f"Auto-dispatch planned {summary.get('created', 0)} trip(s) for {service_date}; {summary.get('offered', 0)} sent to drivers."
            + (f" {len(attention)} item(s) need your attention." if attention else " Nothing needs attention."))
    if summary.get("error"):
        body = f"Auto-dispatch for {service_date} failed and needs manual planning."
    for u in await staff_user_ids(conn, org_id):
        await notify(conn, org_id=org_id, user_id=u, event_type="autodispatch.summary", title="Auto-dispatch finished", body=body,
                     data={"service_date": str(service_date), "attention": attention[:20]}, dedupe_key=f"autodispatch:{org_id}:{service_date}:{u}")


async def run_due(now_utc: datetime | None = None) -> list[dict]:
    """Worker entry point. Returns one result per organisation that ran."""
    now_utc = now_utc or datetime.now(timezone.utc)
    async with system_session() as conn:
        orgs = (await conn.execute(text("""SELECT o.id, o.timezone, s.auto_dispatch_cutoff, s.auto_dispatch_days_ahead, s.auto_dispatch_send_to_drivers
            FROM organizations o JOIN organization_settings s ON s.organization_id=o.id WHERE o.status='active' AND s.auto_dispatch_enabled"""))).all()
    results = []
    for o in orgs:
        d = target_date_if_due(now_utc, o.timezone, o.auto_dispatch_cutoff, o.auto_dispatch_days_ahead)
        if d is None:
            continue
        summary: dict
        try:
            async with system_session() as conn:
                claimed = (await conn.execute(text("INSERT INTO auto_dispatch_runs (organization_id, service_date) VALUES (:o,:d) ON CONFLICT DO NOTHING RETURNING 1"), {"o": o.id, "d": d})).first()
                if claimed is None:
                    continue    # already ran (or another worker holds it)
                summary = await _plan_org(conn, o.id, d, o.auto_dispatch_send_to_drivers)
                await conn.execute(text("UPDATE auto_dispatch_runs SET summary=CAST(:s AS jsonb), ran_at=now() WHERE organization_id=:o AND service_date=:d"),
                                   {"s": json.dumps(summary, default=str), "o": o.id, "d": d})
                await _notify_staff(conn, o.id, d, summary)
        except Exception as exc:     # claim rolled back with the txn: record the failure once so we do not retry every tick
            log.exception("auto-dispatch failed for org %s", o.id)
            summary = {"error": type(exc).__name__, "attention": ["Auto-dispatch failed; plan manually"]}
            async with system_session() as conn:
                await conn.execute(text("INSERT INTO auto_dispatch_runs (organization_id, service_date, summary) VALUES (:o,:d,CAST(:s AS jsonb)) ON CONFLICT DO NOTHING"),
                                   {"o": o.id, "d": d, "s": json.dumps(summary)})
                await _notify_staff(conn, o.id, d, summary)
        results.append({"organization_id": str(o.id), **summary})
    return results
