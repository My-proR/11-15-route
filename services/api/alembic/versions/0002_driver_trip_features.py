"""Driver availability switch and per-passenger boarding code (code is off by default)."""
from pathlib import Path
from alembic import context, op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
SQL = Path(__file__).resolve().parent.parent / "sql" / "driver_trip_features.sql"


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
    DROP FUNCTION IF EXISTS rp.board_self(uuid, uuid);
    DROP FUNCTION IF EXISTS rp.set_my_availability(uuid, boolean);
    ALTER TABLE drivers DROP COLUMN IF EXISTS availability_updated_at;
    DROP FUNCTION IF EXISTS rp.verify_boarding_code(uuid, uuid, uuid, text, int, int);
    DROP FUNCTION IF EXISTS rp.ensure_boarding_codes(uuid, uuid);
    DROP TABLE IF EXISTS trip_boarding_codes;
    ALTER TABLE organization_settings DROP COLUMN IF EXISTS boarding_code_required;
    """)
