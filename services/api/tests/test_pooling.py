from app.services.pooling import Pax, PoolingConfig, VehicleSpec, pool
from app.services.fairness import DriverStats, FairnessConfig, TripLoad, score_candidates
from tests.fakes import GridProvider

OFFICE = (13.00, 80.00)
def px(i, lat, lon, **kw): return Pax(f"r{i}", f"e{i}", (lat, lon), **kw)
CAR4 = [VehicleSpec("v4", 4)]

async def test_nearby_employees_share_cab_and_capacity_respected():
    pax = [px(i, 13.05 + i * 0.002, 80.05) for i in range(6)]
    res = await pool(GridProvider(), OFFICE, pax, PoolingConfig("pickup", max_trip_minutes=120), CAR4)
    assert res.estimate_status == "final"
    assert sum(len(r.pax) for r in res.routes) + len(res.unroutable) == 6
    assert all(len(r.pax) <= 4 for r in res.routes)
    assert len(res.routes) == 2

async def test_far_apart_employees_not_pooled():
    pax = [px(1, 13.05, 80.05), px(2, 12.90, 79.90)]
    res = await pool(GridProvider(), OFFICE, pax, PoolingConfig("pickup", max_detour_ratio=1.2, max_trip_minutes=120), CAR4)
    assert len(res.routes) == 2

async def test_trip_duration_limit_makes_employee_unroutable():
    res = await pool(GridProvider(), OFFICE, [px(1, 13.5, 80.5)], PoolingConfig("pickup", max_trip_minutes=20), CAR4)
    assert res.routes == [] and len(res.unroutable) == 1

async def test_accessibility_requires_capable_vehicle():
    pax = [px(1, 13.02, 80.02, needs=frozenset({"wheelchair"}))]
    assert (await pool(GridProvider(), OFFICE, pax, PoolingConfig("pickup"), CAR4)).unroutable
    ok = await pool(GridProvider(), OFFICE, pax, PoolingConfig("pickup"), [VehicleSpec("w", 4, frozenset({"wheelchair"}))])
    assert ok.routes and not ok.unroutable

async def test_routing_unavailable_is_provisional_without_distances():
    res = await pool(GridProvider(fail=True), OFFICE, [px(1, 13.02, 80.02), px(2, 13.021, 80.02)], PoolingConfig("pickup"), CAR4)
    assert res.estimate_status == "provisional"
    assert all(r.distance_m is None and r.estimate_status == "provisional" for r in res.routes)

async def test_last_drop_policy_prefers_male_last_when_cheap_and_logs_it():
    pax = [px(1, 13.03, 80.03, gender="female"), px(2, 13.031, 80.03, gender="male"), px(3, 13.032, 80.03, gender="female")]
    on = await pool(GridProvider(), OFFICE, pax, PoolingConfig("drop", last_drop_policy=True), CAR4)
    off = await pool(GridProvider(), OFFICE, pax, PoolingConfig("drop", last_drop_policy=False), CAR4)
    assert on.routes[0].notes.get("last_drop_policy") in {"applied", "skipped_infeasible_or_costly", "already_satisfied"}
    assert "last_drop_policy" not in off.routes[0].notes

async def test_large_roster_partitioned_into_bounded_matrices():
    pax = [px(i, 13.0 + (i % 20) * 0.003, 80.0 + (i // 20) * 0.003 + 0.02) for i in range(150)]
    g = GridProvider()
    res = await pool(g, OFFICE, pax, PoolingConfig("pickup", max_trip_minutes=180), [VehicleSpec("v", 6)])
    assert g.calls == 3
    assert sum(len(r.pax) for r in res.routes) + len(res.unroutable) == 150

def test_fairness_prefers_lighter_driver_and_explains():
    ds = [DriverStats("a", planned_km=100, trips=8, duty_minutes=600), DriverStats("b", planned_km=20, trips=2, duty_minutes=120)]
    r = score_candidates(ds, TripLoad(km=15, minutes=40), FairnessConfig())
    assert r[0]["driver_id"] == "b" and r[0]["rank"] == 1 and "planned_km" in r[0]["components"]

def test_local_target_bonus_not_forced_and_excluded_km():
    ds = [DriverStats("a", planned_km=10, trips=1, local_trip_eligible=True, local_trips_today=0),
          DriverStats("b", planned_km=10, trips=1, local_trip_eligible=True, local_trips_today=2)]
    r = score_candidates(ds, TripLoad(km=5, minutes=15, is_local=True), FairnessConfig(local_target_per_day=2))
    assert r[0]["driver_id"] == "a" and r[0]["local_trip_bonus"] > 0
    assert score_candidates(ds, TripLoad(5, 15, is_local=True), FairnessConfig(local_target_per_day=0))[0]["local_trip_bonus"] == 0
    r2 = score_candidates([DriverStats("c", planned_km=50, local_km_in_window=50)], TripLoad(5, 15, is_local=True), FairnessConfig(local_trips_excluded_from_km=True))
    assert r2[0]["components"]["planned_km"] == 0
