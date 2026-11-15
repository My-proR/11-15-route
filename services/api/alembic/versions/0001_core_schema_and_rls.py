"""Core schema, tenant constraints, triggers and row level security.

SQL lives in alembic/sql so the same files can be exported for the Supabase CLI.
Rollback drops everything created here (destructive: takes a backup first).
"""
from pathlib import Path
from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

def _run(sql: str) -> None:
    # Raw driver execution online: the SQL contains literal % (format()) which SQLAlchemy would treat as bind markers.
    if context.is_offline_mode():
        op.execute(sql)
    else:
        raw = op.get_bind().connection.driver_connection      # psycopg connection; execute() without params does no % parsing
        with raw.cursor() as cur:
            cur.execute(sql)


def upgrade():
    for name in ("0001_schema.sql", "0002_rls.sql"):
        _run((SQL_DIR / name).read_text(encoding="utf-8"))

def downgrade():
    """Destructive: drops every RoutePilot table and the rp helper schema. Take a backup first."""
    _run("""
    DO $$ DECLARE t text; BEGIN
      FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version' LOOP
        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(t) || ' CASCADE';
      END LOOP;
    END $$;
    DROP SCHEMA IF EXISTS rp CASCADE;
    """)
