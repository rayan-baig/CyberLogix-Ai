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

    assert "almost certainly the larger half" in costs["warning"]
    assert "goes unclaimed" in costs["warning"]
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
