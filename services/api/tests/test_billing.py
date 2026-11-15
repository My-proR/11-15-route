"""Per cab-day billing: usage, invoice math, tenant isolation, payment, overdue suspension and reinstatement."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.db import system_session
from app.services import billing as svc
from tests.conftest import API, H
from tests.helpers import committed_allocations, count, drive_to_confirmed

TODAY = date.today()


def PA(world) -> dict:
    return {"Authorization": world.platform.h["Authorization"]}      # platform admins send no organization header


@pytest.mark.no_db
def test_amounts_use_integer_paise_and_round_half_up():
    assert svc.amounts(30, 5000, 18) == {"subtotal_paise": 150000, "gst_paise": 27000, "total_paise": 177000}
    assert svc.amounts(1, 5000, "18.0")["gst_paise"] == 900
    assert svc.amounts(1, 1, 18)["gst_paise"] == 0            # 0.18 paise rounds down
    assert svc.amounts(1, 3, 18)["gst_paise"] == 1            # 0.54 paise rounds up
    assert svc.amounts(0, 5000, 18)["total_paise"] == 0


@pytest.mark.no_db
def test_financial_year_and_month_helpers():
    assert svc.financial_year(date(2026, 3, 31)) == "2025-26"
    assert svc.financial_year(date(2026, 4, 1)) == "2026-27"
    assert svc.previous_month(date(2026, 1, 15)) == date(2025, 12, 1)
    assert svc.month_bounds(date(2024, 2, 1)) == (date(2024, 2, 1), date(2024, 2, 29))
    days = [{"date": "2026-11-01", "cabs": 2}, {"date": "2026-11-02", "cabs": 3}]
    assert svc.split_billable(days, None) == (5, 5)
    assert svc.split_billable(days, date(2026, 11, 1)) == (5, 3)


async def _confirmed_today(client, a, *, on: date = TODAY, n: int = 1) -> int:
    """Confirm n trips and move them to `on`; returns the number of distinct cabs among all confirmed trips."""
    allocs = await committed_allocations(client, a)
    for al in allocs[:n]:
        await drive_to_confirmed(client, a, al["id"])
    async with system_session() as conn:
        await conn.execute(text("UPDATE trip_allocations SET service_date=:d WHERE organization_id=:o AND status='CONFIRMED'"), {"d": on, "o": a["org"]})
        return await conn.scalar(text("SELECT count(DISTINCT vehicle_id) FROM trip_allocations WHERE organization_id=:o AND status='CONFIRMED'"), {"o": a["org"]})


async def _enable(client, world, org, **over):
    body = {"billing_enabled": True, "rate_paise": 5000, "gst_percent": "18", "due_days": 7, "grace_days": 7} | over
    r = await client.put(f"{API}/platform/billing/{org}", json=body, headers=PA(world))
    assert r.status_code == 200, r.text
    return r


async def test_usage_invoice_isolation_payment(client, world):
    a, b = world.a, world.b
    cabs = await _confirmed_today(client, a)
    assert cabs >= 1
    # off by default: nothing to see, nothing billed
    r = await client.get(f"{API}/billing", headers=H(a["admin"]))
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["month"] is None
    await _enable(client, world, a["org"])
    live = (await client.get(f"{API}/billing", headers=H(a["admin"]))).json()
    assert live["month"]["cab_days"] == cabs and live["month"]["subtotal_paise"] == cabs * 5000
    month = TODAY.strftime("%Y-%m")
    r = await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": month}, headers=PA(world))
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["billable_cab_days"] == cabs and inv["subtotal_paise"] == cabs * 5000
    assert inv["gst_paise"] == round(cabs * 5000 * 0.18) and inv["total_paise"] == inv["subtotal_paise"] + inv["gst_paise"]
    assert __import__("re").fullmatch(r"RP/\d{4}-\d{2}/\d{6}", inv["invoice_number"])
    again = await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": month}, headers=PA(world))
    assert again.json()["id"] == inv["id"] and await count("SELECT count(*) FROM invoices WHERE organization_id=:o", o=a["org"]) == 1
    # org admin sees the invoice with per-day lines; others cannot
    mine = (await client.get(f"{API}/billing", headers=H(a["admin"]))).json()
    assert [i["id"] for i in mine["invoices"]] == [inv["id"]] and mine["outstanding_paise"] == inv["total_paise"]
    full = await client.get(f"{API}/billing/invoices/{inv['id']}", headers=H(a["admin"]))
    assert full.status_code == 200 and sum(d["cabs"] for d in full.json()["detail"]) == cabs
    assert (await client.get(f"{API}/billing/invoices/{inv['id']}", headers=H(b["admin"]))).status_code == 404
    assert (await client.get(f"{API}/billing", headers=H(b["admin"]))).json()["invoices"] == []
    assert (await client.get(f"{API}/billing", headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.put(f"{API}/platform/billing/{a['org']}", json={"billing_enabled": False}, headers=H(a["admin"]))).status_code == 403
    assert (await client.post(f"{API}/platform/invoices/{inv['id']}/mark-paid", json={"payment_ref": "UPI123456"}, headers=H(a["admin"]))).status_code == 403
    assert (await client.get(f"{API}/platform/invoices", headers=H(b["admin"]))).status_code == 403
    # notified, then paid exactly once
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='invoice.issued'", o=a["org"]) >= 1
    paid = await client.post(f"{API}/platform/invoices/{inv['id']}/mark-paid", json={"payment_ref": "UPI123456"}, headers=PA(world))
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    assert (await client.post(f"{API}/platform/invoices/{inv['id']}/mark-paid", json={"payment_ref": "UPI999999"}, headers=PA(world))).status_code == 409
    assert (await client.post(f"{API}/platform/invoices/{inv['id']}/void", headers=PA(world))).status_code == 409
    assert (await client.get(f"{API}/billing", headers=H(a["admin"]))).json()["outstanding_paise"] == 0
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action IN ('invoice.issued','invoice.paid','billing.settings_changed')", o=a["org"]) >= 3


async def test_free_pilot_validation_and_guards(client, world):
    a = world.a
    await _confirmed_today(client, a)
    for bad in ({"rate_paise": -1}, {"gst_percent": "40"}, {"due_days": 99}, {"grace_days": -1}):
        r = await client.put(f"{API}/platform/billing/{a['org']}", json={"billing_enabled": True} | bad, headers=PA(world))
        assert r.status_code == 422, bad
    month = TODAY.strftime("%Y-%m")
    # billing not enabled yet
    assert (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": month}, headers=PA(world))).status_code == 409
    await _enable(client, world, a["org"], free_until=str(TODAY))                 # whole month is inside the free pilot
    r = await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": month}, headers=PA(world))
    assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_to_bill"
    nxt = date(TODAY.year + (TODAY.month == 12), TODAY.month % 12 + 1, 1).strftime("%Y-%m")
    assert (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": nxt}, headers=PA(world))).status_code == 422
    assert (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": "2026-13"}, headers=PA(world))).status_code == 422
    assert (await client.put(f"{API}/platform/billing/00000000-0000-0000-0000-000000000000", json={"billing_enabled": True}, headers=PA(world))).status_code == 404


async def test_worker_issues_last_months_invoice_once(client, world):
    a = world.a
    prev = svc.previous_month(TODAY)
    await _confirmed_today(client, a, on=prev.replace(day=3))
    await _enable(client, world, a["org"])
    res = await svc.run_due(datetime.now(timezone.utc))
    assert res["invoiced"] >= 1
    assert await count("SELECT count(*) FROM invoices WHERE organization_id=:o AND period_start=:p", o=a["org"], p=prev) == 1
    res2 = await svc.run_due(datetime.now(timezone.utc))
    assert await count("SELECT count(*) FROM invoices WHERE organization_id=:o", o=a["org"]) == 1 and res2["invoiced"] == 0


async def test_overdue_invoice_pauses_access_and_payment_restores_it(client, world):
    a, b = world.a, world.b
    await _confirmed_today(client, a)
    await _enable(client, world, a["org"], due_days=0, grace_days=0)
    inv = (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": TODAY.strftime("%Y-%m")}, headers=PA(world))).json()
    await svc.run_due(datetime.now(timezone.utc))
    assert await count("SELECT status FROM organizations WHERE id=:o", o=a["org"]) == "active"        # due today, not yet overdue
    async with system_session() as conn:
        await conn.execute(text("UPDATE invoices SET due_date = current_date - 1 WHERE id=:i"), {"i": inv["id"]})
    res = await svc.run_due(datetime.now(timezone.utc))
    assert res["suspended"] >= 1
    assert await count("SELECT suspended_reason FROM organizations WHERE id=:o", o=a["org"]) == "billing"
    # paused: staff and drivers are refused, with a clear code; the admin can still see /me and /billing
    r = await client.get(f"{API}/allocations", headers=H(a["dispatcher"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "organization_suspended"
    assert (await client.get(f"{API}/members", headers=H(a["admin"]))).status_code == 403
    assert (await client.get(f"{API}/me/availability", headers=H(a["driver_actors"][0]))).status_code == 403
    me = await client.get(f"{API}/me", headers=H(a["admin"]))
    assert me.status_code == 200 and me.json()["organization_suspended"] is True
    bill = await client.get(f"{API}/billing", headers=H(a["admin"]))
    assert bill.status_code == 200 and bill.json()["suspended"] is True and bill.json()["outstanding_paise"] == inv["total_paise"]
    assert (await client.get(f"{API}/allocations", headers=H(b["dispatcher"]))).status_code == 200       # other tenants unaffected
    assert (await client.get(f"{API}/organizations", headers=PA(world))).status_code == 200        # platform admin unaffected
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='billing.suspended'", o=a["org"]) >= 1
    # paying restores access immediately
    paid = await client.post(f"{API}/platform/invoices/{inv['id']}/mark-paid", json={"payment_ref": "NEFT0001"}, headers=PA(world))
    assert paid.json()["organization_reinstated"] is True
    assert (await client.get(f"{API}/allocations", headers=H(a["dispatcher"]))).status_code == 200


async def test_manual_suspension_is_not_lifted_by_payment(client, world):
    a = world.a
    await _confirmed_today(client, a)
    await _enable(client, world, a["org"])
    inv = (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": TODAY.strftime("%Y-%m")}, headers=PA(world))).json()
    assert (await client.post(f"{API}/organizations/{a['org']}/status", params={"status": "suspended"}, headers=PA(world))).status_code == 200
    assert await count("SELECT suspended_reason FROM organizations WHERE id=:o", o=a["org"]) == "manual"
    paid = await client.post(f"{API}/platform/invoices/{inv['id']}/mark-paid", json={"payment_ref": "UPI777777"}, headers=PA(world))
    assert paid.json()["organization_reinstated"] is False
    assert (await client.get(f"{API}/allocations", headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.post(f"{API}/organizations/{a['org']}/status", params={"status": "active"}, headers=PA(world))).status_code == 200
    assert (await client.get(f"{API}/allocations", headers=H(a["dispatcher"]))).status_code == 200


async def test_void_invoice_lifts_billing_pause_and_overview_lists_orgs(client, world):
    a = world.a
    await _confirmed_today(client, a)
    await _enable(client, world, a["org"], due_days=0, grace_days=0)
    inv = (await client.post(f"{API}/platform/billing/{a['org']}/invoices", json={"month": TODAY.strftime("%Y-%m")}, headers=PA(world))).json()
    async with system_session() as conn:
        await conn.execute(text("UPDATE invoices SET due_date = current_date - 1 WHERE id=:i"), {"i": inv["id"]})
    await svc.run_due(datetime.now(timezone.utc))
    ov = (await client.get(f"{API}/platform/billing", headers=PA(world))).json()
    row = next(o for o in ov if o["id"] == str(a["org"]))
    assert row["status"] == "suspended" and row["overdue_invoices"] == 1 and row["outstanding_paise"] == inv["total_paise"]
    v = await client.post(f"{API}/platform/invoices/{inv['id']}/void", headers=PA(world))
    assert v.json()["organization_reinstated"] is True
    listed = (await client.get(f"{API}/platform/invoices", params={"status": "void"}, headers=PA(world))).json()
    assert [i["id"] for i in listed if i["organization_id"] == str(a["org"])] == [inv["id"]]
