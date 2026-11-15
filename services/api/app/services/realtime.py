"""Authorized realtime fan-out.

Events carry an audience: staff of the organization and/or explicit user ids. A connection only receives events for its own
organization and only if it is in the audience. With REDIS_URL set, events go through Redis pub/sub so multiple API
instances share them; otherwise delivery is in-process (single instance).
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import jwt

from ..config import get_settings

log = logging.getLogger("routepilot.realtime")


@dataclass(eq=False)
class Connection:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))


class Hub:
    def __init__(self) -> None:
        self._conns: set[Connection] = set()
        self._redis = None
        self._task: asyncio.Task | None = None
        self._used: dict[str, float] = {}

    # --- connection management -------------------------------------------------------------
    def count_for(self, user_id: uuid.UUID) -> int:
        return sum(1 for c in self._conns if c.user_id == user_id)

    def add(self, c: Connection) -> None:
        self._conns.add(c)

    def remove(self, c: Connection) -> None:
        self._conns.discard(c)

    # --- tickets (short-lived, single use; avoids putting long-lived tokens in a URL) ---------------
    def issue_ticket(self, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
        s = get_settings()
        now = int(time.time())
        return jwt.encode({"sub": str(user_id), "org": str(org_id), "role": role, "jti": uuid.uuid4().hex, "purpose": "ws",
                           "iat": now, "exp": now + s.realtime_ticket_ttl_seconds}, s.realtime_ticket_secret, algorithm="HS256")

    def redeem_ticket(self, ticket: str) -> Connection | None:
        s = get_settings()
        try:
            c = jwt.decode(ticket, s.realtime_ticket_secret, algorithms=["HS256"], options={"require": ["exp", "jti", "sub", "org"]})
        except jwt.PyJWTError:
            return None
        if c.get("purpose") != "ws":
            return None
        now = time.time()
        self._used = {k: v for k, v in self._used.items() if v > now}
        if c["jti"] in self._used:
            return None
        self._used[c["jti"]] = now + s.realtime_ticket_ttl_seconds + 5
        return Connection(uuid.UUID(c["sub"]), uuid.UUID(c["org"]), c["role"])

    # --- publish / deliver ----------------------------------------------------------------------------
    async def publish(self, org_id: uuid.UUID, event: str, data: dict, *, staff: bool = True, user_ids: list[uuid.UUID] | None = None) -> None:
        msg = {"org": str(org_id), "event": event, "data": data, "staff": staff, "users": [str(u) for u in (user_ids or [])]}
        r = self._redis_client()
        if r is not None:
            try:
                await r.publish("rp:events", json.dumps(msg, default=str))
                return
            except Exception:
                log.warning("redis publish failed; delivering locally")
        self._deliver(msg)

    def _deliver(self, msg: dict) -> None:
        for c in list(self._conns):
            if str(c.org_id) != msg["org"]:
                continue
            allowed = (msg["staff"] and c.role in ("org_admin", "dispatcher")) or str(c.user_id) in msg["users"]
            if not allowed:
                continue
            try:
                c.queue.put_nowait({"event": msg["event"], "data": msg["data"]})
            except asyncio.QueueFull:
                log.warning("slow websocket consumer dropped event")

    def _redis_client(self):
        s = get_settings()
        if s.redis_url and self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(s.redis_url, decode_responses=True)
        return self._redis

    async def start(self) -> None:
        r = self._redis_client()
        if r is None or self._task:
            return

        async def loop() -> None:
            while True:
                try:
                    ps = r.pubsub()
                    await ps.subscribe("rp:events")
                    async for m in ps.listen():
                        if m["type"] == "message":
                            self._deliver(json.loads(m["data"]))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("redis subscriber error; retrying")
                    await asyncio.sleep(2)

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


hub = Hub()
