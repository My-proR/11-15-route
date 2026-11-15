"""Background worker: notification delivery, assignment expiry, retention purge.

Run:  python -m app.worker           (one instance is enough; multiple are safe: rows are claimed with SKIP LOCKED)
Uses system sessions (no RLS) because it acts across organizations; it never accepts request input.
"""
import asyncio
import json
import logging
import signal
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .config import get_settings
from .db import dispose_engine, system_session
from .services import autodispatch, billing
from .services import dispatch as dispatch_svc
from .services.notify_providers import (Message, PermanentError, Provider, Sent, Skipped, TransientError, backoff_seconds, default_providers)

log = logging.getLogger("routepilot.worker")
PLATFORM_GATED = {"whatsapp": "whatsapp", "sms": "sms"}


async def _build_message(conn: AsyncConnection, row) -> Message:
    contact = (await conn.execute(text("SELECT phone, email FROM user_profiles WHERE user_id=:u"), {"u": row.recipient_user_id})).first()
    subs = (await conn.execute(text("SELECT kind, token FROM push_subscriptions WHERE user_id=:u AND organization_id=:o"), {"u": row.recipient_user_id, "o": row.organization_id})).all()
    return Message(str(row.notification_id), row.title, row.body, row.data or {}, row.critical, contact.phone if contact else None, contact.email if contact else None,
                   tuple(s.token for s in subs if s.kind == "expo"), tuple(s.token for s in subs if s.kind == "web"))


async def process_deliveries(conn: AsyncConnection, providers: dict[str, Provider], limit: int = 50) -> dict:
    s = get_settings()
    stats = {"sent": 0, "retry": 0, "failed": 0, "skipped": 0}
    rows = (await conn.execute(text("""
        SELECT d.id, d.organization_id, d.notification_id, d.channel, d.attempts, n.recipient_user_id, n.title, n.body, n.data, n.critical
        FROM notification_deliveries d JOIN notifications n ON n.organization_id=d.organization_id AND n.id=d.notification_id
        WHERE d.status='pending' AND d.next_attempt_at <= now() AND d.channel <> 'in_app'
        ORDER BY n.critical DESC, d.next_attempt_at LIMIT :l FOR UPDATE OF d SKIP LOCKED"""), {"l": limit})).all()
    flags = {r.key: r.enabled for r in (await conn.execute(text("SELECT key, enabled FROM platform_feature_flags"))).all()}
    for r in rows:
        async def finish(status: str, *, err: str | None = None, mid: str | None = None, retry_in: int | None = None, did=r.id) -> None:
            if retry_in is not None:
                await conn.execute(text("UPDATE notification_deliveries SET attempts=attempts+1, last_error=:e, next_attempt_at=now()+make_interval(secs=>:s) WHERE id=:i"), {"e": err, "s": retry_in, "i": did})
            else:
                await conn.execute(text("UPDATE notification_deliveries SET status=:st, attempts=attempts+1, last_error=:e, provider_message_id=:m, sent_at=CASE WHEN :st='sent' THEN now() END WHERE id=:i"),
                                   {"st": status, "e": err, "m": mid, "i": did})
        if not s.notifications_send_real:
            await finish("skipped", err="sending_disabled (NOTIFICATIONS_SEND_REAL=false)"); stats["skipped"] += 1; continue
        gate = PLATFORM_GATED.get(r.channel)
        if gate and not flags.get(gate, False):
            await finish("skipped", err=f"{r.channel} disabled by platform flag"); stats["skipped"] += 1; continue
        provider = providers.get(r.channel)
        if provider is None:
            await finish("skipped", err="no_provider"); stats["skipped"] += 1; continue
        try:
            sent: Sent = await provider.send(await _build_message(conn, r))
            await finish("sent", mid=sent.message_id); stats["sent"] += 1      # 'sent' = provider accepted, NOT delivered
        except Skipped as exc:
            await finish("skipped", err=str(exc)); stats["skipped"] += 1
        except TransientError as exc:
            if r.attempts + 1 >= s.notification_max_attempts:
                await finish("failed", err=f"gave up after {r.attempts + 1} attempts: {exc}"); stats["failed"] += 1
            else:
                await finish("pending", err=str(exc), retry_in=backoff_seconds(r.attempts + 1)); stats["retry"] += 1
        except PermanentError as exc:
            await finish("failed", err=str(exc)); stats["failed"] += 1
        except Exception as exc:     # a buggy adapter must not wedge the queue
            log.exception("provider crashed")
            await finish("failed", err=f"adapter_error: {type(exc).__name__}"); stats["failed"] += 1
    return stats


async def purge_expired(conn: AsyncConnection) -> dict:
    """Retention: SOS coordinates, tracking history, finished idempotency keys."""
    a = (await conn.execute(text("DELETE FROM sos_incident_locations WHERE purge_after < now()"))).rowcount
    b = (await conn.execute(text("DELETE FROM vehicle_location_history WHERE purge_after < now()"))).rowcount
    c = (await conn.execute(text("DELETE FROM idempotency_keys WHERE created_at < now() - interval '3 days'"))).rowcount
    # stale 'last positions' of trips that are no longer in progress
    d = (await conn.execute(text("DELETE FROM vehicle_last_position lp USING trip_allocations a WHERE a.organization_id=lp.organization_id AND a.id=lp.allocation_id AND a.status <> 'IN_PROGRESS'"))).rowcount
    return {"sos_locations": a, "tracking_history": b, "idempotency_keys": c, "stale_positions": d}


async def run_once(providers: dict[str, Provider] | None = None) -> dict:
    providers = providers or default_providers()
    out: dict = {}
    async with system_session() as conn:
        out["expired_assignments"] = await dispatch_svc.expire_stale_assignments(conn)
    async with system_session() as conn:
        out["deliveries"] = await process_deliveries(conn, providers)
    out["auto_dispatch"] = await autodispatch.run_due()
    return out


async def run_loop(stop: asyncio.Event) -> None:
    """The worker loop. Used by the standalone worker (main) and, when RUN_WORKER_IN_API=true, inside the API process."""
    last_purge = 0.0
    providers = default_providers()
    log.info("worker started")
    while not stop.is_set():
        try:
            res = await run_once(providers)
            if any(v for v in (res["expired_assignments"], res["auto_dispatch"], *res["deliveries"].values())):
                log.info(json.dumps(res))
            now = datetime.now(timezone.utc).timestamp()
            if now - last_purge > 3600:
                async with system_session() as conn:
                    log.info("purge " + json.dumps(await purge_expired(conn)))
                last_purge = now
                log.info("billing " + json.dumps(await billing.run_due()))
        except Exception:
            log.exception("worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
    log.info("worker stopped")


async def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await run_loop(stop)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
