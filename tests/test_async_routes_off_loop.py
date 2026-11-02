"""The two webhook routes that must be coroutines keep blocking work off the loop.

`/api/v1/bridge/any` and the Stripe webhook are `async def` because they
read the raw body. Everything after that was called inline: the credential
lookup, SQLite, the whole breach path with its Twilio fan-out and Slack
hooks, and for Stripe the receipt email over SMTP. Inline in a coroutine,
that runs on the one event-loop thread, so a breach reported through the
"send anything" door while Twilio was slow stopped every request in the
process, including other businesses' sensors and /api/health, which is
what the host polls before deciding the container is dead. The ordinary
sensor route was fixed for exactly this; these two were not.
"""

import threading
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient

import hardware_bridge
import payments
from main import app

# How long a blocked handler is held before it is let go regardless. Only
# a broken build ever waits this long: a working one releases it as soon as
# /api/health has answered, which takes milliseconds.
HOLD = 5.0


class _Held:
    """A handler that blocks until told to go, in place of a slow one.

    A real sleep made each test wait out the whole delay even when the
    route was fine. Holding the handler on an event proves more and costs
    less: /api/health is asked while the webhook is still provably stuck,
    and the webhook is let go the moment health has answered.
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def block(self):
        self.entered.set()
        self.release.wait(HOLD)


def _health_while(client, held, call):
    worker = threading.Thread(target=call)
    worker.start()
    assert held.entered.wait(HOLD), "the webhook never reached its slow part"
    started = time.monotonic()
    resp = client.get("/api/health")
    took = time.monotonic() - started
    stuck = not held.release.is_set()
    held.release.set()
    worker.join()
    assert resp.status_code == 200
    assert stuck and took < HOLD / 2, (
        f"/api/health waited {took:.2f}s behind one webhook"
    )


def test_the_any_payload_door_does_not_stop_everything_else(monkeypatch):
    held = _Held()

    def slow_auth(token, authorization):
        held.block()
        raise HTTPException(status_code=401, detail="no")

    monkeypatch.setattr(hardware_bridge, "_authenticate_webhook", slow_auth)
    with TestClient(app) as client:
        _health_while(client, held, lambda: client.post(
            "/api/v1/bridge/any", json={"sensor": "X", "temperature_f": 40}))


def test_the_stripe_webhook_does_not_stop_everything_else(monkeypatch):
    held = _Held()

    def slow_apply(event):
        held.block()
        return {"handled": False}

    monkeypatch.setattr(payments, "verify_signature", lambda payload, sig: None)
    monkeypatch.setattr(payments, "apply_event", slow_apply)
    with TestClient(app) as client:
        _health_while(client, held, lambda: client.post(
            "/api/payments/stripe", content=b'{"id": "evt_1", "type": "x"}',
            headers={"Stripe-Signature": "t=1,v1=x"}))
