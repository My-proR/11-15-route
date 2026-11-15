"""Per cab-day billing: rates, monthly invoices, overdue suspension."""
from pathlib import Path
from alembic import context, op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
SQL = Path(__file__).resolve().parent.parent / "sql" / "billing.sql"


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
    DROP TABLE IF EXISTS invoices;
    DROP SEQUENCE IF EXISTS invoice_number_seq;
    DROP TABLE IF EXISTS organization_billing;
    DROP FUNCTION IF EXISTS rp.my_orgs();
    DROP FUNCTION IF EXISTS rp.is_org_admin(uuid);
    ALTER TABLE organizations DROP COLUMN IF EXISTS suspended_reason;
    """)
