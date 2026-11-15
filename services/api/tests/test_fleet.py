"""Admin fleet view: routes -> cabs -> trips, coverage counts, driver assignment through the normal offer flow, role + tenant isolation."""
import uuid
from sqlalchemy import text
from app.db import system_session
from tests.conftest import API, H, TOMORROW
from tests.helpers import committed_allocations


async def make_route(org, code, vehicle_ids, default_driver=None):
    async with system_session() as conn:
        rid = (await conn.execute(text("INSERT INTO routes (organization_id, code, name) VALUES (:o,:c,:n) RETURNING id"), {"o": org, "c": code, "n": f"Route {code}"})).scalar()
        for v in vehicle_ids:
            await conn.execute(text("UPDATE vehicles SET default_route_id=:r, default_driver_id=:d WHERE id=:v"), {"r": rid, "d": default_driver, "v": v})
    return rid


async def test_fleet_lists_cabs_drivers_and_trips_by_route(client, world):
    a = world.a
    code = "F" + uuid.uuid4().hex[:5]
    allocs = await committed_allocations(client, a)
    async with system_session() as conn:
        used = [r.vehicle_id for r in (await conn.execute(text("SELECT vehicle_id FROM trip_allocations WHERE id = ANY(:i)"), {"i": [uuid.UUID(x["id"]) for x in allocs]})).all()]
    rid = await make_route(a["org"], code, used[:1], default_driver=a["drivers"][0])
    r = await client.get(f"{API}/fleet/routes", params={"service_date": str(TOMORROW)}, headers=H(a["dispatcher"]))
    assert r.status_code == 200
    row = next(x for x in r.json()["routes"] if x["id"] == str(rid))
    assert row["cabs"] == 1 and row["trips"] >= 1
    d = (await client.get(f"{API}/fleet/routes/{rid}", params={"service_date": str(TOMORROW)}, headers=H(a["admin"]))).json()
    cab = d["cabs"][0]
    assert cab["default_driver"]["id"] == str(a["drivers"][0]) and cab["trips"]
    t = cab["trips"][0]
    assert t["passengers"] >= 1 and "needs_driver" in t
    # assign through the standard flow: eligible list -> reassign sends an offer
    el = (await client.get(f"{API}/allocations/{t['id']}/eligible-drivers", headers=H(a["dispatcher"]))).json()["candidates"]
    if el:
        r = await client.post(f"{API}/allocations/{t['id']}/reassign-driver", json={"driver_id": el[0]["driver_id"], "emergency": False}, headers=H(a["dispatcher"]))
        assert r.status_code == 200, r.text


async def test_unrouted_cabs_and_effective_route_override(client, world):
    a = world.a
    async with system_session() as conn:
        await conn.execute(text("UPDATE vehicles SET default_route_id=NULL, default_driver_id=NULL WHERE organization_id=:o"), {"o": a["org"]})
    s = (await client.get(f"{API}/fleet/routes", params={"service_date": str(TOMORROW)}, headers=H(a["dispatcher"]))).json()
    assert len(s["unrouted_cabs"]) >= 1
    v = a["vehicles"][0]
    rid = await make_route(a["org"], "O" + uuid.uuid4().hex[:5], [])
    r = await client.post(f"{API}/vehicles/{v}/route-override", json={"to_route_id": str(rid), "service_date": str(TOMORROW), "reason": "cover shortage"}, headers=H(a["dispatcher"]))
    assert r.status_code == 201, r.text
    d = (await client.get(f"{API}/fleet/routes/{rid}", params={"service_date": str(TOMORROW)}, headers=H(a["dispatcher"]))).json()
    assert [c["id"] for c in d["cabs"]] == [str(v)] and d["cabs"][0]["overridden_today"] is True
    other_day = (await client.get(f"{API}/fleet/routes/{rid}", params={"service_date": str(TOMORROW + __import__('datetime').timedelta(days=3))}, headers=H(a["dispatcher"]))).json()
    assert other_day["cabs"] == []                               # override is one-day only


async def test_fleet_access_control(client, world):
    a, b = world.a, world.b
    rid = await make_route(a["org"], "X" + uuid.uuid4().hex[:5], [])
    for who in (a["employee_actors"][0], a["driver_actors"][0]):
        assert (await client.get(f"{API}/fleet/routes", headers=H(who))).status_code == 403
        assert (await client.get(f"{API}/fleet/routes/{rid}", headers=H(who))).status_code == 403
    # another tenant's admin cannot see this route
    assert (await client.get(f"{API}/fleet/routes/{rid}", headers=H(b["admin"]))).status_code == 404
    ids = {x["id"] for x in (await client.get(f"{API}/fleet/routes", headers=H(b["admin"]))).json()["routes"]}
    assert str(rid) not in ids
