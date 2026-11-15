"""Invite links, language preference, auto-dispatch at a cutoff time."""
from pathlib import Path
from alembic import context, op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
SQL = Path(__file__).resolve().parent.parent / "sql" / "onboarding_autodispatch.sql"


def _run(sql: str) -> None:
    if context.is_offline_mode():
        op.execute(sql)
    else:
        raw = op.get_bind().connection.driver_connection
        with raw.cursor() as cur:
            cur.execute(sql)


def upgrade() -> None:
    _run(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    _run("""
    DROP TABLE IF EXISTS auto_dispatch_runs;
    ALTER TABLE organization_settings DROP COLUMN IF EXISTS auto_dispatch_enabled, DROP COLUMN IF EXISTS auto_dispatch_cutoff,
      DROP COLUMN IF EXISTS auto_dispatch_days_ahead, DROP COLUMN IF EXISTS auto_dispatch_send_to_drivers;
    DROP TABLE IF EXISTS org_invites;
    DROP FUNCTION IF EXISTS rp.set_my_language(text);
    ALTER TABLE user_profiles DROP COLUMN IF EXISTS language;
    """)
