"""Per cab-day billing.

A *cab-day* is one vehicle that has at least one CONFIRMED / IN_PROGRESS / COMPLETED trip on a service date. An organisation is charged
rate_paise per billable cab-day (days on or before free_until are free) plus GST, invoiced monthly in arrears. All money is integer
paise. Invoices past due + grace days pause the organisation's access until paid (the org admin can still open the billing page).
"""
from __future__ import annotations

import calendar
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..common import audit, notify, staff_user_ids
from ..db import system_session

log = logging.getLogger("routepilot.billing")
COUNTED = ("CONFIRMED", "IN_PROGRESS", "COMPLETED")


def month_bounds(first_of_month: date) -> tuple[date, date]:
    return first_of_month, first_of_month.replace(day=calendar.monthrange(first_of_month.year, first_of_month.month)[1])


def previous_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def financial_year(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def amounts(billable_days: int, rate_paise: int, gst_percent) -> dict:
    sub = billable_days * rate_paise
    gst = int((Decimal(sub) * Decimal(str(gst_percent)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return {"subtotal_paise": sub, "gst_paise": gst, "total_paise": sub + gst}


async def cab_days(conn: AsyncConnection, org_id: uuid.UUID, start: date, end: date) -> list[dict]:
    rows = (await conn.execute(text("""SELECT service_date, count(DISTINCT vehicle_id) AS cabs FROM trip_allocations
        WHERE organization_id=:o AND service_date BETWEEN :s AND :e AND status = ANY(:st) GROUP BY service_date ORDER BY service_date"""),
        {"o": org_id, "s": start, "e": end, "st": list(COUNTED)})).all()
    return [{"date": str(r.service_date), "cabs": r.cabs} for r in rows]


def split_billable(days: list[dict], free_until: date | None) -> tuple[int, int]:
    total = sum(d["cabs"] for d in days)
    billable = sum(d["cabs"] for d in days if free_until is None or date.fromisoformat(d["date"]) > free_until)
    return total, billable


async def generate_invoice(conn: AsyncConnection, org_id: uuid.UUID, first_of_month: date, *, actor: uuid.UUID | None = None, today: date | None = None) -> dict | None:
    """Idempotent: returns the existing invoice if the month was already invoiced; None if there is nothing billable."""
    existing = (await conn.execute(text("SELECT * FROM invoices WHERE organization_id=:o AND period_start=:s"), {"o": org_id, "s": first_of_month})).first()
    if existing:
        return dict(existing._mapping)
    b = (await conn.execute(text("SELECT * FROM organization_billing WHERE organization_id=:o AND billing_enabled"), {"o": org_id})).first()
    if b is None:
        return None
    start, end = month_bounds(first_of_month)
    days = await cab_days(conn, org_id, start, end)
    total, billable = split_billable(days, b.free_until)
    if billable == 0:
        return None
    amt = amounts(billable, b.rate_paise, b.gst_percent)
    today = today or datetime.now(timezone.utc).date()
    seq = await conn.scalar(text("SELECT nextval('invoice_number_seq')"))
    number = f"RP/{financial_year(end)}/{seq:06d}"
    row = (await conn.execute(text("""INSERT INTO invoices (organization_id, invoice_number, period_start, period_end, cab_days, billable_cab_days, rate_paise, subtotal_paise,
            gst_percent, gst_paise, total_paise, due_date, detail) VALUES (:o,:n,:s,:e,:cd,:bd,:r,:sub,:gp,:gst,:tot,:due,CAST(:det AS jsonb))
            ON CONFLICT (organization_id, period_start) DO NOTHING RETURNING *"""),
        {"o": org_id, "n": number, "s": start, "e": end, "cd": total, "bd": billable, "r": b.rate_paise, "sub": amt["subtotal_paise"], "gp": b.gst_percent,
         "gst": amt["gst_paise"], "tot": amt["total_paise"], "due": today + timedelta(days=b.due_days), "det": json.dumps(days)})).first()
    if row is None:      # lost a race with another worker; the winner's invoice stands
        return dict((await conn.execute(text("SELECT * FROM invoices WHERE organization_id=:o AND period_start=:s"), {"o": org_id, "s": start})).first()._mapping)
    inv = dict(row._mapping)
    await audit(conn, org_id=org_id, actor=actor, action="invoice.issued", entity_type="invoice", entity_id=inv["id"], details={"number": number, "total_paise": inv["total_paise"]})
    for u in await staff_user_ids(conn, org_id, ("org_admin",)):
        await notify(conn, org_id=org_id, user_id=u, event_type="invoice.issued", title="New invoice",
                     body=f"Invoice {number} for {start:%B %Y}: Rs {inv['total_paise'] / 100:,.2f}, due {inv['due_date']}.", data={"invoice_id": str(inv["id"])}, dedupe_key=f"invoice:{inv['id']}:{u}")
    return inv


async def reinstate_if_clear(conn: AsyncConnection, org_id: uuid.UUID, actor: uuid.UUID | None) -> bool:
    """After a payment/void: lift a billing suspension when no invoice is past its grace period any more."""
    overdue = await conn.scalar(text("""SELECT count(*) FROM invoices i JOIN organization_billing b USING (organization_id)
        WHERE i.organization_id=:o AND i.status='issued' AND i.due_date + b.grace_days < current_date"""), {"o": org_id})
    if overdue:
        return False
    n = (await conn.execute(text("UPDATE organizations SET status='active', suspended_reason=NULL WHERE id=:o AND status='suspended' AND suspended_reason='billing'"), {"o": org_id})).rowcount
    if n:
        await audit(conn, org_id=org_id, actor=actor, action="organization.reinstated_after_payment", entity_type="organization", entity_id=org_id)
    return bool(n)


def _local_today(now_utc: datetime, tz_name: str) -> date:
    try:
        return now_utc.astimezone(ZoneInfo(tz_name)).date()
    except ZoneInfoNotFoundError:
        return now_utc.astimezone(ZoneInfo("Asia/Kolkata")).date()


async def run_due(now_utc: datetime | None = None) -> dict:
    """Worker entry point (hourly). Issues last month's invoices, then pauses organisations whose invoices are overdue past grace."""
    now_utc = now_utc or datetime.now(timezone.utc)
    out = {"invoiced": 0, "suspended": 0}
    async with system_session() as conn:
        orgs = (await conn.execute(text("""SELECT o.id, o.timezone, o.status FROM organizations o JOIN organization_billing b ON b.organization_id=o.id WHERE b.billing_enabled"""))).all()
    for o in orgs:
        today = _local_today(now_utc, o.timezone)
        try:
            async with system_session() as conn:
                had = await conn.scalar(text("SELECT 1 FROM invoices WHERE organization_id=:o AND period_start=:s"), {"o": o.id, "s": previous_month(today)})
                if not had and await generate_invoice(conn, o.id, previous_month(today), today=today):
                    out["invoiced"] += 1
            async with system_session() as conn:
                if o.status == "active":
                    late = (await conn.execute(text("""SELECT i.invoice_number FROM invoices i JOIN organization_billing b USING (organization_id)
                        WHERE i.organization_id=:o AND i.status='issued' AND i.due_date + b.grace_days < :t ORDER BY i.due_date LIMIT 1"""), {"o": o.id, "t": today})).first()
                    if late:
                        await conn.execute(text("UPDATE organizations SET status='suspended', suspended_reason='billing' WHERE id=:o AND status='active'"), {"o": o.id})
                        await audit(conn, org_id=o.id, actor=None, action="organization.suspended_for_billing", entity_type="organization", entity_id=o.id, details={"invoice": late.invoice_number})
                        for u in await staff_user_ids(conn, o.id, ("org_admin",)):
                            await notify(conn, org_id=o.id, user_id=u, event_type="billing.suspended", title="Access paused: invoice overdue", critical=True,
                                         body=f"Invoice {late.invoice_number} is overdue. Pay it and access resumes.", dedupe_key=f"billing-suspended:{o.id}:{late.invoice_number}:{u}")
                        out["suspended"] += 1
        except Exception:
            log.exception("billing run failed for org %s", o.id)
    return out
