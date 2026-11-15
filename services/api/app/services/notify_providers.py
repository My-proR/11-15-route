"""Provider-independent notification delivery.

A provider returns `Sent(message_id)` when the provider ACCEPTED the message ('sent' status, not 'delivered'); only a receipt /
status callback moves a delivery to 'delivered'. Transient errors are retried with exponential backoff; permanent errors and
missing contact details are final. Nothing is sent unless NOTIFICATIONS_SEND_REAL=true and credentials exist.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from ..config import get_settings


@dataclass
class Message:
    notification_id: str
    title: str
    body: str
    data: dict
    critical: bool
    phone: str | None = None
    email: str | None = None
    expo_tokens: tuple[str, ...] = ()
    web_subscriptions: tuple[str, ...] = ()       # JSON-encoded PushSubscription objects


@dataclass
class Sent:
    message_id: str | None = None


class TransientError(Exception):
    """Retry later (network error, 5xx, 429)."""


class PermanentError(Exception):
    """Do not retry (invalid credentials, bad recipient)."""


class Skipped(Exception):
    """Nothing to deliver to / provider disabled."""


class Provider(Protocol):
    channel: str
    async def send(self, msg: Message) -> Sent: ...


def _check(r: httpx.Response) -> None:
    if r.status_code == 429 or r.status_code >= 500:
        raise TransientError(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise PermanentError(f"HTTP {r.status_code}: {r.text[:200]}")


class ExpoPushProvider:
    channel = "push_expo"

    async def send(self, msg: Message) -> Sent:
        if not msg.expo_tokens:
            raise Skipped("no_expo_token")
        s = get_settings()
        payload = [{"to": t, "title": msg.title, "body": msg.body, "data": msg.data, "sound": "default", "priority": "high" if msg.critical else "default"} for t in msg.expo_tokens]
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(s.expo_push_url, json=payload, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise TransientError(type(exc).__name__) from exc
        _check(r)
        tickets = r.json().get("data", [])
        ok = [t for t in tickets if t.get("status") == "ok"]
        if not ok:
            raise PermanentError(f"Expo rejected all tokens: {tickets[:1]}")
        return Sent(",".join(t["id"] for t in ok))


class WebPushProvider:
    channel = "push_web"

    async def send(self, msg: Message) -> Sent:
        s = get_settings()
        if not msg.web_subscriptions:
            raise Skipped("no_web_subscription")
        if not (s.vapid_private_key and s.vapid_subject):
            raise Skipped("web_push_not_configured")
        try:
            from pywebpush import WebPushException, webpush
        except ImportError as exc:       # optional dependency
            raise Skipped("pywebpush_not_installed") from exc
        sent = 0
        for sub in msg.web_subscriptions:
            try:
                await asyncio.to_thread(webpush, subscription_info=json.loads(sub), data=json.dumps({"title": msg.title, "body": msg.body, "data": msg.data}),
                                        vapid_private_key=s.vapid_private_key, vapid_claims={"sub": s.vapid_subject})
                sent += 1
            except WebPushException as exc:
                code = getattr(getattr(exc, "response", None), "status_code", 0)
                if code in (404, 410):
                    continue
                if code == 429 or code >= 500 or code == 0:
                    raise TransientError(str(exc)[:120]) from exc
                raise PermanentError(str(exc)[:120]) from exc
        if not sent:
            raise PermanentError("all web push subscriptions expired")
        return Sent(None)


class TwilioProvider:
    def __init__(self, channel: str):
        self.channel = channel     # 'sms' | 'whatsapp'

    async def send(self, msg: Message) -> Sent:
        s = get_settings()
        sender = s.twilio_sms_from if self.channel == "sms" else s.twilio_whatsapp_from
        if not (s.twilio_account_sid and s.twilio_auth_token and sender):
            raise Skipped(f"{self.channel}_not_configured")
        if not msg.phone:
            raise Skipped("no_phone_number")
        to, frm = msg.phone, sender
        if self.channel == "whatsapp":
            to, frm = f"whatsapp:{to}", f"whatsapp:{sender}"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                                 auth=(s.twilio_account_sid, s.twilio_auth_token), data={"To": to, "From": frm, "Body": f"{msg.title}: {msg.body}"[:1500]})
        except httpx.HTTPError as exc:
            raise TransientError(type(exc).__name__) from exc
        _check(r)
        return Sent(r.json().get("sid"))


class EmailProvider:
    channel = "email"

    async def send(self, msg: Message) -> Sent:
        s = get_settings()
        if not (s.smtp_host and s.smtp_from):
            raise Skipped("email_not_configured")
        if not msg.email:
            raise Skipped("no_email_address")
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = msg.title, s.smtp_from, msg.email
        m.set_content(msg.body)

        def _send() -> None:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(m)
        try:
            await asyncio.to_thread(_send)
        except (smtplib.SMTPException, OSError) as exc:
            raise TransientError(type(exc).__name__) from exc
        return Sent(None)


def default_providers() -> dict[str, Provider]:
    return {p.channel: p for p in (ExpoPushProvider(), WebPushProvider(), TwilioProvider("sms"), TwilioProvider("whatsapp"), EmailProvider())}


def backoff_seconds(attempt: int) -> int:
    """30s, 60s, 120s ... capped at 1h."""
    return min(30 * (2 ** max(attempt - 1, 0)), 3600)


def verify_twilio_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(digest, signature)
