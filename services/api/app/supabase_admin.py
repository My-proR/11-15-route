"""Server-side Supabase Auth admin calls (service-role key; never sent to browsers or mobile apps)."""
import uuid

import httpx

from .app_errors import SupabaseAdminError
from .config import get_settings


async def invite_user(email: str, full_name: str = "") -> uuid.UUID:
    """Invites a user by email via GoTrue and returns their auth user id."""
    s = get_settings()
    if not (s.supabase_url and s.supabase_service_role_key):
        raise SupabaseAdminError("Supabase admin credentials are not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{s.supabase_url.rstrip('/')}/auth/v1/invite",
                              headers={"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"},
                              json={"email": email, "data": {"full_name": full_name}})
    if r.status_code in (200, 201):
        return uuid.UUID(r.json()["id"])
    raise SupabaseAdminError(f"Supabase invite failed (HTTP {r.status_code}). If the user already exists, link them from the dashboard after they sign in once.")
