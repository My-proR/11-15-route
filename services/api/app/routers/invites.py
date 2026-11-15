"""Invite links (shared on WhatsApp) and the language preference.

An invite is bound to one existing driver or employee record. The admin gets the raw token once; only its hash is stored.
Accepting needs a valid Supabase sign-in (Google or email) but NOT an existing profile: the token is the credential that links the
new account to the record, so accepting runs as a trusted server step (system session) with strict checks.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, decode_token, require_roles
from ..common import AppError, audit, client_ip, limiter
from ..db import system_session
from ..schemas import Strict

router = APIRouter(tags=["invites"])
STAFF = ("org_admin", "dispatcher")


class InviteIn(Strict):
    role: Literal["dispatcher", "driver", "employee"]
    driver_id: Optional[uuid.UUID] = None
    employee_id: Optional[uuid.UUID] = None
    email: Optional[str] = Field(None, max_length=254, description="Optional: only this account may accept")
    expires_days: int = Field(7, ge=1, le=30)


class AcceptIn(Strict):
    token: str = Field(min_length=20, max_length=200)


class LanguageIn(Strict):
    language: Literal["en", "ta"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _status(r) -> str:
    if r.revoked_at: return "revoked"
    if r.used_at: return "used"
    if r.expires_at <= datetime.now(timezone.utc): return "expired"
    return "active"


@router.post("/invites", status_code=201)
async def create_invite(payload: InviteIn, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    if payload.role == "dispatcher" and p.role != "org_admin":
        raise AppError(403, "forbidden", "Only an organization admin can invite dispatchers")
    if payload.role == "driver":
        if payload.driver_id is None or payload.employee_id is not None:
            raise AppError(422, "invalid_target", "A driver invite needs driver_id")
        name = await conn.scalar(text("SELECT name FROM drivers WHERE organization_id=:o AND id=:i AND is_active"), {"o": p.org_id, "i": payload.driver_id})
    elif payload.role == "employee":
        if payload.employee_id is None or payload.driver_id is not None:
            raise AppError(422, "invalid_target", "An employee invite needs employee_id")
        name = await conn.scalar(text("SELECT name FROM employees WHERE organization_id=:o AND id=:i AND is_active"), {"o": p.org_id, "i": payload.employee_id})
    else:
        if payload.driver_id or payload.employee_id:
            raise AppError(422, "invalid_target", "A dispatcher invite has no driver or employee")
        name = None
    if payload.role != "dispatcher" and name is None:
        raise AppError(404, "not_found", "Driver or employee not found")
    token = secrets.token_urlsafe(24)
    row = (await conn.execute(text("""INSERT INTO org_invites (organization_id, role, driver_id, employee_id, token_hash, email, created_by, expires_at)
        VALUES (:o,:r,:d,:e,:h,:em,:by, now() + make_interval(days => :days)) RETURNING id, expires_at"""),
        {"o": p.org_id, "r": payload.role, "d": payload.driver_id, "e": payload.employee_id, "h": _hash(token),
         "em": payload.email.lower() if payload.email else None, "by": p.user_id, "days": payload.expires_days})).first()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="invite.created", entity_type="invite", entity_id=row.id,
                details={"role": payload.role, "driver_id": str(payload.driver_id) if payload.driver_id else None,
                         "employee_id": str(payload.employee_id) if payload.employee_id else None}, ip=client_ip(request))
    return {"id": row.id, "token": token, "path": f"/join/{token}", "role": payload.role, "name": name, "expires_at": row.expires_at}


@router.get("/invites")
async def list_invites(p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for), limit: int = Query(100, ge=1, le=200)):
    rows = (await conn.execute(text("""SELECT i.id, i.role, i.email, i.expires_at, i.used_at, i.revoked_at, i.created_at,
            coalesce(d.name, e.name) AS name
        FROM org_invites i LEFT JOIN drivers d ON d.organization_id=i.organization_id AND d.id=i.driver_id
        LEFT JOIN employees e ON e.organization_id=i.organization_id AND e.id=i.employee_id
        WHERE i.organization_id=:o ORDER BY i.created_at DESC LIMIT :l"""), {"o": p.org_id, "l": limit})).all()
    return [{"id": r.id, "role": r.role, "name": r.name, "email": r.email, "status": _status(r), "expires_at": r.expires_at, "created_at": r.created_at} for r in rows]


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE org_invites SET revoked_at=now() WHERE organization_id=:o AND id=:i AND used_at IS NULL AND revoked_at IS NULL"), {"o": p.org_id, "i": invite_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "No active invite with that id")
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="invite.revoked", entity_type="invite", entity_id=invite_id, ip=client_ip(request))


@router.get("/invites/preview")
async def preview_invite(request: Request, token: str = Query(min_length=20, max_length=200)):
    """Public: lets the join page say who invited the person. Reveals only organisation, role and first name."""
    if not await limiter.hit(f"invprev:{client_ip(request)}", 20, window=60):
        raise AppError(429, "rate_limited", "Too many attempts. Try again in a minute.")
    async with system_session() as conn:
        r = (await conn.execute(text("""SELECT i.role, i.expires_at, i.used_at, i.revoked_at, o.name AS org, coalesce(d.name, e.name) AS name
            FROM org_invites i JOIN organizations o ON o.id=i.organization_id
            LEFT JOIN drivers d ON d.organization_id=i.organization_id AND d.id=i.driver_id
            LEFT JOIN employees e ON e.organization_id=i.organization_id AND e.id=i.employee_id
            WHERE i.token_hash=:h"""), {"h": _hash(token)})).first()
    if r is None or _status(r) != "active":
        raise AppError(404, "invite_invalid", "This invite link is not valid. Ask your admin for a new one.")
    return {"organization": r.org, "role": r.role, "first_name": (r.name or "").split(" ")[0] or None}


@router.post("/invites/accept")
async def accept_invite(payload: AcceptIn, request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    claims = decode_token(authorization[7:].strip())
    try:
        uid = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "Invalid token subject") from exc
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise AppError(422, "email_required", "Your sign-in account has no email address")
    if not await limiter.hit(f"invacc:{uid}", 10, window=300):
        raise AppError(429, "rate_limited", "Too many attempts. Try again in a few minutes.")
    meta = claims.get("user_metadata") or {}
    full_name = str(meta.get("full_name") or meta.get("name") or "")[:160]
    async with system_session() as conn:
        inv = (await conn.execute(text("SELECT * FROM org_invites WHERE token_hash=:h FOR UPDATE"), {"h": _hash(payload.token)})).first()
        if inv is None or _status(inv) != "active":
            raise AppError(404, "invite_invalid", "This invite link is not valid. Ask your admin for a new one.")
        if inv.email and inv.email != email:
            raise AppError(403, "invite_email_mismatch", f"This invite is for a different account. Sign in with {inv.email}.")
        org_ok = await conn.scalar(text("SELECT status='active' FROM organizations WHERE id=:o"), {"o": inv.organization_id})
        if not org_ok:
            raise AppError(403, "organization_suspended", "This organization is not active")
        other = await conn.scalar(text("SELECT user_id FROM user_profiles WHERE lower(email)=:e AND user_id<>:u"), {"e": email, "u": uid})
        if other:
            raise AppError(409, "email_in_use", "This email already belongs to another account. Contact your admin.")
        if not await conn.scalar(text("SELECT is_active FROM user_profiles WHERE user_id=:u"), {"u": uid}) and await conn.scalar(text("SELECT 1 FROM user_profiles WHERE user_id=:u"), {"u": uid}):
            raise AppError(403, "account_disabled", "This account is disabled")
        await conn.execute(text("INSERT INTO user_profiles (user_id, email, full_name) VALUES (:u,:e,:n) ON CONFLICT (user_id) DO NOTHING"), {"u": uid, "e": email, "n": full_name})
        cur = await conn.scalar(text("SELECT role FROM organization_members WHERE organization_id=:o AND user_id=:u AND is_active"), {"o": inv.organization_id, "u": uid})
        if cur and cur != inv.role:
            raise AppError(409, "role_conflict", f"You are already a {cur.replace('_', ' ')} in this organization")
        if inv.role == "driver":
            n = (await conn.execute(text("UPDATE drivers SET user_id=:u WHERE organization_id=:o AND id=:i AND (user_id IS NULL OR user_id=:u)"), {"u": uid, "o": inv.organization_id, "i": inv.driver_id})).rowcount
        elif inv.role == "employee":
            n = (await conn.execute(text("UPDATE employees SET user_id=:u WHERE organization_id=:o AND id=:i AND (user_id IS NULL OR user_id=:u)"), {"u": uid, "o": inv.organization_id, "i": inv.employee_id})).rowcount
        else:
            n = 1
        if not n:
            raise AppError(409, "already_linked", "This record is already linked to another account. Ask your admin for a new invite.")
        await conn.execute(text("INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o,:u,:r) ON CONFLICT (organization_id, user_id) DO UPDATE SET is_active=true"),
                           {"o": inv.organization_id, "u": uid, "r": inv.role})
        await conn.execute(text("UPDATE org_invites SET used_at=now(), used_by=:u WHERE id=:i"), {"u": uid, "i": inv.id})
        await audit(conn, org_id=inv.organization_id, actor=uid, action="invite.accepted", entity_type="invite", entity_id=inv.id, details={"role": inv.role}, ip=client_ip(request))
        return {"organization_id": inv.organization_id, "role": inv.role}


@router.put("/me/language")
async def set_language(payload: LanguageIn, p: Principal = Depends(require_roles(*STAFF, "driver", "employee")), conn: AsyncConnection = Depends(db_for)):
    await conn.execute(text("SELECT rp.set_my_language(:l)"), {"l": payload.language})
    return {"language": payload.language}
