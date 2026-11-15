import time

import jwt
import pytest
from fastapi import HTTPException

from app import auth
from app.config import Settings

pytestmark = pytest.mark.no_db


def test_production_requires_jwt_issuer_and_realtime_secret():
    with pytest.raises(ValueError, match="SUPABASE_JWT_ISSUER or SUPABASE_URL"):
        Settings(app_env="production", supabase_jwt_secret="secret", realtime_ticket_secret="x" * 40)


def test_production_accepts_explicit_issuer_and_strong_realtime_secret():
    settings = Settings(
        app_env="production",
        supabase_jwt_secret="local-test-signing-secret",
        supabase_jwt_issuer="https://example.supabase.co/auth/v1",
        realtime_ticket_secret="r" * 40,
        cors_origins="https://routepilot.example",
    )
    assert settings.app_env == "production"


def test_decode_token_rejects_wrong_issuer(monkeypatch):
    secret = "unit-test-secret-that-is-long-enough"
    settings = Settings(
        app_env="test", supabase_jwt_secret=secret,
        supabase_jwt_issuer="https://expected.supabase.co/auth/v1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    token = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "aud": "authenticated",
         "iss": "https://attacker.supabase.co/auth/v1", "exp": int(time.time()) + 300},
        secret, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(token)
    assert exc.value.status_code == 401


def test_decode_token_accepts_expected_issuer(monkeypatch):
    secret = "unit-test-secret-that-is-long-enough"
    settings = Settings(
        app_env="test", supabase_jwt_secret=secret,
        supabase_jwt_issuer="https://expected.supabase.co/auth/v1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    token = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "aud": "authenticated",
         "iss": "https://expected.supabase.co/auth/v1", "exp": int(time.time()) + 300},
        secret, algorithm="HS256",
    )
    assert auth.decode_token(token)["iss"] == settings.supabase_jwt_issuer
