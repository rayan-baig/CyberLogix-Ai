"""The book, for the person who owns it.

There was no fleet-wide view of anything. The scheduler iterated tenants
to escalate alarms and the benchmarks module aggregated them into
percentiles, but nothing told the person who owns the company which
customer is about to leave, which owes money, or whose trial ends on
Thursday. Every figure already existed, one tenant at a time, behind a
credential that tenant holds.
"""

from datetime import timedelta

import pytest

from contracts import run_billing
from store import STORE, add_months, utc_now

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


def _account(api, tenant_factory, owner_headers, sensor_factory,
             name, plan="growth", units=3, vertical="restaurant", tag="A"):
    headers, tenant = tenant_factory(plan=plan, company_name=name)
    owner = owner_headers(headers, email=f"{tag.lower()}@example.com")
    for i in range(units):
        sensor_factory(headers, f"{tag}-{i}", vertical)
    return {**headers, **owner}, tenant


def _kinds_for(api, company):
    rows = api.get("/api/contracts/attention", headers=ADMIN).json()["rows"]
    return {r["kind"] for r in rows if r["company_name"] == company}


def test_the_worklist_is_not_a_customers_to_read(
    api, tenant_factory, owner_headers, sensor_factory
):
    """It spans every account, so it is not behind a tenant credential."""
    headers, _ = _account(api, tenant_factory, owner_headers, sensor_factory,
                          "Bella Vista")
    refused = api.get("/api/contracts/attention", headers=headers)
    assert refused.status_code in (401, 403, 503)


def test_a_trial_about_to_end_is_on_the_list(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Kicking Tyres", plan="trial")
    stored = STORE.get_tenant(tenant["tenant_id"])
    stored.expires_at = utc_now() + timedelta(days=2)
    STORE._db.put("tenant", tenant["tenant_id"], stored.to_row())

    rows = api.get("/api/contracts/attention", headers=ADMIN).json()["rows"]
    trial = [r for r in rows if r["kind"] == "trial_ending"]
    assert len(trial) == 1
    assert trial[0]["urgency"] == "high"
    assert "3 units live" in trial[0]["headline"]
    # Worth the rate card on the estate they have actually built.
    assert trial[0]["at_stake_usd"] == pytest.approx(999.0 * 3 * 12)
    assert trial[0]["contact_email"]


def test_a_healthy_account_needs_nobody(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, _ = _account(api, tenant_factory, owner_headers, sensor_factory,
                          "Steady Eddie")
    api.post("/api/contracts", headers=headers, json={
        "term_years": 3,
        "add_ons": ["assurance", "vault", "benchmarks",
                    "equipment_intelligence"]})
    assert _kinds_for(api, "Steady Eddie") == set()


def test_a_lapsed_account_is_at_the_top(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Gone Quiet")
    stored = STORE.get_tenant(tenant["tenant_id"])
    stored.expires_at = utc_now() - timedelta(days=3)
    STORE._db.put("tenant", tenant["tenant_id"], stored.to_row())

    rows = api.get("/api/contracts/attention", headers=ADMIN).json()["rows"]
    grace = [r for r in rows if r["kind"] == "grace"]
    assert len(grace) == 1
    assert grace[0]["urgency"] == "high"
    assert "monitoring left" in grace[0]["headline"]
    assert rows[0]["urgency"] == "high"


def test_an_overdue_invoice_is_on_the_list_at_its_real_balance(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Slow Payer")
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    invoice.due_at = utc_now() - timedelta(days=22)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())

    rows = api.get("/api/contracts/attention", headers=ADMIN).json()["rows"]
    overdue = [r for r in rows if r["kind"] == "overdue"]
    assert len(overdue) == 1
    assert overdue[0]["at_stake_usd"] == pytest.approx(invoice.balance_usd)
    assert "22 days past due" in overdue[0]["headline"]


def test_a_term_that_already_ended_is_urgent_not_invisible(
    api, tenant_factory, owner_headers, sensor_factory
):
    """The window used to be `0 <= days <= 30`.

    So the row appeared for a month and vanished on the day it started to
    matter. An auto-renewing contract keeps billing past its term at the
    old rate, so nothing else raised a hand either.
    """
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Ran On")
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -13)
    STORE.save_subscription(sub)

    rows = api.get("/api/contracts/attention", headers=ADMIN).json()["rows"]
    renewal = [r for r in rows if r["kind"] == "renewal"]
    assert len(renewal) == 1
    assert renewal[0]["urgency"] == "high"
    assert "ended" in renewal[0]["headline"]
    assert "auto-renewing at the old rate" in renewal[0]["headline"]


def test_a_term_ending_soon_is_a_warning_not_an_alarm(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Coming Up")
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -12) + timedelta(days=10)
    STORE.save_subscription(sub)

    renewal = [r for r in api.get("/api/contracts/attention",
                                  headers=ADMIN).json()["rows"]
               if r["kind"] == "renewal"]
    assert len(renewal) == 1
    assert renewal[0]["urgency"] == "medium"
    assert "ends in" in renewal[0]["headline"]


def test_the_day_counts_read_like_english(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "One Day")
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    sub = STORE.active_subscription(tenant["tenant_id"])
    sub.started_at = add_months(utc_now(), -12) - timedelta(days=1, hours=1)
    STORE.save_subscription(sub)

    row = [r for r in api.get("/api/contracts/attention",
                              headers=ADMIN).json()["rows"]
           if r["kind"] == "renewal"][0]
    assert "1 day ago" in row["headline"], row["headline"]


def test_the_totals_are_the_sum_of_what_is_really_there(
    api, tenant_factory, owner_headers, sensor_factory
):
    paying, _ = _account(api, tenant_factory, owner_headers, sensor_factory,
                         "Paying Co", plan="growth", units=4, tag="P")
    _account(api, tenant_factory, owner_headers, sensor_factory,
             "Trial Co", plan="trial", units=2, tag="T")

    book = api.get("/api/contracts/attention", headers=ADMIN).json()["book"]
    assert book["accounts"] == 2
    assert book["paying"] == 1, "a trial is not revenue"
    assert book["mrr_usd"] == pytest.approx(999.0 * 4)
    assert book["arr_usd"] == pytest.approx(999.0 * 4 * 12)


def test_a_suspended_account_is_not_counted_as_revenue(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, tenant = _account(api, tenant_factory, owner_headers,
                               sensor_factory, "Switched Off")
    STORE.set_suspended(STORE.get_tenant(tenant["tenant_id"]), True)

    body = api.get("/api/contracts/attention", headers=ADMIN).json()
    assert body["book"]["paying"] == 0
    assert body["book"]["mrr_usd"] == 0.0
    assert "suspended" in {r["kind"] for r in body["rows"]}


def test_one_broken_estate_does_not_hide_the_book(
    api, tenant_factory, owner_headers, sensor_factory, monkeypatch
):
    """This is the screen somebody opens to find out what is wrong."""
    _account(api, tenant_factory, owner_headers, sensor_factory, "Fine Co",
             tag="F")
    import contracts

    monkeypatch.setattr(contracts, "unsold_add_ons",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("priced badly")))
    body = api.get("/api/contracts/attention", headers=ADMIN)
    assert body.status_code == 200
    assert body.json()["book"]["accounts"] == 1


def test_a_trial_that_never_started_outranks_everything_priced(
    api, tenant_factory, sensor_factory, admin_headers
):
    """The one account that is certainly not converting sorted last.

    Its stake is zero because there is nothing registered to price, and
    the book ranks by what is at stake — so a trial that signed up and
    never got going sat below every account that was fine. It is also
    the most recoverable thing on the page: they wanted the product
    enough to sign up and have not seen it work yet.
    """
    from datetime import timedelta

    from store import STORE, utc_now

    healthy, _ = tenant_factory(plan="growth", company_name="Northgate Foods")
    sensor_factory(healthy, "FRIDGE-1", "restaurant")

    stalled, tenant = tenant_factory(plan="trial", company_name="Never Started")
    live = STORE.get_tenant(tenant["tenant_id"])
    live.activated_at = utc_now() - timedelta(days=4)

    rows = api.get("/api/contracts/attention", headers=admin_headers).json()["rows"]

    assert rows[0]["company_name"] == "Never Started"
    assert rows[0]["kind"] == "trial_stalled"
    assert rows[0]["urgency"] == "high"
    assert "nothing registered" in rows[0]["headline"]


def test_a_trial_on_its_first_afternoon_is_not_called_stalled(
    api, tenant_factory, admin_headers
):
    """Signing up on a Friday and starting on Monday is not a failure."""
    tenant_factory(plan="trial", company_name="Just Signed Up")

    rows = api.get("/api/contracts/attention", headers=admin_headers).json()["rows"]

    assert [r["kind"] for r in rows] == ["trial_ending"]


def test_a_trial_is_not_offered_add_ons_it_has_no_contract_to_hang_them_on(
    api, tenant_factory, sensor_factory, admin_headers
):
    """Sell them the product before selling them the extras.

    A trial was getting an expansion row for four add-ons it had no base
    contract to attach them to — and in the daily digest that landed as
    a second row for the same company, directly under the one that
    actually mattered: "their trial ends Thursday", followed by "offer
    them an upsell".
    """
    headers, _ = tenant_factory(plan="trial", company_name="Still Deciding")
    sensor_factory(headers, "FRIDGE-1", "restaurant")

    rows = api.get("/api/contracts/attention", headers=admin_headers).json()["rows"]
    kinds = {r["kind"] for r in rows if r["company_name"] == "Still Deciding"}

    assert "trial_ending" in kinds
    assert "expansion" not in kinds


def test_a_paying_account_is_still_offered_them(
    api, tenant_factory, sensor_factory, admin_headers
):
    headers, _ = tenant_factory(plan="growth", company_name="Northgate Foods")
    sensor_factory(headers, "FRIDGE-1", "restaurant")

    rows = api.get("/api/contracts/attention", headers=admin_headers).json()["rows"]

    assert any(r["kind"] == "expansion" for r in rows)
