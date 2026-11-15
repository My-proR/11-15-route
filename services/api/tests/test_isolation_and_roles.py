"""Multi-tenant isolation (API and database level) and server-enforced role permissions."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.db import user_session
from tests.conftest import API, H

EMP = {"employee_code": "X1", "name": "New Person", "work_email": "new.person@example.com"}

async def test_org_a_cannot_list_read_modify_delete_org_b_employees(client, world):
    a, b = world.a, world.b
    listed = (await client.get(f"{API}/employees?page_size=200", headers=H(a["admin"]))).json()
    ids = {i["id"] for i in listed["items"]}
    assert ids == {str(e) for e in a["employees"]} and not ids & {str(e) for e in b["employees"]}
    victim = b["employees"][0]
    assert (await client.get(f"{API}/employees/{victim}", headers=H(a["admin"]))).status_code == 404
    assert (await client.patch(f"{API}/employees/{victim}", json={"name": "Hacked"}, headers=H(a["admin"]))).status_code == 404
    assert (await client.delete(f"{API}/employees/{victim}", headers=H(a["admin"]))).status_code == 404
    name = (await client.get(f"{API}/employees/{victim}", headers=H(b["admin"]))).json()["name"]
    assert name != "Hacked"

async def test_cannot_address_other_organization_via_header(client, world):
    h = {**H(world.a["admin"]), "X-Organization-Id": str(world.b["org"])}
    assert (await client.get(f"{API}/employees", headers=h)).status_code == 403

async def test_cross_org_references_rejected(client, world):
    a, b = world.a, world.b
    # org A admin tries to attach org B's shift / branch to an employee
    r = await client.post(f"{API}/employees", json={**EMP, "shift_id": str(b["shift"])}, headers=H(a["admin"]))
    assert r.status_code == 422, r.text
    r = await client.post(f"{API}/employees", json={**EMP, "employee_code": "X2", "work_email": "x2@example.com", "branch_id": str(b["office"])}, headers=H(a["admin"]))
    assert r.status_code == 422

async def test_allocation_and_sos_not_visible_across_orgs(client, world):
    a, b = world.a, world.b
    r = await client.post(f"{API}/dispatch/allocate", json={"service_date": "2030-01-02", "shift_id": str(a["shift"]), "direction": "pickup", "generate_requests": True, "dry_run": False},
                          headers={**H(a["dispatcher"]), "Idempotency-Key": uuid.uuid4().hex})
    alloc = r.json()["allocations"][0]["id"]
    assert (await client.get(f"{API}/allocations/{alloc}", headers=H(b["dispatcher"]))).status_code == 404
    assert (await client.post(f"{API}/allocations/{alloc}/cancel", json={"reason": "attack"}, headers=H(b["dispatcher"]))).status_code == 404
    assert (await client.get(f"{API}/allocations", headers=H(b["dispatcher"]))).json()["total"] == 0

async def test_database_rls_blocks_cross_tenant_even_without_org_filter(world):
    a, b = world.a, world.b
    async with user_session(a["admin"].id) as conn:
        rows = (await conn.execute(text("SELECT DISTINCT organization_id FROM employees"))).all()      # deliberately no WHERE clause
        assert {r.organization_id for r in rows} == {a["org"]}
        assert (await conn.execute(text("UPDATE employees SET name='x' WHERE organization_id=:o"), {"o": b["org"]})).rowcount == 0
        assert (await conn.execute(text("DELETE FROM vehicles WHERE organization_id=:o"), {"o": b["org"]})).rowcount == 0

async def test_database_rls_rejects_insert_into_other_org(world):
    a, b = world.a, world.b
    with pytest.raises(DBAPIError):
        async with user_session(a["admin"].id) as conn:
            await conn.execute(text("INSERT INTO employees (organization_id, employee_code, name, work_email) VALUES (:o,'Z','Z','z@z.test')"), {"o": b["org"]})

async def test_database_rls_employee_sees_only_self_and_no_other_tables(world):
    a = world.a
    me = a["employee_actors"][0]
    async with user_session(me.id) as conn:
        assert (await conn.execute(text("SELECT count(*) FROM employees"))).scalar() == 1
        assert (await conn.execute(text("SELECT count(*) FROM drivers"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM audit_logs"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM sos_incident_locations"))).scalar() == 0

async def test_no_identity_sees_nothing():
    async with user_session(None) as conn:
        assert (await conn.execute(text("SELECT count(*) FROM employees"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM organizations"))).scalar() == 0

async def test_role_permissions_enforced_on_server(client, world):
    a = world.a
    emp, drv, disp, adm = a["employee_actors"][0], a["driver_actors"][0], a["dispatcher"], a["admin"]
    assert (await client.get(f"{API}/employees", headers=H(emp))).status_code == 403
    assert (await client.get(f"{API}/vehicles", headers=H(drv))).status_code == 403
    assert (await client.post(f"{API}/employees", json=EMP, headers=H(disp))).status_code == 403        # dispatcher cannot manage master data
    assert (await client.post(f"{API}/vehicles", json={"vehicle_code": "Q", "registration_number": "TN01AB1234", "capacity": 4}, headers=H(drv))).status_code == 403
    assert (await client.put(f"{API}/admin/settings", json={"max_trip_minutes": 60}, headers=H(disp))).status_code == 403
    assert (await client.post(f"{API}/dispatch/allocate", json={"service_date": "2030-01-02", "shift_id": str(a["shift"]), "direction": "pickup"}, headers=H(emp))).status_code == 403
    assert (await client.get(f"{API}/audit-logs", headers=H(disp))).status_code == 403
    assert (await client.get(f"{API}/audit-logs", headers=H(adm))).status_code == 200
    assert (await client.post(f"{API}/organizations", json={"name": "Evil", "slug": "evil", "admin_email": "evil@example.com"}, headers=H(adm))).status_code == 403

async def test_token_validation(client, world):
    from tests.conftest import token
    a = world.a["admin"]
    bad = {"Authorization": "Bearer " + token(a.id, secret="wrong-secret-wrong-secret-wrong-1234"), "X-Organization-Id": str(a.org_id)}
    assert (await client.get(f"{API}/me", headers=bad)).status_code == 401
    expired = {"Authorization": "Bearer " + token(a.id, exp_in=-10)}
    assert (await client.get(f"{API}/me", headers=expired)).status_code == 401
    wrong_aud = {"Authorization": "Bearer " + token(a.id, aud="anon")}
    assert (await client.get(f"{API}/me", headers=wrong_aud)).status_code == 401
    unknown = {"Authorization": "Bearer " + token(uuid.uuid4())}
    assert (await client.get(f"{API}/me", headers=unknown)).status_code == 403          # valid JWT but not provisioned

async def test_suspended_organization_loses_access(client, world):
    from app.db import system_session
    async with system_session() as conn:
        await conn.execute(text("UPDATE organizations SET status='suspended' WHERE id=:o"), {"o": world.b["org"]})
    r = await client.get(f"{API}/employees", headers=H(world.b["admin"]))
    assert r.status_code in (403, 400)

async def test_platform_only_endpoints_reject_every_org_role(client, world):
    """Regression: org members (even org_admin) must never pass platform-admin guards."""
    a = world.a
    calls = [("post", f"{API}/organizations", {"name": "Evil", "slug": "evil", "admin_email": "evil@example.com"}),
             ("post", f"{API}/organizations/{a['org']}/status?status=suspended", None),
             ("put", f"{API}/platform/flags/sos_gps", {"enabled": True, "confirm": True}),
             ("get", f"{API}/health/status", None)]
    for actor in (a["admin"], a["dispatcher"], a["driver_actors"][0], a["employee_actors"][0]):
        for method, url, body in calls:
            r = await getattr(client, method)(url, headers=H(actor), **({"json": body} if body is not None else {}))
            assert r.status_code == 403, (actor.role, method, url, r.status_code, r.text)
    assert (await client.get(f"{API}/health/status", headers={"Authorization": H(world.platform)["Authorization"]})).status_code == 200
