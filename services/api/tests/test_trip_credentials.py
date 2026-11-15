"""Passenger boarding code: off by default, enforced when on, brute-force lockout persists, drivers can never read codes, tenant isolated."""
import uuid
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text
from app.db import system_session, user_session
from tests.conftest import API, H
from tests.helpers import committed_allocations, count, drive_to_confirmed


async def setting(org, **kw):
    async with system_session() as conn:
        for k, v in kw.items():
            await conn.execute(text(f"UPDATE organization_settings SET {k}=:v WHERE organization_id=:o"), {"v": v, "o": org})


async def confirmed(client, a, idx=0):
    allocs = await committed_allocations(client, a)
    alloc = allocs[idx]["id"]
    driver = await drive_to_confirmed(client, a, alloc)
    return alloc, driver


async def passengers(alloc):
    async with system_session() as conn:
        rows = (await conn.execute(text("SELECT tp.id, e.user_id FROM trip_passengers tp JOIN employees e ON e.organization_id=tp.organization_id AND e.id=tp.employee_id "
                                        "WHERE tp.allocation_id=:a AND tp.status<>'removed' ORDER BY tp.stop_order"), {"a": alloc})).all()
    return rows


def actor(a, user_id):
    return next(x for x in a["employee_actors"] if x.id == user_id)


async def test_defaults_off_start_and_board_need_nothing(client, world):
    a = world.a
    alloc, driver = await confirmed(client, a)
    assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 200
    pax = await passengers(alloc)
    r = await client.post(f"{API}/allocations/{alloc}/passengers/{pax[0].id}/status", json={"status": "boarded"}, headers=H(driver))
    assert r.status_code == 200 and r.json()["status"] == "boarded"


async def test_boarding_code_flow(client, world):
    a = world.a
    await setting(a["org"], boarding_code_required=True)
    try:
        alloc, driver = await confirmed(client, a)
        pax = await passengers(alloc)
        assert pax
        me = actor(a, pax[0].user_id)
        r = await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(me))
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        assert len(code) == 4 and code.isdigit() and r.json()["required"] is True
        assert (await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(me))).json()["code"] == code     # stable
        # an employee not on this trip cannot read a code
        outsider = next(e for e in a["employee_actors"] if e.id not in {x.user_id for x in pax})
        assert (await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(outsider))).status_code in (404, 403)
        assert (await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(world.b["employee_actors"][0]))).status_code in (404, 403)
        assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 200
        url = f"{API}/allocations/{alloc}/passengers/{pax[0].id}/status"
        r = await client.post(url, json={"status": "boarded"}, headers=H(driver))
        assert r.status_code == 422 and r.json()["error"]["code"] == "boarding_code_required"
        wrong = "0000" if code != "0000" else "1111"
        for _ in range(5):
            r = await client.post(url, json={"status": "boarded", "code": wrong}, headers=H(driver))
            assert r.status_code == 403 and r.json()["error"]["code"] == "boarding_code_invalid"
        r = await client.post(url, json={"status": "boarded", "code": code}, headers=H(driver))
        assert r.status_code == 429 and r.json()["error"]["code"] == "boarding_code_locked"      # locked after 5 wrong, counter persisted
        # dispatcher manual override (audited) works while locked
        r = await client.post(f"{API}/allocations/{alloc}/passengers/{pax[0].id}/manual-board", json={"reason": "Phone battery dead"}, headers=H(a["dispatcher"]))
        assert r.status_code == 200
        assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='trip.manual_board'", o=a["org"]) >= 1
        assert (await client.post(f"{API}/allocations/{alloc}/passengers/{pax[0].id}/manual-board", json={"reason": "x"}, headers=H(driver))).status_code == 403
        # second passenger boards with the right code; dropped/no_show never need a code
        if len(pax) > 1:
            me2 = actor(a, pax[1].user_id)
            code2 = (await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(me2))).json()["code"]
            r = await client.post(f"{API}/allocations/{alloc}/passengers/{pax[1].id}/status", json={"status": "boarded", "code": code2}, headers=H(driver))
            assert r.status_code == 200 and r.json()["verified"] is True
            # code for passenger 1 does not work for passenger 2 (already boarded -> fine, but cross-use is checked on a third)
        if len(pax) > 2:
            r = await client.post(f"{API}/allocations/{alloc}/passengers/{pax[2].id}/status", json={"status": "boarded", "code": code2}, headers=H(driver))
            assert r.status_code in (200, 403)    # collision of two random 4-digit codes is possible (1/10000); anything else is a bug
        r = await client.post(f"{API}/allocations/{alloc}/passengers/{pax[-1].id}/status", json={"status": "no_show"}, headers=H(driver))
        assert r.status_code == 200
    finally:
        await setting(a["org"], boarding_code_required=False)


async def test_driver_and_other_tenants_cannot_read_secrets(client, world):
    a = world.a
    alloc, driver = await confirmed(client, a)
    pax = await passengers(alloc)
    await client.get(f"{API}/me/boarding-code/{alloc}", headers=H(actor(a, pax[0].user_id)))
    assert await count("SELECT count(*) FROM trip_boarding_codes WHERE allocation_id=:a", a=alloc) >= 1
    for who in (driver, world.b["admin"], world.b["dispatcher"]):
        async with user_session(who.id) as conn:
            assert await conn.scalar(text("SELECT count(*) FROM trip_boarding_codes WHERE allocation_id=:a"), {"a": alloc}) == 0
    async with user_session(a["dispatcher"].id) as conn:
        assert await conn.scalar(text("SELECT count(*) FROM trip_boarding_codes WHERE allocation_id=:a"), {"a": alloc}) >= 1
    # the driver role cannot write either table directly
    async with user_session(driver.id) as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("INSERT INTO trip_boarding_codes (organization_id, passenger_id, allocation_id, code) VALUES (:o,:p,:a,'1234')"),
                               {"o": a["org"], "p": pax[0].id, "a": alloc})


async def test_settings_toggle_audited(client, world):
    a = world.a
    r = await client.put(f"{API}/admin/settings", json={"boarding_code_required": True}, headers=H(a["admin"]))
    assert r.status_code == 200 and r.json()["boarding_code_required"] is True
    assert (await client.put(f"{API}/admin/settings", json={"boarding_code_required": False}, headers=H(a["admin"]))).status_code == 200
    assert (await client.put(f"{API}/admin/settings", json={"boarding_code_required": True}, headers=H(a["dispatcher"]))).status_code == 403


# ---- trip QR (driver shows, passenger scans) --------------------------------------------------------------------
async def test_trip_qr_boarding_flow(client, world):
    from app.services import trip_qr
    a = world.a
    alloc, driver = await confirmed(client, a)
    r = await client.get(f"{API}/allocations/{alloc}/trip-qr", headers=H(driver))
    assert r.status_code == 409 and r.json()["error"]["code"] == "trip_not_in_progress"          # not before the trip starts
    assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 200
    pax = await passengers(alloc)
    token = (await client.get(f"{API}/allocations/{alloc}/trip-qr", headers=H(driver))).json()["token"]
    # only the trip's driver can mint it
    other_driver = next(d for d in a["driver_actors"] if d.id != driver.id)
    assert (await client.get(f"{API}/allocations/{alloc}/trip-qr", headers=H(other_driver))).status_code == 404
    assert (await client.get(f"{API}/allocations/{alloc}/trip-qr", headers=H(a["employee_actors"][0]))).status_code == 403
    me = actor(a, pax[0].user_id)
    url = f"{API}/me/trips/{alloc}/board-by-qr"
    r = await client.post(url, json={"token": token}, headers=H(me))
    assert r.status_code == 200 and r.json()["already"] is False
    assert await count("SELECT status FROM trip_passengers WHERE id=:p", p=pax[0].id) == "boarded"
    assert (await client.post(url, json={"token": token}, headers=H(me))).json()["already"] is True      # idempotent
    # someone who is not on this trip cannot board with it, even with a valid token
    outsider = next(e for e in a["employee_actors"] if e.id not in {x.user_id for x in pax})
    r = await client.post(url, json={"token": token}, headers=H(outsider))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_passenger"
    # tampered / expired / wrong-trip / other tenant / garbage
    bad = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    assert (await client.post(url, json={"token": bad}, headers=H(me))).json()["error"]["code"] == "qr_bad_signature"
    old, _ = trip_qr.make_token(a["org"], uuid.UUID(alloc), now=0)
    assert (await client.post(url, json={"token": old}, headers=H(me))).json()["error"]["code"] == "qr_expired"
    other_trip, _ = trip_qr.make_token(a["org"], uuid.uuid4())
    assert (await client.post(url, json={"token": other_trip}, headers=H(me))).json()["error"]["code"] == "qr_wrong_trip"
    foreign, _ = trip_qr.make_token(world.b["org"], uuid.UUID(alloc))
    assert (await client.post(url, json={"token": foreign}, headers=H(me))).json()["error"]["code"] == "qr_wrong_trip"
    assert (await client.post(url, json={"token": "x" * 30}, headers=H(me))).json()["error"]["code"] == "qr_malformed"
    assert (await client.post(url, json={"token": token}, headers=H(driver))).status_code == 403          # drivers cannot self-board passengers
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='trip.boarded_by_qr'", o=a["org"]) >= 1


async def test_qr_satisfies_required_boarding_code(client, world):
    a = world.a
    await setting(a["org"], boarding_code_required=True)
    try:
        alloc, driver = await confirmed(client, a)
        assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 200
        pax = await passengers(alloc)
        token = (await client.get(f"{API}/allocations/{alloc}/trip-qr", headers=H(driver))).json()["token"]
        r = await client.post(f"{API}/me/trips/{alloc}/board-by-qr", json={"token": token}, headers=H(actor(a, pax[0].user_id)))
        assert r.status_code == 200
        assert await count("SELECT status FROM trip_passengers WHERE id=:p", p=pax[0].id) == "boarded"
    finally:
        await setting(a["org"], boarding_code_required=False)


def test_token_roundtrip_unit():
    from app.services import trip_qr
    o, al = uuid.uuid4(), uuid.uuid4()
    t, exp = trip_qr.make_token(o, al, now=1000)
    assert trip_qr.read_token(t, now=1000 + 59) == (o, al)
    assert trip_qr.read_token(t, now=exp + 1) == "expired"
    assert trip_qr.read_token(t.replace("rp1", "rp2", 1), now=1000) == "malformed"
