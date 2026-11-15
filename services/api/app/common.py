"""Shared helpers: audit logging, idempotency, pagination, structured errors, rate limiting, notifications enqueue."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .config import get_settings

log = logging.getLogger("routepilot")


# --- errors ---------------------------------------------------------------------------------------
class AppError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(status_code=status, detail={"code": code, "message": message, "details": details})


def error_body(status: int, detail: Any) -> dict:
    if isinstance(detail, dict) and "code" in detail:
        return {"error": detail}
    return {"error": {"code": f"http_{status}", "message": detail if isinstance(detail, str) else "Request failed", "details": None if isinstance(detail, str) else detail}}


# --- audit ----------------------------------------------------------------------------------------
async def audit(conn: AsyncConnection, *, org_id: uuid.UUID | None, actor: uuid.UUID | None, action: str, entity_type: str,
                entity_id: Any = None, details: dict | None = None, ip: str | None = None) -> None:
    await conn.execute(text(
        "INSERT INTO audit_logs (organization_id, actor_user_id, action, entity_type, entity_id, details, ip) "
        "VALUES (:o, :a, :act, :et, :eid, CAST(:d AS jsonb), CAST(:ip AS inet))"),
        {"o": org_id, "a": actor, "act": action, "et": entity_type, "eid": str(entity_id) if entity_id is not None else None,
         "d": json.dumps(details or {}, default=str), "ip": ip})


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# --- pagination -----------------------------------------------------------------------------------
def page_params(page: int, page_size: int, max_size: int = 200) -> tuple[int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), max_size)
    return page_size, (page - 1) * page_size


def page_response(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def sort_clause(sort: str | None, allowed: dict[str, str], default: str) -> str:
    """Whitelisted ORDER BY (never interpolates user text). sort = 'name' or '-name'."""
    if not sort:
        return default
    desc = sort.startswith("-")
    col = allowed.get(sort.lstrip("-"))
    if not col:
        raise AppError(422, "invalid_sort", f"Sort must be one of {sorted(allowed)}")
    return f"{col} {'DESC' if desc else 'ASC'}"


# --- idempotency ----------------------------------------------------------------------------------
async def idempotent(conn: AsyncConnection, *, org_id: uuid.UUID, user_id: uuid.UUID, scope: str, key: str | None, payload: Any):
    """Returns (cached_response|None). If key is new it is reserved; call `idempotency_store` after success."""
    if not key:
        return None
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    row = (await conn.execute(text(
        "INSERT INTO idempotency_keys (organization_id, user_id, scope, key, request_hash) VALUES (:o,:u,:s,:k,:h) "
        "ON CONFLICT DO NOTHING RETURNING key"), {"o": org_id, "u": user_id, "s": scope, "k": key, "h": h})).first()
    if row:
        return None
    existing = (await conn.execute(text(
        "SELECT request_hash, response FROM idempotency_keys WHERE organization_id=:o AND user_id=:u AND scope=:s AND key=:k"),
        {"o": org_id, "u": user_id, "s": scope, "k": key})).first()
    if existing.request_hash != h:
        raise AppError(422, "idempotency_key_reuse", "Idempotency-Key was already used with a different request")
    if existing.response is None:
        raise AppError(409, "request_in_progress", "A request with this Idempotency-Key is still being processed")
    return existing.response


async def idempotency_store(conn: AsyncConnection, *, org_id, user_id, scope: str, key: str | None, response: Any) -> None:
    if key:
        await conn.execute(text(
            "UPDATE idempotency_keys SET response = CAST(:r AS jsonb) WHERE organization_id=:o AND user_id=:u AND scope=:s AND key=:k"),
            {"r": json.dumps(response, default=str), "o": org_id, "u": user_id, "s": scope, "k": key})


# --- rate limiting ----------------------------------------------------------------------------------
class RateLimiter:
    """Sliding window. Redis-backed when REDIS_URL is set (shared across instances), else in-process."""
    def __init__(self) -> None:
        self._mem: dict[str, deque[float]] = defaultdict(deque)
        self._redis = None

    def _client(self):
        s = get_settings()
        if s.redis_url and self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(s.redis_url, decode_responses=True)
        return self._redis

    async def hit(self, key: str, limit: int, window: int = 60) -> bool:
        r = self._client()
        if r is not None:
            try:
                bucket = f"rl:{key}:{int(time.time() // window)}"
                n = await r.incr(bucket)
                if n == 1:
                    await r.expire(bucket, window + 1)
                return n <= limit
            except Exception:   # Redis outage must not take the API down; fall back to local limiting
                log.warning("rate limiter redis unavailable; using in-memory window")
        now = time.monotonic()
        q = self._mem[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()


async def enforce_rate_limit(request: Request, *, scope: str, limit: int) -> None:
    p = getattr(request.state, "principal", None)
    ident = str(p.user_id) if p else (request.client.host if request.client else "anon")
    if not await limiter.hit(f"{scope}:{ident}", limit):
        raise AppError(429, "rate_limited", "Too many requests; slow down and retry shortly")


def rate_limit_response(msg: str) -> JSONResponse:
    return JSONResponse(status_code=429, content={"error": {"code": "rate_limited", "message": msg, "details": None}}, headers={"Retry-After": "30"})


# --- notifications enqueue --------------------------------------------------------------------------
async def notify(conn: AsyncConnection, *, org_id: uuid.UUID, user_id: uuid.UUID, event_type: str, title: str, body: str,
                 data: dict | None = None, critical: bool = False, dedupe_key: str | None = None) -> uuid.UUID | None:
    """Create an in-app notification + pending deliveries for the user's enabled channels. Deduplicated by dedupe_key."""
    args = {"o": org_id, "u": user_id, "e": event_type, "t": title, "b": body, "d": json.dumps(data or {}, default=str), "c": critical, "dk": dedupe_key}
    if await _is_system(conn):
        nid = (await conn.execute(text(
            "INSERT INTO notifications (organization_id, recipient_user_id, event_type, title, body, data, critical, dedupe_key) "
            "VALUES (:o,:u,:e,:t,:b,CAST(:d AS jsonb),:c,:dk) ON CONFLICT (organization_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING RETURNING id"), args)).scalar()
    else:
        nid = (await conn.execute(text("SELECT rp.create_notification(:o,:u,:e,:t,:b,CAST(:d AS jsonb),:c,:dk)"), args)).scalar()
    if nid is None:
        return None
    org_channels = (await conn.execute(text("SELECT notification_channels FROM organization_settings WHERE organization_id=:o"), {"o": org_id})).scalar() or {}
    if await _is_system(conn):
        pref = (await conn.execute(text("SELECT push, whatsapp, sms, email FROM notification_preferences WHERE organization_id=:o AND user_id=:u"), {"o": org_id, "u": user_id})).first()
    else:
        pref = (await conn.execute(text("SELECT * FROM rp.channel_prefs(:o, :u)"), {"o": org_id, "u": user_id})).first()
    want = {"push": True, "whatsapp": False, "sms": False, "email": False}
    if pref:
        want = {"push": pref.push, "whatsapp": pref.whatsapp, "sms": pref.sms, "email": pref.email}
    channels = ["in_app"]
    if want["push"] and org_channels.get("push", True):
        channels += ["push_expo", "push_web"]
    for ch in ("whatsapp", "sms", "email"):
        if (want[ch] or critical) and org_channels.get(ch, False):
            channels.append(ch)
    for ch in channels:
        await conn.execute(text(
            "INSERT INTO notification_deliveries (organization_id, notification_id, channel, status, delivered_at) "
            "VALUES (:o,:n,:c, CASE WHEN :c='in_app' THEN 'delivered' ELSE 'pending' END, CASE WHEN :c='in_app' THEN now() END) ON CONFLICT DO NOTHING"),
            {"o": org_id, "n": nid, "c": ch})
    return nid


async def _is_system(conn: AsyncConnection) -> bool:
    """True for trusted backend sessions (no request user, not the RLS role)."""
    return bool(await conn.scalar(text("SELECT coalesce(current_setting('app.user_id', true), '') = '' AND current_user <> 'routepilot_app'")))


async def staff_user_ids(conn: AsyncConnection, org_id: uuid.UUID, roles: tuple[str, ...] = ("org_admin", "dispatcher")) -> list[uuid.UUID]:
    if await _is_system(conn):
        rows = await conn.execute(text("SELECT user_id FROM organization_members WHERE organization_id=:o AND is_active AND role = ANY(:r)"), {"o": org_id, "r": list(roles)})
    else:
        rows = await conn.execute(text("SELECT rp.staff_ids(:o, :r) AS user_id"), {"o": org_id, "r": list(roles)})
    return [r.user_id for r in rows]
