"""Money arriving, and the three ways an unauthenticated webhook goes wrong.

This endpoint takes no credential of ours, because Stripe holds none. The
signature *is* the authentication, and without it the URL is a button for
marking every invoice in the system paid — a strictly better attack than
breaking in, because it looks like being paid.

The other two: Stripe retries anything that is not a 2xx for days, so a
payment applied per delivery is a payment applied five times; and a
payment that cannot be tied to an invoice must be visible rather than
dropped, because money we have taken and cannot account for means a
customer who has paid and is still being chased for it.
"""

import hashlib
import hmac
import json
import time

import pytest

import payments
from contracts import run_billing
from store import STORE

SECRET = "whsec_test_secret"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", SECRET)


def sign(payload: bytes, secret: str = SECRET, at: int = None) -> str:
    at = at if at is not None else int(time.time())
    digest = hmac.new(
        secret.encode(), f"{at}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={at},v1={digest}"


def event(invoice_id=None, cents=10000, kind="checkout.session.completed",
          event_id="evt_1", currency="usd", status="paid"):
    obj = {"amount_total": cents, "currency": currency, "payment_status": status}
    if invoice_id:
        obj["client_reference_id"] = invoice_id
    return {"id": event_id, "type": kind, "data": {"object": obj}}


def post(api, body: dict, signature=None, secret=SECRET, at=None):
    payload = json.dumps(body).encode()
    header = signature if signature is not None else sign(payload, secret, at)
    return api.post(
        "/api/payments/stripe", content=payload,
        headers={"Stripe-Signature": header, "Content-Type": "application/json"},
    )


@pytest.fixture()
def invoice(api, tenant_factory, sensor_factory, owner_headers, mailbox):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    api.post("/api/contracts", headers={**headers, **owner},
             json={"term_years": 1, "escalator_percent": 5.0})
    run_billing()
    mailbox.clear()
    return STORE.invoices_for(tenant["tenant_id"])[0]


# ---- the signature is the authentication -------------------------------


def test_a_payment_without_a_signature_is_refused(api, invoice):
    resp = post(api, event(invoice.invoice_id), signature="")

    assert resp.status_code == 400
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0


def test_a_forged_signature_is_refused(api, invoice):
    """The whole attack: anyone who learns the URL marks everything paid."""
    resp = post(api, event(invoice.invoice_id), secret="whsec_attacker")

    assert resp.status_code == 400
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0


def test_a_body_changed_after_signing_is_refused(api, invoice):
    """The signature covers the bytes, so tampering has to break it.

    An attacker who captures a real $1 payment and edits it to $50,000 —
    or repoints it at another customer's invoice — must not be believed.
    """
    payload = json.dumps(event(invoice.invoice_id, cents=100)).encode()
    header = sign(payload)
    tampered = payload.replace(b'"amount_total": 100', b'"amount_total": 5000000')

    resp = api.post("/api/payments/stripe", content=tampered,
                    headers={"Stripe-Signature": header})

    assert resp.status_code == 400
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0


def test_an_old_signature_cannot_be_replayed(api, invoice):
    """A valid signature stays valid forever without a timestamp window,
    so one captured request would settle invoices for as long as the
    secret lives."""
    stale = int(time.time()) - payments.SIGNATURE_TOLERANCE_SECONDS - 60

    resp = post(api, event(invoice.invoice_id), at=stale)

    assert resp.status_code == 400
    assert "replay" in resp.json()["detail"]


def test_a_signature_from_the_near_past_is_accepted(api, invoice):
    """Clocks drift and networks are slow; the window is not zero."""
    recent = int(time.time()) - 30

    assert post(api, event(invoice.invoice_id), at=recent).status_code == 200


def test_an_unconfigured_deployment_refuses_everything(api, invoice, monkeypatch):
    """Rather than trusting the body, which is the same as no check."""
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "")

    resp = post(api, event(invoice.invoice_id))

    assert resp.status_code == 503
    assert "cannot tell Stripe from anybody else" in resp.json()["detail"]


def test_a_rotated_secret_accepts_either_signature(api, invoice):
    """Stripe sends several v1 signatures during a rotation."""
    payload = json.dumps(event(invoice.invoice_id)).encode()
    at = int(time.time())
    good = sign(payload, SECRET, at).split("v1=")[1]
    header = f"t={at},v1=deadbeef,v1={good}"

    resp = api.post("/api/payments/stripe", content=payload,
                    headers={"Stripe-Signature": header})

    assert resp.status_code == 200


# ---- it settles the invoice --------------------------------------------


def test_a_payment_settles_the_invoice_it_names(api, invoice, mailbox):
    resp = post(api, event(invoice.invoice_id, cents=int(invoice.total_usd * 100)))

    assert resp.status_code == 200
    body = resp.json()
    assert body["handled"] is True and body["applied"] is True

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.state == "paid"
    assert live.balance_usd == 0.0
    # And the customer is told, which is how they learn the chasing stopped.
    assert "paid" in mailbox[-1]["Subject"]


def test_a_part_payment_leaves_it_open_and_chaseable(api, invoice):
    post(api, event(invoice.invoice_id, cents=10000))

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.state == "part_paid"
    assert live.balance_usd == round(invoice.total_usd - 100.0, 2)


def test_cents_are_read_as_cents(api, invoice):
    """Stripe counts in the smallest unit. Getting this wrong by 100x is
    the classic integration bug, in whichever direction."""
    post(api, event(invoice.invoice_id, cents=12345))

    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 123.45


# ---- retries ------------------------------------------------------------


def test_the_same_event_delivered_five_times_pays_once(api, invoice, mailbox):
    """Stripe retries anything that is not a 2xx, for days."""
    for _ in range(5):
        resp = post(api, event(invoice.invoice_id, cents=10000, event_id="evt_same"))
        assert resp.status_code == 200

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.amount_paid_usd == 100.0
    assert len(live.payments) == 1
    assert len([m for m in mailbox if "received" in m["Subject"]]) == 1


def test_two_genuine_payments_are_two(api, invoice):
    post(api, event(invoice.invoice_id, cents=10000, event_id="evt_1"))
    post(api, event(invoice.invoice_id, cents=25000, event_id="evt_2"))

    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 350.0


def test_an_event_we_do_not_handle_gets_a_polite_200(api, invoice):
    """A non-2xx makes Stripe retry a thing it will never like better."""
    resp = post(api, event(invoice.invoice_id, kind="customer.subscription.updated"))

    assert resp.status_code == 200
    assert resp.json()["handled"] is False


def test_an_abandoned_checkout_pays_nothing(api, invoice):
    """Stripe sends the event whether or not they went through with it."""
    resp = post(api, event(invoice.invoice_id, status="unpaid"))

    assert resp.json()["handled"] is False
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0


# ---- money we cannot account for ---------------------------------------


def test_a_payment_naming_no_invoice_is_recorded_not_dropped(api, invoice):
    """Every unmatched row is somebody who has paid and is still being
    chased for it. Guessing which invoice it belongs to would be worse."""
    resp = post(api, event(None, cents=50000, event_id="evt_orphan"))

    assert resp.status_code == 200
    assert resp.json()["reason"] == "unmatched"
    rows = payments.unmatched()
    assert [r["event_id"] for r in rows] == ["evt_orphan"]
    assert rows[0]["amount_usd"] == 500.0


def test_a_payment_naming_an_invoice_that_does_not_exist_is_recorded(api):
    post(api, event("INV-000999", event_id="evt_ghost"))

    assert payments.unmatched()[0]["why"] == "no invoice INV-000999 exists"


def test_a_payment_in_another_currency_is_not_converted(api, invoice):
    """Converting would invent an exchange rate and put it on a
    customer's ledger."""
    post(api, event(invoice.invoice_id, currency="eur", event_id="evt_eur"))

    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0
    assert payments.unmatched()[0]["why"] == "not a USD payment"


def test_a_payment_against_a_voided_invoice_needs_a_person(
    api, admin_headers, invoice
):
    api.post(f"/api/invoices/{invoice.invoice_id}/void",
             params={"tenant_id": invoice.tenant_id}, headers=admin_headers)

    post(api, event(invoice.invoice_id, event_id="evt_void"))

    assert "voided" in payments.unmatched()[0]["why"]


def test_the_unmatched_list_needs_the_platform_key(api):
    assert api.get("/api/payments/unmatched").status_code == 401


def test_the_operator_can_see_and_clear_unmatched_money(
    api, admin_headers, invoice
):
    post(api, event(None, cents=50000, event_id="evt_orphan"))

    body = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert body["count"] == 1 and body["total_usd"] == 500.0

    cleared = api.request("DELETE", "/api/payments/unmatched/evt_orphan",
                          headers=admin_headers)
    assert cleared.status_code == 200
    assert payments.unmatched() == []


def test_unmatched_money_reaches_the_operator_digest(api, invoice, monkeypatch):
    """It is not a technical curiosity. It is a customer being chased for
    money they have already sent."""
    import digest

    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "founder@example.com")
    post(api, event(None, cents=50000, event_id="evt_orphan"))

    warnings = digest.operator_digest()["warnings"]
    assert any("could not be matched" in w for w in warnings)


# ---- the link that makes matching possible -----------------------------


def test_the_pay_link_carries_the_invoice_id(api, invoice, monkeypatch):
    """Without it every payment arrives unlabelled, and the reconciliation
    the webhook exists to remove is back, by hand."""
    import mail

    monkeypatch.setattr(
        mail, "PAY_URL", "https://pay.example/x?client_reference_id={invoice}"
    )

    assert mail.pay_link(invoice).endswith(invoice.invoice_id)
    assert invoice.invoice_id in mail.payment_instructions(invoice)


def test_a_plain_pay_url_still_works(api, invoice, monkeypatch):
    import mail

    monkeypatch.setattr(mail, "PAY_URL", "https://pay.example/checkout")

    assert mail.pay_link(invoice) == "https://pay.example/checkout"
