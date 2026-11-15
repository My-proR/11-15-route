from tests.conftest import API, H

async def test_health_and_me(client, world):
    assert (await client.get("/health/live")).json() == {"status": "alive"}
    r = await client.get(f"{API}/me", headers=H(world.a["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "org_admin"

async def test_requires_auth(client):
    assert (await client.get(f"{API}/employees")).status_code == 401
