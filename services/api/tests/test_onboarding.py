"""Invite links (WhatsApp), Google-style first sign-in, language preference."""
import time
import uuid

import jwt
from sqlalchemy import text

from app.db import system_session
from tests.conftest import API, JWT_SECRET, H
from tests.helpers import count


def google_token(uid, email, name="Ravi K"):
    return jwt.encode({"sub": str(uid), "aud": "authenticated", "exp": int(time.time()) + 3600, "email": email, "user_metadata": {"full_name": name}}, JWT_SECRET, algorithm="HS256")


def bearer(uid, email, **kw):
    return {"Authorization": f"Bearer {google_token(uid, email, **kw)}"}


async def new_unlinked_driver(org):
    async with system_session() as conn:
        return (await conn.execute(text("INSERT INTO drivers (organization_id, driver_code, name, phone, license_number, license_valid_until) VALUES (:o,:c,'Ravi Kumar','+919000000001',:l, current_date + 300) RETURNING id"),
                                   {"o": org, "c": "N" + uuid.uuid4().hex[:5], "l": "L" + uuid.uuid4().hex[:8]})).scalar()


async def test_driver_joins_by_invite_with_google_account(client, world):
    a = world.a
    did = await new_unlinked_driver(a["org"])
    r = await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did)}, headers=H(a["dispatcher"]))
    assert r.status_code == 201, r.text
    tok = r.json()["token"]
    assert r.json()["path"] == f"/join/{tok}"
    # only the hash is stored
    assert await count("SELECT count(*) FROM org_invites WHERE token_hash=:t", t=tok) == 0
    pv = await client.get(f"{API}/invites/preview", params={"token": tok})
    assert pv.status_code == 200 and pv.json()["role"] == "driver" and pv.json()["first_name"] == "Ravi"
    uid = uuid.uuid4()
    # before accepting: signed in with Google but not provisioned
    assert (await client.get(f"{API}/me", headers=bearer(uid, "ravi@gmail.test"))).status_code == 403
    acc = await client.post(f"{API}/invites/accept", json={"token": tok}, headers=bearer(uid, "ravi@gmail.test"))
    assert acc.status_code == 200 and acc.json()["role"] == "driver"
    me = await client.get(f"{API}/me", headers=bearer(uid, "ravi@gmail.test"))
    assert me.status_code == 200 and me.json()["role"] == "driver" and me.json()["language"] == "en"
    assert (await client.get(f"{API}/me/availability", headers=bearer(uid, "ravi@gmail.test"))).status_code == 200
    assert await count("SELECT user_id FROM drivers WHERE id=:d", d=did) == uid
    # single use
    again = await client.post(f"{API}/invites/accept", json={"token": tok}, headers=bearer(uuid.uuid4(), "other@gmail.test"))
    assert again.status_code == 404
    assert (await client.get(f"{API}/invites/preview", params={"token": tok})).status_code == 404
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='invite.accepted'", o=a["org"]) >= 1


async def test_invite_rules(client, world):
    a, b = world.a, world.b
    did = await new_unlinked_driver(a["org"])
    # dispatcher cannot invite dispatchers; employees/drivers cannot create invites; other tenant's record is not found
    assert (await client.post(f"{API}/invites", json={"role": "dispatcher"}, headers=H(a["dispatcher"]))).status_code == 403
    assert (await client.post(f"{API}/invites", json={"role": "dispatcher"}, headers=H(a["admin"]))).status_code == 201
    assert (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did)}, headers=H(a["driver_actors"][0]))).status_code == 403
    assert (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did)}, headers=H(b["admin"]))).status_code == 404
    assert (await client.post(f"{API}/invites", json={"role": "driver"}, headers=H(a["admin"]))).status_code == 422
    # tenant B cannot see or revoke A's invites
    inv = (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did)}, headers=H(a["admin"]))).json()
    assert inv["id"] not in [i["id"] for i in (await client.get(f"{API}/invites", headers=H(b["admin"]))).json()]
    assert (await client.delete(f"{API}/invites/{inv['id']}", headers=H(b["admin"]))).status_code == 404
    # revoke then accept fails
    assert (await client.delete(f"{API}/invites/{inv['id']}", headers=H(a["admin"]))).status_code == 204
    r = await client.post(f"{API}/invites/accept", json={"token": inv["token"]}, headers=bearer(uuid.uuid4(), "x@gmail.test"))
    assert r.status_code == 404
    statuses = {i["id"]: i["status"] for i in (await client.get(f"{API}/invites", headers=H(a["admin"]))).json()}
    assert statuses[inv["id"]] == "revoked"


async def test_email_lock_expiry_and_already_linked(client, world):
    a = world.a
    did = await new_unlinked_driver(a["org"])
    inv = (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did), "email": "Locked@gmail.test"}, headers=H(a["admin"]))).json()
    wrong = await client.post(f"{API}/invites/accept", json={"token": inv["token"]}, headers=bearer(uuid.uuid4(), "intruder@gmail.test"))
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "invite_email_mismatch"
    ok = await client.post(f"{API}/invites/accept", json={"token": inv["token"]}, headers=bearer(uuid.uuid4(), "locked@gmail.test"))
    assert ok.status_code == 200
    # expired
    did2 = await new_unlinked_driver(a["org"])
    inv2 = (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(did2)}, headers=H(a["admin"]))).json()
    async with system_session() as conn:
        await conn.execute(text("UPDATE org_invites SET expires_at = now() - interval '1 minute' WHERE id=:i"), {"i": inv2["id"]})
    assert (await client.post(f"{API}/invites/accept", json={"token": inv2["token"]}, headers=bearer(uuid.uuid4(), "late@gmail.test"))).status_code == 404
    # record already linked to a real account -> a second invite cannot take it over
    inv3 = (await client.post(f"{API}/invites", json={"role": "driver", "driver_id": str(a["drivers"][0])}, headers=H(a["admin"]))).json()
    r = await client.post(f"{API}/invites/accept", json={"token": inv3["token"]}, headers=bearer(uuid.uuid4(), "thief@gmail.test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_linked"
    assert await count("SELECT count(*) FROM user_profiles WHERE lower(email)='thief@gmail.test' AND false") == 0
    # garbage / missing credentials
    assert (await client.post(f"{API}/invites/accept", json={"token": "x" * 30})).status_code == 401
    assert (await client.post(f"{API}/invites/accept", json={"token": "x" * 30}, headers=bearer(uuid.uuid4(), "g@gmail.test"))).status_code == 404


async def test_employee_invite_and_language(client, world):
    a = world.a
    async with system_session() as conn:
        eid = (await conn.execute(text("INSERT INTO employees (organization_id, employee_code, name, work_email, shift_id) VALUES (:o,:c,'Priya S','p@x.test',:s) RETURNING id"),
                                  {"o": a["org"], "c": "N" + uuid.uuid4().hex[:5], "s": a["shift"]})).scalar()
    inv = (await client.post(f"{API}/invites", json={"role": "employee", "employee_id": str(eid)}, headers=H(a["dispatcher"]))).json()
    uid = uuid.uuid4()
    assert (await client.post(f"{API}/invites/accept", json={"token": inv["token"]}, headers=bearer(uid, "priya@gmail.test"))).status_code == 200
    h = bearer(uid, "priya@gmail.test")
    assert (await client.put(f"{API}/me/language", json={"language": "ta"}, headers=h)).json() == {"language": "ta"}
    assert (await client.get(f"{API}/me", headers=h)).json()["language"] == "ta"
    assert (await client.put(f"{API}/me/language", json={"language": "fr"}, headers=h)).status_code == 422
    # language change touches only the caller's profile
    assert await count("SELECT count(*) FROM user_profiles WHERE language='ta'") >= 1
    assert await count("SELECT language FROM user_profiles WHERE user_id=:u", u=a["admin"].id) == "en"
