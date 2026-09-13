"""Where the money actually goes, and the cost nobody invoices you for.

"Cut costs" is easy to say and hard to aim. The ranking this module
produces is not the one anybody guesses: the discount we choose to give
away costs more than every real cost combined, and the metered spend
this codebase has an entire module of controls for is 0.05% of revenue.

The tests worth having are the ones that keep that ranking honest, and
that stop the analysis quietly becoming a pricing change nobody asked
for.
"""

import pytest

import margin
from store import STORE


def test_the_prepay_discount_is_the_largest_line(api):
    """Bigger than card fees, bad debt and usage put together.

    And invisible: nobody sends a bill for revenue you never charged, so
    this is the one cost that can grow for years unnoticed.
    """
    lines = margin.per_unit_year("restaurant")["lines"]

    assert "prepay" in lines[0]["line"].lower(), (
        f"expected the discount on top, got {lines[0]['line']}"
    )
    rest = sum(row["usd"] for row in lines[1:])
    assert lines[0]["usd"] > rest, (
        f"discount {lines[0]['usd']} vs everything else {rest}"
    )


def test_metered_usage_is_a_rounding_error(api):
    """The cost everyone tries to cut. Cutting it trades alerting for
    pennies, and the spend-control module already caps it."""
    lines = {row["line"]: row for row in margin.per_unit_year()["lines"]}
    usage = lines["Metered usage (AI, SMS, voice)"]

    assert usage["percent_of_revenue"] < 0.5, usage


def test_the_discount_is_measured_against_what_it_buys(api):
    """A discount is a purchase: certainty and cash, bought with margin."""
    verdict = margin.prepay_verdict()

    assert verdict["break_even_percent"] == pytest.approx(
        margin.BAD_DEBT_PERCENT + margin.CARD_PERCENT, abs=0.01
    )
    assert verdict["worth_it"] is False
    assert verdict["overpaying_by_points"] > 0


def test_financing_value_is_zero_unless_somebody_says_otherwise(
    api, monkeypatch
):
    """Assuming the cash is worth something is how a discount justifies
    itself. A company with these margins is not short of money."""
    assert margin.COST_OF_CAPITAL_PERCENT == 0.0

    monkeypatch.setattr(margin, "COST_OF_CAPITAL_PERCENT", 10.0)
    generous = margin.prepay_verdict()

    assert generous["break_even_percent"] > margin.BAD_DEBT_PERCENT + margin.CARD_PERCENT


def test_the_analysis_does_not_change_anybody_s_prices(api):
    """The point of the module is to inform a commercial decision, not to
    make one. A smaller discount only saves anything if the customer
    still prepays, and that is not a thing code can know."""
    from pricing import ANNUAL_PREPAY_DISCOUNT_PERCENT

    assert ANNUAL_PREPAY_DISCOUNT_PERCENT == 10.0
    assert margin.prepay_verdict()["current_percent"] == 10.0


def test_the_discount_can_be_tuned_without_a_deploy(monkeypatch):
    """It was a constant in a file. A commercial lever nobody can pull
    without shipping code does not get pulled."""
    monkeypatch.setenv("CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT", "6.0")
    import importlib

    import pricing

    importlib.reload(pricing)
    try:
        assert pricing.ANNUAL_PREPAY_DISCOUNT_PERCENT == 6.0
    finally:
        monkeypatch.delenv("CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT")
        importlib.reload(pricing)


def test_fixed_costs_are_shared_not_per_customer(api):
    """Which is why the margin barely moves between one customer and two
    hundred, and why cutting hosting is cutting the product."""
    one = margin.statement("restaurant", 1)
    many = margin.statement("restaurant", 200)

    assert one["fixed_costs_usd"] == many["fixed_costs_usd"]
    assert many["before_tax_percent"] > one["before_tax_percent"]


def test_the_statement_stops_before_tax_and_says_so(api):
    """Tax is bigger than every operating cost combined and is not
    something a constant in a file should have an opinion about."""
    body = margin.statement()

    assert "before_tax_usd" in body
    assert "tax" not in {k.lower() for k in body if k != "note"}
    assert "not something this file should have an opinion about" in body["note"]


def test_the_marginal_cost_of_one_more_customer_is_small(api):
    """The number that decides whether this is a business."""
    assert margin.statement("restaurant", 1)[
        "marginal_cost_of_one_more_unit_usd"
    ] < 100


def test_the_numbers_need_the_platform_key(api):
    assert api.get("/api/margin").status_code == 401


def test_the_operator_can_read_it(api, admin_headers):
    body = api.get("/api/margin", headers=admin_headers).json()

    assert body["statement"]["invoiced_usd"] > 0
    assert body["annual_prepay"]["worth_it"] is False
    assert body["per_unit_year"]["lines"][0]["usd"] > 0


# ---- the cut that is actually made --------------------------------------


def test_the_invoice_leads_with_bank_transfer(monkeypatch):
    """A card processor takes 2.9% of everything it touches, and most
    finance departments pay by whichever method the invoice leads with.
    Ordering the block is the whole intervention."""
    import invoicing
    import mail

    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "Acme Bank\nAcct 1234")
    monkeypatch.setattr(mail, "PAY_URL", "https://pay.example/x")

    block = mail.payment_instructions()

    assert block.index("bank transfer") < block.index("card")
    assert "Acct 1234" in block


def test_a_card_only_deployment_still_says_how_to_pay(monkeypatch):
    import invoicing
    import mail

    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "")
    monkeypatch.setattr(mail, "PAY_URL", "https://pay.example/x")

    assert "Pay online: https://pay.example/x" in mail.payment_instructions()


def test_with_neither_it_still_admits_it(monkeypatch):
    import invoicing
    import mail

    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "")
    monkeypatch.setattr(mail, "PAY_URL", "")

    assert "not configured" in mail.payment_instructions()


# ---- measuring, rather than assuming ------------------------------------


def _traded(store, *, invoices=30, unpaid=2, by_card=0, price=999.0):
    """A history of real invoices, some paid by wire, some by card, some not."""
    from datetime import timedelta

    from store import utc_now

    tenant = store.create_tenant(
        company_name="Northgate", contact_name="Dana",
        contact_phone="+15550100", contact_email="d@north.example",
        plan="growth",
    )
    slow = store.create_tenant(
        company_name="Slow Payer Ltd", contact_name="Kim",
        contact_phone="+15550199", contact_email="k@slow.example",
        plan="growth",
    )
    now = utc_now()
    for index in range(invoices):
        owes = index >= invoices - unpaid
        invoice = store.create_invoice(
            tenant=slow if owes else tenant,
            lines=[{"kind": "subscription", "description": "Monitoring",
                    "quantity": 1, "unit_price_usd": price,
                    "amount_usd": price}],
            period_days=30, terms_days=30,
        )
        invoice.issued_at = now - timedelta(days=120 + index)
        store._db.put("invoice", invoice.invoice_id, invoice.to_row())
        if owes:
            continue
        reference = (f"evt_stripe_{index}" if index < by_card
                     else f"WIRE-{index}")
        store.settle_invoice(invoice, reference, price)
    return tenant, slow


def test_a_thin_ledger_says_so_instead_of_inventing_a_rate(api):
    """Three customers where one paid late is not a 33% bad-debt rate.
    It is three customers."""
    rates = margin.measured_rates()

    assert rates["measuring_bad_debt"] is False
    assert rates["bad_debt_percent_used"] == margin.BAD_DEBT_PERCENT
    assert "noise" in rates["why"]


def test_a_real_ledger_is_measured_rather_than_assumed(api):
    _traded(STORE)

    rates = margin.measured_rates()

    assert rates["measuring_bad_debt"] is True
    assert rates["measured_bad_debt_percent"] == pytest.approx(6.67, abs=0.01)
    assert rates["bad_debt_percent_used"] == rates["measured_bad_debt_percent"]


def test_measuring_is_allowed_to_make_the_number_worse(api):
    """The entire reason to measure.

    The assumption was 3%. This ledger says 6.67%. A margin that
    improves whenever somebody edits a constant is a dashboard, not a
    measurement — and had the constant been lowered to hit the target,
    it would have read 96% while the business made 91.7%.
    """
    assumed = margin.statement("restaurant", 1)["before_tax_percent"]
    _traded(STORE)
    measured = margin.statement("restaurant", 1)["before_tax_percent"]

    assert margin.measured_rates()["measured_bad_debt_percent"] > \
        margin.BAD_DEBT_PERCENT
    assert measured < assumed


def test_the_card_fee_follows_who_actually_used_a_card(api):
    """Invoices lead with bank transfer. Assuming everybody wires it is
    as wrong as assuming everybody swipes."""
    _traded(STORE, invoices=30, unpaid=0, by_card=28)
    mostly_card = margin.effective_card_percent()

    STORE.reset()
    _traded(STORE, invoices=30, unpaid=0, by_card=0)
    all_wire = margin.effective_card_percent()

    assert mostly_card > all_wire
    assert all_wire == 0.0
    assert mostly_card <= margin.CARD_PERCENT


# ---- the target ---------------------------------------------------------


def test_the_target_is_missed_honestly_and_the_gap_is_named(api):
    _traded(STORE)

    verdict = margin.target("restaurant", 1)

    assert verdict["met"] is False
    assert verdict["gap_points"] > 0
    assert verdict["biggest_lever"]["line"] == "bad debt"


def test_good_collection_meets_the_target_without_touching_a_setting(api):
    """Which is the only way it is allowed to be met."""
    _traded(STORE, invoices=40, unpaid=0)

    verdict = margin.target("restaurant", 1)

    assert verdict["achieved_percent"] >= 95.0
    assert verdict["met"] is True


def test_it_says_what_bad_debt_rate_would_meet_the_target(api):
    """A target without a number to aim at is a mood."""
    _traded(STORE)

    ceiling = margin.target("restaurant", 1)["bad_debt_ceiling_percent"]

    assert 2.0 < ceiling < 5.0
    assert margin.measured_rates()["measured_bad_debt_percent"] > ceiling


def test_the_bad_debt_is_named_because_nobody_collects_from_a_percentage(api):
    _traded(STORE)

    owed = margin.target("restaurant", 1)["who_owes_it"]

    assert [r["company_name"] for r in owed] == ["Slow Payer Ltd"]
    assert owed[0]["owed_usd"] == 1998.0
    assert owed[0]["invoices"] == 2
    assert owed[0]["oldest_days"] > 100
    assert owed[0]["contact_email"] == "k@slow.example"


def test_a_recent_unpaid_invoice_is_not_bad_debt_yet(api):
    """Counting last week's invoices as bad debt would make the rate a
    function of how recently anybody was billed."""
    tenant, _ = _traded(STORE, invoices=30, unpaid=0)
    fresh = STORE.create_invoice(
        tenant=tenant,
        lines=[{"kind": "subscription", "description": "Monitoring",
                "quantity": 1, "unit_price_usd": 999.0, "amount_usd": 999.0}],
        period_days=30, terms_days=30,
    )

    assert fresh.state == "issued"
    assert margin.measured_rates()["measured_bad_debt_percent"] == 0.0
    assert margin.target("restaurant", 1)["who_owes_it"] == []
