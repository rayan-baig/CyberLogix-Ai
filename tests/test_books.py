"""The books, and the year-end that decides which tax year revenue is in.

Tax is not something this codebase can cut. What it can do is make the
person who files cheap, and make sure nothing deductible is forgotten
because it lived only in a database.

The test that carries the file is the year-end one. An invoice issued on
28 December and paid on 4 January is revenue in 2026 or 2027 depending
on which basis the entity uses, and getting that wrong is a misfiled
return rather than a rounding error. Both bases are reported; neither is
chosen.
"""

from datetime import datetime, timezone

import pytest

import books
from contracts import run_billing
from store import STORE


def _at(invoice, when):
    invoice.issued_at = when
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    return invoice


@pytest.fixture()
def billed(api, tenant_factory, sensor_factory, owner_headers, mailbox):
    headers, tenant = tenant_factory(
        plan="enterprise", company_name="Northgate Foods"
    )
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    api.post("/api/contracts", headers={**headers, **owner},
             json={"term_years": 1, "escalator_percent": 5.0})
    run_billing()
    return headers, tenant, STORE.invoices_for(tenant["tenant_id"])[0]


# ---- the year-end ------------------------------------------------------


def test_an_invoice_issued_in_december_and_paid_in_january(
    api, billed, settle, mailbox
):
    """The whole reason both bases are reported.

    Accrual puts this in the year it was issued. Cash puts it in the year
    the money arrived. They are different tax years, and which one
    applies is a fact about the entity, not about the data.
    """
    headers, tenant, invoice = billed
    _at(invoice, datetime(2026, 12, 28, tzinfo=timezone.utc))

    settle(tenant["tenant_id"], invoice.invoice_id,
           reference="WIRE-1", amount_usd=500.0)
    live = STORE.get_invoice(invoice.invoice_id)
    live.payments[0]["at"] = "2027-01-04T10:00:00Z"
    STORE._db.put("invoice", live.invoice_id, live.to_row())

    y2026 = books.summary(year=2026)
    y2027 = books.summary(year=2027)

    assert y2026["accrual_basis"]["revenue_usd"] == invoice.total_usd
    assert y2026["cash_basis"]["revenue_usd"] == 0.0
    assert y2027["accrual_basis"]["revenue_usd"] == 0.0
    assert y2027["cash_basis"]["revenue_usd"] == 500.0


def test_neither_basis_is_presented_as_the_answer(api, billed):
    """Picking one would be advice about an entity this file knows
    nothing about."""
    body = books.summary(year=2026)

    assert body["accrual_basis"]["what_it_means"]
    assert body["cash_basis"]["what_it_means"]
    assert "not a return" in body["note"]
    assert "whoever signs the filing" in body["note"]


# ---- the ledgers -------------------------------------------------------


def test_the_sales_ledger_lists_every_invoice_issued(api, billed):
    headers, tenant, invoice = billed
    _at(invoice, datetime(2026, 6, 1, tzinfo=timezone.utc))

    rows = books.sales_ledger(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    assert [r["number"] for r in rows] == [invoice.number]
    assert rows[0]["customer"] == "Northgate Foods"
    assert rows[0]["total_usd"] == invoice.total_usd


def test_the_cash_book_lists_each_payment_separately(api, billed, settle):
    """Only possible because each payment is kept with its own date and
    reference. While an invoice had one `payment_reference` overwritten
    by each payment, no cash-basis figure could be derived at all."""
    headers, tenant, invoice = billed

    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-1",
           amount_usd=100.0)
    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-2",
           amount_usd=250.0)

    rows = books.cash_book(
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2200, 1, 1, tzinfo=timezone.utc),
    )

    assert [r["reference"] for r in rows] == ["WIRE-1", "WIRE-2"]
    assert sum(r["amount_usd"] for r in rows) == 350.0


def test_a_voided_invoice_shows_as_a_write_off(api, admin_headers, billed):
    headers, tenant, invoice = billed
    api.post(f"/api/invoices/{invoice.invoice_id}/void",
             params={"tenant_id": tenant["tenant_id"]}, headers=admin_headers)

    body = books.summary(since="2000-01-01", until="2200-01-01")

    assert body["written_off_usd"] == invoice.total_usd


def test_outstanding_money_is_reported_at_period_end(api, billed):
    body = books.summary(since="2000-01-01", until="2200-01-01")
    _, _, invoice = billed

    assert body["outstanding_at_period_end_usd"] == invoice.total_usd


# ---- the expense side is honest about being partial --------------------


def test_the_cost_side_says_what_it_cannot_see(api, billed):
    """A deduction nobody claims is worth more than one nobody records.

    Presenting the metered spend as the whole expense side would cost
    more in unclaimed deductions than the export saves in effort.
    """
    costs = books.summary(since="2000-01-01", until="2200-01-01")["known_costs"]

    assert "only if somebody entered it" in costs["warning"]
    assert "nobody claims" in costs["warning"]
    assert costs["fixed_infrastructure_usd"] > 0


# ---- the file the accountant opens -------------------------------------


def test_the_ledger_downloads_as_a_csv(api, admin_headers, billed):
    resp = api.get("/api/books/ledger.csv", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in resp.headers["content-disposition"]
    assert "Northgate Foods" in resp.text
    assert resp.text.splitlines()[0].startswith("date,number,customer")


def test_a_company_name_cannot_become_a_spreadsheet_formula(
    api, admin_headers, tenant_factory, sensor_factory, owner_headers, mailbox
):
    """A customer picks their own company name, and it lands in a file
    somebody opens in Excel."""
    hostile = "=cmd|'/c calc'!A1"
    headers, tenant = tenant_factory(plan="enterprise", company_name=hostile)
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    api.post("/api/contracts", headers={**headers, **owner},
             json={"term_years": 1, "escalator_percent": 5.0})
    run_billing()

    text = api.get("/api/books/ledger.csv", headers=admin_headers).text

    assert "=cmd" not in text.replace("'=cmd", "")


def test_the_cash_book_downloads_too(api, admin_headers, billed, settle):
    headers, tenant, invoice = billed
    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-1",
           amount_usd=100.0)

    resp = api.get("/api/books/cash.csv", headers=admin_headers)

    assert resp.status_code == 200
    assert "WIRE-1" in resp.text


def test_the_books_need_the_platform_key(api):
    """Every customer's name and what they owe, in one file."""
    for path in ("", "/ledger", "/cash", "/ledger.csv", "/cash.csv"):
        assert api.get(f"/api/books{path}").status_code == 401, path


# ---- the expense side, which is the actual tax lever -------------------


def test_an_expense_lowers_the_profit_the_return_is_based_on(api, admin_headers):
    """Tax is charged on profit. Profit is revenue minus what you can
    evidence. This is the whole mechanism, and until there was somewhere
    to put them, everything bought on a card the app never sees existed
    only in somebody's memory at filing time."""
    before = api.get("/api/books", headers=admin_headers).json()

    made = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 1499.00, "category": "hardware",
        "description": "Laptop", "supplier": "Apple", "receipt": "receipts/2026/laptop.pdf",
    })
    assert made.status_code == 201, made.text

    after = api.get("/api/books", headers=admin_headers).json()
    assert (after["known_costs"]["known_total_usd"]
            - before["known_costs"]["known_total_usd"]) == pytest.approx(1499.0)
    assert after["known_costs"]["by_category"]["hardware"] == 1499.0


def test_an_expense_without_a_receipt_is_flagged_not_refused(api, admin_headers):
    """It is still a real expense. It is just one you may not get to keep
    if anybody asks."""
    made = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 40.0, "category": "software", "description": "Editor",
    })

    assert made.status_code == 201
    assert "No receipt reference" in made.json()["note"]
    body = api.get("/api/books", headers=admin_headers).json()
    assert body["known_costs"]["without_a_receipt"] == 1
    assert "cannot evidence" in body["known_costs"]["warning"]


def test_an_invented_category_is_refused_with_the_real_ones(api, admin_headers):
    resp = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 10.0, "category": "miscellaneous", "description": "x",
    })

    assert resp.status_code == 422
    assert "'other' is a real answer" in resp.json()["detail"]


def test_an_expense_cannot_be_dated_in_the_future(api, admin_headers):
    """Which year an expense falls in is which year it reduces."""
    resp = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 10.0, "category": "other", "description": "x",
        "spent_at": "2199-01-01",
    })

    assert resp.status_code == 422
    assert "future" in resp.json()["detail"]


def test_a_misspelt_amount_field_is_refused(api, admin_headers):
    """Silently dropping it would record a deduction worth nothing."""
    resp = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount": 500.0, "category": "other", "description": "x",
    })

    assert resp.status_code == 422


def test_expenses_land_in_the_year_they_were_spent(api, admin_headers):
    api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 100.0, "category": "travel", "description": "Site visit",
        "spent_at": "2026-03-04",
    })

    assert api.get("/api/books?year=2026",
                   headers=admin_headers).json()[
        "known_costs"]["recorded_expenses_usd"] == 100.0
    assert api.get("/api/books?year=2025",
                   headers=admin_headers).json()[
        "known_costs"]["recorded_expenses_usd"] == 0.0


def test_an_expense_entered_by_mistake_can_be_removed(api, admin_headers):
    made = api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 10.0, "category": "other", "description": "oops",
    }).json()["expense"]

    gone = api.request("DELETE", f"/api/books/expenses/{made['expense_id']}",
                       headers=admin_headers)

    assert gone.status_code == 200
    assert api.get("/api/books/expenses",
                   headers=admin_headers).json()["count"] == 0


def test_expenses_download_as_a_csv(api, admin_headers):
    api.post("/api/books/expenses", headers=admin_headers, json={
        "amount_usd": 1499.0, "category": "hardware", "description": "Laptop",
        "supplier": "Apple", "receipt": "receipts/laptop.pdf",
    })

    resp = api.get("/api/books/expenses.csv", headers=admin_headers)

    assert resp.status_code == 200
    assert "Laptop" in resp.text and "receipts/laptop.pdf" in resp.text


def test_the_expense_ledger_needs_the_platform_key(api):
    assert api.get("/api/books/expenses").status_code == 401
    assert api.post("/api/books/expenses", json={
        "amount_usd": 1.0, "category": "other", "description": "x"}).status_code == 401


def test_the_categories_are_offered_rather_than_guessed_at(api, admin_headers):
    body = api.get("/api/books/expense-categories", headers=admin_headers).json()

    assert "professional_fees" in body["categories"]
    assert "other" in body["categories"]


# --- the cost of a server nobody had yet ------------------------------------
#
# Fixed infrastructure was priced as rate x months, with months taken
# straight from the requested period. Nothing in that sentence mentions
# the business, so the file happily charged for time the business did
# not exist: $9,968 of hosting against an empty database, because the
# default period begins at the Unix epoch. Asking for a future year was
# worse — a full year of cost, already spent, in 2031.
#
# These are not cosmetic. This file exists to be handed to whoever signs
# a tax return, and a deduction for a server nobody rented is the exact
# thing I declined to write in by hand when asked to cut the bill.


def _trading_since(tenant_id, when):
    """Backdate an account so the business has a real operating history."""
    from dataclasses import replace

    live = STORE._tenants[tenant_id]
    STORE._tenants[tenant_id] = replace(live, activated_at=when)


def test_a_new_deployment_is_charged_one_month_not_six_hundred(
    api, admin_headers
):
    """The default period starts in 1970. The business did not.

    A server that has been up for an hour has cost one month of a
    monthly subscription, which is where the floor comes from. It has
    not cost 680 of them, which is what the epoch-to-now period used to
    produce against a database with nothing in it.
    """
    costs = api.get("/api/books", headers=admin_headers).json()["known_costs"]

    assert costs["months_covered"] == 1.0, costs["months_covered"]
    assert costs["fixed_infrastructure_usd"] < 100, "a deduction nobody may claim"


def test_no_infrastructure_is_charged_for_a_future_year(api, admin_headers,
                                                        tenant_factory):
    _, tenant = tenant_factory()
    _trading_since(tenant["tenant_id"], datetime(2025, 3, 1, tzinfo=timezone.utc))

    costs = api.get("/api/books?year=2031",
                    headers=admin_headers).json()["known_costs"]

    assert costs["fixed_infrastructure_usd"] == 0, "cost that has not been spent"


def test_no_infrastructure_is_charged_before_the_business_existed(
    api, admin_headers, tenant_factory
):
    _, tenant = tenant_factory()
    _trading_since(tenant["tenant_id"], datetime(2025, 3, 1, tzinfo=timezone.utc))

    costs = api.get("/api/books?year=2024",
                    headers=admin_headers).json()["known_costs"]

    assert costs["fixed_infrastructure_usd"] == 0


def test_a_part_year_is_charged_only_for_the_part_it_traded(
    api, admin_headers, tenant_factory
):
    """Trading from 1 March 2025 is ten months of 2025, not twelve."""
    _, tenant = tenant_factory()
    _trading_since(tenant["tenant_id"], datetime(2025, 3, 1, tzinfo=timezone.utc))

    costs = api.get("/api/books?year=2025",
                    headers=admin_headers).json()["known_costs"]

    assert 9.5 < costs["months_covered"] < 10.5, costs["months_covered"]
    assert costs["infrastructure_charged_from"].startswith("2025-03-01")


def test_the_current_year_stops_at_today_not_at_december(
    api, admin_headers, tenant_factory
):
    """You cannot deduct next month's hosting in September."""
    _, tenant = tenant_factory()
    _trading_since(tenant["tenant_id"], datetime(2020, 1, 1, tzinfo=timezone.utc))
    now = books.utc_now()

    costs = api.get(f"/api/books?year={now.year}",
                    headers=admin_headers).json()["known_costs"]

    elapsed = (now - datetime(now.year, 1, 1, tzinfo=timezone.utc))
    elapsed_months = elapsed.total_seconds() / (30.44 * 86400)
    assert costs["months_covered"] <= elapsed_months + 0.01
    assert costs["infrastructure_charged_to"].startswith(str(now.year))


def test_the_charged_window_is_stated_so_the_number_can_be_checked(
    api, admin_headers, tenant_factory
):
    _, tenant = tenant_factory()
    _trading_since(tenant["tenant_id"], datetime(2025, 3, 1, tzinfo=timezone.utc))

    costs = api.get("/api/books?year=2025",
                    headers=admin_headers).json()["known_costs"]

    assert costs["infrastructure_charged_from"] is not None
    assert costs["infrastructure_charged_to"] is not None
    assert "actually running" in costs["warning"]
