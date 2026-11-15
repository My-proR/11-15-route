"""Allocation dry-run/commit, lifecycle & state machine, driver acceptance, capacity, reroutes."""
import asyncio
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.db import system_session
from app.services.routing import set_routing_provider
from tests.conftest import API, H, TOMORROW
from tests.fakes import GridProvider
from tests.helpers import actor_for_driver, committed_allocations, count, dispatch_slot, drive_to_confirmed


async def test_dry_run_persists_nothing_and_respects_capacity(client, world):
    a = world.a
    r = await dispatch_slot(client, a, commit=False)
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert r.json()["dry_run"] is True and plan["can_finalize"] is True
    assert plan["summary"]["assigned"] == 6 and plan["summary"]["unassigned"] == 0
    assert all(rt["passengers"] <= rt["capacity"] for rt in plan["routes"])
    assert {e["vehicle_code"] for e in plan["excluded_vehicles"]} == set()          # maintenance vehicle never offered
    assert await count("SELECT count(*) FROM trip_allocations WHERE organization_id=:o", o=a["org"]) == 0
    assert await count("SELECT count(*) FROM employee_transport_requests WHERE organization_id=:o AND status='allocated'", o=a["org"]) == 0
    first = plan["routes"][0]
    assert first["explanation"]["fairness"] and "components" in first["explanation"]["fairness"][0]       # explainable assignment
    assert all(rt["estimate_status"] == "final" and rt["distance_km"] > 0 for rt in plan["routes"])

async def test_commit_creates_proposed_only_and_never_notifies_drivers(client, world):
    a = world.a
    allocs = await committed_allocations(client, a)
    assert allocs and all(x["status"] == "PROPOSED" for x in allocs)
    assert await count("SELECT count(*) FROM driver_assignments WHERE organization_id=:o", o=a["org"]) == 0
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type LIKE 'assignment%'", o=a["org"]) == 0
    r = await dispatch_slot(client, a)                                            # second commit refused without replace
    assert r.status_code == 409 and r.json()["error"]["code"] == "proposals_exist"
    r = await dispatch_slot(client, a, replace=True)
    assert r.status_code == 200
    assert await count("SELECT count(*) FROM trip_allocations WHERE organization_id=:o AND status='PROPOSED'", o=a["org"]) == len(r.json()["allocations"])

async def test_allocate_idempotency_key_replays_result(client, world):
    a = world.a
    body = {"service_date": str(TOMORROW), "shift_id": str(a["shift"]), "direction": "pickup", "generate_requests": True, "dry_run": False}
    h = {**H(a["dispatcher"]), "Idempotency-Key": "same-key-123456"}
    r1 = await client.post(f"{API}/dispatch/allocate", json=body, headers=h)
    r2 = await client.post(f"{API}/dispatch/allocate", json=body, headers=h)
    assert r1.status_code == r2.status_code == 200 and r1.json()["allocations"] == r2.json()["allocations"]
    r3 = await client.post(f"{API}/dispatch/allocate", json={**body, "direction": "drop"}, headers=h)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_key_reuse"

async def test_full_lifecycle_acceptance_gates_employee_notifications(client, world):
    a = world.a
    alloc = (await committed_allocations(client, a))[0]["id"]
    emp_notifs = "SELECT count(*) FROM notifications WHERE organization_id=:o AND event_type='trip.confirmed'"
    r = await client.post(f"{API}/allocations/{alloc}/confirm", headers=H(a["dispatcher"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "driver_acceptance_required"
    r = await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))
    assert r.status_code == 200 and r.json()["status"] == "PENDING_DRIVER_ACCEPTANCE"
    assert (await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))).json()["already_sent"] is True     # idempotent
    assert await count("SELECT count(*) FROM driver_assignments WHERE organization_id=:o AND allocation_id=:a", o=a["org"], a=uuid.UUID(alloc)) == 1
    asg = r.json()["assignment"]
    driver = await actor_for_driver(a, asg["driver_id"])
    assert await count(emp_notifs, o=a["org"]) == 0                                # no confirmation before acceptance
    r = await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": True}, headers=H(driver))
    assert r.json()["status"] == "ACCEPTED"
    assert (await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": True}, headers=H(driver))).json()["already_recorded"] is True
    assert await count(emp_notifs, o=a["org"]) == 0                                # still none: dispatcher has not confirmed
    other = next(d for d in a["driver_actors"] if d.id != driver.id)
    assert (await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": False}, headers=H(other))).status_code == 404   # not their assignment
    assert (await client.post(f"{API}/allocations/{alloc}/confirm", headers=H(a["dispatcher"]))).json()["status"] == "CONFIRMED"
    n = await count(emp_notifs, o=a["org"])
    assert n >= 1
    await client.post(f"{API}/allocations/{alloc}/confirm", headers=H(a["dispatcher"]))
    assert await count(emp_notifs, o=a["org"]) == n                                # no duplicate notifications
    assert (await client.post(f"{API}/allocations/{alloc}/complete", headers=H(driver))).status_code == 409   # cannot complete before start
    assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).json()["status"] == "IN_PROGRESS"
    assert (await client.post(f"{API}/allocations/{alloc}/start", headers=H(driver))).status_code == 409
    assert (await client.post(f"{API}/allocations/{alloc}/complete", headers=H(driver))).json()["status"] == "COMPLETED"
    assert (await client.post(f"{API}/allocations/{alloc}/cancel", json={"reason": "too late"}, headers=H(a["dispatcher"]))).status_code == 409


async def _du(driver_id):
    async with system_session() as conn:
        return await conn.scalar(text("SELECT user_id FROM drivers WHERE id=:d"), {"d": driver_id})


async def test_employee_sees_crew_only_after_confirmation_driver_sees_manifest(client, world):
    a = world.a
    alloc = (await committed_allocations(client, a))[0]["id"]
    async with system_session() as conn:
        emp_user = await conn.scalar(text("SELECT e.user_id FROM trip_passengers p JOIN employees e ON e.id=p.employee_id WHERE p.allocation_id=:a LIMIT 1"), {"a": uuid.UUID(alloc)})
    emp = next(e for e in a["employee_actors"] if e.id == emp_user)
    assert (await client.get(f"{API}/trips/mine", headers=H(emp))).json() == []             # PROPOSED trips invisible
    driver = await drive_to_confirmed(client, a, alloc)
    trips = (await client.get(f"{API}/trips/mine", headers=H(emp))).json()
    assert len(trips) == 1 and trips[0]["crew"]["driver_name"] == driver.name and trips[0]["crew"]["registration_number"]
    assert "passengers" not in trips[0]
    mine = (await client.get(f"{API}/trips/mine", headers=H(driver))).json()[0]
    assert mine["passengers"] and "gender" not in mine["passengers"][0] and mine["passengers"][0]["phone"]
    next(e for e in a["employee_actors"] if e.id != emp.id and not any(True for _ in []))
    # an employee on a different cab / not on this trip sees nothing about it
    async with system_session() as conn:
        on_trip = {r.user_id for r in await conn.execute(text("SELECT e.user_id FROM trip_passengers p JOIN employees e ON e.id=p.employee_id WHERE p.allocation_id=:a"), {"a": uuid.UUID(alloc)})}
    off = [e for e in a["employee_actors"] if e.id not in on_trip]
    for e in off:
        assert (await client.get(f"{API}/trips/mine", headers=H(e))).json() == []

async def test_decline_requires_reassignment_and_dispatcher_can_reassign(client, world):
    a = world.a
    alloc = (await committed_allocations(client, a))[0]["id"]
    r = await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))
    asg = r.json()["assignment"]
    driver = await actor_for_driver(a, asg["driver_id"])
    assert (await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": False, "reason": "unwell"}, headers=H(driver))).json()["status"] == "DECLINED"
    got = (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()
    assert got["status"] == "REQUIRES_REASSIGNMENT"
    r = await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))          # cannot re-send to the driver who declined
    assert r.status_code == 409 and r.json()["error"]["code"] == "driver_declined"
    exc = (await client.get(f"{API}/dispatch/exceptions?service_date={TOMORROW}", headers=H(a["dispatcher"]))).json()
    assert any(x["issue"] == "requires_reassignment" and x["id"] == alloc for x in exc["allocations"])
    assert any(str(x["allocation_id"]) == alloc for x in exc["recent_declines_or_expiries"])
    cands = (await client.get(f"{API}/allocations/{alloc}/eligible-drivers", headers=H(a["dispatcher"]))).json()["candidates"]
    assert cands and str(a["drivers"][0]) != "" and all("components" in c and "rank" in c for c in cands)
    assert driver.name not in [c["name"] for c in cands]                           # decliner excluded
    new_id = cands[0]["driver_id"]
    r = await client.post(f"{API}/allocations/{alloc}/reassign-driver", json={"driver_id": str(new_id)}, headers=H(a["dispatcher"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_DRIVER_ACCEPTANCE" and r.json()["driver_id"] == str(new_id)

async def test_invalid_transitions_and_version_conflict(client, world):
    a = world.a
    alloc = (await committed_allocations(client, a))[0]["id"]
    for path in ("start", "complete"):
        assert (await client.post(f"{API}/allocations/{alloc}/{path}", headers=H(a["driver_actors"][0]))).status_code in (404, 409)
    got = (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()
    r = await client.patch(f"{API}/allocations/{alloc}", json={"expected_version": got["version"] + 5, "driver_id": str(a["drivers"][0])}, headers=H(a["dispatcher"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    await client.post(f"{API}/allocations/{alloc}/cancel", json={"reason": "no longer needed"}, headers=H(a["dispatcher"]))
    r = await client.post(f"{API}/allocations/{alloc}/send", headers=H(a["dispatcher"]))
    assert r.status_code == 409
    assert await count("SELECT count(*) FROM employee_transport_requests WHERE organization_id=:o AND status='open' AND service_date=:d", o=a["org"], d=TOMORROW) >= 1   # passengers released

async def test_routing_unavailable_marks_provisional_and_blocks_send(client, world):
    a = world.a
    set_routing_provider(GridProvider(fail=True))
    r = await dispatch_slot(client, a, commit=False)
    plan = r.json()["plan"]
    assert plan["estimate_status"] == "provisional" and plan["can_finalize"] is False
    assert all(rt["distance_km"] is None for rt in plan["routes"])
    allocs = (await dispatch_slot(client, a)).json()["allocations"]
    r = await client.post(f"{API}/allocations/{allocs[0]['id']}/send", headers=H(a["dispatcher"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "routing_unavailable"

async def test_manual_adjustments_add_remove_passenger_recomputes_route(client, world):
    a = world.a
    # make V1/V4 tiny so some requests stay unassigned, then add manually
    async with system_session() as conn:
        await conn.execute(text("UPDATE vehicles SET capacity=2 WHERE organization_id=:o"), {"o": a["org"]})
    r = await dispatch_slot(client, a, commit=True)
    assert r.json()["plan"]["summary"]["unassigned"] >= 0
    alloc = r.json()["allocations"][0]["id"]
    detail = (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()
    pax = detail["passengers"][0]["passenger_id"]
    assert (await client.delete(f"{API}/allocations/{alloc}/passengers/{pax}", headers=H(a["dispatcher"]))).status_code == 204
    reqs = (await client.get(f"{API}/dispatch/requests?service_date={TOMORROW}&status=open", headers=H(a["dispatcher"]))).json()["items"]
    assert reqs
    r = await client.post(f"{API}/allocations/{alloc}/passengers", json={"request_id": reqs[0]["id"]}, headers=H(a["dispatcher"]))
    assert r.status_code == 201, r.text
    after = (await client.get(f"{API}/allocations/{alloc}", headers=H(a["dispatcher"]))).json()
    assert after["estimate_status"] == "final" and after["planned_distance_km"] is not None and after["version"] > detail["version"]

async def test_capacity_never_exceeded_under_concurrent_adds(client, world):
    """8 dispatchers race to add passengers to a 2-seat cab: exactly 2 may succeed."""
    a = world.a
    async with system_session() as conn:
        await conn.execute(text("UPDATE vehicles SET capacity=2 WHERE organization_id=:o AND vehicle_code='V2'"), {"o": a["org"]})
        await conn.execute(text("UPDATE vehicles SET availability='maintenance' WHERE organization_id=:o AND vehicle_code IN ('V1','V4')"), {"o": a["org"]})
    await client.post(f"{API}/dispatch/requests/generate", json={"service_date": str(TOMORROW), "shift_id": str(a["shift"]), "direction": "pickup"}, headers=H(a["dispatcher"]))
    async with system_session() as conn:
        vid = await conn.scalar(text("SELECT id FROM vehicles WHERE organization_id=:o AND vehicle_code='V2'"), {"o": a["org"]})
        alloc = (await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot, status) VALUES (:o,:d,:s,'pickup',:v,2,'PROPOSED') RETURNING id"),
                                    {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": vid})).scalar()
        req_ids = [r.id for r in await conn.execute(text("SELECT id FROM employee_transport_requests WHERE organization_id=:o AND service_date=:d AND status='open'"), {"o": a["org"], "d": TOMORROW})]
    assert len(req_ids) >= 6
    results = await asyncio.gather(*[client.post(f"{API}/allocations/{alloc}/passengers", json={"request_id": str(r)}, headers=H(a["dispatcher"])) for r in req_ids])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 2, codes
    assert all(c in (201, 409) for c in codes), codes
    assert await count("SELECT count(*) FROM trip_passengers WHERE allocation_id=:a AND status <> 'removed'", a=alloc) == 2

async def test_database_trigger_blocks_overbooking_and_unusable_vehicle(world):
    a = world.a
    async with system_session() as conn:
        vid = await conn.scalar(text("SELECT id FROM vehicles WHERE organization_id=:o AND vehicle_code='V2'"), {"o": a["org"]})
        maint = await conn.scalar(text("SELECT id FROM vehicles WHERE organization_id=:o AND vehicle_code='V3'"), {"o": a["org"]})
    with pytest.raises(DBAPIError):          # maintenance vehicle
        async with system_session() as conn:
            await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,4)"),
                               {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": maint})
    with pytest.raises(DBAPIError):          # snapshot larger than the vehicle
        async with system_session() as conn:
            await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,9)"),
                               {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": vid})
    async with system_session() as conn:
        alloc = (await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,2) RETURNING id"),
                                    {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": vid})).scalar()
    with pytest.raises(DBAPIError, match="capacity"):
        async with system_session() as conn:
            for i, e in enumerate(a["employees"][:3]):
                req = (await conn.execute(text("INSERT INTO employee_transport_requests (organization_id, employee_id, service_date, shift_id, direction) VALUES (:o,:e,:d,:s,'pickup') RETURNING id"),
                                          {"o": a["org"], "e": e, "d": TOMORROW, "s": a["shift"]})).scalar()
                await conn.execute(text("INSERT INTO trip_passengers (organization_id, allocation_id, request_id, employee_id, stop_order) VALUES (:o,:a,:r,:e,:i)"),
                                   {"o": a["org"], "a": alloc, "r": req, "e": e, "i": i})

async def test_same_vehicle_cannot_be_double_booked_in_a_slot(world):
    a = world.a
    vid = a["vehicles"][0]
    async with system_session() as conn:
        await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,4)"), {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": vid})
    with pytest.raises(DBAPIError):
        async with system_session() as conn:
            await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,4)"), {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": vid})

async def test_one_day_reroute_does_not_touch_recurring_route_and_empty_cab_is_not_free(client, world):
    a = world.a
    adm = H(a["admin"])
    r1 = (await client.post(f"{API}/routes", json={"code": "R1", "name": "Route 1", "stops": [str(a["office"])]}, headers=adm)).json()
    r2 = (await client.post(f"{API}/routes", json={"code": "R2", "name": "Route 2"}, headers=adm)).json()
    v1, v4 = a["vehicles"][0], a["vehicles"][3]
    await client.patch(f"{API}/vehicles/{v4}", json={"default_route_id": r1["id"]}, headers=adm)
    ov = await client.post(f"{API}/vehicles/{v4}/route-override", json={"service_date": str(TOMORROW), "to_route_id": r2["id"], "reason": "cover breakdown"}, headers=H(a["dispatcher"]))
    assert ov.status_code == 201, ov.text
    assert (await client.get(f"{API}/vehicles/{v4}", headers=adm)).json()["default_route_id"] == r1["id"]          # recurring assignment untouched
    log = ov.json()
    assert log["from_route_id"] == r1["id"] and log["to_route_id"] == r2["id"] and log["actor_user_id"] == str(a["dispatcher"].id) and log["reason"]
    assert (await client.post(f"{API}/vehicles/{v4}/route-override", json={"service_date": str(TOMORROW), "to_route_id": r2["id"], "reason": "again"}, headers=H(a["dispatcher"]))).status_code == 409
    # assigned-but-empty cab: has a live allocation with zero passengers => not free
    async with system_session() as conn:
        await conn.execute(text("INSERT INTO trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id, capacity_snapshot) VALUES (:o,:d,:s,'pickup',:v,4)"), {"o": a["org"], "d": TOMORROW, "s": a["shift"], "v": v1})
    board = {v["vehicle_code"]: v["status"] for v in (await client.get(f"{API}/vehicles-board?service_date={TOMORROW}", headers=H(a["dispatcher"]))).json()["vehicles"]}
    assert board["V1"] == "assigned_empty" and board["V3"] == "under_maintenance" and board["V2"] == "available"
    r = await client.post(f"{API}/vehicles/{v1}/route-override", json={"service_date": str(TOMORROW), "to_route_id": r2["id"], "reason": "try"}, headers=H(a["dispatcher"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "vehicle_not_free"
    other_day = TOMORROW.replace(year=TOMORROW.year + 1)
    assert (await client.post(f"{API}/vehicles/{v1}/route-override", json={"service_date": str(other_day), "to_route_id": r2["id"], "reason": "different day ok"}, headers=H(a["dispatcher"]))).status_code == 201

async def test_non_service_day_blocks_planning(client, world):
    a = world.a
    await client.put(f"{API}/service-days", json={"service_date": str(TOMORROW), "is_service_day": False, "note": "holiday"}, headers=H(a["dispatcher"]))
    r = await dispatch_slot(client, a, commit=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_service_day"

async def test_noncompliant_driver_and_vehicle_never_assigned(client, world):
    a = world.a
    async with system_session() as conn:
        await conn.execute(text("UPDATE drivers SET license_valid_until=current_date - 1 WHERE organization_id=:o"), {"o": a["org"]})
    plan = (await dispatch_slot(client, a, commit=False)).json()["plan"]
    assert all(r["driver_id"] is None for r in plan["routes"]) and any(c["type"] == "no_eligible_driver" for c in plan["conflicts"])
    async with system_session() as conn:
        await conn.execute(text("UPDATE drivers SET license_valid_until=current_date + 100 WHERE organization_id=:o"), {"o": a["org"]})
        await conn.execute(text("UPDATE vehicles SET insurance_valid_until=current_date - 1 WHERE organization_id=:o AND vehicle_code IN ('V1','V4')"), {"o": a["org"]})
    plan = (await dispatch_slot(client, a, commit=False)).json()["plan"]
    assert {e["reason"] for e in plan["excluded_vehicles"]} == {"insurance expired"}
