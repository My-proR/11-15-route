from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RoutePilot API"
    app_env: str = "development"          # development | test | staging | production
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    metrics_token: str = ""               # empty => /metrics disabled

    database_url: str = "postgresql://postgres@localhost:5432/routepilot"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_statement_timeout_ms: int = 15000
    run_worker_in_api: bool = False       # free single-process hosting: run the background worker inside the API process (otherwise run `python -m app.worker`)
    redis_url: str = ""                   # empty => in-memory rate limiting / fan-out (single instance only)

    # --- Supabase Auth -------------------------------------------------------------------
    supabase_url: str = ""
    supabase_jwt_issuer: str = ""         # expected `iss`; defaults to SUPABASE_URL + /auth/v1
    supabase_jwt_secret: str = ""         # HS256 projects
    supabase_jwks_url: str = ""           # asymmetric-key projects (…/auth/v1/.well-known/jwks.json)
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str = ""   # server-side only; used by scripts/bootstrap_admin.py

    # --- Deployment feature switches (authoritative, server-side, default OFF) ------------
    gps_sos_feature_enabled: bool = False
    live_tracking_feature_enabled: bool = False
    live_tracking_min_interval_seconds: int = 10
    sos_location_max_age_seconds: int = 120

    # --- Billing (shown on invoices) ---------------------------------------------------------
    billing_seller_name: str = ""
    billing_seller_gstin: str = ""
    billing_payment_instructions: str = ""      # e.g. UPI id / bank details printed on every invoice

    # --- Routing ---------------------------------------------------------------------------
    routing_provider: str = "osrm"        # osrm | none
    osrm_base_url: str = ""
    routing_timeout_seconds: float = 4.0
    routing_cache_ttl_seconds: int = 3600

    # --- CORS / limits -----------------------------------------------------------------------
    cors_origins: str = "http://localhost:5173"
    rate_limit_per_minute: int = 240
    rate_limit_sensitive_per_minute: int = 20
    max_import_bytes: int = 2_000_000
    max_import_rows: int = 5000
    ws_max_connections_per_user: int = 3
    realtime_ticket_secret: str = "dev-only-change-me-realtime-ticket-secret"
    realtime_ticket_ttl_seconds: int = 60

    # --- Notification providers (optional) ------------------------------------------------------
    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_sms_from: str = ""
    twilio_whatsapp_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""
    notifications_send_real: bool = False   # tests and dev never send real messages unless true
    notification_max_attempts: int = 5

    @model_validator(mode="after")
    def _production_guards(self):
        if self.app_env.lower() in {"production", "prod"}:
            if not (self.supabase_jwt_secret or self.supabase_jwks_url):
                raise ValueError("Production requires SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL")
            if not (self.supabase_jwt_issuer or self.supabase_url):
                raise ValueError("Production requires SUPABASE_JWT_ISSUER or SUPABASE_URL for JWT issuer validation")
            if self.realtime_ticket_secret.startswith("dev-only") or len(self.realtime_ticket_secret) < 32:
                raise ValueError("Production requires REALTIME_TICKET_SECRET (>= 32 chars)")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origins are not allowed in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
