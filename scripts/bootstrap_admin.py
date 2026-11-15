"""Create (or promote) the first platform administrator.

Supabase Auth owns passwords. Create the user first (Dashboard -> Authentication -> Users -> Add user, or `--invite`),
then run this against the same database to give that auth user a RoutePilot profile with platform-admin rights.

  python scripts/bootstrap_admin.py --user-id <auth.users.id> --email admin@example.com --name "Platform Admin"
  python scripts/bootstrap_admin.py --invite admin@example.com          # uses SUPABASE_SERVICE_ROLE_KEY (server-side only)

Reads DATABASE_URL (and for --invite: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) from the environment / services/api/.env.
Never prints secrets. Idempotent.
"""
import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))
os.chdir(Path(__file__).resolve().parent.parent / "services" / "api")

from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, system_session  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-id")
    ap.add_argument("--email")
    ap.add_argument("--name", default="Platform Administrator")
    ap.add_argument("--invite", metavar="EMAIL", help="Invite this email through Supabase Auth first")
    a = ap.parse_args()
    user_id, email = a.user_id, a.email
    if a.invite:
        from app.supabase_admin import invite_user
        user_id, email = str(await invite_user(a.invite)), a.invite
        print(f"Invited {email} (auth user {user_id}). They must accept the email invitation to set a password.")
    if not user_id or not email:
        ap.error("--user-id and --email are required (or use --invite EMAIL)")
    uid = uuid.UUID(user_id)
    async with system_session() as conn:
        await conn.execute(text("""INSERT INTO user_profiles (user_id, email, full_name, is_platform_admin) VALUES (:u,:e,:n,true)
            ON CONFLICT (user_id) DO UPDATE SET is_platform_admin = true, is_active = true"""), {"u": uid, "e": email, "n": a.name})
        await conn.execute(text("""INSERT INTO audit_logs (organization_id, actor_user_id, action, entity_type, entity_id, details)
            VALUES (NULL, NULL, 'platform_admin.bootstrapped', 'user', :u, '{}'::jsonb)"""), {"u": str(uid)})
    await dispose_engine()
    print(f"{email} is now a platform administrator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
