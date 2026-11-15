"""Short-lived signed trip QR. The driver's app shows it for an IN_PROGRESS trip; a passenger's app scans it to board.
Token = "rp1." + base64url(json{o: org, a: allocation, e: expiry epoch, n: nonce}) + "." + HMAC-SHA256 signature. Stateless; expires in TTL seconds."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from ..config import get_settings

TTL_SECONDS = 60
PREFIX = "rp1"


def _key() -> bytes:
    return ("trip-qr:" + get_settings().realtime_ticket_secret).encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(org_id: uuid.UUID, allocation_id: uuid.UUID, *, now: float | None = None) -> tuple[str, int]:
    exp = int((now if now is not None else time.time()) + TTL_SECONDS)
    body = _b64(json.dumps({"o": str(org_id), "a": str(allocation_id), "e": exp, "n": secrets.token_hex(4)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())
    return f"{PREFIX}.{body}.{sig}", exp


def read_token(token: str, *, now: float | None = None) -> tuple[uuid.UUID, uuid.UUID] | str:
    """Returns (org_id, allocation_id) or an error code: malformed | bad_signature | expired."""
    try:
        prefix, body, sig = token.split(".")
        if prefix != PREFIX:
            return "malformed"
        want = _b64(hmac.new(_key(), f"{prefix}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return "bad_signature"
        data = json.loads(_unb64(body))
        if int(data["e"]) < (now if now is not None else time.time()):
            return "expired"
        return uuid.UUID(data["o"]), uuid.UUID(data["a"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return "malformed"
