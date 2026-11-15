"""SOS: alert always works; coordinates stored only when deployment env AND platform flag AND organization setting AND device permission allow."""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.db import system_session
from tests.conftest import API, H
from tests.helpers import count, enable_all, set_features

def fix(**kw):
    base = {"status": "obtained", "latitude": 13.08, "longitude": 80.27, "accuracy_m": 12.0, "captured_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base

def key(): return uuid.uuid4().hex

async def raise_sos(client, actor, location=None, k=None, **extra):
    body = {"message": "help", **extra}
    if location is not None:
        body["location"] = location
    return await client.post(f"{API}/sos", json=body, headers={**H(actor), "Idempotency-Key": k or key()})

async def stored(org):
    return await count("SELECT count(*) FROM sos_incident_locations WHERE organization_id=:o", o=org)

# ---- the full on/off matrix ---------------------------------------------------------------------------
@pytest.mark.parametrize("deploy,platform,org_on,expect_stored,expect_status", [
    (False, True, True, False, "disabled"),      # deployment flag off
    (True, False, True, False, "disabled"),      # platform flag off
    (True, True, False, False, "disabled"),      # organization setting off
    (False, False, False, False, "disabled"),
    (True, True, True, True, "obtained"),        # all three on
])
async def test_gps_matrix(client, world, deploy, platform, org_on, expect_stored, expect_status):
    a = world.a
    await set_features(a["org"], sos=org_on, deploy_sos=deploy, platform_sos=platform)
    emp = a["employee_actors"][0]
    r = await raise_sos(client, emp, fix())
    assert r.status_code == 201, r.text                                           # the alert itself is never blocked
    assert r.json()["location_status"] == expect_status
    assert (await stored(a["org"]) == 1) is expect_stored
    if not expect_stored:
        assert r.json()["location"]["stored"] is False

async def test_default_configuration_collects_no_coordinates(client, world):
    a = world.a
    r = await raise_sos(client, a["employee_actors"][0], fix())
    assert r.status_code == 201 and r.json()["location_status"] == "disabled" and await stored(a["org"]) == 0
    assert (await client.get(f"{API}/features", headers=H(a["employee_actors"][0]))).json()["sos_gps"]["blocked_by"] == "deployment"

async def test_sos_without_location_and_denied_and_unavailable(client, world):
    a = world.a
    await enable_all(a["org"])
    e = a["employee_actors"]
    r = await raise_sos(client, e[0])
    assert r.status_code == 201 and r.json()["location_status"] == "not_requested"
    r = await raise_sos(client, e[1], {"status": "denied"})
    assert r.status_code == 201 and r.json()["location_status"] == "denied"
    r = await raise_sos(client, e[2], {"status": "unavailable"})
    assert r.status_code == 201 and r.json()["location_status"] == "unavailable"
    assert await stored(a["org"]) == 0

async def test_two_step_flow_alert_first_then_location(client, world):
    a = world.a
    await enable_all(a["org"])
    emp = a["employee_actors"][0]
    inc = (await raise_sos(client, emp)).json()
    r = await client.put(f"{API}/sos/{inc['id']}/location", json=fix(accuracy_m=250), headers=H(emp))
    assert r.status_code == 200 and r.json()["location_status"] == "obtained" and r.json()["location"]["accuracy_poor"] is True     # poor accuracy is flagged, never hidden
    detail = (await client.get(f"{API}/sos/{inc['id']}", headers=H(a["dispatcher"]))).json()
    assert detail["location"]["latitude"] == 13.08 and detail["location"]["accuracy_poor"] is True

async def test_invalid_stale_and_future_fixes(client, world):
    a = world.a
    await enable_all(a["org"])
    e = a["employee_actors"]
    assert (await raise_sos(client, e[0], fix(latitude=95))).status_code == 422
    assert (await raise_sos(client, e[0], fix(longitude=-200))).status_code == 422
    assert (await raise_sos(client, e[0], fix(accuracy_m=-1))).status_code == 422
    assert (await raise_sos(client, e[0], {"status": "obtained", "latitude": 1.0})).status_code == 422
    assert (await raise_sos(client, e[0], fix(captured_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()))).status_code == 422
    stale = await raise_sos(client, e[1], fix(captured_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()))
    assert stale.status_code == 201 and stale.json()["location_status"] == "unavailable" and await stored(a["org"]) == 0

async def test_duplicate_sos_requests_create_one_incident(client, world):
    a = world.a
    emp = a["employee_actors"][0]
    k = key()
    r1 = await raise_sos(client, emp, k=k)
    r2 = await raise_sos(client, emp, k=k)
    assert r1.json()["id"] == r2.json()["id"] and r2.json()["duplicate"] is True
    import asyncio
    k2 = key()
    rs = await asyncio.gather(*[raise_sos(client, a["employee_actors"][1], k=k2) for _ in range(6)])
    assert len({r.json()["id"] for r in rs}) == 1
    assert await count("SELECT count(*) FROM sos_incidents WHERE organization_id=:o", o=a["org"]) == 2
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='sos.raised'", o=a["org"]) == 2 * 2    # 2 incidents x 2 staff, no duplicates
    assert (await client.post(f"{API}/sos", json={"message": "x"}, headers=H(emp))).status_code == 422                                    # Idempotency-Key required

async def test_unauthorized_users_cannot_see_incident_coordinates(client, world):
    a, b = world.a, world.b
    await enable_all(a["org"])
    emp, other_emp = a["employee_actors"][0], a["employee_actors"][1]
    inc = (await raise_sos(client, emp, fix())).json()
    iid = inc["id"]
    assert (await client.get(f"{API}/sos/{iid}", headers=H(emp))).status_code == 403                  # even the reporter cannot re-read coordinates via the staff endpoint
    assert (await client.get(f"{API}/sos/{iid}", headers=H(other_emp))).status_code == 403
    assert (await client.get(f"{API}/sos/{iid}", headers=H(a["driver_actors"][0]))).status_code == 403
    assert (await client.get(f"{API}/sos/{iid}", headers=H(b["admin"]))).status_code == 404            # other organization
    listing = (await client.get(f"{API}/sos", headers=H(emp))).json()["items"]
    assert [i["id"] for i in listing] == [iid] and "location" not in listing[0] and "latitude" not in str(listing)
    assert (await client.get(f"{API}/sos", headers=H(other_emp))).json()["total"] == 0                   # others see nothing
    assert (await client.put(f"{API}/sos/{iid}/location", json=fix(), headers=H(other_emp))).status_code == 404   # cannot attach to someone else's incident
    assert (await client.get(f"{API}/sos/{iid}", headers=H(a["dispatcher"]))).status_code == 200
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='sos.location_viewed'", o=a["org"]) == 1      # access is logged
    from app.db import user_session
    async with user_session(other_emp.id) as conn:                                                      # and the database refuses too
        assert (await conn.execute(text("SELECT count(*) FROM sos_incident_locations"))).scalar() == 0
    async with user_session(a["driver_actors"][0].id) as conn:
        assert (await conn.execute(text("SELECT count(*) FROM sos_incident_locations"))).scalar() == 0

async def test_database_trigger_refuses_coordinates_when_switches_off(client, world):
    """Defence in depth: even a buggy/compromised API path cannot insert coordinates while the platform or org switch is off."""
    a = world.a
    inc = (await raise_sos(client, a["employee_actors"][0])).json()["id"]
    ins = text("INSERT INTO sos_incident_locations (organization_id, incident_id, latitude, longitude, captured_at, purge_after) VALUES (:o,:i,1,1,now(),now()+interval '1 day')")
    for sos, platform in ((False, False), (True, False), (False, True)):
        await set_features(a["org"], sos=sos, platform_sos=platform)
        with pytest.raises(DBAPIError, match="SOS GPS is disabled"):
            async with system_session() as conn:
                await conn.execute(ins, {"o": a["org"], "i": uuid.UUID(inc)})
    await set_features(a["org"], sos=True, platform_sos=True)
    async with system_session() as conn:
        await conn.execute(ins, {"o": a["org"], "i": uuid.UUID(inc)})

async def test_acknowledge_resolve_stops_location_and_retention_delete(client, world):
    a = world.a
    await enable_all(a["org"])
    emp = a["employee_actors"][0]
    iid = (await raise_sos(client, emp, fix())).json()["id"]
    assert (await client.post(f"{API}/sos/{iid}/acknowledge", headers=H(a["dispatcher"]))).json()["status"] == "acknowledged"
    assert (await client.post(f"{API}/sos/{iid}/acknowledge", headers=H(a["dispatcher"]))).status_code == 409
    assert await count("SELECT count(*) FROM notifications WHERE recipient_user_id=:u AND event_type='sos.acknowledged'", u=emp.id) == 1
    assert (await client.post(f"{API}/sos/{iid}/resolve", json={"note": "safe"}, headers=H(a["dispatcher"]))).json()["status"] == "resolved"
    assert (await client.put(f"{API}/sos/{iid}/location", json=fix(), headers=H(emp))).status_code == 409          # sharing stopped with the incident
    assert (await client.delete(f"{API}/sos/{iid}/location", headers=H(a["dispatcher"]))).status_code == 403       # only org admin may purge early
    assert (await client.delete(f"{API}/sos/{iid}/location", headers=H(a["admin"]))).status_code == 204
    assert await stored(a["org"]) == 0
    assert (await client.get(f"{API}/sos/{iid}", headers=H(a["dispatcher"]))).json()["location"] is None
    async with system_session() as conn:                                                                          # retention purge
        await conn.execute(text("INSERT INTO sos_incident_locations (organization_id, incident_id, latitude, longitude, captured_at, purge_after) VALUES (:o,:i,1,1,now(),now()-interval '1 day')"), {"o": a["org"], "i": uuid.UUID(iid)})
        from app.worker import purge_expired
        assert (await purge_expired(conn))["sos_locations"] == 1

async def test_driver_sos_during_trip_driver_ack_without_coordinates(client, world):
    from tests.helpers import committed_allocations, drive_to_confirmed
    a = world.a
    await enable_all(a["org"])
    alloc = (await committed_allocations(client, a))[0]["id"]
    driver = await drive_to_confirmed(client, a, alloc)
    await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))
    r = await raise_sos(client, driver, fix(), allocation_id=alloc)
    assert r.status_code == 201 and r.json()["location_status"] == "obtained"
    async with system_session() as conn:
        pax_user = await conn.scalar(text("SELECT e.user_id FROM trip_passengers p JOIN employees e ON e.id=p.employee_id WHERE p.allocation_id=:a LIMIT 1"), {"a": uuid.UUID(alloc)})
    pax = next(e for e in a["employee_actors"] if e.id == pax_user)
    pr = await raise_sos(client, pax, fix(), allocation_id=alloc)
    assert pr.status_code == 201
    assert (await client.post(f"{API}/sos/{pr.json()['id']}/acknowledge", headers=H(driver))).status_code == 200      # driver of the trip may acknowledge
    assert (await client.get(f"{API}/sos/{pr.json()['id']}", headers=H(driver))).status_code == 403                 # but never reads coordinates
    assert (await raise_sos(client, driver, allocation_id=str(uuid.uuid4()))).status_code == 404
    assert (await client.get(f"{API}/reports/sos-incidents", headers=H(a["dispatcher"]))).json()["rows"]
    assert "latitude" not in (await client.get(f"{API}/reports/sos-incidents", headers=H(a["dispatcher"]))).text
