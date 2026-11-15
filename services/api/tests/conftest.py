"""Integration test harness. Uses a real PostgreSQL (TEST_DATABASE_URL server) and applies the real Alembic migration to a fresh database."""
import os
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import jwt
import pytest
import pytest_asyncio

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres@localhost:5433/postgres")
DB_NAME = "routepilot_it"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{DB_NAME}"
JWT_SECRET = "test-secret-test-secret-test-secret-123456"

os.environ.update({"DATABASE_URL": TEST_URL, "SUPABASE_JWT_SECRET": JWT_SECRET, "APP_ENV": "test", "RATE_LIMIT_PER_MINUTE": "100000",
                   "RATE_LIMIT_SENSITIVE_PER_MINUTE": "100000", "REDIS_URL": "", "NOTIFICATIONS_SEND_REAL": "false"})

from app.config import get_settings  # noqa: E402
from app.db import system_session  # noqa: E402
from app.services.routing import set_routing_provider  # noqa: E402
from tests.fakes import GridProvider  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _psql_admin(sql: str) -> None:
    import psycopg
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(sql)


@pytest.fixture(scope="session", autouse=True)
def database(request):
    # Pure unit-test selections should not require a local PostgreSQL daemon.
    if request.session.items and all(item.get_closest_marker("no_db") for item in request.session.items):
        yield
        return
    _psql_admin(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
    _psql_admin(f"CREATE DATABASE {DB_NAME}")
    api_dir = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=api_dir, env={**os.environ, "DATABASE_URL": TEST_URL}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    yield
    _psql_admin(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")


@pytest.fixture(autouse=True)
def reset_flags():
    s = get_settings()
    s.gps_sos_feature_enabled = False
    s.live_tracking_feature_enabled = False
    set_routing_provider(GridProvider())
    from app.common import limiter
    limiter._mem.clear()
    yield


def token(user_id, *, exp_in=3600, secret=JWT_SECRET, aud="authenticated") -> str:
    return jwt.encode({"sub": str(user_id), "aud": aud, "exp": int(time.time()) + exp_in, "role": "authenticated"}, secret, algorithm="HS256")


class Actor:
    def __init__(self, user_id, org_id, role, name=""):
        self.id, self.org_id, self.role, self.name = user_id, org_id, role, name

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {token(self.id)}", "X-Organization-Id": str(self.org_id)}


class World:
    """Two organizations. A is fully populated; B is a bystander used to prove isolation."""


async def _user(conn, org, role, name):
    uid = uuid.uuid4()
    await conn.execute(text("INSERT INTO user_profiles (user_id, email, full_name, phone) VALUES (:u,:e,:n,:p)"),
                       {"u": uid, "e": f"{name.lower().replace(' ', '.')}.{uid.hex[:6]}@example.test", "n": name, "p": "+91900000" + str(uid.int % 10000).zfill(4)})
    await conn.execute(text("INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o,:u,:r)"), {"o": org, "u": uid, "r": role})
    return Actor(uid, org, role, name)


async def build_org(conn, slug: str, *, n_emps=6, n_drivers=3) -> dict:
    org = (await conn.execute(text("INSERT INTO organizations (name, slug) VALUES (:n,:s) RETURNING id"), {"n": slug.upper(), "s": slug})).scalar()
    await conn.execute(text("INSERT INTO organization_settings (organization_id) VALUES (:o)"), {"o": org})
    d = {"org": org}
    d["admin"] = await _user(conn, org, "org_admin", f"Admin {slug}")
    d["dispatcher"] = await _user(conn, org, "dispatcher", f"Dispatcher {slug}")
    d["office"] = (await conn.execute(text("INSERT INTO locations (organization_id, kind, name, address, latitude, longitude, geocode_source) VALUES (:o,'branch','HQ','HQ road',13.0,80.0,'user') RETURNING id"), {"o": org})).scalar()
    d["shift"] = (await conn.execute(text("INSERT INTO shifts (organization_id, name, start_time, end_time) VALUES (:o,'Morning','09:00','18:00') RETURNING id"), {"o": org})).scalar()
    d["drivers"], d["driver_actors"] = [], []
    for i in range(n_drivers):
        a = await _user(conn, org, "driver", f"Driver {slug} {i}")
        did = (await conn.execute(text("INSERT INTO drivers (organization_id, user_id, driver_code, name, phone, license_number, license_valid_until, local_trip_eligible) VALUES (:o,:u,:c,:n,'+919000000000',:l,:v,:loc) RETURNING id"),
                                  {"o": org, "u": a.id, "c": f"D{i}", "n": a.name, "l": f"DL{slug}{i}", "v": date.today() + timedelta(days=365), "loc": i == 0})).scalar()
        d["drivers"].append(did); d["driver_actors"].append(a)
    d["vehicles"] = []
    for code, cap, avail in (("V1", 4, "available"), ("V2", 2, "available"), ("V3", 4, "maintenance"), ("V4", 4, "available")):
        d["vehicles"].append((await conn.execute(text("INSERT INTO vehicles (organization_id, vehicle_code, registration_number, capacity, availability) VALUES (:o,:c,:r,:cap,:a) RETURNING id"),
                                                 {"o": org, "c": code, "r": f"TN{slug.upper()}{code}", "cap": cap, "a": avail})).scalar())
    d["employees"], d["employee_actors"] = [], []
    for i in range(n_emps):
        a = await _user(conn, org, "employee", f"Emp {slug} {i}")
        eid = (await conn.execute(text("""INSERT INTO employees (organization_id, user_id, employee_code, branch_id, name, work_email, phone, gender, shift_id, pickup_address, pickup_lat, pickup_lon, drop_address, drop_lat, drop_lon)
            VALUES (:o,:u,:c,:b,:n,:e,'+919111111111',:g,:s,'addr',:la,:lo,'home',:la,:lo) RETURNING id"""),
                                 {"o": org, "u": a.id, "c": f"E{i}", "b": d["office"], "n": a.name, "e": f"emp{i}@{slug}.test", "g": "male" if i % 2 else "female", "s": d["shift"],
                                  "la": 13.05 + i * 0.002, "lo": 80.05})).scalar()
        d["employees"].append(eid); d["employee_actors"].append(a)
    return d


@pytest_asyncio.fixture(loop_scope="session")
async def world():
    async with system_session() as conn:
        w = World()
        slug = uuid.uuid4().hex[:6]
        w.a = await build_org(conn, "a" + slug)
        w.b = await build_org(conn, "b" + slug)
        pa = uuid.uuid4()
        await conn.execute(text("INSERT INTO user_profiles (user_id, email, full_name, is_platform_admin) VALUES (:u,:e,'Platform Admin',true)"), {"u": pa, "e": f"pa.{slug}@example.test"})
        w.platform = Actor(pa, None, None, "Platform Admin")
    yield w


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    import httpx
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def H(actor: Actor) -> dict:
    return actor.h


API = "/api/v1"
TOMORROW = date.today() + timedelta(days=1)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def reset_platform_flags(request):
    yield
    if request.session.items and all(item.get_closest_marker("no_db") for item in request.session.items):
        return
    async with system_session() as conn:
        await conn.execute(text("UPDATE platform_feature_flags SET enabled=false"))
