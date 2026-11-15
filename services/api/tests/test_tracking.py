"""Live vehicle tracking: default OFF; positions only from the assigned driver of an IN_PROGRESS trip; nothing stored when disabled."""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
import pytest
from app.common import limiter
from app.db import system_session
from tests.conftest import API, H
from tests.helpers import committed_allocations, count, drive_to_confirmed, enable_all, set_features


def pos(alloc, **kw):
    base = {"allocation_id": alloc, "latitude": 13.01, "longitude": 80.01, "accuracy_m": 8, "speed_kmh": 30, "heading": 90,
            "captured_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


async def in_progress(client, a, index=0, allocs=None):
    allocs = allocs or await committed_allocations(client, a)
    alloc = allocs[index]["id"]
    driver = await drive_to_confirmed(client, a, alloc)
    assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 200
    return alloc, driver


async def stored(org):
    return await count("SELECT count(*) FROM vehicle_last_position WHERE organization_id=:o", o=org)


async def test_default_off_rejects_and_stores_nothing(client, world):
    a = world.a
    alloc, driver = await in_progress(client, a)
    r = await client.post(f"{API}/tracking/position", json=pos(alloc), headers=H(driver))
    assert r.status_code == 403 and r.json()["error"]["code"] == "live_tracking_disabled"
    assert await stored(a["org"]) == 0
    assert (await client.get(f"{API}/tracking/status", headers=H(driver))).json()["enabled"] is False
    v = (await client.get(f"{API}/tracking/vehicles", headers=H(a["dispatcher"]))).json()
    assert v["enabled"] is False and v["vehicles"] == []
    assert (await client.get(f"{API}/tracking/vehicles/{uuid.uuid4()}", headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.get(f"{API}/tracking/my-trip", headers=H(a["employee_actors"][0]))).json() == {"enabled": False, "position": None}


@pytest.mark.parametrize("deploy,platform,org_on", [(False, True, True), (True, False, True), (True, True, False)])
async def test_every_layer_must_be_on(client, world, deploy, platform, org_on):
    a = world.a
    alloc, driver = await in_progress(client, a)
    await set_features(a["org"], live=org_on, deploy_live=deploy, platform_live=platform)
    r = await client.post(f"{API}/tracking/position", json=pos(alloc), headers=H(driver))
    assert r.status_code == 403 and await stored(a["org"]) == 0


async def test_enabling_in_settings_is_blocked_until_deployment_and_platform_allow(client, world):
    a = world.a
    r = await client.put(f"{API}/admin/settings", json={"live_tracking_enabled": True, "confirm": True}, headers=H(a["admin"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "feature_unavailable"
    await set_features(a["org"], deploy_live=True, platform_live=True)
    r = await client.put(f"{API}/admin/settings", json={"live_tracking_enabled": True, "confirm": True}, headers=H(a["admin"]))
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/tracking/status", headers=H(a["dispatcher"]))).json()["enabled"] is True
    assert (await client.put(f"{API}/admin/settings", json={"live_tracking_enabled": False}, headers=H(a["dispatcher"]))).status_code == 403


async def test_only_assigned_driver_on_in_progress_trip(client, world):
    a = world.a
    await enable_all(a["org"])
    allocs = await committed_allocations(client, a)
    first = allocs[0]["id"]
    driver = await drive_to_confirmed(client, a, first)
    r = await client.post(f"{API}/tracking/position", json=pos(first), headers=H(driver))
    assert r.status_code == 409 and r.json()["error"]["code"] == "trip_not_in_progress"     # confirmed but not started
    await client.post(f"{API}/allocations/{first}/start", headers=H(driver))
    other = next(d for d in a["driver_actors"] if d.id != driver.id)
    assert (await client.post(f"{API}/tracking/position", json=pos(first), headers=H(other))).status_code == 404
    assert (await client.post(f"{API}/tracking/position", json=pos(first), headers=H(a["employee_actors"][0]))).status_code == 403
    assert (await client.post(f"{API}/tracking/position", json=pos(first), headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.post(f"{API}/tracking/position", json=pos(first), headers=H(world.b["driver_actors"][0]))).status_code in (403, 404)
    ok = await client.post(f"{API}/tracking/position", json=pos(first), headers=H(driver))
    assert ok.status_code == 202 and await stored(a["org"]) == 1


async def test_validation_and_interval_rate_limit_and_one_row_per_vehicle(client, world):
    a = world.a
    await enable_all(a["org"])
    alloc, driver = await in_progress(client, a)
    assert (await client.post(f"{API}/tracking/position", json=pos(alloc, latitude=91), headers=H(driver))).status_code == 422
    assert (await client.post(f"{API}/tracking/position", json=pos(alloc, captured_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()), headers=H(driver))).status_code == 422
    assert (await client.post(f"{API}/tracking/position", json=pos(alloc, captured_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()), headers=H(driver))).status_code == 422
    assert await stored(a["org"]) == 0
    assert (await client.post(f"{API}/tracking/position", json=pos(alloc), headers=H(driver))).status_code == 202
    r = await client.post(f"{API}/tracking/position", json=pos(alloc, latitude=13.02), headers=H(driver))
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_frequent"
    limiter._mem.clear()
    assert (await client.post(f"{API}/tracking/position", json=pos(alloc, latitude=13.03), headers=H(driver))).status_code == 202
    assert await stored(a["org"]) == 1                                                                 # upsert, not append
    assert await count("SELECT count(*) FROM vehicle_location_history WHERE organization_id=:o", o=a["org"]) == 0   # history off by default


async def test_staff_view_employee_view_and_isolation(client, world):
    a, b = world.a, world.b
    await enable_all(a["org"], employee_view=False)
    alloc, driver = await in_progress(client, a)
    await client.post(f"{API}/tracking/position", json=pos(alloc, latitude=13.05), headers=H(driver))
    v = (await client.get(f"{API}/tracking/vehicles", headers=H(a["dispatcher"]))).json()
    assert v["enabled"] and len(v["vehicles"]) == 1 and v["vehicles"][0]["latitude"] == 13.05 and v["vehicles"][0]["stale"] is False
    vid = v["vehicles"][0]["vehicle_id"]
    assert (await client.get(f"{API}/tracking/vehicles/{vid}", headers=H(a["admin"]))).status_code == 200
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='tracking.vehicle_viewed'", o=a["org"]) == 1
    # other org sees nothing and cannot address the vehicle
    await enable_all(b["org"])
    assert (await client.get(f"{API}/tracking/vehicles", headers=H(b["dispatcher"]))).json()["vehicles"] == []
    assert (await client.get(f"{API}/tracking/vehicles/{vid}", headers=H(b["dispatcher"]))).status_code == 404
    # employee on the trip: only if the org enabled employee view
    async with system_session() as conn:
        uid = await conn.scalar(text("SELECT e.user_id FROM trip_passengers p JOIN employees e ON e.id=p.employee_id WHERE p.allocation_id=:a LIMIT 1"), {"a": uuid.UUID(alloc)})
    pax = next(e for e in a["employee_actors"] if e.id == uid)
    bystander = next(e for e in a["employee_actors"] if e.id != uid)
    assert (await client.get(f"{API}/tracking/my-trip", headers=H(pax))).json()["enabled"] is False
    await set_features(a["org"], employee_view=True)
    mine = (await client.get(f"{API}/tracking/my-trip", headers=H(pax))).json()
    assert mine["enabled"] and mine["position"]["latitude"] == 13.05
    assert (await client.get(f"{API}/tracking/my-trip", headers=H(bystander))).json()["position"] is None     # not their cab
    assert (await client.get(f"{API}/tracking/vehicles", headers=H(pax))).status_code == 403                   # employees never get the fleet list


async def test_history_only_when_enabled_and_audited(client, world):
    a = world.a
    await enable_all(a["org"], history=True)
    alloc, driver = await in_progress(client, a)
    await client.post(f"{API}/tracking/position", json=pos(alloc), headers=H(driver))
    vid = (await client.get(f"{API}/tracking/vehicles", headers=H(a["dispatcher"]))).json()["vehicles"][0]["vehicle_id"]
    h = await client.get(f"{API}/tracking/vehicles/{vid}/history", headers=H(a["dispatcher"]))
    assert h.status_code == 200 and len(h.json()["points"]) == 1
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='tracking.history_viewed'", o=a["org"]) == 1
    await set_features(a["org"], history=False)
    assert (await client.get(f"{API}/tracking/vehicles/{vid}/history", headers=H(a["dispatcher"]))).status_code == 403


async def test_position_cleared_on_completion_and_when_disabled(client, world):
    a = world.a
    await enable_all(a["org"])
    allocs = await committed_allocations(client, a)
    alloc, driver = await in_progress(client, a, 0, allocs)
    await client.post(f"{API}/tracking/position", json=pos(alloc), headers=H(driver))
    assert await stored(a["org"]) == 1
    assert (await client.post(f"{API}/allocations/{alloc}/complete", headers=H(driver))).status_code == 200
    assert await stored(a["org"]) == 0
    # turning the setting off through the admin API also wipes live rows
    alloc2, driver2 = await in_progress(client, a, 1, allocs)
    limiter._mem.clear()
    await client.post(f"{API}/tracking/position", json=pos(alloc2), headers=H(driver2))
    assert await stored(a["org"]) == 1
    r = await client.put(f"{API}/admin/settings", json={"live_tracking_enabled": False, "confirm": True}, headers=H(a["admin"]))
    assert r.status_code == 200 and await stored(a["org"]) == 0


async def test_database_trigger_blocks_position_when_off(client, world):
    a = world.a
    alloc = (await committed_allocations(client, a))[0]["id"]
    async with system_session() as conn:
        vid = await conn.scalar(text("SELECT vehicle_id FROM trip_allocations WHERE id=:a"), {"a": uuid.UUID(alloc)})
    ins = text("INSERT INTO vehicle_last_position (organization_id, vehicle_id, allocation_id, latitude, longitude, captured_at) VALUES (:o,:v,:a,1,1,now())")
    with pytest.raises(DBAPIError, match="(?i)live tracking"):
        async with system_session() as conn:
            await conn.execute(ins, {"o": a["org"], "v": vid, "a": uuid.UUID(alloc)})
    assert await stored(a["org"]) == 0
