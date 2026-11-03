"""One business through trial, paid, late and paid again, by card.

Stripe is played by a local fake that does what a payment link does: the
customer follows the link on the invoice, pays, and Stripe reports it as
three signed webhooks (the checkout, the payment intent and the charge),
retries them, and sometimes delivers them out of order or days later.
Nothing leaves the machine. The webhooks go through the real endpoint,
signed with the real scheme, so the signature check, the matching and the
ledger are the code that ships.

At every step the test checks what the customer sees and what keeps
running: the invoice's state, the banner on the console, which features
are on, and that a freezer going warm is still texted and still phoned
about. That last one most of all. A late payment may hold back reports;
it must never silence a freezer alarm.

Found here: an account under a signed contract had its monitoring
stopped by its own billing cycle. A month is billed in advance and paid
net 30, but the licence only runs on from a paid invoice, so it ran out
before the next invoice was even due and lapsed two weeks later, with
nothing overdue. Ingest answered 402 and the sweep stopped watching.
And the console said nothing about a late bill until day 45, when the
reports went.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

import mail
import payments
from auth import licence_state
from contracts import DELINQUENT_AFTER_DAYS, LATE_FEE_DAYS, run_billing, run_dunning
from automation import sweep_tenant
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, add_months, utc_now

SECRET = "whsec_local_fake_stripe"
PAY_URL = "https://pay.example/b/cyberlogix?client_reference_id={invoice}"
PASSWORD = "correct-horse-battery"


# ---- the fake Stripe ------------------------------------------------------


class FakeStripe:
    """A payment link, a checkout and the webhooks Stripe sends about it."""

    def __init__(self, api, secret=SECRET):
        self.api = api
        self.secret = secret
        self.sequence = 0
        self.delivered = []

    def _id(self, prefix):
        self.sequence += 1
        return f"{prefix}_fake{self.sequence:04d}"

    def checkout(self, link: str, cents: int):
        """The customer follows `link` and pays `cents`.

        A payment link passes `client_reference_id` from its URL straight
        onto the session, which is how the payment finds its invoice.
        Returns the three events Stripe would send, in its usual order.
        """
        query = parse_qs(urlsplit(link).query)
        reference = (query.get("client_reference_id") or [None])[0]
        intent = self._id("pi")
        session = {
            "id": self._id("cs"), "object": "checkout.session",
            "amount_total": cents, "currency": "usd",
            "payment_status": "paid", "status": "complete",
            "payment_intent": intent, "client_reference_id": reference,
            "metadata": {},
        }
        intent_obj = {
            "id": intent, "object": "payment_intent", "amount": cents,
            "amount_received": cents, "currency": "usd", "status": "succeeded",
            "metadata": {},
        }
        charge = {
            "id": self._id("ch"), "object": "charge", "amount": cents,
            "currency": "usd", "status": "succeeded", "paid": True,
            "payment_intent": intent, "metadata": {},
        }
        return [
            {"id": self._id("evt"), "type": "checkout.session.completed",
             "data": {"object": session}},
            {"id": self._id("evt"), "type": "payment_intent.succeeded",
             "data": {"object": intent_obj}},
            {"id": self._id("evt"), "type": "charge.succeeded",
             "data": {"object": charge}},
        ]

    def sign(self, payload: bytes, at: int) -> str:
        digest = hmac.new(self.secret.encode(), f"{at}.".encode() + payload,
                          hashlib.sha256).hexdigest()
        return f"t={at},v1={digest}"

    def deliver(self, event, signed_at=None, header=None):
        """One delivery attempt. Each retry is signed afresh, as Stripe does."""
        payload = json.dumps(event).encode()
        at = int(time.time()) if signed_at is None else signed_at
        resp = self.api.post(
            "/api/payments/stripe", content=payload,
            headers={"Stripe-Signature": header or self.sign(payload, at),
                     "Content-Type": "application/json"},
        )
        self.delivered.append((event["id"], resp.status_code))
        return resp


# ---- a phone company that remembers ---------------------------------------


@pytest.fixture()
def phone(monkeypatch):
    """Twilio, recording every text and call it is handed."""
    import notifications

    log = {"sms": [], "calls": []}

    class _Sent:
        def __init__(self, sid):
            self.sid = sid
            self.status = "queued"

    class _Endpoint:
        def __init__(self, kind):
            self.kind = kind

        def create(self, **kwargs):
            log[self.kind].append(kwargs)
            return _Sent(f"{self.kind}-{len(log[self.kind])}")

    class _Client:
        messages = _Endpoint("sms")
        calls = _Endpoint("calls")

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", _Client())
    monkeypatch.setattr(notifications, "_client_error", None)
    return log


@pytest.fixture()
def stripe(api, monkeypatch):
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(mail, "PAY_URL", PAY_URL)
    return FakeStripe(api)


# ---- the business ---------------------------------------------------------


class Business:
    """A restaurant group's one walk-in, and the owner's view of it."""

    def __init__(self, api):
        self.api = api
        made = api.post("/api/signup", json={
            "company_name": "Larkspur Kitchens", "full_name": "Robin Vale",
            "email": "robin@larkspur.example", "password": PASSWORD,
            "contact_phone": "+15550142", "industry_vertical": "restaurant",
        })
        assert made.status_code == 201, made.text
        body = made.json()
        self.tenant_id = body["tenant"]["tenant_id"]
        self.key = {"X-CyberLogix-Key": body["api_key"]}
        self.owner = {"Authorization": f"Bearer {body['token']}"}
        registered = api.post("/api/licenses/me/sensors", headers=self.key, json={
            "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
            "location_name": "Main kitchen / Walk-in freezer"})
        assert registered.status_code == 201, registered.text

    @property
    def tenant(self):
        return STORE.get_tenant(self.tenant_id)

    def pulse(self, fahrenheit):
        return self.api.post("/api/sensor-pulse", headers=self.key, json={
            "sensor_id": "WALKIN-1", "temperature_fahrenheit": fahrenheit})

    def account(self):
        resp = self.api.get("/api/console/overview", headers=self.owner)
        assert resp.status_code == 200, resp.text
        return resp.json()["account"]

    def features(self):
        """Which reporting features answer, by status code."""
        return {
            "compliance": self.api.get("/api/autopilot/compliance",
                                       headers=self.owner).status_code,
            "inspector_log": self.api.get("/api/autopilot/inspector-log",
                                          headers=self.owner).status_code,
            "benchmarks": self.api.get("/api/benchmarks/restaurant",
                                       headers=self.owner).status_code,
        }

    def invoices(self):
        resp = self.api.get("/api/invoices", headers=self.owner)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def freezer_alarm_still_works(self, phone):
        """A warm freezer is texted about, then phoned about, then answered.

        The whole point of the product, checked the same way at every step.
        """
        texts_before, calls_before = len(phone["sms"]), len(phone["calls"])
        warm = self.pulse(48.0)
        assert warm.status_code == 200, warm.text
        assert warm.json()["status"].startswith("CRITICAL"), warm.json()
        (incident,) = STORE.open_incidents(self.tenant_id)
        assert len(phone["sms"]) > texts_before, "the breach was not texted"

        # Nobody answers, so the sweep phones them, on a plan with calls.
        incident.opened_at -= timedelta(minutes=VOICE_ESCALATION_GRACE_MINUTES + 1)
        STORE._db.put("incident", incident.incident_id, incident.to_row())
        calls = 1 if self.tenant.entitlements()["voice_escalation"] else 0
        swept = sweep_tenant(self.tenant, auto_escalate=True)
        assert swept["voice_calls_placed"] == calls, swept
        assert len(phone["calls"]) == calls_before + calls

        # Somebody deals with it; the freezer comes back.
        acked = self.api.post(f"/api/voice/acknowledge/{incident.incident_id}",
                              headers=self.owner, json={})
        assert acked.status_code == 200, acked.text
        resolved = self.api.post(f"/api/voice/resolve/{incident.incident_id}",
                                 headers=self.owner, json={})
        assert resolved.status_code == 200, resolved.text
        assert self.pulse(-2.0).status_code == 200
        assert not STORE.open_incidents(self.tenant_id)


def _main_invoices(tenant_id):
    return [i for i in STORE.invoices_for(tenant_id)
            if not (i.source or "").startswith("late_fee:") and i.state != "void"]


def _a_month_passes(business):
    """Move the contract's start, and the paid-up time, a month back.

    The same as a month going by: the next period falls due for billing,
    and the month the first payment bought has been used up.
    """
    sub = STORE.active_subscription(business.tenant_id)
    sub.started_at = add_months(sub.started_at, -1) - timedelta(hours=1)
    STORE.save_subscription(sub)
    tenant = business.tenant
    tenant.expires_at = utc_now() - timedelta(hours=1)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())


def _late_by(invoice, days, business):
    """This invoice is `days` past due, and the paid-up time ran out with it."""
    invoice = STORE.get_invoice(invoice.invoice_id)
    invoice.due_at = utc_now() - timedelta(days=days, hours=1)
    invoice.issued_at = invoice.due_at - timedelta(days=30)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    tenant = business.tenant
    tenant.expires_at = invoice.issued_at
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())
    return invoice


def _cents(invoice):
    return int(round(STORE.get_invoice(invoice.invoice_id).balance_usd * 100))


def _link_in(message_bodies, invoice):
    for body in message_bodies:
        for word in body.split():
            if word.startswith("https://pay.example/") and invoice.invoice_id in word:
                return word
    raise AssertionError(f"no pay link for {invoice.number} in the mail")


# ---- the walk ---------------------------------------------------------------


def test_trial_paid_late_paid_by_card(api, stripe, phone, mailbox, admin_headers):
    biz = Business(api)
    assert biz.pulse(-2.0).status_code == 200

    # ---- 1. The trial ----------------------------------------------------
    account = biz.account()
    assert account["on_trial"] and account["licence_state"] == "active"
    assert account["severity"] == "ok" and account["headline"] is None
    assert not account["under_contract"] and account["pay_url"] == ""
    assert biz.invoices()["count"] == 0
    assert biz.features() == {"compliance": 200, "inspector_log": 200,
                              "benchmarks": 200}
    biz.freezer_alarm_still_works(phone)

    # ---- 2. Paid ---------------------------------------------------------
    upgraded = api.post("/api/licenses/me/plan", headers=biz.owner,
                        json={"plan": "growth"})
    assert upgraded.status_code == 200, upgraded.text
    signed = api.post("/api/contracts", headers=biz.owner,
                      json={"term_years": 1, "escalator_percent": 5.0})
    assert signed.status_code == 201, signed.text
    mailbox.clear()
    assert run_billing()["invoices_issued"] == 1
    (first,) = _main_invoices(biz.tenant_id)
    assert first.state == "issued" and not first.overdue()

    # The invoice email carries a pay link that names this invoice, and the
    # customer pays through it.
    link = _link_in(mailbox.bodies(), first)
    trial_ends = biz.tenant.expires_at
    first_events = stripe.checkout(link, _cents(first))
    results = [stripe.deliver(e) for e in first_events]
    assert [r.status_code for r in results] == [200, 200, 200]
    # Three events, one payment: applied once.
    assert [r.json().get("applied") for r in results] == [True, False, False]

    first = STORE.get_invoice(first.invoice_id)
    assert first.state == "paid" and first.balance_usd == 0
    assert len(first.payments) == 1
    assert biz.tenant.expires_at >= trial_ends + timedelta(days=29)
    receipts = [s for s in mailbox.subjects() if "receipt" in s.lower()
                or "paid" in s.lower()]
    assert len(receipts) == 1, mailbox.subjects()

    account = biz.account()
    assert not account["on_trial"] and account["under_contract"]
    assert account["severity"] == "ok" and account["headline"] is None
    assert account["pay_url"] == ""
    assert biz.invoices()["outstanding_count"] == 0
    assert biz.features() == {"compliance": 200, "inspector_log": 200,
                              "benchmarks": 200}
    biz.freezer_alarm_still_works(phone)

    # ---- 3. Late ---------------------------------------------------------
    _a_month_passes(biz)
    assert run_billing()["invoices_issued"] == 1
    second = [i for i in _main_invoices(biz.tenant_id)
              if i.invoice_id != first.invoice_id][0]

    # Issued, not yet due, and the month the first payment bought is over.
    # Under a contract that is not a lapse: the bill is not even late yet.
    assert biz.tenant.expired
    assert licence_state(biz.tenant) == "active"
    account = biz.account()
    assert account["severity"] == "ok", account["headline"]
    biz.freezer_alarm_still_works(phone)

    # A fortnight and more past due: chased, a late fee, and the paid-up
    # time well past where a lapse would have stopped the monitoring.
    second = _late_by(second, LATE_FEE_DAYS + 6, biz)
    chased = run_dunning()
    assert chased["notices_count"] >= 1 and len(chased["late_fees_issued"]) == 1
    assert utc_now() > biz.tenant.expires_at + timedelta(days=14)
    assert licence_state(biz.tenant) == "active"

    account = biz.account()
    assert account["severity"] == "warning"
    assert "past due" in account["headline"]
    assert "Reports pause" in account["headline"]
    assert second.invoice_id in account["pay_url"]
    assert account["alerting_continues"] is True
    listed = biz.invoices()
    assert listed["overdue_count"] == 1
    # Not yet delinquent: reports are still there.
    assert biz.features() == {"compliance": 200, "inspector_log": 200,
                              "benchmarks": 200}
    biz.freezer_alarm_still_works(phone)

    # Past the delinquency line: reports pause, the alarm does not.
    second = _late_by(second, DELINQUENT_AFTER_DAYS + 5, biz)
    account = biz.account()
    assert account["severity"] == "warning"
    assert "Reporting is withheld" in account["headline"]
    assert account["alerting_continues"] is True
    assert biz.features() == {"compliance": 402, "inspector_log": 402,
                              "benchmarks": 402}
    refused = api.get("/api/autopilot/compliance", headers=biz.owner).json()
    assert "alerting" in refused["detail"].lower()
    biz.freezer_alarm_still_works(phone)

    # ---- 4. Paid again, with Stripe being Stripe ------------------------
    second_link = account["pay_url"]
    events = stripe.checkout(second_link, _cents(second))
    checkout, intent, charge = events

    # A delivery captured ten minutes ago and replayed now is refused,
    # and changes nothing.
    stale = stripe.deliver(checkout, signed_at=int(time.time()) - 600)
    assert stale.status_code == 400
    assert STORE.get_invoice(second.invoice_id).state != "paid"

    # Out of order: the payment intent arrives first. It does not carry
    # the invoice id (only the checkout session does), so on its own it is
    # money nobody can place yet, and it is listed as such.
    first_in = stripe.deliver(intent)
    assert first_in.status_code == 200 and first_in.json()["reason"] == "unmatched"
    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["count"] == 1
    # The checkout arrives late and settles the invoice; the row the
    # intent left is the same payment, and goes.
    settled = stripe.deliver(checkout)
    assert settled.status_code == 200 and settled.json()["applied"] is True
    assert STORE.get_invoice(second.invoice_id).state == "paid"
    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["count"] == 0, listed
    # The charge, and every retry of all three, change nothing.
    for event in (charge, checkout, intent, charge):
        again = stripe.deliver(event)
        assert again.status_code == 200
        assert again.json()["applied"] is False

    # Last month's checkout, retried by Stripe weeks later, changes nothing.
    replayed = stripe.deliver(first_events[0])
    assert replayed.status_code == 200 and replayed.json()["applied"] is False
    assert len(STORE.get_invoice(first.invoice_id).payments) == 1

    second = STORE.get_invoice(second.invoice_id)
    assert second.state == "paid" and len(second.payments) == 1
    assert second.amount_paid_usd == pytest.approx(second.total_usd)

    # The late fee is its own bill, paid the same way.
    (fee,) = [i for i in STORE.invoices_for(biz.tenant_id)
              if (i.source or "").startswith("late_fee:")]
    fee_events = stripe.checkout(PAY_URL.replace("{invoice}", fee.invoice_id),
                                 _cents(fee))
    assert [stripe.deliver(e).status_code for e in fee_events] == [200] * 3
    assert STORE.get_invoice(fee.invoice_id).state == "paid"

    # Nothing paid twice, nothing left unaccounted for.
    unmatched = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert unmatched["count"] == 0, unmatched
    listed = biz.invoices()
    assert listed["outstanding_count"] == 0 and listed["overdue_count"] == 0

    account = biz.account()
    assert account["severity"] == "ok" and account["headline"] is None
    assert account["pay_url"] == ""
    assert biz.features() == {"compliance": 200, "inspector_log": 200,
                              "benchmarks": 200}
    biz.freezer_alarm_still_works(phone)


# ---- the bug, on its own ----------------------------------------------------


def test_a_contract_customer_is_watched_while_a_bill_is_unpaid(
    api, stripe, phone, mailbox
):
    """Months after the paid-up time ran out, with the bill unpaid, the
    walk-in is still watched, still texted about and still phoned about."""
    biz = Business(api)
    assert api.post("/api/licenses/me/plan", headers=biz.owner,
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/contracts", headers=biz.owner,
                    json={"term_years": 1}).status_code == 201
    run_billing()
    (invoice,) = _main_invoices(biz.tenant_id)
    _late_by(invoice, 90, biz)

    assert licence_state(biz.tenant) == "active"
    biz.freezer_alarm_still_works(phone)
    # The quiet-sensor text, too: it is only sent for an estate being watched.
    from automation import _tell_about_quiet_sensors

    sensor = STORE.get_sensor("WALKIN-1")
    sensor.last_seen = utc_now() - timedelta(hours=3)
    assert _tell_about_quiet_sensors(biz.tenant, [sensor], utc_now()) != []


def test_without_a_contract_the_licence_still_runs_out(api, phone):
    """The fix is about contracts. A trial nobody bought still ends, after
    its grace, as the agreement and the sign-up page say."""
    biz = Business(api)
    tenant = biz.tenant
    tenant.expires_at = utc_now() - timedelta(days=60)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    assert licence_state(biz.tenant) == "lapsed"
    assert biz.pulse(48.0).status_code == 402


def test_a_cancelled_contract_does_not_keep_the_licence_on(api, phone):
    biz = Business(api)
    assert api.post("/api/licenses/me/plan", headers=biz.owner,
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/contracts", headers=biz.owner,
                    json={"term_years": 1}).status_code == 201
    sub = STORE.active_subscription(biz.tenant_id)
    sub.cancelled_at = utc_now()
    STORE.save_subscription(sub)
    tenant = biz.tenant
    tenant.expires_at = utc_now() - timedelta(days=60)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    assert licence_state(biz.tenant) == "lapsed"


def test_a_contract_customer_is_not_told_their_monitoring_is_ending(api, stripe):
    """The trial's end date still sits on the licence after an upgrade; a
    countdown to it told a contract customer monitoring was about to stop."""
    biz = Business(api)
    tenant = biz.tenant
    tenant.expires_at = utc_now() + timedelta(days=2)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())
    assert "ends" in (biz.account()["headline"] or "")

    assert api.post("/api/licenses/me/plan", headers=biz.owner,
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/contracts", headers=biz.owner,
                    json={"term_years": 1}).status_code == 201

    account = biz.account()
    assert account["headline"] is None and account["severity"] == "ok"


def test_the_console_offers_the_pay_link_on_the_banner():
    """The pay link the account carries is drawn on the banner."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "console.js").read_text()
    block = js[js.index('const account = $("account-banner");'):]
    block = block[:block.index("const banner = $(\"delivery-banner\");")]
    assert "a.pay_url" in block and "esc(a.pay_url)" in block
    assert 'rel="noopener"' in block


# ---- one payment, three events, and only the first names the invoice --------


@pytest.fixture()
def billed(api, stripe, mailbox):
    biz = Business(api)
    assert api.post("/api/licenses/me/plan", headers=biz.owner,
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/contracts", headers=biz.owner,
                    json={"term_years": 1}).status_code == 201
    run_billing()
    (invoice,) = _main_invoices(biz.tenant_id)
    return biz, invoice


def test_the_intent_and_charge_after_a_matched_checkout_are_not_unmatched_money(
    api, stripe, billed, admin_headers
):
    """A payment link puts the invoice id on the checkout session only.
    The payment intent and charge that follow named no invoice, so each
    paid invoice also left a row on the unmatched list -- the same money,
    waiting for somebody to credit it by hand a second time."""
    biz, invoice = billed
    for event in stripe.checkout(PAY_URL.replace("{invoice}", invoice.invoice_id),
                                 _cents(invoice)):
        assert stripe.deliver(event).status_code == 200

    assert STORE.get_invoice(invoice.invoice_id).state == "paid"
    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["count"] == 0, listed


def test_an_intent_before_its_checkout_leaves_nothing_on_the_list(
    api, stripe, billed, admin_headers
):
    biz, invoice = billed
    checkout, intent, charge = stripe.checkout(
        PAY_URL.replace("{invoice}", invoice.invoice_id), _cents(invoice))
    stripe.deliver(intent)
    stripe.deliver(charge)
    assert api.get("/api/payments/unmatched", headers=admin_headers).json()["count"] == 1

    stripe.deliver(checkout)

    assert STORE.get_invoice(invoice.invoice_id).state == "paid"
    assert len(STORE.get_invoice(invoice.invoice_id).payments) == 1
    assert api.get("/api/payments/unmatched", headers=admin_headers).json()["count"] == 0


def test_a_payment_nobody_can_place_is_still_listed(api, stripe, billed, admin_headers):
    """The fix only recognises a payment the ledger already holds."""
    _, intent, _ = stripe.checkout("https://pay.example/b/cyberlogix", 5000)
    stripe.deliver(intent)
    listed = api.get("/api/payments/unmatched", headers=admin_headers).json()
    assert listed["count"] == 1 and listed["total_usd"] == pytest.approx(50.0)
