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

SLOW = 1.5


def _health_while(client, call):
    worker = threading.Thread(target=call)
    worker.start()
    time.sleep(0.3)
    started = time.monotonic()
    resp = client.get("/api/health")
    took = time.monotonic() - started
    worker.join()
    assert resp.status_code == 200
    return took


def test_the_any_payload_door_does_not_stop_everything_else(monkeypatch):
    def slow_auth(token, authorization):
        time.sleep(SLOW)
        raise HTTPException(status_code=401, detail="no")

    monkeypatch.setattr(hardware_bridge, "_authenticate_webhook", slow_auth)
    with TestClient(app) as client:
        took = _health_while(client, lambda: client.post(
            "/api/v1/bridge/any", json={"sensor": "X", "temperature_f": 40}))
    assert took < SLOW - 0.5, f"/api/health waited {took:.2f}s behind one webhook"


def test_the_stripe_webhook_does_not_stop_everything_else(monkeypatch):
    def slow_apply(event):
        time.sleep(SLOW)
        return {"handled": False}

    monkeypatch.setattr(payments, "verify_signature", lambda payload, sig: None)
    monkeypatch.setattr(payments, "apply_event", slow_apply)
    with TestClient(app) as client:
        took = _health_while(client, lambda: client.post(
            "/api/payments/stripe", content=b'{"id": "evt_1", "type": "x"}',
            headers={"Stripe-Signature": "t=1,v1=x"}))
    assert took < SLOW - 0.5, f"/api/health waited {took:.2f}s behind one webhook"
