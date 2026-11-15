"""Cab/driver continuity: same people on the same shift keep the same cab and, when eligible, the same driver; never at the cost of capacity or availability."""
import uuid
from datetime import timedelta
from types import SimpleNamespace as NS

from sqlalchemy import text
from app.db import system_session
from app.services import continuity as c
from tests.conftest import API, H, TOMORROW
from tests.helpers import committed_allocations, drive_to_confirmed


def spec(i, cap):
    return NS(vehicle_id=i, capacity=cap)


# ---- pure rules -------------------------------------------------------------------------------------------------
def test_vehicle_prefers_history_but_respects_spare_seat_limit():
    fit = [spec("a", 4), spec("b", 4), spec("c", 12)]
    v, why = c.pick_vehicle(fit, 3, {"b": 5}, 3)
    assert v.vehicle_id == "b" and "same cab" in why
    v, why = c.pick_vehicle(fit, 3, {"c": 9}, 3)            # 12-seater for 3 people: too wasteful
    assert v.vehicle_id == "a" and why is None
    v, why = c.pick_vehicle(fit, 3, {}, 3)
    assert v.vehicle_id == "a" and why is None


def test_vehicle_history_ties_prefer_smaller_cab():
    v, _ = c.pick_vehicle([spec("big", 6), spec("small", 4)], 3, {"big": 2, "small": 2}, 3)
    assert v.vehicle_id == "small"


def test_driver_preference_vs_fairness():
    ranked = [{"driver_id": "x", "score": 1.0, "rank": 1}, {"driver_id": "y", "score": 1.2, "rank": 2}, {"driver_id": "z", "score": 3.0, "rank": 3}]
    assert c.pick_driver(ranked, [("y", "regular")]) == ("y", "regular")                 # within slack: sticky wins
    d, why = c.pick_driver(ranked, [("z", "regular")])                                   # far less fair: fairness wins
    assert d == "x" and why is None
    d, why = c.pick_driver(ranked, [("ghost", "regular"), ("y", "history")])             # ineligible preference skipped
    assert d == "y"
    assert c.pick_driver([], [("y", "r")]) == (None, None)
    d, why = c.pick_driver(ranked, [("x", "regular")])
    assert d == "x" and "fairest" in why


# ---- integration --------------------------------------------------------------------------------------------------
async def plan_for(client, a, day, *, commit):
    body = {"service_date": str(day), "shift_id": str(a["shift"]), "direction": "pickup", "generate_requests": True, "dry_run": not commit, "replace_proposed": True}
    r = await client.post(f"{API}/dispatch/allocate", json=body, headers={**H(a["dispatcher"]), "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    return r.json()


async def test_next_day_reuses_same_cab_and_driver(client, world):
    a = world.a
    day2 = TOMORROW + timedelta(days=1)
    allocs = await committed_allocations(client, a)
    for al in allocs:                       # confirm every trip of day 1 so it counts as history
        await drive_to_confirmed(client, a, al["id"])
    async with system_session() as conn:
        used = {str(r.vehicle_id): str(r.driver_id) for r in (await conn.execute(text("SELECT vehicle_id, driver_id FROM trip_allocations WHERE id = ANY(:i)"), {"i": [uuid.UUID(x["id"]) for x in allocs]})).all()}
    plan = (await plan_for(client, a, day2, commit=False))["plan"]
    assert plan["routes"], plan
    same = [r for r in plan["routes"] if r["explanation"]["vehicle"].startswith("same cab")]
    assert same, "no route kept its cab; continuity did not apply"
    for r in same:
        assert r["vehicle_id"] in used
        # the cab's previous driver keeps it unless fairness clearly says otherwise (then the explanation says so)
        assert r["driver_id"] == used[r["vehicle_id"]] or r["explanation"]["driver_choice"] == "fairest eligible driver"
    assert all("driver_choice" in r["explanation"] for r in plan["routes"])
