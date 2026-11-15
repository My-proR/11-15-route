import base64, hashlib, hmac, uuid
import pytest
from sqlalchemy import text
from app.common import notify
from app.config import get_settings
from app.db import system_session
from app.services.notify_providers import PermanentError, Sent, Skipped, TransientError, backoff_seconds, verify_twilio_signature
from app.worker import process_deliveries
from tests.conftest import API, H
from tests.helpers import count


class Fake:
    def __init__(self, channel, behaviour):
        self.channel, self.behaviour, self.calls = channel, behaviour, 0

    async def send(self, msg):
        self.calls += 1
        r = self.behaviour(self.calls)
        if isinstance(r, Exception):
            raise r
        return r


def providers(behaviour):
    return {c: Fake(c, behaviour) for c in ("push_expo", "push_web", "sms", "whatsapp", "email")}


async def make_notification(org, user, key=None, critical=False):
    async with system_session() as conn:
        return await notify(conn, org_id=org, user_id=user, event_type="test.event", title="t", body="b", critical=critical, dedupe_key=key)


async def run(provs, **kw):
    async with system_session() as conn:
        return await process_deliveries(conn, provs, **kw)


async def deliveries(org):
    async with system_session() as conn:
        return [dict(r._mapping) for r in await conn.execute(text("SELECT channel,status,attempts,last_error,provider_message_id FROM notification_deliveries WHERE organization_id=:o ORDER BY channel"), {"o": org})]


@pytest.fixture(autouse=True)
async def clean_queue(world):
    """The worker claims rows across all organizations, so each test starts from an empty queue."""
    async with system_session() as conn:
        await conn.execute(text("DELETE FROM notification_deliveries"))
        await conn.execute(text("DELETE FROM notifications"))
    yield


@pytest.fixture
def real_sending():
    s = get_settings()
    s.notifications_send_real = True
    yield
    s.notifications_send_real = False


async def test_sending_disabled_by_default_marks_skipped_never_sent(client, world):
    a = world.a
    await make_notification(a["org"], a["employee_actors"][0].id)
    out = await run(providers(lambda n: Sent("x")))
    assert out["sent"] == 0 and out["skipped"] == 2
    d = {x["channel"]: x for x in await deliveries(a["org"])}
    assert d["in_app"]["status"] == "delivered" and d["push_expo"]["status"] == "skipped" and "NOTIFICATIONS_SEND_REAL" in d["push_expo"]["last_error"]


async def test_sent_is_not_delivered_and_message_id_recorded(client, world, real_sending):
    a = world.a
    await make_notification(a["org"], a["employee_actors"][0].id)
    p = providers(lambda n: Sent("prov-1"))
    out = await run(p)
    assert out["sent"] == 2
    d = {x["channel"]: x for x in await deliveries(a["org"])}
    assert d["push_expo"]["status"] == "sent" and d["push_expo"]["provider_message_id"] == "prov-1"
    assert await count("SELECT count(*) FROM notification_deliveries WHERE organization_id=:o AND status='delivered' AND channel<>'in_app'", o=a["org"]) == 0
    assert (await run(p))["sent"] == 0                                    # already processed: no double send


async def test_transient_retry_backoff_then_give_up(client, world, real_sending):
    a = world.a
    s = get_settings()
    await make_notification(a["org"], a["employee_actors"][0].id)
    p = providers(lambda n: TransientError("HTTP 503"))
    out = await run(p)
    assert out["retry"] == 2
    assert (await run(p))["retry"] == 0                                   # not due yet (backoff)
    for _ in range(s.notification_max_attempts - 1):
        async with system_session() as conn:
            await conn.execute(text("UPDATE notification_deliveries SET next_attempt_at=now() WHERE status='pending'"))
        last = await run(p)
    assert last["failed"] == 2
    d = (await deliveries(a["org"]))
    assert all(x["status"] == "failed" and "gave up" in x["last_error"] for x in d if x["channel"] != "in_app")
    assert [backoff_seconds(i) for i in (1, 2, 3, 20)] == [30, 60, 120, 3600]


async def test_permanent_failure_and_adapter_crash_do_not_wedge_queue(client, world, real_sending):
    a = world.a
    await make_notification(a["org"], a["employee_actors"][0].id)
    seq = {"push_expo": PermanentError("bad token"), "push_web": RuntimeError("boom")}
    p = {c: Fake(c, (lambda e: (lambda n: e))(seq[c])) for c in seq}
    out = await run(p)
    assert out["failed"] == 2
    d = {x["channel"]: x for x in await deliveries(a["org"])}
    assert d["push_expo"]["last_error"] == "bad token" and d["push_web"]["last_error"] == "adapter_error: RuntimeError"
    # staff retry of failed deliveries is audited; a delivered one cannot be retried
    d_id = None
    async with system_session() as conn:
        d_id = await conn.scalar(text("SELECT id FROM notification_deliveries WHERE organization_id=:o AND channel='push_expo'"), {"o": a["org"]})
        in_app = await conn.scalar(text("SELECT id FROM notification_deliveries WHERE organization_id=:o AND channel='in_app'"), {"o": a["org"]})
    assert (await client.post(f"{API}/notifications/deliveries/{in_app}/retry", headers=H(a["dispatcher"]))).status_code == 409
    assert (await client.post(f"{API}/notifications/deliveries/{d_id}/retry", headers=H(a["employee_actors"][0]))).status_code == 403
    assert (await client.post(f"{API}/notifications/deliveries/{d_id}/retry", headers=H(world.b["dispatcher"]))).status_code == 409
    assert (await client.post(f"{API}/notifications/deliveries/{d_id}/retry", headers=H(a["dispatcher"]))).status_code == 200
    assert await count("SELECT count(*) FROM audit_logs WHERE organization_id=:o AND action='notification.retry'", o=a["org"]) == 1


async def test_dedupe_key_creates_one_notification(client, world):
    a = world.a
    u = a["employee_actors"][0].id
    first = await make_notification(a["org"], u, key="evt:1")
    second = await make_notification(a["org"], u, key="evt:1")
    assert first and second is None
    assert await count("SELECT count(*) FROM notifications WHERE organization_id=:o", o=a["org"]) == 1
    assert await count("SELECT count(*) FROM notification_deliveries WHERE organization_id=:o AND channel='in_app'", o=a["org"]) == 1


async def test_platform_flag_gates_sms_and_whatsapp(client, world, real_sending):
    a = world.a
    await client.put(f"{API}/admin/settings", json={"notification_channels": {"push": True, "sms": True, "whatsapp": True, "email": False}}, headers=H(a["admin"]))
    await make_notification(a["org"], a["employee_actors"][0].id, critical=True)
    p = providers(lambda n: Sent("m"))
    await run(p)
    d = {x["channel"]: x for x in await deliveries(a["org"])}
    assert d["sms"]["status"] == "skipped" and "platform flag" in d["sms"]["last_error"] and p["sms"].calls == 0
    assert d["whatsapp"]["status"] == "skipped"
    await make_notification(a["org"], a["employee_actors"][0].id, critical=True, key="again")
    async with system_session() as conn:
        await conn.execute(text("UPDATE platform_feature_flags SET enabled=true WHERE key='sms'"))
    await run(p)
    assert p["sms"].calls == 1


async def test_provider_skipped_exception_is_recorded(client, world, real_sending):
    a = world.a
    await make_notification(a["org"], a["employee_actors"][0].id)
    await run(providers(lambda n: Skipped("no device tokens")))
    d = {x["channel"]: x for x in await deliveries(a["org"])}
    assert d["push_expo"]["status"] == "skipped" and d["push_expo"]["last_error"] == "no device tokens"


async def test_inbox_isolated_and_read_state(client, world):
    a, b = world.a, world.b
    e0, e1 = a["employee_actors"][0], a["employee_actors"][1]
    await make_notification(a["org"], e0.id)
    inbox = (await client.get(f"{API}/notifications", headers=H(e0))).json()
    assert inbox["total"] == 1
    nid = inbox["items"][0]["id"]
    assert (await client.get(f"{API}/notifications", headers=H(e1))).json()["total"] == 0
    assert (await client.post(f"{API}/notifications/{nid}/read", headers=H(e1))).status_code in (204, 404)
    assert (await client.get(f"{API}/notifications?unread=true", headers=H(e0))).json()["total"] == 1
    assert (await client.post(f"{API}/notifications/{nid}/read", headers=H(e0))).status_code == 204
    assert (await client.get(f"{API}/notifications?unread=true", headers=H(e0))).json()["total"] == 0
    assert (await client.get(f"{API}/notifications/deliveries", headers=H(e0))).status_code == 403
    assert (await client.get(f"{API}/notifications/deliveries", headers=H(b["dispatcher"]))).json()["total"] == 0


async def test_preferences_and_push_registration(client, world):
    a = world.a
    e = a["employee_actors"][0]
    assert (await client.put(f"{API}/notifications/preferences", json={"push": False, "whatsapp": False, "sms": False, "email": False}, headers=H(e))).status_code == 200
    await make_notification(a["org"], e.id)
    assert [x["channel"] for x in await deliveries(a["org"])] == ["in_app"]                 # push opted out
    tok = "ExponentPushToken[" + uuid.uuid4().hex + "]"
    assert (await client.post(f"{API}/notifications/push-subscriptions", json={"kind": "expo", "token": tok}, headers=H(e))).status_code == 201
    assert (await client.post(f"{API}/notifications/push-subscriptions", json={"kind": "expo", "token": tok}, headers=H(e))).status_code == 201   # idempotent
    assert await count("SELECT count(*) FROM push_subscriptions WHERE organization_id=:o", o=a["org"]) == 1
    assert (await client.delete(f"{API}/notifications/push-subscriptions", params={"token": tok}, headers=H(e))).status_code == 204


async def test_twilio_webhook_requires_valid_signature_and_only_callback_marks_delivered(client, world):
    a = world.a
    s = get_settings()
    s.twilio_auth_token = "tok-secret"
    try:
        async with system_session() as conn:
            nid = await make_notification(a["org"], a["employee_actors"][0].id)
            await conn.execute(text("INSERT INTO notification_deliveries (organization_id, notification_id, channel, status, provider_message_id) VALUES (:o,:n,'whatsapp','sent','SM123')"), {"o": a["org"], "n": nid})
        url = "http://test" + API + "/notifications/webhooks/twilio"
        form = {"MessageSid": "SM123", "MessageStatus": "delivered"}
        sig = base64.b64encode(hmac.new(b"tok-secret", (url + "".join(k + form[k] for k in sorted(form))).encode(), hashlib.sha1).digest()).decode()
        assert verify_twilio_signature(url, form, sig, "tok-secret") and not verify_twilio_signature(url, form, "bad", "tok-secret")
        assert (await client.post(url, data=form, headers={"X-Twilio-Signature": "bad"})).status_code == 403
        assert await count("SELECT count(*) FROM notification_deliveries WHERE provider_message_id='SM123' AND status='delivered'") == 0
        r = await client.post(url, data=form, headers={"X-Twilio-Signature": sig})
        assert r.status_code == 200, r.text
        assert await count("SELECT count(*) FROM notification_deliveries WHERE provider_message_id='SM123' AND status='delivered'") == 1
    finally:
        s.twilio_auth_token = ""
