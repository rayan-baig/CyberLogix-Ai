"""The contract bills itself, once, at the rate that was signed.

Three failures are worth more than everything else in this file:

  * billing the rate card instead of the escalated rate, which quietly
    hands back the escalator the term was signed to earn;
  * billing a period twice, which is the only bug here that takes money
    from a customer who did nothing wrong;
  * never billing at all, which is what the product did before this
    module existed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from contracts import (
    DELINQUENT_AFTER_DAYS,
    LATE_FEE_DAYS,
    bill_period,
    delinquency,
    run_billing,
    run_dunning,
)
from store import STORE, add_months, utc_now


def _sign(api, headers, owner, **body):
    payload = {"term_years": 1, "escalator_percent": 5.0}
    payload.update(body)
    resp = api.post("/api/contracts", headers={**headers, **owner}, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _backdate(tenant_id, months):
    """Move a contract's start into the past so periods have come due."""
    sub = STORE.active_subscription(tenant_id)
    sub.started_at = add_months(utc_now(), -months)
    STORE.save_subscription(sub)
    return sub


# ---- calendar arithmetic ----------------------------------------------


@pytest.mark.parametrize(
    "start,months,expected",
    [
        ((2026, 1, 31), 1, (2026, 2, 28)),
        ((2028, 1, 31), 1, (2028, 2, 29)),  # leap year
        ((2026, 1, 31), 12, (2027, 1, 31)),
        ((2026, 12, 31), 1, (2027, 1, 31)),
        ((2026, 3, 15), 24, (2028, 3, 15)),
    ],
)
def test_month_arithmetic_clamps_without_drifting(start, months, expected):
    moment = datetime(*start, tzinfo=timezone.utc)
    got = add_months(moment, months)
    assert (got.year, got.month, got.day) == expected


def test_billing_dates_never_drift_off_the_31st():
    """Period 13 of a contract signed on the 31st is still the 31st.

    The bug this catches is computing each period from the last one: a
    January 31st contract becomes February 28th, then March 28th, and by
    the end of the term the invoice date has walked three days off the
    contract.
    """
    from store import Subscription

    sub = Subscription(
        "SUB-X", "T", 3, 5.0, False,
        started_at=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )
    days = [sub.period_start(i).day for i in range(0, 37, 12)]
    assert days == [31, 31, 31, 31]


# ---- the escalator actually reaches the invoice ------------------------


def test_year_two_bills_the_escalated_rate(api, tenant_factory, sensor_factory,
                                           owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")  # $899/mo
    _sign(api, headers, owner, term_years=3, escalator_percent=5.0)

    sub = _backdate(tenant["tenant_id"], months=13)
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])

    first = bill_period(tenant_obj, sub, 0)
    assert first.total_usd == pytest.approx(899.0 + 1500.0)  # + commissioning

    # Skip ahead to the first period of contract year two.
    sub.periods_billed = 12
    STORE.save_subscription(sub)
    year_two = bill_period(tenant_obj, sub, 12)

    assert sub.contract_year(12) == 2
    assert year_two.total_usd == pytest.approx(round(899.0 * 1.05, 2))
    assert year_two.total_usd > 899.0, (
        "Year two billed the rate card, so the escalator was never charged."
    )


def test_commissioning_is_billed_once_and_never_escalates(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner, term_years=3)

    sub = _backdate(tenant["tenant_id"], months=14)
    tenant_obj = STORE.get_tenant(tenant["tenant_id"])

    first = bill_period(tenant_obj, sub, 0)
    setup = [line for line in first.lines if line["kind"] == "setup"]
    assert len(setup) == 1
    assert setup[0]["amount_usd"] == 1500.0

    second = bill_period(tenant_obj, sub, 1)
    assert not [line for line in second.lines if line["kind"] == "setup"]


def test_annual_prepay_bills_twelve_months_with_the_discount(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner, term_years=3, annual_prepay=True)

    sub = STORE.active_subscription(tenant["tenant_id"])
    assert sub.months_per_period == 12

    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    invoice = bill_period(tenant_obj, sub, 0)

    recurring = 899.0 * 12
    discount = round(recurring * 0.10, 2)
    assert invoice.total_usd == pytest.approx(recurring - discount + 1500.0)
    kinds = {line["kind"] for line in invoice.lines}
    assert "discount" in kinds

    # And the second period is a year later, not a month.
    assert sub.period_start(1) == add_months(sub.period_start(0), 12)


def test_add_ons_ride_the_escalator_too(api, tenant_factory, sensor_factory,
                                        owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner, term_years=3, add_ons=["vault"])

    sub = _backdate(tenant["tenant_id"], months=13)
    sub.periods_billed = 12
    STORE.save_subscription(sub)
    invoice = bill_period(STORE.get_tenant(tenant["tenant_id"]), sub, 12)

    add_on = [line for line in invoice.lines if line["kind"] == "add_on"][0]
    assert add_on["amount_usd"] == pytest.approx(round(499.0 * 1.05, 2))


# ---- never twice -------------------------------------------------------


def test_a_period_is_never_billed_twice(api, tenant_factory, sensor_factory,
                                        owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)

    tenant_obj = STORE.get_tenant(tenant["tenant_id"])
    sub = STORE.active_subscription(tenant["tenant_id"])

    assert bill_period(tenant_obj, sub, 0) is not None
    assert bill_period(tenant_obj, sub, 0) is None, (
        "The same period was billed twice — the customer is charged for a "
        "month they already paid for."
    )
    assert len(STORE.invoices_for(tenant["tenant_id"])) == 1


def test_concurrent_billing_runs_issue_one_invoice(
    api, tenant_factory, sensor_factory, owner_headers
):
    """Two runs racing on the same subscription.

    A scheduler tick landing on a manual month-end close is not a rare
    event; it is what happens on the first of the month.
    """
    import threading

    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    # Two periods due, comfortably under the catch-up ceiling. Sitting on
    # the ceiling would make the test itself racy: every thread would
    # truncate its list at the same three, and whether a fourth period got
    # picked up in the same window would depend on scheduling rather than
    # on the thing being tested.
    _backdate(tenant["tenant_id"], months=1)

    barrier = threading.Barrier(8)
    results = []

    def go():
        barrier.wait()
        results.append(run_billing())

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    issued = [n for r in results for n in r["numbers"]]
    assert len(issued) == len(set(issued)), "An invoice number was issued twice."
    invoices = STORE.invoices_for(tenant["tenant_id"])
    assert len(invoices) == 2, (
        f"Eight racing runs issued {len(invoices)} invoices for two periods."
    )
    assert STORE.active_subscription(tenant["tenant_id"]).periods_billed == 2


def test_billing_run_is_idempotent(api, tenant_factory, sensor_factory,
                                   owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)

    first = run_billing()
    second = run_billing()
    assert first["invoices_issued"] == 1
    assert second["invoices_issued"] == 0


def test_an_estate_with_nothing_registered_does_not_burn_a_period(
    api, tenant_factory, owner_headers
):
    """Signing before the first sensor is fitted must not skip a month."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    _sign(api, headers, owner)

    assert run_billing()["invoices_issued"] == 0
    assert STORE.active_subscription(tenant["tenant_id"]).periods_billed == 0


def test_catch_up_is_capped(api, tenant_factory, sensor_factory, owner_headers):
    """A backdated contract must not fire a year of invoices at once."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    _backdate(tenant["tenant_id"], months=40)

    result = run_billing()
    assert result["invoices_issued"] == 3
    assert result["catchup_capped"] == [
        STORE.active_subscription(tenant["tenant_id"]).subscription_id
    ]


def test_a_failed_invoice_hands_the_period_back(
    api, tenant_factory, sensor_factory, owner_headers, monkeypatch
):
    """A claim held over a failure is a month nobody is ever billed for."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)

    import contracts

    monkeypatch.setattr(
        contracts.STORE,
        "create_invoice",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    sub = STORE.active_subscription(tenant["tenant_id"])
    with pytest.raises(RuntimeError):
        bill_period(STORE.get_tenant(tenant["tenant_id"]), sub, 0)

    assert sub.periods_billed == 0
    monkeypatch.undo()
    assert bill_period(STORE.get_tenant(tenant["tenant_id"]), sub, 0) is not None


# ---- lifecycle ---------------------------------------------------------


def test_a_trial_estate_cannot_countersign(api, tenant_factory, owner_headers):
    headers, _ = tenant_factory(plan="trial")
    owner = owner_headers(headers)
    resp = api.post(
        "/api/contracts", headers={**headers, **owner}, json={"term_years": 1}
    )
    assert resp.status_code == 409
    assert "trial" in resp.json()["detail"].lower()


def test_two_live_contracts_are_refused(api, tenant_factory, owner_headers):
    headers, _ = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    _sign(api, headers, owner)
    resp = api.post(
        "/api/contracts", headers={**headers, **owner}, json={"term_years": 1}
    )
    assert resp.status_code == 409


def test_cancelling_stops_billing_but_not_the_debt(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    run_billing()

    # Backdate first: after cancellation there is no live contract to move,
    # and the point is that these elapsed periods are never billed.
    _backdate(tenant["tenant_id"], months=6)
    resp = api.post(
        "/api/contracts/cancel",
        headers={**headers, **owner},
        json={"reason": "acquired"},
    )
    assert resp.status_code == 200
    assert resp.json()["still_owed_usd"] > 0

    assert run_billing()["invoices_issued"] == 0


def test_renewal_keeps_the_escalated_rate(api, tenant_factory, sensor_factory,
                                          owner_headers):
    """A renewal that resets to the year-one rate gives the term away."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner, term_years=3, escalator_percent=5.0)

    # A full three-year term billed out: periods 0-35, so the last rate
    # charged was year three's.
    sub = _backdate(tenant["tenant_id"], months=37)
    sub.periods_billed = 36
    STORE.save_subscription(sub)

    resp = api.post(
        "/api/contracts/renew", headers={**headers, **owner}, json={"term_years": 2}
    )
    assert resp.status_code == 200
    fresh = STORE.active_subscription(tenant["tenant_id"])
    assert fresh.carried_multiplier == pytest.approx(1.1025)
    assert fresh.rate_multiplier(0) == pytest.approx(1.1025)
    assert fresh.subscription_id != sub.subscription_id
    assert STORE.get_subscription(sub.subscription_id).active is False


def test_the_signed_contract_survives_a_restart(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner, term_years=3, add_ons=["vault"])
    run_billing()

    before = STORE.active_subscription(tenant["tenant_id"])
    STORE._subscriptions.clear()
    STORE.load()
    after = STORE.active_subscription(tenant["tenant_id"])

    assert after is not None
    assert after.periods_billed == before.periods_billed == 1
    assert after.add_ons == ["vault"]
    assert after.term_years == 3
    # And it does not re-bill the period it already billed.
    assert run_billing()["invoices_issued"] == 0


# ---- collections -------------------------------------------------------


def _age_invoice(invoice_id, days):
    invoice = STORE.get_invoice(invoice_id)
    invoice.due_at = utc_now() - timedelta(days=days)
    invoice.issued_at = invoice.due_at - timedelta(days=30)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    return invoice


def _billed_tenant(api, tenant_factory, sensor_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    return headers, owner, tenant, invoice


def test_the_notice_matches_how_late_the_invoice_is(
    api, tenant_factory, sensor_factory, owner_headers
):
    """Stage tracks lateness; it is not a counter that gets walked."""
    _, _, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )

    seen = []
    for days in (1, 8, 15, 31, 46):
        _age_invoice(invoice.invoice_id, days)
        seen += [n["stage"] for n in run_dunning()["notices"]]
        # A second pass on the same day says nothing more.
        assert run_dunning()["notices_count"] == 0

    assert seen == [1, 2, 3, 4, 5]


def test_a_long_silence_sends_one_notice_not_five(
    api, tenant_factory, sensor_factory, owner_headers
):
    """The process was off for three months. The customer gets one letter."""
    _, _, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, 90)

    notices = run_dunning()["notices"]
    assert [n["stage"] for n in notices] == [5]
    assert "delinquent" in notices[0]["message"]
    assert run_dunning()["notices_count"] == 0


def test_nothing_is_chased_before_it_is_due(
    api, tenant_factory, sensor_factory, owner_headers
):
    _billed_tenant(api, tenant_factory, sensor_factory, owner_headers)
    assert run_dunning()["notices_count"] == 0


def test_a_paid_invoice_is_never_chased(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, 60)
    api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        headers={**headers, **owner},
        json={"reference": "WIRE-1"},
    )
    assert run_dunning()["notices_count"] == 0


def test_a_part_payment_is_still_chased_for_the_balance(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, 10)
    api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        headers={**headers, **owner},
        json={"reference": "PART-1", "amount_usd": 100.0},
    )
    notices = run_dunning()["notices"]
    assert len(notices) == 1
    assert notices[0]["balance_usd"] == pytest.approx(
        invoice.total_usd - 100.0
    )


def test_the_late_fee_is_a_separate_invoice_issued_once(
    api, tenant_factory, sensor_factory, owner_headers
):
    """An issued invoice whose total moves is a dispute."""
    _, _, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    original_total = invoice.total_usd
    _age_invoice(invoice.invoice_id, LATE_FEE_DAYS + 1)

    for _ in range(4):
        run_dunning()

    refreshed = STORE.get_invoice(invoice.invoice_id)
    assert refreshed.total_usd == original_total, "The original was mutated."
    assert refreshed.late_fee_invoice_id is not None

    fees = [
        i
        for i in STORE.invoices_for(tenant["tenant_id"])
        if i.source.startswith("late_fee:")
    ]
    assert len(fees) == 1
    assert fees[0].total_usd == pytest.approx(round(original_total * 0.015, 2))


def test_no_late_fee_before_the_grace_period(
    api, tenant_factory, sensor_factory, owner_headers
):
    _, _, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, LATE_FEE_DAYS - 1)
    run_dunning()
    assert STORE.get_invoice(invoice.invoice_id).late_fee_invoice_id is None


def test_concurrent_dunning_sends_one_notice_and_one_fee(
    api, tenant_factory, sensor_factory, owner_headers
):
    import threading

    _, _, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, 30)

    barrier = threading.Barrier(8)
    results = []

    def go():
        barrier.wait()
        results.append(run_dunning())

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    notices = [n for r in results for n in r["notices"]]
    assert len(notices) == 1, f"The customer was chased {len(notices)} times."
    fees = [f for r in results for f in r["late_fees_issued"]]
    assert len(fees) == 1


# ---- what delinquency does and does not do -----------------------------


def test_a_delinquent_account_still_gets_its_alerts(
    api, tenant_factory, sensor_factory, owner_headers, configured_twilio
):
    """The rule the whole company depends on not breaking.

    A monitoring company that stops monitoring over an unpaid invoice has
    sold the wrong product, and the first freezer it loses that way is the
    last customer it ever signs.
    """
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, DELINQUENT_AFTER_DAYS + 10)
    assert delinquency(tenant["tenant_id"])["delinquent"] is True

    pulse = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={
            "sensor_id": "RACK-01",
            "temperature_fahrenheit": 150.0,
            "industry_vertical": "cybersecurity",
        },
    )
    assert pulse.status_code == 200, pulse.text
    body = pulse.json()
    assert body["status"].startswith("CRITICAL")

    incidents = api.get("/api/voice/incidents", headers=headers)
    assert incidents.status_code == 200
    assert incidents.json()["incidents"]

    # And the console still works.
    assert api.get("/api/console/overview", headers=headers).status_code == 200


def test_a_delinquent_account_loses_its_reporting(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, DELINQUENT_AFTER_DAYS + 1)

    for path in ("/api/benchmarks/cybersecurity", "/api/vault/attestation"):
        resp = api.get(path, headers=headers)
        assert resp.status_code == 402, f"{path} -> {resp.status_code}"
        assert "alerting" in resp.json()["detail"]


def test_paying_up_restores_reporting_immediately(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    _age_invoice(invoice.invoice_id, DELINQUENT_AFTER_DAYS + 1)
    assert api.get("/api/vault/attestation", headers=headers).status_code == 402

    api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        headers={**headers, **owner},
        json={"reference": "WIRE-9"},
    )
    assert api.get("/api/vault/attestation", headers=headers).status_code == 200


def test_verification_survives_delinquency(
    api, tenant_factory, sensor_factory, owner_headers
):
    """A document already handed to an insurer stays checkable."""
    headers, owner, tenant, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={
            "sensor_id": "RACK-01",
            "temperature_fahrenheit": 70.0,
            "industry_vertical": "cybersecurity",
        },
    )
    _age_invoice(invoice.invoice_id, DELINQUENT_AFTER_DAYS + 30)
    assert api.get("/api/vault/verify/RACK-01", headers=headers).status_code == 200


# ---- what the estate is worth -----------------------------------------


def test_pipeline_prices_the_add_ons_not_yet_sold(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    for i in range(3):
        sensor_factory(headers, f"RACK-{i}", "cybersecurity")
    _sign(api, headers, owner, add_ons=["vault"])

    body = api.get("/api/contracts/pipeline", headers=headers).json()
    keys = {o["key"] for o in body["opportunities"]}
    assert "vault" not in keys, "An add-on already sold is not an opportunity."
    assert {"assurance", "benchmarks", "equipment_intelligence"} <= keys

    assurance = next(o for o in body["opportunities"] if o["key"] == "assurance")
    assert assurance["monthly_usd"] == pytest.approx(149.0 * 3)
    assert body["identified_annual_usd"] > 0
    assert body["current_arr_usd"] == pytest.approx(899.0 * 3 * 12)


def test_pipeline_flags_a_site_with_nothing_on_it(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    api.post(
        "/api/sites",
        headers={**headers, **owner},
        json={"name": "Reno DC", "address": "1 Nowhere"},
    )
    body = api.get("/api/contracts/pipeline", headers=headers).json()
    gaps = [o for o in body["opportunities"] if o["kind"] == "coverage_gap"]
    assert len(gaps) == 1
    assert "Reno DC" in gaps[0]["name"]


def test_read_says_plainly_when_nothing_bills_itself(api, tenant_factory):
    headers, _ = tenant_factory(plan="enterprise")
    body = api.get("/api/contracts", headers=headers).json()
    assert body["contract"] is None
    assert "by hand" in body["note"]


# ---- the claims themselves --------------------------------------------
#
# Mutation testing found the gap these close. Removing the guard inside
# `claim_reminder` and the one inside `claim_late_fee` broke nothing: the
# concurrency tests above still passed, because `run_dunning` pre-checks
# the same condition and the window between that check and the write is
# only a few microseconds wide. A test that can only *hope* to observe a
# race is not a test of the invariant. These assert it directly.


def test_a_reminder_stage_is_claimable_exactly_once(
    api, tenant_factory, sensor_factory, owner_headers
):
    _, _, _, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    assert STORE.claim_reminder(invoice, 3) is True
    assert STORE.claim_reminder(invoice, 3) is False, "Stage 3 sent twice."
    assert STORE.claim_reminder(invoice, 2) is False, "The sequence went back."
    assert STORE.claim_reminder(invoice, 4) is True
    assert STORE.get_invoice(invoice.invoice_id).reminders_sent == 4


def test_a_settled_invoice_refuses_a_reminder_claim(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, owner, _, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        headers={**headers, **owner},
        json={"reference": "WIRE-2"},
    )
    assert STORE.claim_reminder(invoice, 1) is False


def test_a_late_fee_is_claimable_exactly_once(
    api, tenant_factory, sensor_factory, owner_headers
):
    _, _, _, invoice = _billed_tenant(
        api, tenant_factory, sensor_factory, owner_headers
    )
    assert STORE.claim_late_fee(invoice, "INV-FEE-1") is True
    assert STORE.claim_late_fee(invoice, "INV-FEE-2") is False, (
        "Two late charges were attached to one invoice."
    )
    assert STORE.get_invoice(invoice.invoice_id).late_fee_invoice_id == "INV-FEE-1"


def test_a_billing_period_is_claimable_exactly_once(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    sub = STORE.active_subscription(tenant["tenant_id"])

    assert STORE.claim_billing_period(sub, 0) is True
    assert STORE.claim_billing_period(sub, 0) is False, "Period 0 billed twice."
    assert STORE.claim_billing_period(sub, 5) is False, (
        "A period was billed out of order, skipping the ones between."
    )
    assert STORE.claim_billing_period(sub, 1) is True
    assert STORE.get_subscription(sub.subscription_id).periods_billed == 2


def test_a_cancelled_contract_refuses_a_billing_claim(
    api, tenant_factory, sensor_factory, owner_headers
):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    sub = STORE.active_subscription(tenant["tenant_id"])
    STORE.cancel_subscription(sub, "done")
    assert STORE.claim_billing_period(sub, 0) is False


def test_releasing_a_period_only_undoes_the_newest_claim(
    api, tenant_factory, sensor_factory, owner_headers
):
    """A release must never reopen a period some other run already billed."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    sub = STORE.active_subscription(tenant["tenant_id"])

    STORE.claim_billing_period(sub, 0)
    STORE.claim_billing_period(sub, 1)
    STORE.release_billing_period(sub, 0)  # stale release from a failed run
    assert STORE.get_subscription(sub.subscription_id).periods_billed == 2
    STORE.release_billing_period(sub, 1)
    assert STORE.get_subscription(sub.subscription_id).periods_billed == 1
