"""The log an inspector is handed: per unit, for chosen dates, every time
it went out of range and who answered -- and nothing of anybody else's.

The printed page is checked in a real browser (tools/console_smoke.js and
docs/TESTING.md); these hold the figures, the boundaries and the wiring.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
LOG = "/api/autopilot/inspector-log"


def _estate(api, operator_factory, sensor_factory, company="Harbor Grill",
            email="owner@example.com", prefix="HG"):
    headers, tenant, _ = operator_factory(company_name=company, email=email)
    sensor_factory(headers, sensor_id=f"{prefix}-WALKIN", vertical="restaurant",
                   location="Kitchen walk-in")
    sensor_factory(headers, sensor_id=f"{prefix}-BAR", vertical="restaurant",
                   location="Bar freezer")
    for temp in (20.0, 22.0, 45.0, 58.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": f"{prefix}-WALKIN", "temperature_fahrenheit": temp})
    for temp in (1.0, 3.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": f"{prefix}-BAR", "temperature_fahrenheit": temp})
    return headers, tenant


def test_each_unit_has_its_log_and_every_breach_who_answered_it(
        api, operator_factory, sensor_factory):
    headers, _ = _estate(api, operator_factory, sensor_factory)
    incident = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    api.post(f"/api/voice/acknowledge/{incident['incident_id']}", headers=headers, json={})
    api.post(f"/api/voice/corrective-action/{incident['incident_id']}", headers=headers,
             json={"action": "Door found open and closed", "product_disposition": "moved"})

    log = api.get(LOG, headers=headers).json()

    assert log["company_name"] == "Harbor Grill"
    assert log["temperature_unit"] == "F"
    walkin = next(u for u in log["units"] if u["sensor_id"] == "HG-WALKIN")
    bar = next(u for u in log["units"] if u["sensor_id"] == "HG-BAR")
    assert walkin["location_name"] == "Kitchen walk-in"
    assert walkin["limit_above"] == 32.0
    assert walkin["readings"] == 4 and walkin["out_of_range_readings"] == 2
    assert walkin["lowest"] == 20.0 and walkin["highest"] == 58.0
    assert walkin["in_range_percent"] == 50.0
    day = walkin["days"][-1]
    assert day["day"] == utc_now().date().isoformat()
    assert (day["low"], day["high"], day["readings"], day["out_of_range"]) == (20.0, 58.0, 4, 2)
    assert sum(h["readings"] for h in walkin["hours"]) == 4

    [breach] = walkin["breaches"]
    assert breach["worst"] == 58.0
    assert breach["acknowledged_by"].startswith("Dana Reyes")
    assert breach["minutes_to_acknowledge"] is not None
    assert breach["corrective_action"] == "Door found open and closed"
    assert breach["product_disposition"] == "moved"
    assert bar["breaches"] == [] and bar["out_of_range_readings"] == 0
    assert log["breaches"] == 1 and log["unanswered"] == 0


def test_an_unanswered_breach_is_counted_as_one(api, operator_factory, sensor_factory):
    headers, _ = _estate(api, operator_factory, sensor_factory)
    log = api.get(LOG, headers=headers).json()
    assert log["unanswered"] == 1
    walkin = next(u for u in log["units"] if u["sensor_id"] == "HG-WALKIN")
    assert walkin["breaches"][0]["acknowledged_by"] == ""


def test_only_this_businesss_units_and_breaches(api, operator_factory, sensor_factory):
    grill, _ = _estate(api, operator_factory, sensor_factory)
    bakery, _ = _estate(api, operator_factory, sensor_factory, company="Harbor Bakery",
                        email="baker@example.com", prefix="HB")

    for headers, mine, theirs in ((grill, "HG-", "HB-"), (bakery, "HB-", "HG-")):
        log = api.get(LOG, headers=headers).json()
        ids = {u["sensor_id"] for u in log["units"]}
        assert ids and all(i.startswith(mine) for i in ids), ids
        assert theirs not in str(log)


def test_the_dates_chosen_are_the_dates_shown(api, operator_factory, sensor_factory):
    headers, _ = _estate(api, operator_factory, sensor_factory)
    # Move one breach back ten days: outside a week, inside a fortnight.
    reading = STORE.readings_for("HG-WALKIN")[-1]
    reading.recorded_at -= timedelta(days=10)
    today = utc_now().date()

    week = api.get(LOG, headers=headers, params={
        "start": (today - timedelta(days=6)).isoformat(), "end": today.isoformat()}).json()
    walkin = next(u for u in week["units"] if u["sensor_id"] == "HG-WALKIN")
    assert walkin["highest"] == 45.0
    assert week["period_start"] == (today - timedelta(days=6)).isoformat()

    old = (today - timedelta(days=10)).isoformat()
    that_day = api.get(LOG, headers=headers, params={"start": old, "end": old}).json()
    walkin = next(u for u in that_day["units"] if u["sensor_id"] == "HG-WALKIN")
    assert walkin["readings"] == 1 and walkin["highest"] == 58.0
    assert [d["day"] for d in walkin["days"]] == [old]


@pytest.mark.parametrize("params", [
    {"start": "2026-02-30"},
    {"start": "yesterday"},
    {"start": "2026-05-10", "end": "2026-05-01"},
    {"start": "2026-01-01", "end": "2026-12-31"},
])
def test_a_bad_range_is_refused_in_words(api, operator_factory, sensor_factory, params):
    headers, _ = _estate(api, operator_factory, sensor_factory)
    resp = api.get(LOG, headers=headers, params=params)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_it_needs_a_credential(api):
    assert api.get(LOG).status_code in (401, 403)


def test_one_click_prints_it_and_the_paper_copy_is_set_for_ink():
    """The card's button fetches, draws the document and opens the print
    dialog; the print stylesheet shows only the document, in black on
    white, with each table's headings repeated on every page."""
    console = (ROOT / "static" / "console.js").read_text()
    page = (ROOT / "static" / "console.html").read_text()
    theme = (ROOT / "static" / "theme.css").read_text()

    assert 'id="inspector-from"' in page and 'id="inspector-to"' in page
    assert 'id="inspector-print"' in page
    handler = console[console.index("async function openInspectorLog("):]
    handler = handler[:handler.index("\n}\n")]
    assert "/api/autopilot/inspector-log?start=" in handler
    assert "window.print()" in handler and "if (print)" in handler
    assert "MTRX Horizyn" in handler
    printing = theme[theme.index("@media print {"):]
    assert "body.drawer-open > *:not(#drawer) { display: none !important; }" in printing
    assert "thead { display: table-header-group; }" in printing
    assert ".log-out td" in printing


def test_the_invoice_prints_with_its_period_and_the_company_footer(
        api, operator_factory, sensor_factory, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", "admin-key-for-tests-1234")
    headers, tenant, _ = operator_factory(company_name="Harbor Grill")
    sensor_factory(headers, sensor_id="HG-1", vertical="restaurant")
    made = api.post(f"/api/invoices?tenant_id={tenant['tenant_id']}",
                    headers={"X-CyberLogix-Admin": "admin-key-for-tests-1234"},
                    json={"period_days": 30})
    assert made.status_code == 201, made.text
    invoice = api.get(f"/api/invoices/{made.json()['invoice_id']}", headers=headers).json()

    assert invoice["billed_to"]["company_name"] == "Harbor Grill"
    period = invoice["service_period"]
    assert period["start"] and period["end"] and " to " in period["label"]
    console = (ROOT / "static" / "console.js").read_text()
    view = console[console.index("async function openInvoice("):]
    view = view[:view.index("\n}\n")]
    assert "inv.service_period.label" in view
    assert "a product of MTRX Horizyn" in view
    assert "Subtotal" in view and "Balance due" in view
