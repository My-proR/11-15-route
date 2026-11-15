"""Drivers switch themselves available/unavailable; dispatch only offers trips to available drivers."""
from sqlalchemy import text
from app.db import system_session
from tests.conftest import API, H
from tests.helpers import committed_allocations, count, dispatch_slot, drive_to_confirmed


async def test_driver_toggle_roundtrip_and_audit(client, world):
    a = world.a
    d = a["driver_actors"][0]
    r = await client.get(f"{API}/me/availability", headers=H(d))
    assert r.status_code == 200 and r.json()["available"] is True
    r = await client.put(f"{API}/me/availability", json={"available": False}, headers=H(d))
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["updated_at"]
    assert await count("SELECT is_available FROM drivers WHERE user_id=:u", u=d.id) is False
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='driver.availability_changed'", o=a["org"]) >= 1
    r = await client.put(f"{API}/me/availability", json={"available": True}, headers=H(d))
    assert r.json()["available"] is True


async def test_only_drivers_and_only_own_row(client, world):
    a = world.a
    for who in (a["employee_actors"][0], a["dispatcher"], a["admin"]):
        assert (await client.put(f"{API}/me/availability", json={"available": False}, headers=H(who))).status_code == 403
    before = await count("SELECT count(*) FROM drivers WHERE organization_id=:o AND is_available", o=a["org"])
    await client.put(f"{API}/me/availability", json={"available": False}, headers=H(a["driver_actors"][1]))
    assert await count("SELECT count(*) FROM drivers WHERE organization_id=:o AND is_available", o=a["org"]) == before - 1       # exactly one row changed
    assert await count("SELECT count(*) FROM drivers WHERE organization_id=:o AND is_available", o=world.b["org"]) == len(world.b["drivers"])
    await client.put(f"{API}/me/availability", json={"available": True}, headers=H(a["driver_actors"][1]))
    assert (await client.put(f"{API}/me/availability", json={"available": "x", "extra": 1}, headers=H(a["driver_actors"][1]))).status_code == 422


async def test_unavailable_driver_is_not_assigned(client, world):
    a = world.a
    async with system_session() as conn:
        await conn.execute(text("UPDATE drivers SET is_available=true WHERE organization_id=:o"), {"o": a["org"]})
    off = a["driver_actors"][0]
    assert (await client.put(f"{API}/me/availability", json={"available": False}, headers=H(off))).status_code == 200
    try:
        r = await dispatch_slot(client, a, commit=False)
        assert r.status_code == 200
        off_id = str(a["drivers"][0])
        for route in r.json()["plan"]["routes"]:
            assert route["driver_id"] != off_id
    finally:
        await client.put(f"{API}/me/availability", json={"available": True}, headers=H(off))


async def test_cannot_go_off_duty_mid_trip(client, world):
    a = world.a
    allocs = await committed_allocations(client, a)
    driver = await drive_to_confirmed(client, a, allocs[0]["id"])
    assert (await client.post(f"{API}/allocations/{allocs[0]['id']}/start", headers=H(driver))).status_code == 200
    r = await client.put(f"{API}/me/availability", json={"available": False}, headers=H(driver))
    assert r.status_code == 409 and r.json()["error"]["code"] == "trip_in_progress"
