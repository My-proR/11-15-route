"""Billing: per cab-day usage, monthly invoices, payment tracking, suspension for unpaid invoices.

Organisation admins see their usage and invoices (even while the organisation is paused, so they can resolve it).
Only platform administrators change rates, issue invoices manually, mark them paid or void them. No card data is handled here:
payment is recorded by the platform operator after UPI / bank transfer (an online payment gateway is a separate future step).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip
from ..config import get_settings
from ..db import system_session
from ..schemas import Strict
from ..services import billing as svc

router = APIRouter(tags=["billing"])


class BillingIn(Strict):
    billing_enabled: bool
    rate_paise: int = Field(5000, ge=0, le=10_000_000, description="Charge per cab-day in paise (5000 = Rs 50)")
    gst_percent: Decimal = Field(Decimal("18"), ge=0, le=28)
    free_until: Optional[date] = None
    due_days: int = Field(7, ge=0, le=60)
    grace_days: int = Field(7, ge=0, le=60)
    billing_email: Optional[str] = Field(None, max_length=254)


class GenerateIn(Strict):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")


class PaidIn(Strict):
    payment_ref: str = Field(min_length=3, max_length=120, description="UPI reference / bank transaction id")


def _seller() -> dict:
    s = get_settings()
    return {"name": s.billing_seller_name or None, "gstin": s.billing_seller_gstin or None, "payment_instructions": s.billing_payment_instructions or None}


def _inv(r, detail: bool = False) -> dict:
    d = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in dict(r._mapping).items()}
    if not detail:
        d.pop("detail", None)
    return d


# ---- organisation admin ---------------------------------------------------------------------------------------
@router.get("/billing")
async def my_billing(p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    b = (await conn.execute(text("SELECT * FROM organization_billing WHERE organization_id=:o"), {"o": p.org_id})).first()
    async with system_session() as sconn:      # a paused organisation is hidden from RLS; its verified admin may still read its own status
        org = (await sconn.execute(text("SELECT status, suspended_reason FROM organizations WHERE id=:o"), {"o": p.org_id})).first()
    out: dict = {"enabled": bool(b and b.billing_enabled), "suspended": org.status != "active", "suspended_reason": org.suspended_reason, "seller": _seller(), "month": None, "invoices": [], "outstanding_paise": 0}
    if b:
        out.update({"rate_paise": b.rate_paise, "gst_percent": str(b.gst_percent), "free_until": b.free_until, "due_days": b.due_days, "grace_days": b.grace_days})
    if b and b.billing_enabled:
        today = datetime.now(timezone.utc).date()
        start, end = svc.month_bounds(today.replace(day=1))
        days = await svc.cab_days(conn, p.org_id, start, end)
        total, billable = svc.split_billable(days, b.free_until)
        out["month"] = {"start": start, "end": end, "cab_days": total, "billable_cab_days": billable, **svc.amounts(billable, b.rate_paise, b.gst_percent), "days": days}
    rows = (await conn.execute(text("SELECT * FROM invoices WHERE organization_id=:o ORDER BY period_start DESC LIMIT 12"), {"o": p.org_id})).all()
    out["invoices"] = [_inv(r) for r in rows]
    out["outstanding_paise"] = sum(r.total_paise for r in rows if r.status == "issued")
    return out


@router.get("/billing/invoices/{invoice_id}")
async def my_invoice(invoice_id: uuid.UUID, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text("SELECT * FROM invoices WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": invoice_id})).first()
    if r is None:
        raise AppError(404, "not_found", "Invoice not found")
    async with system_session() as sconn:
        name = await sconn.scalar(text("SELECT name FROM organizations WHERE id=:o"), {"o": p.org_id})
    return {**_inv(r, detail=True), "organization_name": name, "seller": _seller()}


# ---- platform administrator ---------------------------------------------------------------------------------------
@router.get("/platform/billing")
async def platform_overview(p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    rows = (await conn.execute(text("""SELECT o.id, o.name, o.status, o.suspended_reason, b.billing_enabled, b.rate_paise, b.gst_percent, b.free_until, b.due_days, b.grace_days,
            coalesce((SELECT sum(total_paise) FROM invoices i WHERE i.organization_id=o.id AND i.status='issued'), 0)::bigint AS outstanding_paise,
            (SELECT count(*) FROM invoices i WHERE i.organization_id=o.id AND i.status='issued' AND i.due_date < current_date) AS overdue_invoices
        FROM organizations o LEFT JOIN organization_billing b ON b.organization_id=o.id ORDER BY o.name"""))).all()
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in dict(r._mapping).items()} | {"billing_enabled": bool(r.billing_enabled)} for r in rows]


@router.put("/platform/billing/{org_id}")
async def set_billing(org_id: uuid.UUID, payload: BillingIn, request: Request, p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    if not await conn.scalar(text("SELECT 1 FROM organizations WHERE id=:o"), {"o": org_id}):
        raise AppError(404, "not_found", "Organization not found")
    await conn.execute(text("""INSERT INTO organization_billing (organization_id, billing_enabled, rate_paise, gst_percent, free_until, due_days, grace_days, billing_email)
        VALUES (:o,:en,:r,:g,:f,:d,:gr,:e) ON CONFLICT (organization_id) DO UPDATE SET billing_enabled=EXCLUDED.billing_enabled, rate_paise=EXCLUDED.rate_paise,
        gst_percent=EXCLUDED.gst_percent, free_until=EXCLUDED.free_until, due_days=EXCLUDED.due_days, grace_days=EXCLUDED.grace_days, billing_email=EXCLUDED.billing_email, updated_at=now()"""),
        {"o": org_id, "en": payload.billing_enabled, "r": payload.rate_paise, "g": payload.gst_percent, "f": payload.free_until, "d": payload.due_days, "gr": payload.grace_days, "e": payload.billing_email})
    await audit(conn, org_id=org_id, actor=p.user_id, action="billing.settings_changed", entity_type="organization", entity_id=org_id,
                details={"enabled": payload.billing_enabled, "rate_paise": payload.rate_paise}, ip=client_ip(request))
    return {"organization_id": org_id, **payload.model_dump(mode="json")}


@router.post("/platform/billing/{org_id}/invoices", status_code=201)
async def generate(org_id: uuid.UUID, payload: GenerateIn, p: Principal = Depends(require_roles(platform_admin=True))):
    y, m = (int(x) for x in payload.month.split("-"))
    first = date(y, m, 1)
    if first > datetime.now(timezone.utc).date().replace(day=1):
        raise AppError(422, "future_month", "Cannot invoice a future month")
    async with system_session() as conn:      # trusted server step: reads usage across the organisation regardless of the caller's RLS view
        if not await conn.scalar(text("SELECT 1 FROM organization_billing WHERE organization_id=:o AND billing_enabled"), {"o": org_id}):
            raise AppError(409, "billing_disabled", "Billing is not enabled for this organization")
        inv = await svc.generate_invoice(conn, org_id, first, actor=p.user_id)
    if inv is None:
        raise AppError(422, "nothing_to_bill", "No billable cab-days in that month")
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in inv.items() if k != "detail"}


@router.get("/platform/invoices")
async def platform_invoices(p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for),
                            status: Optional[Literal["issued", "paid", "void"]] = None, organization_id: Optional[uuid.UUID] = None, limit: int = Query(100, ge=1, le=500)):
    rows = (await conn.execute(text("""SELECT i.*, o.name AS organization_name FROM invoices i JOIN organizations o ON o.id=i.organization_id
        WHERE (CAST(:s AS text) IS NULL OR i.status=:s) AND (CAST(:o AS uuid) IS NULL OR i.organization_id=:o) ORDER BY i.issued_at DESC LIMIT :l"""), {"s": status, "o": organization_id, "l": limit})).all()
    return [_inv(r) for r in rows]


@router.post("/platform/invoices/{invoice_id}/mark-paid")
async def mark_paid(invoice_id: uuid.UUID, payload: PaidIn, request: Request, p: Principal = Depends(require_roles(platform_admin=True))):
    async with system_session() as conn:
        r = (await conn.execute(text("UPDATE invoices SET status='paid', paid_at=now(), payment_ref=:r WHERE id=:i AND status='issued' RETURNING organization_id, invoice_number"), {"r": payload.payment_ref, "i": invoice_id})).first()
        if r is None:
            raise AppError(409, "not_payable", "Invoice not found or not in 'issued' state")
        await audit(conn, org_id=r.organization_id, actor=p.user_id, action="invoice.paid", entity_type="invoice", entity_id=invoice_id, details={"number": r.invoice_number, "ref": payload.payment_ref}, ip=client_ip(request))
        reinstated = await svc.reinstate_if_clear(conn, r.organization_id, p.user_id)
    return {"id": invoice_id, "status": "paid", "organization_reinstated": reinstated}


@router.post("/platform/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(platform_admin=True))):
    async with system_session() as conn:
        r = (await conn.execute(text("UPDATE invoices SET status='void' WHERE id=:i AND status='issued' RETURNING organization_id, invoice_number"), {"i": invoice_id})).first()
        if r is None:
            raise AppError(409, "not_voidable", "Invoice not found or not in 'issued' state")
        await audit(conn, org_id=r.organization_id, actor=p.user_id, action="invoice.voided", entity_type="invoice", entity_id=invoice_id, details={"number": r.invoice_number}, ip=client_ip(request))
        reinstated = await svc.reinstate_if_clear(conn, r.organization_id, p.user_id)
    return {"id": invoice_id, "status": "void", "organization_reinstated": reinstated}
