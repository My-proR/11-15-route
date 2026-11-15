import uuid
from datetime import timedelta
from sqlalchemy import text
from app.db import system_session
from app.services import dispatch as svc
from tests.conftest import API, H, TOMORROW
from tests.helpers import actor_for_driver, committed_allocations, count


async def _offered_driver(client, a, alloc):
    r = await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))
    assert r.status_code == 200, r.text
    return r.json()["assignment"]


async def test_leave_approval_finds_affected_trips_and_offers_replacement_pending_acceptance(client, world):
    a = world.a
    allocs = await committed_allocations(client, a)
    alloc = allocs[0]["id"]
    asg = await _offered_driver(client, a, alloc)
    driver = await actor_for_driver(a, asg["driver_id"])
    assert (await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": True}, headers=H(driver))).status_code == 200
    r = await client.post(f"{API}/driver-leave", json={"starts_on": str(TOMORROW), "ends_on": str(TOMORROW + timedelta(days=1)), "reason": "family"}, headers=H(driver))
    assert r.status_code == 201, r.text
    leave = r.json()
    assert leave["status"] == "requested"
    assert (await client.post(f"{API}/driver-leave", json={"starts_on": str(TOMORROW), "ends_on": str(TOMORROW), "reason": "dup"}, headers=H(driver))).status_code == 409   # overlap
    res = await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": True}, headers=H(a["dispatcher"]))
    assert res.status_code == 200, res.text
    out = res.json()
    assert [x["allocation_id"] for x in out["affected_allocations"]] == [alloc] and out["affected_allocations"][0]["outcome"] == "offered"
    detail = (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()
    assert detail["status"] == "PENDING_DRIVER_ACCEPTANCE"                                    # NOT finalized until the replacement accepts
    new_asg = [x for x in detail["assignments"] if x["status"] == "PENDING"][0]
    assert new_asg["driver_id"] != asg["driver_id"] and new_asg["replacement_for_leave_id"] == leave["id"]
    assert [x["status"] for x in detail["assignments"] if x["id"] == asg["id"]] == ["CANCELLED"]
    # the driver on leave can no longer be offered work on those days
    cands = (await client.get(f"{API}/allocations/{alloc}/eligible-drivers", headers=H(a["dispatcher"]))).json()["candidates"]
    assert asg["driver_id"] not in [c["driver_id"] for c in cands]
    rep = await actor_for_driver(a, new_asg["driver_id"])
    assert (await client.post(f"{API}/driver-assignments/{new_asg['id']}/respond", json={"accept": True}, headers=H(rep))).status_code == 200
    assert (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()["status"] == "DRIVER_ACCEPTED"
    assert (await client.post(f"{API}/allocations/{alloc}/confirm", headers=H(a["dispatcher"]))).json()["status"] == "CONFIRMED"


async def test_replacement_decline_reopens_then_flags_dispatcher_when_none_left(client, world):
    a = world.a
    allocs = await committed_allocations(client, a)
    assert len(allocs) == 2
    first = allocs[0]["id"]
    asg = await _offered_driver(client, a, first)
    await _offered_driver(client, a, allocs[1]["id"])
    await actor_for_driver(a, asg["driver_id"])
    leave = (await client.post(f"{API}/driver-leave", json={"driver_id": asg["driver_id"], "starts_on": str(TOMORROW), "ends_on": str(TOMORROW), "reason": "sick"}, headers=H(a["dispatcher"]))).json()
    out = (await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": True}, headers=H(a["dispatcher"]))).json()
    assert out["affected_allocations"][0]["outcome"] == "offered"
    pending = (await client.get(f"{API}/allocations/{first}", headers=H(a["dispatcher"]))).json()
    cur = [x for x in pending["assignments"] if x["status"] == "PENDING"][0]
    rep = await actor_for_driver(a, cur["driver_id"])
    r = await client.post(f"{API}/driver-assignments/{cur['id']}/respond", json={"accept": False}, headers=H(rep))
    assert r.json()["status"] == "DECLINED"
    final = (await client.get(f"{API}/allocations/{first}", headers=H(a["dispatcher"]))).json()
    assert final["status"] == "REQUIRES_REASSIGNMENT"                                           # nobody else eligible
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='assignment.no_replacement'", o=a["org"]) >= 1


async def test_unanswered_offer_expires_and_chain_continues(client, world):
    a = world.a
    allocs = await committed_allocations(client, a)
    first = allocs[0]["id"]
    asg = await _offered_driver(client, a, first)
    leave = (await client.post(f"{API}/driver-leave", json={"driver_id": asg["driver_id"], "starts_on": str(TOMORROW), "ends_on": str(TOMORROW)}, headers=H(a["dispatcher"]))).json()
    await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": True}, headers=H(a["dispatcher"]))
    async with system_session() as conn:
        await conn.execute(text("UPDATE driver_assignments SET expires_at = now() - interval '1 minute' WHERE organization_id=:o AND allocation_id=:a AND status='PENDING'"), {"o": a["org"], "a": uuid.UUID(first)})
        n = await svc.expire_stale_assignments(conn)
    assert n == 1
    detail = (await client.get(f"{API}/allocations/{first}", headers=H(a["dispatcher"]))).json()
    assert "EXPIRED" in [x["status"] for x in detail["assignments"]]
    assert detail["status"] in ("REQUIRES_REASSIGNMENT", "PENDING_DRIVER_ACCEPTANCE")
    pend = [x for x in detail["assignments"] if x["status"] == "PENDING"]
    if pend:
        assert pend[0]["driver_id"] not in {x["driver_id"] for x in detail["assignments"] if x["status"] == "EXPIRED"}
    late = [x for x in detail["assignments"] if x["status"] == "EXPIRED"][0]
    rep = await actor_for_driver(a, late["driver_id"])
    r = await client.post(f"{API}/driver-assignments/{late['id']}/respond", json={"accept": True}, headers=H(rep))
    assert r.status_code == 409                                                                   # too late to accept


async def test_leave_permissions_and_rejection(client, world):
    a = world.a
    d0, d1 = a["driver_actors"][0], a["driver_actors"][1]
    leave = (await client.post(f"{API}/driver-leave", json={"starts_on": str(TOMORROW), "ends_on": str(TOMORROW)}, headers=H(d0))).json()
    assert (await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": True}, headers=H(d0))).status_code == 403     # driver cannot self-approve
    assert (await client.get(f"{API}/driver-leave", headers=H(d1))).json()["total"] == 0                                                   # drivers see only their own
    assert (await client.get(f"{API}/driver-leave", headers=H(world.b["dispatcher"]))).json()["total"] == 0
    assert (await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": False}, headers=H(a["dispatcher"]))).json()["status"] == "rejected"
    assert (await client.post(f"{API}/driver-leave/{leave['id']}/decision", json={"approve": True}, headers=H(a["dispatcher"]))).status_code == 409
    other = (await client.post(f"{API}/driver-leave", json={"driver_id": str(a["drivers"][1]), "starts_on": str(TOMORROW), "ends_on": str(TOMORROW)}, headers=H(d0)))
    assert other.status_code == 403                                                                                                         # not on behalf of someone else
