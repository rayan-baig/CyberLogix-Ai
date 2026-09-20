"""Invoices.

Two properties carry the whole module: an issued invoice's figures never
move, and its number is never reused. Everything else is bookkeeping.
"""

def estate(api, operator_factory, sensor_factory, units=2, sites=1):
    headers, _, _ = operator_factory(plan="enterprise")
    for n in range(sites):
        api.post("/api/sites", headers=headers, json={"name": f"Site {n}"})
    for n in range(units):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")
    return headers


# Issuing, settling and voiding are the vendor's side of the transaction,
# so the suite holds the platform credential to exercise them. A customer
# reaching any of these is the thing test_the_customer_cannot_write_their
# _own_ledger checks does not happen.
ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


def tenant_id_of(api, headers):
    return api.get("/api/licenses/me", headers=headers).json()["tenant_id"]


def issue(api, headers, **params):
    body = {"include_add_ons": "", "include_setup": False, "period_days": 30}
    body.update(params)
    resp = api.post("/api/invoices", params={"tenant_id": tenant_id_of(api, headers)},
                    headers=ADMIN, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def settle(api, headers, invoice_id, **body):
    return api.post(f"/api/invoices/{invoice_id}/paid",
                    params={"tenant_id": tenant_id_of(api, headers)},
                    headers=ADMIN, json=body)


def void(api, headers, invoice_id):
    return api.post(f"/api/invoices/{invoice_id}/void",
                    params={"tenant_id": tenant_id_of(api, headers)},
                    headers=ADMIN, json={})


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
    invoice = issue(api, headers, include_add_ons="vault,benchmarks",
                    include_setup=True)

    kinds = [line["kind"] for line in invoice["lines"]]
    assert kinds.count("subscription") == 1
    assert kinds.count("add_on") == 2
    assert kinds.count("setup") == 1

    setup = next(l for l in invoice["lines"] if l["kind"] == "setup")
    assert setup["quantity"] == 2
    assert setup["amount_usd"] == 3000.0
    # Read from the rate card rather than typed in. The figures used to
    # be literals, and when an add-on was withdrawn the test failed on
    # arithmetic rather than on the thing it is about, which is that each
    # charge gets its own line and they add up.
    from pricing import ADD_ONS

    add_ons = sum(ADD_ONS[key]["monthly_usd"] for key in ("vault", "benchmarks"))
    assert all(ADD_ONS[key]["basis"] == "per estate"
               for key in ("vault", "benchmarks")), (
        "a per-unit add-on would need multiplying by the unit count here")
    assert invoice["total_usd"] == round(2 * 999.0 + add_ons + 3000.0, 2)


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
    void(api, headers, second['invoice_id'])
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


def test_a_short_payment_leaves_the_invoice_open(
    api, operator_factory, sensor_factory
):
    """A short payment that quietly closes an invoice is money never chased.

    This used to set state to "paid" regardless and write the shortfall
    into a prose string, so $1 against $48,000 dropped the remaining
    $47,999 out of the ledger entirely.
    """
    headers = estate(api, operator_factory, sensor_factory, units=2)
    invoice = issue(api, headers)          # 2 x $999 = $1,998

    out = settle(api, headers, invoice['invoice_id'], **{"reference": "WIRE-8823", "amount_usd": 1000.0}).json()
    assert out["invoice"]["state"] == "part_paid"
    assert out["invoice"]["amount_paid_usd"] == 1000.0
    assert out["invoice"]["balance_usd"] == 998.0
    assert out["invoice"]["open"] is True
    assert "998.00 is still outstanding" in out["message"]

    # And it is still being chased.
    ledger = api.get("/api/invoices", headers=headers).json()
    assert ledger["outstanding_count"] == 1
    assert ledger["outstanding_usd"] == 998.0
    assert ledger["part_paid_count"] == 1


def test_the_balance_settles_on_the_second_payment(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory, units=2)
    invoice = issue(api, headers)
    iid = invoice["invoice_id"]

    settle(api, headers, iid, **{"reference": "WIRE-1", "amount_usd": 1000.0})
    out = settle(api, headers, iid, **{"reference": "WIRE-2", "amount_usd": 998.0}).json()

    assert out["invoice"]["state"] == "paid"
    assert out["invoice"]["balance_usd"] == 0.0
    ledger = api.get("/api/invoices", headers=headers).json()
    assert ledger["outstanding_count"] == 0
    assert ledger["outstanding_usd"] == 0.0


def test_a_part_paid_invoice_still_goes_overdue(
    api, operator_factory, sensor_factory
):
    """The half nobody paid does not stop being late."""
    from datetime import timedelta

    from store import STORE

    headers = estate(api, operator_factory, sensor_factory, units=2)
    invoice = issue(api, headers)
    settle(api, headers, invoice['invoice_id'], **{"reference": "WIRE-1", "amount_usd": 500.0})
    STORE.get_invoice(invoice["invoice_id"]).due_at -= timedelta(days=45)

    ledger = api.get("/api/invoices", headers=headers).json()
    assert ledger["overdue_count"] == 1
    assert ledger["outstanding_usd"] == 1498.0


def test_a_paid_invoice_cannot_be_voided(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    settle(api, headers, invoice['invoice_id'], **{"reference": "WIRE-1"})

    resp = void(api, headers, invoice['invoice_id'])
    assert resp.status_code == 409
    assert "credit note" in resp.json()["detail"]


def test_a_voided_invoice_cannot_be_paid(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory)
    invoice = issue(api, headers)
    void(api, headers, invoice['invoice_id'])

    resp = settle(api, headers, invoice['invoice_id'], **{"reference": "WIRE-1"})
    assert resp.status_code == 409


def test_billing_nothing_is_refused(api, operator_factory):
    """An invoice for zero is a mistake, not a document."""
    headers, tenant, _ = operator_factory(plan="enterprise")
    resp = api.post("/api/invoices", params={"tenant_id": tenant["tenant_id"]},
                    headers=ADMIN,
                    json={"include_add_ons": "", "include_setup": False,
                          "period_days": 30})
    assert resp.status_code == 409


def test_the_ledger_reports_what_is_outstanding(
    api, operator_factory, sensor_factory
):
    headers = estate(api, operator_factory, sensor_factory, units=2)
    first = issue(api, headers)
    issue(api, headers)
    settle(api, headers, first['invoice_id'], **{"reference": "WIRE-1"})

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


def test_the_customer_cannot_write_their_own_ledger(
    api, operator_factory, sensor_factory
):
    """The one that matters most in this file.

    Issuing an invoice and recording that it was paid used to be
    `require_role("owner")` — which is the *customer's* owner, on the
    other side of the transaction. Measured before the fix: a customer's
    own owner could POST to /paid with a made-up reference and write off a
    $2,499 invoice in one call. The balance went to zero, it dropped out
    of collections, and nothing anywhere recorded that no money had
    arrived.

    No tenant role reaches these now, not even an owner, and not with the
    tenant API key either.
    """
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    invoice = issue(api, headers)
    params = {"tenant_id": tenant["tenant_id"]}

    for label, call in (
        ("issue", lambda h: api.post(
            "/api/invoices", params=params, headers=h,
            json={"include_add_ons": "", "include_setup": False,
                  "period_days": 30})),
        ("write off", lambda h: api.post(
            f"/api/invoices/{invoice['invoice_id']}/paid", params=params,
            headers=h, json={"reference": "i-said-so"})),
        ("void", lambda h: api.post(
            f"/api/invoices/{invoice['invoice_id']}/void", params=params,
            headers=h, json={})),
    ):
        resp = call(headers)
        assert resp.status_code in (401, 403), (
            f"the customer's own owner could {label} an invoice: "
            f"{resp.status_code}"
        )

    # And the invoice is untouched by any of it.
    fresh = api.get(f"/api/invoices/{invoice['invoice_id']}",
                    headers=headers).json()
    assert fresh["state"] == "issued"
    assert fresh["amount_paid_usd"] == 0.0
    assert fresh["balance_usd"] == invoice["total_usd"]


def test_the_operator_key_is_what_reaches_the_ledger(
    api, operator_factory, sensor_factory
):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    invoice = issue(api, headers)

    settled = settle(api, headers, invoice["invoice_id"], reference="WIRE-1")
    assert settled.status_code == 200, settled.text
    assert settled.json()["invoice"]["state"] == "paid"


def test_a_wrong_operator_key_is_refused(api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    invoice = issue(api, headers)

    resp = api.post(
        f"/api/invoices/{invoice['invoice_id']}/paid",
        params={"tenant_id": tenant["tenant_id"]},
        headers={"X-CyberLogix-Admin": "not-the-key"},
        json={"reference": "nice try"},
    )
    assert resp.status_code == 401


def test_with_no_operator_key_configured_the_ledger_is_closed(
    api, operator_factory, sensor_factory, monkeypatch
):
    """Unset must mean closed, not open."""
    import auth

    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    invoice = issue(api, headers)

    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", "")
    resp = api.post(
        f"/api/invoices/{invoice['invoice_id']}/paid",
        params={"tenant_id": tenant["tenant_id"]},
        headers=ADMIN, json={"reference": "WIRE-1"},
    )
    assert resp.status_code == 503
    assert "CYBERLOGIX_ADMIN_KEY" in resp.json()["detail"]


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


# ---- a payment is applied once, however many times it arrives ----------


def _billed(api, tenant_factory, sensor_factory, owner_headers):
    from contracts import run_billing
    from store import STORE

    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    resp = api.post(
        "/api/contracts",
        headers={**headers, **owner},
        json={"term_years": 1, "escalator_percent": 5.0},
    )
    assert resp.status_code == 201, resp.text
    run_billing()
    return headers, tenant, STORE.invoices_for(tenant["tenant_id"])[0]


def test_the_same_payment_reference_is_only_applied_once(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    """Every payment processor delivers at least once.

    A Stripe retry is ordinary, not exceptional, and this endpoint is
    where a Stripe adapter lands. Measured before the fix: the same $100
    event delivered three times recorded $300, which took the invoice to
    "paid" on a third of the money and dropped it out of collections for
    good. That is a customer writing off their own invoice, dug from our
    side.
    """
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )

    for _ in range(3):
        resp = settle(
            tenant["tenant_id"], invoice.invoice_id,
            reference="STRIPE-EVT-1", amount_usd=100.0,
        )
        assert resp.status_code == 200, resp.text

    from store import STORE

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.amount_paid_usd == 100.0
    assert len(live.payments) == 1
    assert live.state == "part_paid"


def test_a_repeat_answers_200_so_the_processor_stops_retrying(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    """Tell a webhook its delivery failed and it delivers again, which is
    the thing this refusal exists to survive."""
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )

    settle(tenant["tenant_id"], invoice.invoice_id, reference="EVT-1",
           amount_usd=50.0)
    again = settle(tenant["tenant_id"], invoice.invoice_id, reference="EVT-1",
                   amount_usd=50.0)

    assert again.status_code == 200
    body = again.json()
    assert body["already_recorded"] is True
    assert "give it its own reference" in body["message"]


def test_different_references_are_two_different_payments(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )

    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-1",
           amount_usd=100.0)
    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-2",
           amount_usd=250.0)

    from store import STORE

    live = STORE.get_invoice(invoice.invoice_id)
    assert live.amount_paid_usd == 350.0
    assert [p["reference"] for p in live.payments] == ["WIRE-1", "WIRE-2"]


def test_every_payment_is_kept_not_just_the_last(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    """`payment_reference` was the whole record and each payment
    overwrote it, so after three part payments the ledger knew the total
    and the last reference. Reconciling that against a bank statement is
    guesswork."""
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )

    for index, amount in enumerate((100.0, 200.0, 300.0), start=1):
        settle(tenant["tenant_id"], invoice.invoice_id,
               reference=f"WIRE-{index}", amount_usd=amount)

    from store import STORE

    live = STORE.get_invoice(invoice.invoice_id)
    assert [(p["reference"], p["amount_usd"]) for p in live.payments] == [
        ("WIRE-1", 100.0), ("WIRE-2", 200.0), ("WIRE-3", 300.0),
    ]
    assert all(p["at"] for p in live.payments)


def test_the_customer_can_see_their_own_payment_history(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )
    settle(tenant["tenant_id"], invoice.invoice_id, reference="WIRE-1",
           amount_usd=100.0)

    body = api.get(f"/api/invoices/{invoice.invoice_id}", headers=headers).json()

    assert [p["reference"] for p in body["payments"]] == ["WIRE-1"]


def test_the_payment_history_survives_a_restart(
    api, tenant_factory, sensor_factory, owner_headers, settle
):
    """Otherwise a restart re-arms the duplicate."""
    headers, tenant, invoice = _billed(
        api, tenant_factory, sensor_factory, owner_headers
    )
    settle(tenant["tenant_id"], invoice.invoice_id, reference="EVT-1",
           amount_usd=100.0)

    from store import STORE

    STORE._invoices.clear()
    STORE.load()

    settle(tenant["tenant_id"], invoice.invoice_id, reference="EVT-1",
           amount_usd=100.0)

    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 100.0


def test_an_invoice_written_before_payments_were_kept_still_loads(api):
    """Old rows have no payments list. They must not explode on read."""
    from store import Invoice

    row = {
        "invoice_id": "INV-000999", "tenant_id": "TEN-000001",
        "number": "CLX-2026-0999", "company_name": "Legacy",
        "lines": [], "subtotal_usd": 100.0, "total_usd": 100.0,
        "amount_paid_usd": 40.0, "payment_reference": "WIRE-OLD",
        "state": "part_paid",
    }
    restored = Invoice.from_row(row)

    assert restored.payments == []
    assert restored.amount_paid_usd == 40.0
