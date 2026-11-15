"""Notifications: inbox, preferences, push registration, delivery monitoring, provider status callbacks."""
import uuid

from fastapi import APIRouter, Depends, Form, Header, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip, page_params, page_response
from ..config import get_settings
from ..db import system_session
from ..schemas import PreferencesIn, PushRegister
from ..services.notify_providers import verify_twilio_signature

router = APIRouter(prefix="/notifications", tags=["notifications"])
ALL = ("org_admin", "dispatcher", "driver", "employee")
STAFF = ("org_admin", "dispatcher")


def _d(r) -> dict:
    return dict(r._mapping)


@router.get("")
async def inbox(unread: bool = False, p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for),
                page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    size, off = page_params(page, page_size, 100)
    cond = "organization_id=:o AND recipient_user_id=:u" + (" AND read_at IS NULL" if unread else "")
    params = {"o": p.org_id, "u": p.user_id}
    total = await conn.scalar(text(f"SELECT count(*) FROM notifications WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT id, event_type, title, body, data, critical, read_at, created_at FROM notifications WHERE {cond} ORDER BY created_at DESC LIMIT :l OFFSET :f"), {**params, "l": size, "f": off})).all()
    unread_n = await conn.scalar(text("SELECT count(*) FROM notifications WHERE organization_id=:o AND recipient_user_id=:u AND read_at IS NULL"), params)
    return {**page_response([_d(r) for r in rows], total, page, size), "unread": unread_n}


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: uuid.UUID, p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE notifications SET read_at=coalesce(read_at, now()) WHERE organization_id=:o AND id=:i AND recipient_user_id=:u"), {"o": p.org_id, "i": notification_id, "u": p.user_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "Notification not found")


@router.post("/read-all", status_code=204)
async def mark_all_read(p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    await conn.execute(text("UPDATE notifications SET read_at=now() WHERE organization_id=:o AND recipient_user_id=:u AND read_at IS NULL"), {"o": p.org_id, "u": p.user_id})


@router.get("/preferences")
async def get_prefs(p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text("SELECT push, whatsapp, sms, email FROM notification_preferences WHERE organization_id=:o AND user_id=:u"), {"o": p.org_id, "u": p.user_id})).first()
    return _d(r) if r else {"push": True, "whatsapp": False, "sms": False, "email": False}


@router.put("/preferences")
async def set_prefs(payload: PreferencesIn, p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    await conn.execute(text("""INSERT INTO notification_preferences (organization_id, user_id, push, whatsapp, sms, email) VALUES (:o,:u,:p,:w,:s,:e)
        ON CONFLICT (organization_id, user_id) DO UPDATE SET push=EXCLUDED.push, whatsapp=EXCLUDED.whatsapp, sms=EXCLUDED.sms, email=EXCLUDED.email"""),
                       {"o": p.org_id, "u": p.user_id, "p": payload.push, "w": payload.whatsapp, "s": payload.sms, "e": payload.email})
    return payload.model_dump()


@router.post("/push-subscriptions", status_code=201)
async def register_push(payload: PushRegister, p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    await conn.execute(text("INSERT INTO push_subscriptions (organization_id, user_id, kind, token) VALUES (:o,:u,:k,:t) ON CONFLICT (user_id, token) DO NOTHING"),
                       {"o": p.org_id, "u": p.user_id, "k": payload.kind, "t": payload.token})
    return {"registered": True}


@router.delete("/push-subscriptions", status_code=204)
async def unregister_push(token: str, p: Principal = Depends(require_roles(*ALL)), conn: AsyncConnection = Depends(db_for)):
    await conn.execute(text("DELETE FROM push_subscriptions WHERE user_id=:u AND token=:t"), {"u": p.user_id, "t": token})


@router.get("/deliveries")
async def deliveries(status: str | None = None, channel: str | None = None, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for),
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "s": status, "c": channel}
    cond = "d.organization_id=:o AND (CAST(:s AS text) IS NULL OR d.status=:s) AND (CAST(:c AS text) IS NULL OR d.channel=:c)"
    total = await conn.scalar(text(f"SELECT count(*) FROM notification_deliveries d WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT d.id, d.channel, d.status, d.attempts, d.last_error, d.sent_at, d.delivered_at, d.created_at, n.event_type, n.critical FROM notification_deliveries d "
                                    f"JOIN notifications n ON n.organization_id=d.organization_id AND n.id=d.notification_id WHERE {cond} ORDER BY d.created_at DESC LIMIT :l OFFSET :f"),
                               {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)


@router.post("/deliveries/{delivery_id}/retry")
async def retry(delivery_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    r = (await conn.execute(text("UPDATE notification_deliveries SET status='pending', next_attempt_at=now(), attempts=0 WHERE organization_id=:o AND id=:i AND status='failed' RETURNING id"), {"o": p.org_id, "i": delivery_id})).first()
    if r is None:
        raise AppError(409, "not_retryable", "Only failed deliveries can be retried")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="notification.retry", entity_type="notification_delivery", entity_id=delivery_id, ip=client_ip(request))
    return {"id": delivery_id, "status": "pending"}


@router.post("/webhooks/twilio", include_in_schema=False)
async def twilio_status(request: Request, x_twilio_signature: str = Header(default=""), MessageSid: str = Form(), MessageStatus: str = Form()):
    """Provider status callback: the only path that marks SMS/WhatsApp as delivered. Signature-verified."""
    s = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not s.twilio_auth_token or not verify_twilio_signature(str(request.url), form, x_twilio_signature, s.twilio_auth_token):
        raise AppError(403, "bad_signature", "Invalid signature")
    status = {"delivered": "delivered", "read": "delivered", "undelivered": "failed", "failed": "failed"}.get(MessageStatus)
    if status:
        async with system_session() as conn:
            await conn.execute(text("UPDATE notification_deliveries SET status=:s, delivered_at=CASE WHEN :s='delivered' THEN now() END, last_error=CASE WHEN :s='failed' THEN 'provider reported ' || :m END WHERE provider_message_id=:sid"),
                               {"s": status, "m": MessageStatus, "sid": MessageSid})
    return {"ok": True}
