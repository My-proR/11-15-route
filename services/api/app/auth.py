"""Supabase JWT verification and server-side role enforcement."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .config import get_settings
from .db import user_session, system_session

STAFF = ("org_admin", "dispatcher")
_jwks_client: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    org_id: uuid.UUID | None
    role: str | None            # org role in the selected organization
    org_suspended: bool = False  # the selected organization is paused (unpaid invoice or manual suspension)

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF


def decode_token(token: str) -> dict:
    s = get_settings()
    issuer = s.supabase_jwt_issuer or (f"{s.supabase_url.rstrip('/')}/auth/v1" if s.supabase_url else None)
    options = {"require": ["exp", "sub"] + (["iss"] if issuer else [])}
    try:
        if s.supabase_jwks_url:
            global _jwks_client
            if _jwks_client is None:
                _jwks_client = jwt.PyJWKClient(s.supabase_jwks_url, cache_keys=True)
            key = _jwks_client.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["ES256", "RS256", "EdDSA"], audience=s.supabase_jwt_audience,
                              issuer=issuer, options=options)
        if s.supabase_jwt_secret:
            return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience=s.supabase_jwt_audience,
                              issuer=issuer, options=options)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc
    raise HTTPException(503, "Authentication is not configured")


async def _verify(token: str) -> dict:
    return await asyncio.to_thread(decode_token, token)   # JWKS fetch is blocking


async def resolve_principal(token: str, org_header: str | None) -> Principal:
    claims = await _verify(token)
    try:
        uid = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "Invalid token subject") from exc
    # Profile + memberships are read through RLS as the user themselves.
    async with user_session(uid) as conn:
        prof = (await conn.execute(text(
            "SELECT email, full_name, is_platform_admin, is_active FROM user_profiles WHERE user_id = :u"), {"u": uid})).first()
        if not prof or not prof.is_active:
            raise HTTPException(403, "Account is not provisioned or is disabled")
        members = (await conn.execute(text(
            "SELECT organization_id, role, suspended FROM rp.my_orgs()"))).all()
    org_id: uuid.UUID | None = None
    role: str | None = None
    suspended = False
    if org_header:
        try:
            wanted = uuid.UUID(org_header)
        except ValueError as exc:
            raise HTTPException(400, "Invalid X-Organization-Id") from exc
        match = next((m for m in members if m.organization_id == wanted), None)
        if match is None and not prof.is_platform_admin:
            raise HTTPException(403, "Not a member of this organization")
        if match:
            org_id, role, suspended = match.organization_id, match.role, bool(match.suspended)
        else:
            org_id = wanted   # platform admin addressing an org: no org role, platform endpoints only
    elif len(members) == 1:
        org_id, role, suspended = members[0].organization_id, members[0].role, bool(members[0].suspended)
    elif len(members) > 1:
        raise HTTPException(400, "Multiple organizations: send X-Organization-Id")
    return Principal(uid, prof.email, prof.full_name, prof.is_platform_admin, org_id, role, suspended and not prof.is_platform_admin)


async def current_principal(request: Request, authorization: str | None = Header(default=None),
                            x_organization_id: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    principal = await resolve_principal(authorization[7:].strip(), x_organization_id)
    request.state.principal = principal
    if principal.org_suspended:
        # A paused organization can still sign in, see who it is and open its billing page to pay; everything else is refused.
        prefix = get_settings().api_v1_prefix
        path = request.url.path
        if not (path == f"{prefix}/me" or path == f"{prefix}/billing" or path.startswith(f"{prefix}/billing/")):
            from .common import AppError
            raise AppError(403, "organization_suspended", "Access for your organization is paused. An organization admin can open Billing to resolve it.")
    return principal


def require_roles(*roles: str, platform_admin: bool = False) -> Callable:
    """Dependency enforcing an org role (and/or platform admin) on the server."""
    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if platform_admin and p.is_platform_admin:
            return p
        if platform_admin and not roles:
            raise HTTPException(403, "Platform administrator required")      # platform-only endpoint: org roles never qualify
        if p.org_id is None or p.role is None:
            raise HTTPException(403, "Organization membership required")
        if roles and p.role not in roles:
            raise HTTPException(403, "Insufficient role for this operation")
        return p
    return dep


async def db_for(p: Principal = Depends(current_principal)) -> AsyncIterator[AsyncConnection]:
    """Per-request RLS-enforced connection. Commits when the request handler returns normally."""
    async with user_session(p.user_id) as conn:
        yield conn


async def lookup_member_user(org_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    async with system_session() as conn:
        return await conn.scalar(text(
            "SELECT role FROM organization_members WHERE organization_id=:o AND user_id=:u AND is_active"),
            {"o": org_id, "u": user_id})
