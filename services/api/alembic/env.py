import os
from alembic import context
from sqlalchemy import create_engine, pool
from app.database_url import normalize_migration_database_url

url = normalize_migration_database_url(
    os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/routepilot"))

def run_offline():
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()

run_offline() if context.is_offline_mode() else run_online()
