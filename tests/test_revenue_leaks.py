"""Five ways the product delivered service and did not get paid for it.

Each of these was measured against the running app before it was fixed,
and the figure in each docstring is the one that came back. They are
regression tests in the strict sense: the leak existed, it was closed, and
this is what re-opening it looks like.
"""

from datetime import timedelta

import pytest

import costs
import auth
from contracts import run_billing
from store import STORE, add_months, utc_now


@pytest.fixture()
def admin_headers(monkeypatch):
    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", "root-key")
    return {"X-CyberLogix-Admin": "root-key"}


def _estate(api, tenant_factory, owner_headers, sensor_factory, units=5,
            vertical="cybersecurity", tag="A"):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    for i in range(units):
        sensor_factory(headers, f"{tag}-{i}", vertical)
    signed = api.post(
        "/api/contracts", headers={**headers, **owner}, json={"term_years": 1}
    )
    assert signed.status_code == 201, signed.text
    return headers, owner, tenant


def _age_one_period(tenant_id, sensor_prefix, at_fraction):
    """Backdate the contract a month and place sensors inside that window."""
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    span = sub.period_start(1) - sub.period_start(0)
    joined = sub.period_start(0) + span * at_fraction
    for sensor in STORE.sensors_for(tenant_id):
        if sensor.sensor_id.startswith(sensor_prefix):
            sensor.registered_at = joined
            STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    return sub


# ---- A: mid-period growth rode free ------------------------------------


def test_units_added_mid_period_are_charged_for_the_days_they_ran(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """Measured before the fix: $17,980 of service delivered, never billed.

    Billing runs in advance, so the invoice for a month is priced on the
    day the month opens. A customer who signs for five racks and rolls out
    twenty more the following week was monitored on twenty-five and billed
    for five, for the rest of the month.
    """
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=5, tag="A"
    )
    run_billing()

    for i in range(20):
        sensor_factory(headers, f"B-{i}", "cybersecurity")
    _age_one_period(tenant["tenant_id"], "B-", 1 / 3)
    run_billing()

    invoices = STORE.invoices_for(tenant["tenant_id"])
    assert len(invoices) == 2
    latest = max(invoices, key=lambda i: i.number)
    arrears = [line for line in latest.lines if line["kind"] == "arrears"]

    assert arrears, "The twenty racks added mid-period were never charged for."
    assert arrears[0]["quantity"] == 20
    # Two thirds of a month at the rack rate.
    assert arrears[0]["amount_usd"] == pytest.approx(20 * 899.0 * (2 / 3), rel=1e-3)


def test_nobody_is_charged_for_days_before_their_sensor_existed(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """The other direction, which would be a genuine overcharge."""
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=2, tag="A"
    )
    run_billing()
    for i in range(4):
        sensor_factory(headers, f"B-{i}", "cybersecurity")
    # Registered nine tenths of the way through the period.
    _age_one_period(tenant["tenant_id"], "B-", 0.9)
    run_billing()

    latest = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    arrears = [l for l in latest.lines if l["kind"] == "arrears"][0]
    full_month = 4 * 899.0
    assert arrears["amount_usd"] < full_month * 0.15, (
        "A sensor added on the 27th was charged most of the month."
    )
    assert arrears["amount_usd"] > 0


def test_a_sensor_registered_before_the_period_is_not_charged_twice(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -1)
    STORE.save_subscription(sub)
    # Every sensor predates the window.
    for sensor in STORE.sensors_for(tenant["tenant_id"]):
        sensor.registered_at = sub.period_start(0) - timedelta(days=5)
        STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    run_billing()

    latest = max(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    assert not [l for l in latest.lines if l["kind"] == "arrears"]
    assert latest.total_usd == pytest.approx(3 * 899.0)


def test_the_first_invoice_has_no_arrears_to_carry(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """There is no previous period, so there is nothing to catch up on."""
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    first = STORE.invoices_for(tenant["tenant_id"])[0]
    assert not [l for l in first.lines if l["kind"] == "arrears"]


# ---- B: a voided invoice gave the month away ---------------------------


def test_voiding_an_invoice_puts_its_period_back_to_be_billed(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """Measured before the fix: $4,197 that could never be re-issued.

    `periods_billed` is a high-water mark, so once period 4 was billed the
    run moved to period 5 whether or not the invoice for 4 still existed.
    One typo in a PO gave a month away.
    """
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    original = STORE.invoices_for(tenant["tenant_id"])[0]

    api.post(f"/api/invoices/{original.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})

    assert run_billing()["invoices_issued"] == 1, (
        "The voided month was never re-billed."
    )
    reissued = [
        i for i in STORE.invoices_for(tenant["tenant_id"])
        if i.invoice_id != original.invoice_id
    ]
    assert len(reissued) == 1
    assert reissued[0].billing_period == original.billing_period


def test_the_commissioning_fee_is_not_charged_twice_on_a_re_bill(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    original = STORE.invoices_for(tenant["tenant_id"])[0]
    assert [l for l in original.lines if l["kind"] == "setup"]

    api.post(f"/api/invoices/{original.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})
    run_billing()

    reissued = [
        i for i in STORE.invoices_for(tenant["tenant_id"])
        if i.invoice_id != original.invoice_id
    ][0]
    assert not [l for l in reissued.lines if l["kind"] == "setup"], (
        "Commissioning was charged a second time on the replacement."
    )


def test_a_re_billed_period_is_only_re_billed_once(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    original = STORE.invoices_for(tenant["tenant_id"])[0]
    api.post(f"/api/invoices/{original.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})

    assert run_billing()["invoices_issued"] == 1
    assert run_billing()["invoices_issued"] == 0
    assert STORE.active_subscription(tenant["tenant_id"]).rebill_periods == []


def test_voiding_an_old_invoice_does_not_re_issue_the_newer_ones(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """Why the hole is recorded rather than the counter rewound."""
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -2)
    STORE.save_subscription(sub)
    run_billing()
    assert len(STORE.invoices_for(tenant["tenant_id"])) == 3

    oldest = min(STORE.invoices_for(tenant["tenant_id"]), key=lambda i: i.number)
    api.post(f"/api/invoices/{oldest.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})

    assert run_billing()["invoices_issued"] == 1, (
        "Rewinding the counter re-issued every period after the hole too."
    )
    assert STORE.active_subscription(tenant["tenant_id"]).periods_billed == 3


def test_a_voided_manual_invoice_touches_no_subscription(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """An invoice raised by hand has no period to hand back."""
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    manual = api.post("/api/invoices",
                      params={"tenant_id": tenant["tenant_id"]},
                      headers=admin_headers,
                      json={"period_days": 30}).json()
    assert manual["billing_period"] is None
    api.post(f"/api/invoices/{manual['invoice_id']}/void",
             headers={**headers, **owner}, json={})
    assert STORE.active_subscription(tenant["tenant_id"]).rebill_periods == []


def test_the_rebill_list_survives_a_restart(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=3, tag="A"
    )
    run_billing()
    original = STORE.invoices_for(tenant["tenant_id"])[0]
    api.post(f"/api/invoices/{original.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})

    STORE._subscriptions.clear()
    STORE.load()
    assert STORE.active_subscription(tenant["tenant_id"]).rebill_periods == [
        original.billing_period
    ]
    assert run_billing()["invoices_issued"] == 1


# ---- E: a free trial spent real money ----------------------------------


def test_a_trial_has_its_own_much_lower_spend_ceilings(api, tenant_factory):
    """Measured before the fix: $43.50/month of Twilio and Google per trial.

    The caps were identical for trial and paid. That was survivable while
    the only way to get an account was to ask somebody; with a public
    sign-up door, fifty trials would be $2,175 a month of real cash out
    against a fixed cost base of $8.85.
    """
    trial_headers, trial = tenant_factory(plan="trial", company_name="Kicking Tyres")
    paid_headers, paid = tenant_factory(plan="enterprise", company_name="Real Co")

    trial_caps = costs.caps_for(trial["tenant_id"])
    paid_caps = costs.caps_for(paid["tenant_id"])

    for channel in ("sms", "voice_calls", "ai_calls"):
        assert trial_caps[channel] < paid_caps[channel], (
            f"A trial can spend as much {channel} as a paying customer."
        )


def test_the_trial_ceiling_is_actually_enforced(api, tenant_factory, monkeypatch):
    monkeypatch.setattr(costs, "TRIAL_MAX_SMS_PER_DAY", 1)
    _, trial = tenant_factory(plan="trial", company_name="Kicking Tyres")

    allowed, _ = costs.allow_message(trial["tenant_id"], "sms")
    assert allowed is True
    STORE.bump_usage(trial["tenant_id"], "sms_sent", 1)
    allowed, reason = costs.allow_message(trial["tenant_id"], "sms")
    assert allowed is False
    assert "trial allowance" in reason, (
        "A trial that hits its ceiling should be told it is a trial ceiling; "
        "otherwise it reads as the product being broken."
    )


def test_a_paying_customer_is_not_held_to_the_trial_ceiling(
    api, tenant_factory, monkeypatch
):
    """The fix must not throttle the people who actually pay."""
    monkeypatch.setattr(costs, "TRIAL_MAX_SMS_PER_DAY", 1)
    _, paid = tenant_factory(plan="enterprise", company_name="Real Co")
    for _ in range(5):
        STORE.bump_usage(paid["tenant_id"], "sms_sent", 1)
    allowed, _ = costs.allow_message(paid["tenant_id"], "sms")
    assert allowed is True


def test_upgrading_off_the_trial_lifts_the_ceiling_immediately(
    api, tenant_factory, owner_headers, monkeypatch
):
    monkeypatch.setattr(costs, "TRIAL_MAX_SMS_PER_DAY", 1)
    headers, trial = tenant_factory(plan="trial", company_name="Converting Co")
    owner = owner_headers(headers)
    STORE.bump_usage(trial["tenant_id"], "sms_sent", 1)
    assert costs.allow_message(trial["tenant_id"], "sms")[0] is False

    moved = api.post("/api/licenses/me/plan",
                     headers={**headers, **owner}, json={"plan": "growth"})
    assert moved.status_code == 200, moved.text
    assert costs.allow_message(trial["tenant_id"], "sms")[0] is True


def test_the_spend_report_shows_the_caps_that_actually_apply(
    api, tenant_factory
):
    """A trial shown the paid ceilings is told it has headroom it has not."""
    headers, trial = tenant_factory(plan="trial", company_name="Kicking Tyres")
    report = api.get("/api/costs", headers=headers).json()
    assert report["on_trial_allowance"] is True
    assert report["daily_caps"]["sms"] == costs.TRIAL_MAX_SMS_PER_DAY


def test_a_cap_change_reaches_the_code_that_enforces_it(
    api, tenant_factory, monkeypatch
):
    """CAPS is a snapshot taken at import.

    Routing enforcement through it meant a cap could be changed and the
    code that actually refuses a send would carry on using the old number.
    """
    _, paid = tenant_factory(plan="enterprise", company_name="Real Co")
    monkeypatch.setattr(costs, "MAX_SMS_PER_DAY", 1)
    STORE.bump_usage(paid["tenant_id"], "sms_sent", 1)
    assert costs.allow_message(paid["tenant_id"], "sms")[0] is False


def test_nobody_is_billed_for_days_before_the_contract_existed(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """The realistic shape of this: sensors are fitted during evaluation.

    A customer installs the units, runs them for two weeks unbilled, and
    then signs. If the arrears window is allowed to run before period
    zero, their very first invoice carries a fortnight of charges for a
    contract that did not exist — a mutation removing the guard produced
    exactly that, and nothing caught it because the sensors in the other
    tests were registered seconds before signing.
    """
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    for i in range(4):
        sensor_factory(headers, f"EVAL-{i}", "cybersecurity")

    # Two weeks of evaluation before anybody signed anything.
    fortnight_ago = utc_now() - timedelta(days=14)
    for sensor in STORE.sensors_for(tenant["tenant_id"]):
        sensor.registered_at = fortnight_ago
        STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())

    assert api.post(
        "/api/contracts", headers={**headers, **owner}, json={"term_years": 1}
    ).status_code == 201
    run_billing()

    first = STORE.invoices_for(tenant["tenant_id"])[0]
    assert not [l for l in first.lines if l["kind"] == "arrears"], (
        "The first invoice charged for evaluation days before the contract."
    )
    assert first.total_usd == pytest.approx(4 * 899.0 + 1500.0)


def test_a_part_paid_invoice_that_is_voided_earns_no_commission(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """A dispute settled by voiding must claw the commission back too.

    The obvious test — void an unpaid invoice — proves nothing, because an
    unpaid invoice has no payment to count either way. This is the case
    that separates them: money arrived, then the invoice was voided.
    """
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")
    api.post("/api/contracts", headers={**headers, **owner},
             json={"term_years": 1})
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]

    made = api.post("/api/partners", headers=admin_headers, json={
        "company_name": "Cold Chain Services", "contact_name": "P",
        "contact_email": "p@chain.example", "commission_percent": 20.0})
    assert made.status_code == 201, made.text
    partner_key = {"X-CyberLogix-Partner": made.json()["api_key"]}
    api.post(f"/api/partners/{made.json()['partner_id']}/accounts",
             headers=admin_headers, json={"tenant_id": tenant["tenant_id"]})

    api.post(f"/api/invoices/{invoice.invoice_id}/paid",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers,
             json={"reference": "WIRE-1", "amount_usd": 500.0})
    assert api.get("/api/partners/me/statement",
                   headers=partner_key).json()["commission_usd"] == 100.0

    voided = api.post(f"/api/invoices/{invoice.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]},
             headers=admin_headers, json={})
    assert voided.status_code == 200, voided.text
    assert api.get("/api/partners/me/statement",
                   headers=partner_key).json()["commission_usd"] == 0.0, (
        "Commission was still owed on an invoice that was voided."
    )


# ---- turning the loop off must not turn the money off ------------------


def test_the_scheduler_says_that_turning_it_off_stops_billing_too(api):
    """A perfectly good reason to disable the loop — more than one replica
    — silently stopped the company invoicing, and the note only warned
    about escalation. The only sign would have been an empty ledger."""
    import scheduler

    note = scheduler.status()["note"]
    assert "/api/contracts/run" in note
    assert "billing" in note.lower()
    assert scheduler.status()["billing_interval_seconds"] > 0


def test_the_fleet_can_be_billed_from_outside(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    headers, owner, tenant = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=2, tag="A"
    )
    out = api.post("/api/contracts/run", headers=admin_headers)
    assert out.status_code == 200, out.text
    assert out.json()["billing"]["invoices_issued"] == 1
    assert len(STORE.invoices_for(tenant["tenant_id"])) == 1


def test_the_fleet_billing_route_is_not_a_tenants_to_call(
    api, admin_headers, tenant_factory, owner_headers, sensor_factory
):
    """It runs across every account, so it is not a customer's button."""
    headers, owner, _ = _estate(
        api, tenant_factory, owner_headers, sensor_factory, units=2, tag="A"
    )
    refused = api.post("/api/contracts/run", headers={**headers, **owner})
    assert refused.status_code in (401, 403, 503), refused.text
