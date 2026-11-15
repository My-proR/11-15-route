import io, time, uuid
import jwt
import openpyxl
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
import pytest
from app.config import get_settings
from app.db import system_session
from app.services.realtime import Hub, hub
from tests.conftest import API, H
from tests.helpers import committed_allocations, count

CSV = ("Employee ID,Full Name,Email,Mobile,Home Address,Lat,Lon,Branch,Shift\n"
       "N001,Asha Rao,asha@example.com,9000000001,12 Main Rd,13.01,80.02,HQ,Morning\n"
       "N002,Bala K,bala@example.com,9000000002,34 Lake St,13.02,80.03,HQ,Morning\n"
       "N003,Bad Email,not-an-email,9000000003,x,13.0,80.0,HQ,Morning\n"
       "N001,Dup In File,dup@example.com,,y,13.0,80.0,HQ,Morning\n"
       "N004,Bad Lat,bl@example.com,,z,abc,80.0,HQ,Morning\n")


def files(content, name="emps.csv"):
    return {"file": (name, content if isinstance(content, bytes) else content.encode(), "text/csv")}


async def test_csv_preview_commit_duplicates_and_retry(client, world):
    a = world.a
    pr = await client.post(f"{API}/employees/import/preview", files=files(CSV), headers=H(a["admin"]))
    assert pr.status_code == 200, pr.text
    j = pr.json()
    assert j["total_rows"] == 5 and j["valid_rows"] == 2 and j["duplicate_rows"] == 1 and j["error_rows"] == 2
    assert {e["row"] for e in j["errors"]} == {4, 5, 6}
    assert await count("SELECT count(*) FROM employees WHERE organization_id=:o AND employee_code LIKE 'N0%'", o=a["org"]) == 0     # preview writes nothing
    c1 = await client.post(f"{API}/employees/import/{j['job_id']}/commit", headers=H(a["admin"]))
    assert c1.status_code == 200 and c1.json()["inserted"] == 2
    c2 = await client.post(f"{API}/employees/import/{j['job_id']}/commit", headers=H(a["admin"]))
    assert c2.json()["already_committed"] is True and c2.json()["inserted"] == 2
    assert await count("SELECT count(*) FROM employees WHERE organization_id=:o AND employee_code LIKE 'N0%'", o=a["org"]) == 2
    # re-import: existing employees are reported as duplicates and are NOT overwritten
    changed = CSV.replace("Asha Rao", "Changed Name")
    j2 = (await client.post(f"{API}/employees/import/preview", files=files(changed), headers=H(a["admin"]))).json()
    assert j2["valid_rows"] == 0 and j2["duplicate_rows"] >= 2
    await client.post(f"{API}/employees/import/{j2['job_id']}/commit", headers=H(a["admin"]))
    assert await count("SELECT name FROM employees WHERE organization_id=:o AND employee_code='N001'", o=a["org"]) == "Asha Rao"
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action IN ('import.previewed','import.committed')", o=a["org"]) >= 3


async def test_import_xlsx_mapping_and_permissions(client, world):
    a, b = world.a, world.b
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Code", "Employee Name", "Work Email", "Pickup"])
    ws.append(["X1", "Xavier", "x1@example.com", "Somewhere"])
    buf = io.BytesIO(); wb.save(buf)
    r = await client.post(f"{API}/employees/import/preview", files=files(buf.getvalue(), "e.xlsx"), headers=H(a["admin"]))
    assert r.status_code == 200 and r.json()["valid_rows"] == 1, r.text
    jid = r.json()["job_id"]
    assert (await client.post(f"{API}/employees/import/preview", files=files(CSV), headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.post(f"{API}/employees/import/{jid}/commit", headers=H(b["admin"]))).status_code == 404            # other tenant's job
    assert (await client.get(f"{API}/employees/import/{jid}", headers=H(b["admin"]))).status_code == 404
    assert (await client.post(f"{API}/employees/import/preview", files=files("a,b\n1,2\n"), headers=H(a["admin"]))).status_code == 422   # required columns missing
    assert (await client.post(f"{API}/employees/import/preview", files=files(b"\x00\x01garbage", "x.xlsx"), headers=H(a["admin"]))).status_code in (400, 422)
    assert (await client.post(f"{API}/employees/import/preview", files=files("x", "x.exe"), headers=H(a["admin"]))).status_code in (400, 415, 422)


async def test_reports_roles_csv_export_injection_and_audit(client, world):
    a, b = world.a, world.b
    await committed_allocations(client, a)
    for name in ("vehicle-utilization", "passenger-utilization", "unassigned-employees", "driver-workloads", "leave-replacements", "cancelled-trips", "sos-incidents", "notification-delivery", "operational-exceptions"):
        r = await client.get(f"{API}/reports/{name}", headers=H(a["dispatcher"]))
        assert r.status_code == 200, (name, r.text)
    assert (await client.get(f"{API}/reports/nope", headers=H(a["dispatcher"]))).status_code == 404
    assert (await client.get(f"{API}/reports/vehicle-utilization", headers=H(a["employee_actors"][0]))).status_code == 403
    assert (await client.get(f"{API}/reports/driver-workloads?format=csv", headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.get(f"{API}/reports/vehicle-utilization?start=2026-01-10&end=2025-01-01", headers=H(a["dispatcher"]))).status_code == 422
    w = (await client.get(f"{API}/reports/driver-workloads", headers=H(a["dispatcher"]))).json()
    assert "Actual kilometres are not recorded" in w["note"] and all(r["actual_km"] is None for r in w["rows"])
    assert (await client.get(f"{API}/reports/vehicle-utilization", headers=H(b["dispatcher"]))).json()["row_count"] == 0
    async with system_session() as conn:
        await conn.execute(text("UPDATE drivers SET name='=cmd|calc' WHERE organization_id=:o AND id=:d"), {"o": a["org"], "d": a["drivers"][0]})
    c = await client.get(f"{API}/reports/driver-workloads?format=csv", headers=H(a["admin"]))
    assert c.status_code == 200 and "'=cmd|calc" in c.text and c.headers["content-type"].startswith("text/csv")
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='report.exported'", o=a["org"]) == 1
    ov = (await client.get(f"{API}/reports", headers=H(a["dispatcher"]))).json()
    assert ov["drivers_active"] == 3 and ov["trips_requiring_reassignment"] == 0


async def test_audit_log_is_append_only_and_tenant_scoped(client, world):
    a, b = world.a, world.b
    await client.put(f"{API}/admin/settings", json={"max_trip_minutes": 80}, headers=H(a["admin"]))
    logs = (await client.get(f"{API}/audit-logs", headers=H(a["admin"]))).json()
    assert logs["total"] >= 1
    assert (await client.get(f"{API}/audit-logs", headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.get(f"{API}/audit-logs", headers=H(b["admin"]))).json()["total"] == 0
    with pytest.raises(DBAPIError):
        async with system_session() as conn:
            await conn.execute(text("UPDATE audit_logs SET action='tampered' WHERE organization_id=:o"), {"o": a["org"]})
    with pytest.raises(DBAPIError):
        async with system_session() as conn:
            await conn.execute(text("DELETE FROM audit_logs WHERE organization_id=:o"), {"o": a["org"]})


async def test_settings_confirmation_and_validation(client, world):
    a = world.a
    from tests.helpers import set_features
    await set_features(a["org"], deploy_sos=True, platform_sos=True)
    r = await client.put(f"{API}/admin/settings", json={"sos_gps_enabled": True}, headers=H(a["admin"]))
    assert r.status_code == 422                                                      # confirm required for privacy-sensitive switches
    assert (await client.put(f"{API}/admin/settings", json={"sos_gps_enabled": True, "confirm": True}, headers=H(a["admin"]))).status_code == 200
    assert (await client.put(f"{API}/admin/settings", json={"max_detour_ratio": 0.5}, headers=H(a["admin"]))).status_code == 422
    assert (await client.put(f"{API}/admin/settings", json={"fairness_weights": {"bogus": 1}}, headers=H(a["admin"]))).status_code == 422
    assert (await client.put(f"{API}/admin/settings", json={"surprise": 1}, headers=H(a["admin"]))).status_code == 422
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action LIKE 'settings%'", o=a["org"]) >= 1


# ---- realtime -----------------------------------------------------------------------------------------
async def test_ticket_single_use_expiry_and_wrong_secret(client, world):
    a = world.a
    t = (await client.post(f"{API}/realtime/ticket", headers=H(a["dispatcher"]))).json()["ticket"]
    assert hub.redeem_ticket(t) is not None
    assert hub.redeem_ticket(t) is None                                              # single use
    assert hub.redeem_ticket("garbage") is None
    s = get_settings()
    now = int(time.time())
    expired = jwt.encode({"sub": str(uuid.uuid4()), "org": str(a["org"]), "role": "driver", "jti": "j1", "purpose": "ws", "exp": now - 5}, s.realtime_ticket_secret, algorithm="HS256")
    forged = jwt.encode({"sub": str(uuid.uuid4()), "org": str(a["org"]), "role": "org_admin", "jti": "j2", "purpose": "ws", "exp": now + 60}, "other-secret-other-secret-other-secret", algorithm="HS256")
    wrong_purpose = jwt.encode({"sub": str(uuid.uuid4()), "org": str(a["org"]), "role": "driver", "jti": "j3", "purpose": "x", "exp": now + 60}, s.realtime_ticket_secret, algorithm="HS256")
    assert hub.redeem_ticket(expired) is None and hub.redeem_ticket(forged) is None and hub.redeem_ticket(wrong_purpose) is None
    assert (await client.post(f"{API}/realtime/ticket")).status_code == 401


async def test_hub_audience_filtering_across_tenants_and_roles():
    h = Hub()
    from app.services.realtime import Connection
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    staff_a = Connection(uuid.uuid4(), org_a, "dispatcher")
    staff_b = Connection(uuid.uuid4(), org_b, "dispatcher")
    emp_a, emp_a2 = Connection(uuid.uuid4(), org_a, "employee"), Connection(uuid.uuid4(), org_a, "employee")
    for c in (staff_a, staff_b, emp_a, emp_a2):
        h.add(c)
    await h.publish(org_a, "x", {"n": 1})                                           # staff only
    assert staff_a.queue.qsize() == 1 and staff_b.queue.qsize() == 0 and emp_a.queue.qsize() == 0
    await h.publish(org_a, "y", {"n": 2}, user_ids=[emp_a.user_id])                 # staff + one specific employee
    assert staff_a.queue.qsize() == 2 and emp_a.queue.qsize() == 1 and emp_a2.queue.qsize() == 0 and staff_b.queue.qsize() == 0
    await h.publish(org_a, "z", {}, staff=False, user_ids=[emp_a2.user_id])
    assert emp_a2.queue.qsize() == 1 and staff_a.queue.qsize() == 2


def test_websocket_end_to_end_with_ticket(world):
    """Real WebSocket handshake through Starlette's test client: bad ticket rejected, good ticket receives a pushed event, ticket not reusable."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import create_app
    org = uuid.uuid4()
    with TestClient(create_app()) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect(f"{API}/ws?ticket=bad"):
                pass
        t = hub.issue_ticket(uuid.uuid4(), org, "dispatcher")
        with tc.websocket_connect(f"{API}/ws?ticket={t}") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect(f"{API}/ws?ticket={t}"):
                pass
