"""Auto-dispatch at the organisation's cutoff time."""
from datetime import date, datetime, time, timezone

from sqlalchemy import text

from app.db import system_session
from app.services import autodispatch
from tests.conftest import API, H
from tests.helpers import count, dispatch_slot


def test_due_logic_uses_org_timezone():
    utc = datetime(2026, 11, 10, 12, 29, tzinfo=timezone.utc)           # 17:59 in Chennai
    assert autodispatch.target_date_if_due(utc, "Asia/Kolkata", time(18, 0), 1) is None
    utc = datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc)           # 18:00 in Chennai
    assert autodispatch.target_date_if_due(utc, "Asia/Kolkata", time(18, 0), 1) == date(2026, 11, 11)
    after_midnight = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)    # 01:30 on the 11th in Chennai: new local day, cutoff not yet reached
    assert autodispatch.target_date_if_due(after_midnight, "Asia/Kolkata", time(18, 0), 1) is None
    evening = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)           # 22:30 on the 10th in Chennai
    assert autodispatch.target_date_if_due(evening, "Asia/Kolkata", time(18, 0), 2) == date(2026, 11, 12)
    assert autodispatch.target_date_if_due(utc, "Not/AZone", time(18, 0), 1) == date(2026, 11, 11)   # bad tz falls back


async def _enable(org, *, send=True, cutoff="00:00"):
    async with system_session() as conn:
        await conn.execute(text("UPDATE organizations SET timezone='UTC' WHERE id=:o"), {"o": org})   # tests use the UTC calendar day for "tomorrow"
        await conn.execute(text("UPDATE organization_settings SET auto_dispatch_enabled=true, auto_dispatch_cutoff=:c, auto_dispatch_days_ahead=1, auto_dispatch_send_to_drivers=:s WHERE organization_id=:o"),
                           {"o": org, "c": time.fromisoformat(cutoff), "s": send})


async def test_default_is_off_and_not_due_before_cutoff(world):
    a = world.a
    assert await count("SELECT auto_dispatch_enabled FROM organization_settings WHERE organization_id=:o", o=a["org"]) is False
    assert not [r for r in await autodispatch.run_due() if r["organization_id"] == str(a["org"])]
    await _enable(a["org"], cutoff="18:00")
    before_cutoff = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)       # 12:00 UTC, before the 18:00 cutoff
    assert not [r for r in await autodispatch.run_due(before_cutoff) if r["organization_id"] == str(a["org"])]
    assert await count("SELECT count(*) FROM auto_dispatch_runs WHERE organization_id=:o", o=a["org"]) == 0


async def test_runs_once_plans_and_offers_to_drivers(client, world):
    a = world.a
    await _enable(a["org"])
    res = [r for r in await autodispatch.run_due() if r["organization_id"] == str(a["org"])]
    assert len(res) == 1 and res[0]["created"] >= 1
    pend = await count("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='PENDING_DRIVER_ACCEPTANCE'", o=a["org"])
    assert pend == res[0]["offered"] and pend >= 1
    assert await count("SELECT count(*) FROM auto_dispatch_runs WHERE organization_id=:o", o=a["org"]) == 1
    # staff are told once; drivers got a request
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='autodispatch.summary'", o=a["org"]) == 2   # admin + dispatcher
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='assignment.requested'", o=a["org"]) == pend
    # second tick the same day does nothing
    assert not [r for r in await autodispatch.run_due() if r["organization_id"] == str(a["org"])]
    # the dispatcher can still see and manage them with the normal screens
    r = await client.get(f"{API}/allocations", headers=H(a["dispatcher"]))
    assert r.status_code == 200 and r.json()["total"] >= 1


async def test_propose_only_mode_leaves_drafts_for_staff(world):
    a = world.a
    await _enable(a["org"], send=False)
    res = [r for r in await autodispatch.run_due() if r["organization_id"] == str(a["org"])][0]
    assert res["created"] >= 1 and res["offered"] == 0
    assert await count("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='PROPOSED'", o=a["org"]) == res["created"]
    assert await count("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='PENDING_DRIVER_ACCEPTANCE'", o=a["org"]) == 0


async def test_existing_work_is_never_overwritten(client, world):
    a = world.a
    r = await dispatch_slot(client, a)               # dispatcher already planned tomorrow's pickup by hand
    assert r.status_code == 200
    before = sorted(x["id"] for x in r.json()["allocations"])
    await _enable(a["org"])
    res = [x for x in await autodispatch.run_due() if x["organization_id"] == str(a["org"])][0]
    skipped = [s for s in res["slots"] if s["direction"] == "pickup"]
    assert skipped and skipped[0].get("skipped") == "allocations already exist"
    now = [str(x) for x in (await _ids(a["org"], "pickup"))]
    assert sorted(now) == before


async def _ids(org, direction):
    async with system_session() as conn:
        rows = (await conn.execute(text("SELECT id FROM trip_allocations WHERE organization_id=:o AND direction=:d AND status<>'CANCELLED'"), {"o": org, "d": direction})).all()
    return [r.id for r in rows]


async def test_only_org_admin_can_change_the_setting_and_values_are_validated(client, world):
    a = world.a
    ok = await client.put(f"{API}/admin/settings", json={"auto_dispatch_enabled": True, "auto_dispatch_cutoff": "17:30:00", "auto_dispatch_days_ahead": 1}, headers=H(a["admin"]))
    assert ok.status_code == 200 and ok.json()["auto_dispatch_enabled"] is True
    assert (await client.put(f"{API}/admin/settings", json={"auto_dispatch_enabled": False}, headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.put(f"{API}/admin/settings", json={"auto_dispatch_days_ahead": 9}, headers=H(a["admin"]))).status_code == 422
