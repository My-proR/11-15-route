import asyncio

import pytest

from app import main as main_mod
from app import worker
from app.config import get_settings

pytestmark = pytest.mark.no_db


async def _lifespan_runs(monkeypatch, enabled: bool) -> int:
    calls = {"n": 0}

    async def fake_run_once(providers):
        calls["n"] += 1
        return {"expired_assignments": 0, "auto_dispatch": 0, "deliveries": {}}

    async def fake_purge(conn):
        return {}

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setenv("RUN_WORKER_IN_API", "true" if enabled else "false")
    get_settings.cache_clear()
    app = main_mod.create_app()
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
    get_settings.cache_clear()
    return calls["n"]


async def test_embedded_worker_runs_and_stops_with_the_api(monkeypatch):
    assert await _lifespan_runs(monkeypatch, True) >= 1


async def test_worker_not_started_by_default(monkeypatch):
    assert await _lifespan_runs(monkeypatch, False) == 0
