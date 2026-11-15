"""Identity, organizations, members, settings, platform flags, audit logs, health."""
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import supabase_admin
from ..app_errors import SupabaseAdminError
from ..auth import Principal, current_principal, db_for, require_roles
from ..common import AppError, audit, client_ip, page_params, page_response
from ..config import get_settings
from ..db import system_session
from ..flags import feature_states
from ..schemas import FlagPatch, MemberIn, OrganizationCreate, SettingsPatch
from ..services.routing import get_routing_provider

router = APIRouter()
STAFF = ("org_admin", "dispatcher")


def _d(r) -> dict:
    return dict(r._mapping)


# ---- health ------------------------------------------------------------------------------------------
@router.get("/health/live", tags=["health"])
async def live():
    return {"status": "alive"}


@router.get("/health/ready", tags=["health"])
async def ready():
    t = time.perf_counter()
    async with system_session() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok", "db_ms": round((time.perf_counter() - t) * 1000, 1)}


@router.get("/health/status", tags=["health"])
async def service_status(p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    """Operational status for the platform admin console (real checks, no hard-coded values)."""
    s = get_settings()
    out: dict = {"environment": s.app_env, "version": "1.0.0"}
    t = time.perf_counter()
    await conn.execute(text("SELECT 1"))
    out["database"] = {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    if s.redis_url:
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(s.redis_url)
            t = time.perf_counter()
            await r.ping()
            out["redis"] = {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
            await r.aclose()
        except Exception as exc:
            out["redis"] = {"ok": False, "error": type(exc).__name__}
    else:
        out["redis"] = {"configured": False}
    prov = get_routing_provider()
    out["routing"] = {"provider": prov.name, "configured": prov.name != "none"}
    out["deployment_flags"] = {"gps_sos_feature_enabled": s.gps_sos_feature_enabled, "live_tracking_feature_enabled": s.live_tracking_feature_enabled}
    out["notifications"] = {"send_real": s.notifications_send_real, "pending_deliveries": await conn.scalar(text("SELECT count(*) FROM notification_deliveries WHERE status='pending'")),
                            "failed_deliveries": await conn.scalar(text("SELECT count(*) FROM notification_deliveries WHERE status='failed'"))}
    out["organizations"] = {"total": await conn.scalar(text("SELECT count(*) FROM organizations")), "suspended": await conn.scalar(text("SELECT count(*) FROM organizations WHERE status='suspended'"))}
    return out


# ---- identity ----------------------------------------------------------------------------------------
@router.get("/me", tags=["auth"])
async def me(p: Principal = Depends(current_principal), conn: AsyncConnection = Depends(db_for)):
    orgs = (await conn.execute(text("SELECT organization_id, name, slug, role, suspended FROM rp.my_orgs() ORDER BY name"))).all()
    lang = await conn.scalar(text("SELECT language FROM user_profiles WHERE user_id=:u"), {"u": p.user_id})
    return {"user_id": p.user_id, "email": p.email, "full_name": p.full_name, "language": lang, "is_platform_admin": p.is_platform_admin,
            "active_organization_id": p.org_id, "role": p.role, "organization_suspended": p.org_suspended, "organizations": [_d(o) for o in orgs]}


@router.get("/features", tags=["settings"])
async def features(p: Principal = Depends(require_roles("org_admin", "dispatcher", "driver", "employee")), conn: AsyncConnection = Depends(db_for)):
    """Effective switches for client UX. Informational only: the server re-checks on every location request."""
    states = await feature_states(conn, p.org_id)
    return {k: v.as_dict() for k, v in states.items()}


# ---- organizations (platform admin) -----------------------------------------------------------------------
@router.get("/organizations", tags=["organizations"])
async def list_orgs(p: Principal = Depends(current_principal), conn: AsyncConnection = Depends(db_for), q: str | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    size, off = page_params(page, page_size)
    pat = f"%{(q or '').replace('%', '')[:80]}%"
    total = await conn.scalar(text("SELECT count(*) FROM organizations WHERE name ILIKE :q OR slug ILIKE :q"), {"q": pat})
    rows = (await conn.execute(text("SELECT * FROM organizations WHERE name ILIKE :q OR slug ILIKE :q ORDER BY name LIMIT :l OFFSET :f"), {"q": pat, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)    # RLS: members see only their orgs, platform admins all


@router.post("/organizations", status_code=201, tags=["organizations"])
async def create_org(payload: OrganizationCreate, request: Request, p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(payload.timezone)
    except Exception as exc:
        raise AppError(422, "invalid_timezone", "Unknown IANA timezone") from exc
    admin_id = await _ensure_profile(payload.admin_email, payload.admin_name)
    org = (await conn.execute(text("INSERT INTO organizations (name, slug, timezone) VALUES (:n,:s,:t) RETURNING *"), {"n": payload.name, "s": payload.slug, "t": payload.timezone})).first()
    await conn.execute(text("INSERT INTO organization_settings (organization_id, updated_by) VALUES (:o,:u)"), {"o": org.id, "u": p.user_id})
    await conn.execute(text("INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o,:u,'org_admin')"), {"o": org.id, "u": admin_id})
    await audit(conn, org_id=org.id, actor=p.user_id, action="organization.created", entity_type="organization", entity_id=org.id, details={"slug": payload.slug}, ip=client_ip(request))
    return _d(org)


@router.post("/organizations/{org_id}/status", tags=["organizations"])
async def set_org_status(org_id: uuid.UUID, status: str = Query(pattern="^(active|suspended)$"), request: Request = None,
                         p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    n = (await conn.execute(text("UPDATE organizations SET status=:s, suspended_reason=CASE WHEN :s='suspended' THEN 'manual' ELSE NULL END WHERE id=:i"), {"s": status, "i": org_id})).rowcount
    if not n:
        raise AppError(404, "not_found", "Organization not found")
    await audit(conn, org_id=org_id, actor=p.user_id, action=f"organization.{status}", entity_type="organization", entity_id=org_id, ip=client_ip(request) if request else None)
    return {"id": org_id, "status": status}


async def _ensure_profile(email: str, full_name: str) -> uuid.UUID:
    """Find the profile by email or invite through Supabase Auth and create it. Trusted server-side step."""
    async with system_session() as conn:
        uid = await conn.scalar(text("SELECT user_id FROM user_profiles WHERE lower(email)=lower(:e)"), {"e": email})
    if uid:
        return uid
    try:
        uid = await supabase_admin.invite_user(email, full_name)
    except SupabaseAdminError as exc:
        raise AppError(502, "auth_admin_unavailable", str(exc)) from exc
    async with system_session() as conn:
        await conn.execute(text("INSERT INTO user_profiles (user_id, email, full_name) VALUES (:u,:e,:n) ON CONFLICT (user_id) DO NOTHING"), {"u": uid, "e": email, "n": full_name})
    return uid


# ---- members (org admin) -------------------------------------------------------------------------------------
@router.get("/members", tags=["members"])
async def list_members(p: Principal = Depends(require_roles("org_admin", "dispatcher")), conn: AsyncConnection = Depends(db_for), role: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "r": role}
    cond = "m.organization_id=:o AND (CAST(:r AS text) IS NULL OR m.role=:r)"
    total = await conn.scalar(text(f"SELECT count(*) FROM organization_members m WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT m.id, m.user_id, m.role, m.is_active, up.email, up.full_name FROM organization_members m JOIN user_profiles up ON up.user_id=m.user_id WHERE {cond} ORDER BY up.email LIMIT :l OFFSET :f"),
                               {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)


@router.post("/members", status_code=201, tags=["members"])
async def add_member(payload: MemberIn, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    uid = await _ensure_profile(str(payload.email), payload.full_name)
    r = (await conn.execute(text("INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o,:u,:r) "
                                 "ON CONFLICT (organization_id, user_id) DO UPDATE SET role=EXCLUDED.role, is_active=true RETURNING id, user_id, role, is_active"),
                            {"o": p.org_id, "u": uid, "r": payload.role})).first()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="member.set", entity_type="member", entity_id=uid, details={"role": payload.role}, ip=client_ip(request))
    return _d(r)


@router.delete("/members/{user_id}", status_code=204, tags=["members"])
async def remove_member(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    admins = await conn.scalar(text("SELECT count(*) FROM organization_members WHERE organization_id=:o AND role='org_admin' AND is_active AND user_id <> :u"), {"o": p.org_id, "u": user_id})
    target = await conn.scalar(text("SELECT role FROM organization_members WHERE organization_id=:o AND user_id=:u AND is_active"), {"o": p.org_id, "u": user_id})
    if target is None:
        raise AppError(404, "not_found", "Member not found")
    if target == "org_admin" and admins == 0:
        raise AppError(409, "last_admin", "Cannot remove the last organization administrator")
    await conn.execute(text("UPDATE organization_members SET is_active=false WHERE organization_id=:o AND user_id=:u"), {"o": p.org_id, "u": user_id})
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="member.deactivated", entity_type="member", entity_id=user_id, ip=client_ip(request))


# ---- organization settings ------------------------------------------------------------------------------------------
async def _settings_view(conn: AsyncConnection, org_id: uuid.UUID) -> dict:
    row = (await conn.execute(text("SELECT s.*, up.full_name AS updated_by_name, up.email AS updated_by_email FROM organization_settings s LEFT JOIN user_profiles up ON up.user_id=s.updated_by WHERE s.organization_id=:o"), {"o": org_id})).first()
    if row is None:
        raise AppError(404, "not_found", "Settings not found")
    states = await feature_states(conn, org_id)
    d = _d(row)
    d["features"] = {k: v.as_dict() for k, v in states.items()}
    return d


@router.get("/admin/settings", tags=["settings"])
async def get_settings_ep(p: Principal = Depends(require_roles(*STAFF)), conn: AsyncConnection = Depends(db_for)):
    return await _settings_view(conn, p.org_id)


@router.put("/admin/settings", tags=["settings"])
async def update_settings(payload: SettingsPatch, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    data = payload.model_dump(exclude_unset=True, exclude={"confirm"})
    if not data:
        raise AppError(422, "empty_update", "No settings supplied")
    switches = {k: v for k, v in data.items() if k in ("sos_gps_enabled", "live_tracking_enabled")}
    if switches and not payload.confirm:
        raise AppError(422, "confirmation_required", "Changing SOS GPS or live tracking requires confirm=true", {"fields": sorted(switches)})
    states = await feature_states(conn, p.org_id)
    for key, feature in (("sos_gps_enabled", "sos_gps"), ("live_tracking_enabled", "live_tracking")):
        if data.get(key) is True:
            st = states[feature]
            if not (st.deployment_enabled and st.platform_enabled):
                raise AppError(409, "feature_unavailable", f"{feature} cannot be enabled: disabled at the {st.blocked_by} level", {"blocked_by": st.blocked_by})
    import json as _json
    sets, params = [], {"o": p.org_id, "u": p.user_id}
    for k, v in data.items():
        if k in ("fairness_weights", "notification_channels"):
            sets.append(f"{k} = CAST(:{k} AS jsonb)"); params[k] = _json.dumps(v)
        else:
            sets.append(f"{k} = :{k}"); params[k] = v
    before = (await conn.execute(text("SELECT sos_gps_enabled, live_tracking_enabled FROM organization_settings WHERE organization_id=:o"), {"o": p.org_id})).first()
    await conn.execute(text(f"UPDATE organization_settings SET {', '.join(sets)}, updated_by=:u WHERE organization_id=:o"), params)
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="settings.updated", entity_type="organization_settings", entity_id=p.org_id,
                details={"changed": data, "before": {"sos_gps_enabled": before.sos_gps_enabled, "live_tracking_enabled": before.live_tracking_enabled}}, ip=client_ip(request))
    if data.get("live_tracking_enabled") is False:
        await conn.execute(text("DELETE FROM vehicle_last_position WHERE organization_id=:o"), {"o": p.org_id})   # stop and clear when switched off
    return await _settings_view(conn, p.org_id)


# ---- platform flags --------------------------------------------------------------------------------------------------
@router.get("/platform/flags", tags=["platform"])
async def list_flags(p: Principal = Depends(current_principal), conn: AsyncConnection = Depends(db_for)):
    s = get_settings()
    rows = (await conn.execute(text("SELECT f.key, f.enabled, f.description, f.updated_at, up.email AS updated_by_email FROM platform_feature_flags f LEFT JOIN user_profiles up ON up.user_id=f.updated_by ORDER BY f.key"))).all()
    env = {"sos_gps": s.gps_sos_feature_enabled, "live_tracking": s.live_tracking_feature_enabled}
    return [{**_d(r), "deployment_enabled": env.get(r.key)} for r in rows]


@router.put("/platform/flags/{key}", tags=["platform"])
async def set_flag(key: str, payload: FlagPatch, request: Request, p: Principal = Depends(require_roles(platform_admin=True)), conn: AsyncConnection = Depends(db_for)):
    if not payload.confirm:
        raise AppError(422, "confirmation_required", "Changing a platform flag requires confirm=true")
    r = (await conn.execute(text("UPDATE platform_feature_flags SET enabled=:e, updated_by=:u WHERE key=:k RETURNING key, enabled"), {"e": payload.enabled, "k": key, "u": p.user_id})).first()
    if r is None:
        raise AppError(404, "not_found", "Unknown flag")
    await audit(conn, org_id=None, actor=p.user_id, action="platform_flag.set", entity_type="platform_flag", entity_id=key, details={"enabled": payload.enabled}, ip=client_ip(request))
    return _d(r)


# ---- audit logs ---------------------------------------------------------------------------------------------------------
@router.get("/audit-logs", tags=["audit"])
async def audit_logs(p: Principal = Depends(require_roles("org_admin", platform_admin=True)), conn: AsyncConnection = Depends(db_for), action: str | None = None,
                     entity_type: str | None = None, actor: uuid.UUID | None = None, since: datetime | None = None, until: datetime | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    size, off = page_params(page, page_size)
    params = {"o": p.org_id, "a": action, "e": entity_type, "u": actor, "s": since, "t": until}
    cond = ("organization_id = :o AND (CAST(:a AS text) IS NULL OR action LIKE :a || '%') AND (CAST(:e AS text) IS NULL OR entity_type=:e) AND (CAST(:u AS uuid) IS NULL OR actor_user_id=:u) "
            "AND (CAST(:s AS timestamptz) IS NULL OR created_at >= :s) AND (CAST(:t AS timestamptz) IS NULL OR created_at < :t)")
    total = await conn.scalar(text(f"SELECT count(*) FROM audit_logs WHERE {cond}"), params)
    rows = (await conn.execute(text(f"SELECT id, actor_user_id, action, entity_type, entity_id, details, created_at FROM audit_logs WHERE {cond} ORDER BY id DESC LIMIT :l OFFSET :f"), {**params, "l": size, "f": off})).all()
    return page_response([_d(r) for r in rows], total, page, size)
