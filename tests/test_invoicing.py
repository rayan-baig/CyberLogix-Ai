"""Invoices.

Two properties carry the whole module: an issued invoice's figures never
move, and its number is never reused. Everything else is bookkeeping.
"""

import pytest


def estate(api, operator_factory, sensor_factory, units=2, sites=1):
    headers, _, _ = operator_factory(plan="enterprise")
    for n in range(sites):
        api.post("/api/sites", headers=headers, json={"name": f"Site {n}"})
    for n in range(units):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")
    return headers


def issue(api, headers, **params):
    body = {"include_add_ons": "", "include_setup": False, "period_days": 30}
    body.update(params)
    resp = api.post("/api/invoices", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_an_invoice_totals_its_lines(api, operator_factory, sensor_factory):
    headers = estate(api, operator_factory, sensor_factory, units=3)
    invoice = issue(api, headers)

    assert invoice["state"] == "issued"
    assert invoice["lines"][0]["quantity"] == 3
    assert invoice["total_usd"] == 3 * 999.0
    assert invoice["total_usd"] == round(
        sum(line["amount_usd"] for line in invoice["lines"]), 2)


def test_add_ons_and_setup_appear_as_their_own_lines(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory, units=2, sites=2)
    invoice = issue(api, headers, include_add_ons="assurance,vault",
                    include_setup=True)

    kinds = [line["kind"] for line in invoice["lines"]]
    assert kinds.count("subscription") == 1
    assert kinds.count("add_on") == 2
    assert kinds.count("setup") == 1

    setup = next(l for l in invoice["lines"] if l["kind"] == "setup")
    assert setup["quantity"] == 2
    assert setup["amount_usd"] == 3000.0
    assert invoice["total_usd"] == round(
        2 * 999.0 + 2 * 149.0 + 499.0 + 3000.0, 2)


def test_the_figures_never_move_after_issue(
    api, operator_factory, sensor_factory
):
    """An invoice whose total changes after it was sent is a dispute."""
    headers = estate(api, operator_factory, sensor_factory, units=2)
    invoice = issue(api, headers)
    original = invoice["total_usd"]

    # The estate doubles the next morning.
    for n in range(2, 6):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")

    reread = api.get(f"/api/invoices/{invoice['invoice_id']}",
                     headers=headers).json()
    assert reread["total_usd"] == original
    assert reread["lines"][0]["quantity"] == 2

    # And the next invoice reflects the new estate.
    later = issue(api, headers)
    assert later["total_usd"] == 6 * 999.0


def test_numbers_are_sequential_and_never_reused(
    api, operator_factory, sensor_factory
):
    """A gap in the sequence is the first thing an auditor asks about."""
    headers = estate(api, operator_factory, sensor_factory)
    first = issue(api, headers)
    second = issue(api, headers)

    year = first["number"].split("-")[1]
    assert first["number"] == f"CLX-{year}-0001"
    assert second["number"] == f"CLX-{year}-0002"

    # Voiding does not free the number.
    api.post(f"/api/invoices/{second['invoice_id']}/void", headers=headers)
    third = issue(api, headers)
    assert third["number"] == f"CLX-{year}-0003"


def test_a_number_survives_a_restart(tmp_path):
    """A counter reset by a restart would reissue a number."""
    from db import Database
    from store import HubStore

    path = str(tmp_path / "inv.db")
    lines = [{"kind": "subscription", "description": "x", "quantity": 1,
              "unit_price_usd": 100.0, "amount_usd": 100.0}]

    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    one = first.create_invoice(tenant, lines)

    second = HubStore(db=Database(path))
    tenant = second.get_tenant(tenant.tenant_id)
    two = second.create_invoice(tenant, lines)

    assert two.number != one.number
    assert int(two.number.split("-")[2]) == int(one.number.split("-")[2]) + 1


def test_a_line_cannot_be_mutated_after_issue(tmp_path):
    """The caller's list must not be a live handle into the record."""
    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("A", "n", "+1", "a@example.com", "growth")
    lines = [{"kind": "subscription", "description": "x", "quantity": 1,
              "unit_price_usd": 100.0, "amount_usd": 100.0}]
    invoice = store.create_invoice(tenant, lines)

    lines[0]["amount_usd"] = 1.0
    assert invoice.lines[0]["amount_usd"] == 100.0


def test_settlement_records_a_short_payment(
    api, operator_factory, sensor_factory
):
    """A short payment that quietly closes an invoice is money never chased."""
    headers = estate(api, operator_factory, sensor_factory, units=2)
    invoice = issue(api, headers)

    out = api.post(f"/api/invoices/{invoice['invoice_id']}/paid", headers=headers,
                   json={"reference": "WIRE-8823", "amount_usd": 1000.0}).json()
    assert out["invoice"]["state"] == "paid"
    assert "paid $1,000.00 of $1,998.00" in out["invoice"]["payment_reference"]


def test_a_paid_invoice_cannot_be_voided(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    api.post(f"/api/invoices/{invoice['invoice_id']}/paid", headers=headers,
             json={"reference": "WIRE-1"})

    resp = api.post(f"/api/invoices/{invoice['invoice_id']}/void", headers=headers)
    assert resp.status_code == 409
    assert "credit note" in resp.json()["detail"]


def test_a_voided_invoice_cannot_be_paid(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    api.post(f"/api/invoices/{invoice['invoice_id']}/void", headers=headers)

    resp = api.post(f"/api/invoices/{invoice['invoice_id']}/paid", headers=headers,
                    json={"reference": "WIRE-1"})
    assert resp.status_code == 409


def test_billing_nothing_is_refused(api, operator_factory):
    """An invoice for zero is a mistake, not a document."""
    headers, _, _ = operator_factory(plan="enterprise")
    resp = api.post("/api/invoices", headers=headers,
                    json={"include_add_ons": "", "include_setup": False,
                          "period_days": 30})
    assert resp.status_code == 409


def test_the_ledger_reports_what_is_outstanding(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory, units=2)
    first = issue(api, headers)
    issue(api, headers)
    api.post(f"/api/invoices/{first['invoice_id']}/paid", headers=headers,
             json={"reference": "WIRE-1"})

    ledger = api.get("/api/invoices", headers=headers).json()
    assert ledger["count"] == 2
    assert ledger["outstanding_count"] == 1
    assert ledger["outstanding_usd"] == 2 * 999.0
    assert ledger["overdue_count"] == 0


def test_an_overdue_invoice_is_flagged(api, operator_factory, sensor_factory):
    from datetime import timedelta

    from store import STORE

    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    STORE.get_invoice(invoice["invoice_id"]).due_at -= timedelta(days=45)

    ledger = api.get("/api/invoices", headers=headers).json()
    assert ledger["overdue_count"] == 1
    assert ledger["invoices"][0]["overdue"] is True
    assert ledger["invoices"][0]["days_until_due"] < 0


def test_only_an_owner_can_issue(api, operator_factory, sensor_factory,
                                 api_key_headers=None):
    """Billing is an owner's act, not an operator's."""
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    api.post("/api/accounts/users", headers=headers,
             json={"email": "ops@example.com", "full_name": "Sam Cole",
                   "role": "operator", "password": "correct-horse-battery"})
    signed = api.post("/api/accounts/login",
                      json={"email": "ops@example.com",
                            "password": "correct-horse-battery"}).json()
    operator = {"Authorization": f"Bearer {signed['token']}"}

    resp = api.post("/api/invoices", headers=operator,
                    json={"include_add_ons": "", "include_setup": False,
                          "period_days": 30})
    assert resp.status_code == 403


def test_another_tenants_invoice_is_invisible(
    api, operator_factory, sensor_factory
):
    theirs = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, theirs)

    mine, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    assert api.get(f"/api/invoices/{invoice['invoice_id']}",
                   headers=mine).status_code == 404


def test_an_invoice_names_who_is_asking_to_be_paid(
    api, operator_factory, sensor_factory, monkeypatch
):
    """A finance department cannot pay a document with no issuer on it."""
    import invoicing

    monkeypatch.setitem(invoicing.ISSUER, "legal_name", "CyberLogix AI LLC")
    monkeypatch.setitem(invoicing.ISSUER, "address", "1 Harbor Way, Boca Raton FL")
    monkeypatch.setitem(invoicing.ISSUER, "tax_id", "88-1234567")
    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "Chase ****4419")

    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    doc = api.get(f"/api/invoices/{invoice['invoice_id']}", headers=headers).json()

    assert doc["issued_by"]["legal_name"] == "CyberLogix AI LLC"
    assert doc["issued_by"]["tax_id"] == "88-1234567"
    assert "note" not in doc["issued_by"]


def test_unconfigured_issuer_details_say_so_rather_than_printing_blanks(
    api, operator_factory, sensor_factory, monkeypatch
):
    """A line reading "Tax ID:" with nothing after it looks like a fault."""
    import invoicing

    for key in ("address", "tax_id", "remit_to", "email"):
        monkeypatch.setitem(invoicing.ISSUER, key, "")

    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    doc = api.get(f"/api/invoices/{invoice['invoice_id']}", headers=headers).json()

    assert "address" not in doc["issued_by"]
    assert "before sending an invoice" in doc["issued_by"]["note"]
