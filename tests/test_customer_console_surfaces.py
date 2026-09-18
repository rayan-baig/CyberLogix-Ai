"""The three things a customer could pay for and never see.

Found by counting endpoints against pages: 132 routes, and fourteen with
no interface at all. Three of those were not obscure -- they were the
product's own selling points, reachable only with curl:

* **The forecast.** The summary tile has always said "6 projected to
  breach". Nothing said which six. On an estate of two hundred sensors
  that number is unusable: you would open every drawer in turn to find
  the ones that matter.
* **What could be claimed.** A packet could always be produced for an
  incident, and nothing listed the incidents one could be produced for
  -- so using the feature required already knowing the answer.
* **Who an alert actually reaches.** The roster is what was configured.
  The dispatcher's ladder is what would happen, and a contact with both
  channels muted is on one and not the other.

The tests are the same key contract as the operator console: the names
the page reads out of each response, checked against what the endpoint
returns. Nothing in JavaScript objects when a name is missing -- it
renders `undefined`, which is how `opened_at_display` and
`location_name`, neither of which this endpoint has ever returned, got
written into the first version of the claims list.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "static" / "console.html"


def script() -> str:
    src = PAGE.read_text()
    return src[src.index("<script>"): src.rindex("</script>")]


CONTRACT = {
    "/api/forecast/fleet": [
        "window_hours", "sensors_analysed", "risk_tally", "at_risk",
    ],
    "/api/claims/eligible": ["count", "period_days", "incidents"],
    "/api/contacts/preview": ["sms_recipients", "voice_ladder"],
}


@pytest.mark.parametrize("path", sorted(CONTRACT))
def test_the_console_reads_names_the_api_returns(path, api, enterprise):
    headers, _ = enterprise
    resp = api.get(path, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    missing = [key for key in CONTRACT[path] if key not in body]
    assert not missing, f"{path} no longer returns {missing}"


@pytest.fixture()
def enterprise(api, tenant_factory, owner_headers, sensor_factory):
    """A plan that includes forecasting, with something to forecast."""
    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name="Harbor Cold Store")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-1", "cybersecurity")
    return {**headers, **owner}, tenant


@pytest.fixture()
def breached(api, enterprise):
    """One real excursion, so there is something claimable to inspect.

    Every call asserted: the first version of this posted to
    /api/telemetry, which is not a route, and got a silent 404 -- so the
    test it fed failed for a reason that had nothing to do with the
    thing under test.
    """
    headers, _ = enterprise

    for temp in (70.0, 95.0, 96.0):
        pulsed = api.post("/api/sensor-pulse", headers=headers,
                          json={"sensor_id": "RACK-1",
                                "temperature_fahrenheit": temp})
        assert pulsed.status_code == 200, pulsed.text
    return headers


def test_a_forecast_row_only_reads_fields_the_forecast_has(api, enterprise):
    """Every field the card renders, on a real row."""
    headers, _ = enterprise
    body = api.get("/api/forecast/fleet", headers=headers).json()

    assert body["forecasts"], "nothing to check against"
    row = body["forecasts"][0]
    for key in ("sensor_id", "location_name", "likely_catastrophe",
                "risk_level", "hours_until_breach", "trend_f_per_hour"):
        assert key in row, key


def test_a_claimable_incident_only_reads_fields_it_has(
    api, enterprise, breached
):
    """This is the one that shipped wrong: the first version rendered
    `opened_at_display` and `location_name`, and this endpoint has never
    returned either. JavaScript does not object -- it writes "undefined"
    into the page.

    The field list is read off a real response rather than written down
    here, so the test cannot drift from the endpoint the way the page
    did."""
    headers, _ = enterprise
    body = api.get("/api/claims/eligible", headers=headers).json()
    assert body["incidents"], "no incident to read the shape from"
    returned = set(body["incidents"][0])

    js = script()
    block = js[js.index("function renderClaims("):]
    block = block[:block.index("function renderEscalation(")]
    read = set(re.findall(r"\bi\.([a-z_]+)", block))

    assert read <= returned, f"renderClaims reads {sorted(read - returned)}"


def test_the_forecast_card_exists_and_is_wired():
    page = PAGE.read_text()
    js = script()

    assert 'id="forecast-body"' in page
    assert "/api/forecast/fleet" in js
    assert "renderForecast" in js


def test_a_plan_without_forecasting_is_told_the_price_not_an_error(
    api, tenant_factory, owner_headers, sensor_factory
):
    """A plan that does not include something is not a failure. Saying
    "Could not be loaded" about it reads as a bug in the product rather
    than a line on the price list."""
    headers, _ = tenant_factory(plan="trial", company_name="Trying It")
    owner = owner_headers(headers)
    sensor_factory(headers, "T-1", "restaurant")

    refused = api.get("/api/forecast/fleet", headers={**headers, **owner})

    assert refused.status_code == 403
    assert "does not include" in refused.json()["detail"]
    # And the page distinguishes it rather than calling it a failure.
    js = script()
    block = js[js.index("function panelUnavailable("):]
    block = block[:block.index("async function refresh(")]
    assert "403" in block
    assert "Not on this plan" in block


def test_the_packet_button_is_handled():
    """A button whose action nothing handles does nothing, silently."""
    js = script()

    assert 'data-packet="' in js
    assert "btn.dataset.packet" in js
    assert "openPacket(btn.dataset.packet)" in js
