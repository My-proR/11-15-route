"""Database access.

Every API request runs in ONE transaction that first drops to the NOBYPASSRLS role `routepilot_app` and sets
`app.user_id`, so PostgreSQL row level security applies to all queries (see alembic/sql/0002_rls.sql).
The trusted worker and migrations use `system_session()` which does not drop privileges.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .config import get_settings
from .database_url import normalize_async_database_url

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            normalize_async_database_url(s.database_url),
            pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_pre_ping=True,
            connect_args={"server_settings": {"statement_timeout": str(s.db_statement_timeout_ms)}},
        )
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def _enter_rls(conn: AsyncConnection, user_id: uuid.UUID | None) -> None:
    await conn.execute(text("SET LOCAL ROLE routepilot_app"))
    await conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id) if user_id else ""})


@asynccontextmanager
async def user_session(user_id: uuid.UUID | None) -> AsyncIterator[AsyncConnection]:
    """Single transaction, RLS enforced. Commits on success, rolls back on any exception."""
    async with get_engine().connect() as conn:
        async with conn.begin():
            await _enter_rls(conn, user_id)
            yield conn


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncConnection]:
    """Trusted backend work (worker, retention purge). Bypasses RLS; never expose to request input unchecked."""
    async with get_engine().connect() as conn:
        async with conn.begin():
            yield conn
