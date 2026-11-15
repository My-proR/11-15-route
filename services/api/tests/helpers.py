import uuid
from sqlalchemy import text
from app.config import get_settings
from app.db import system_session
from tests.conftest import API, H, TOMORROW


async def set_features(org, *, sos=None, live=None, deploy_sos=None, deploy_live=None, platform_sos=None, platform_live=None, history=None, employee_view=None):
    """Directly sets all three layers (env, platform flag, org setting) for a test."""
    s = get_settings()
    if deploy_sos is not None: s.gps_sos_feature_enabled = deploy_sos
    if deploy_live is not None: s.live_tracking_feature_enabled = deploy_live
    async with system_session() as conn:
        for key, val in (("sos_gps", platform_sos), ("live_tracking", platform_live)):
            if val is not None:
                await conn.execute(text("UPDATE platform_feature_flags SET enabled=:v WHERE key=:k"), {"v": val, "k": key})
        for col, val in (("sos_gps_enabled", sos), ("live_tracking_enabled", live), ("live_tracking_history_enabled", history), ("live_tracking_employee_view", employee_view)):
            if val is not None:
                await conn.execute(text(f"UPDATE organization_settings SET {col}=:v WHERE organization_id=:o"), {"v": val, "o": org})


async def enable_all(org, **kw):
    await set_features(org, sos=True, live=True, deploy_sos=True, deploy_live=True, platform_sos=True, platform_live=True, **kw)


async def dispatch_slot(client, a, *, commit=True, replace=False):
    body = {"service_date": str(TOMORROW), "shift_id": str(a["shift"]), "direction": "pickup", "generate_requests": True, "dry_run": not commit, "replace_proposed": replace}
    return await client.post(f"{API}/dispatch/allocate", json=body, headers={**H(a["dispatcher"]), "Idempotency-Key": uuid.uuid4().hex})


async def committed_allocations(client, a):
    r = await dispatch_slot(client, a)
    assert r.status_code == 200, r.text
    return r.json()["allocations"]


async def drive_to_confirmed(client, a, alloc_id):
    """send -> driver accepts -> dispatcher confirms. Returns the driver's Actor."""
    r = await client.post(f"{API}/allocations/{alloc_id}/send", headers=H(a["dispatcher"]))
    assert r.status_code == 200, r.text
    asg = r.json()["assignment"]
    driver = await actor_for_driver(a, asg["driver_id"])
    r = await client.post(f"{API}/driver-assignments/{asg['id']}/respond", json={"accept": True}, headers=H(driver))
    assert r.status_code == 200, r.text
    r = await client.post(f"{API}/allocations/{alloc_id}/confirm", headers=H(a["dispatcher"]))
    assert r.status_code == 200, r.text
    return driver


async def _driver_user(driver_id):
    async with system_session() as conn:
        return await conn.scalar(text("SELECT user_id FROM drivers WHERE id=:d"), {"d": driver_id})


async def count(sql, **params):
    async with system_session() as conn:
        return await conn.scalar(text(sql), params)


async def actor_for_driver(a, driver_id):
    uid = await _driver_user(uuid.UUID(str(driver_id)))
    return next(d for d in a["driver_actors"] if d.id == uid)
