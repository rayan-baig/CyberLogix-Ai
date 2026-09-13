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
