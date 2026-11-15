"""Normalize hosted PostgreSQL URLs for SQLAlchemy's async and migration drivers."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _rewrite_url(raw_url: str, driver: str, *, async_driver: bool) -> str:
    parts = urlsplit(raw_url)
    scheme = parts.scheme
    if scheme in {"postgres", "postgresql"}:
        scheme = f"postgresql+{driver}"
    elif scheme.startswith("postgresql+"):
        scheme = f"postgresql+{driver}"

    query = parse_qsl(parts.query, keep_blank_values=True)
    if async_driver:
        # Neon and other hosted providers commonly supply libpq parameters.
        # asyncpg uses `ssl` and does not accept libpq's `channel_binding` option.
        normalized = []
        for key, value in query:
            if key == "sslmode":
                normalized.append(("ssl", value))
            elif key == "channel_binding":
                continue
            else:
                normalized.append((key, value))
        query = normalized

    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def normalize_async_database_url(raw_url: str) -> str:
    """Convert common PostgreSQL URLs into a SQLAlchemy asyncpg URL."""
    return _rewrite_url(raw_url, "asyncpg", async_driver=True)


def normalize_migration_database_url(raw_url: str) -> str:
    """Convert common PostgreSQL URLs into a SQLAlchemy psycopg URL."""
    return _rewrite_url(raw_url, "psycopg", async_driver=False)
