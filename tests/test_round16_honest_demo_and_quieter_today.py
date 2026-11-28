"""Round 16 (3.39.0): a forecast that leaves failed units to the alerts,
a demo whose numbers agree (the alerts open at their first reading out,
the money is the food kept, the texts are pretend), a /today that says
each thing once and lists the warm fridge first, sectors in a
restaurant's order, a Money tab that fits a phone, readings with their
tenth, and a hint for a key sent in the wrong header.

Pilot's new answers with no key (another fridge, trying without a
sensor, an app, this week's alerts, the warmest, the bar fridge, a
butcher, a count at each location, the lead line once, on-call times, no
trial line for somebody signed in) and the robot keeping off the words in
its corners are in tests/js/kryozyn-pilot.test.js; /today's order and
heading in tests/js/today.test.js. The browser checks at 320px, 375px and
desktop were run by hand against the demo account (CHANGELOG 3.39.0).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

import claims
import demo_account
import disposition
import firstday
import pricing
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
THEME = (STATIC / "theme.css").read_text()
DEMO_JS = (STATIC / "demo.js").read_text()
INDUSTRY_JS = (STATIC / "industry.js").read_text()
PASSWORD = "demo-password-for-tests"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start) + 2]


@pytest.fixture()
def demo():
    firstday.forget()
    return demo_account.seed(password=PASSWORD)


def _owner(api):
    resp = api.post("/api/accounts/login", json={"email": demo_account.OWNER[1], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _alert(tenant_id, sensor_id):
    return next(i for i in STORE.incidents_for(tenant_id) if i.sensor_id == sensor_id)


# --- 1. the forecast leaves a failed unit to the alerts -------------------------------


def test_a_unit_out_of_range_now_is_not_heading_for_trouble(demo, api):
    owner = _owner(api)
    fleet = api.get("/api/forecast/fleet", headers=owner).json()
    assert demo_account.STILL_OPEN not in [f["sensor_id"] for f in fleet["at_risk"]]
    assert all(not f.get("already_breached") for f in fleet["at_risk"])
    # Still forecast, and still critical: only left out of "at risk".
    still = next(f for f in fleet["forecasts"] if f["sensor_id"] == demo_account.STILL_OPEN)
    assert still["already_breached"] is True
    overview = api.get("/api/console/overview", headers=owner).json()
    assert overview["summary"]["at_risk"] == 0
    row = next(s for s in overview["sensors"] if s["sensor_id"] == demo_account.STILL_OPEN)
    assert row["breaching"] and row["already_breached"]


def test_a_drifting_unit_still_in_range_is_still_at_risk(api, tenant_factory, sensor_factory):
    from store import utc_now

    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="DRIFT-1", vertical="restaurant")
    sensor = STORE.get_sensor("DRIFT-1")
    for n, temp in enumerate((36.0, 37.0, 38.0, 39.0)):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=temp, humidity_percent=50.0, breached=False,
                             at=utc_now() - timedelta(minutes=10 * (3 - n)))
    fleet = api.get("/api/forecast/fleet", headers=headers).json()
    assert [f["sensor_id"] for f in fleet["at_risk"]] == ["DRIFT-1"]
    assert api.get("/api/console/overview", headers=headers).json()["summary"]["at_risk"] == 1


def test_the_forecast_card_rounds_the_drift_to_one_decimal():
    body = _function(CONSOLE_JS, "renderForecast")
    assert "Math.round((state.unit === \"C\" ? f.trend_f_per_hour * 5 / 9 : f.trend_f_per_hour) * 10) / 10" in body
    assert "perHour.toFixed(1)" in body


# --- 2. the demo's numbers agree ------------------------------------------------------


def test_the_open_alert_opens_at_its_reading_then_and_46_5_is_its_worst(demo):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    readings = STORE.readings_for(demo_account.STILL_OPEN)
    at_open = next(r for r in readings if r.recorded_at == still.opened_at)
    assert at_open.temperature_fahrenheit == still.temperature_fahrenheit == still.first_out_f == 43.5
    assert still.breach_details == "Too warm: 43.5°F (limit 41°F)."
    assert "43.5°F, 2.5°F above" in still.sms_text
    assert still.peak_f == 46.5 == readings[-1].temperature_fahrenheit
    assert still.out_since == still.opened_at


def test_the_handled_alert_opens_at_its_first_reading_out_of_range(demo):
    handled = _alert(demo["tenant_id"], demo_account.HANDLED)
    # Three days back is past what memory holds at a reading every 5
    # minutes (3.41.0): from the record.
    readings = STORE.readings_for(demo_account.HANDLED, complete=True)
    first_out = next(r for r in readings if r.breached)
    assert first_out.recorded_at == handled.opened_at == handled.out_since
    assert handled.temperature_fahrenheit == first_out.temperature_fahrenheit == 12.5
    assert handled.breach_details == "Too warm: 12.5°F (limit 10°F)."
    assert handled.peak_f == 21.0
    # Closed after its last reading out of range, never before it.
    last_out = [r for r in readings if r.breached][-1]
    assert handled.resolved_at > last_out.recorded_at
    assert handled.acknowledged_at - handled.opened_at == timedelta(minutes=7)


def test_the_money_tab_counts_the_food_kept_as_today_does(demo, api):
    owner = _owner(api)
    overview = api.get("/api/console/overview", headers=owner).json()
    roi = overview["roi"]
    assert roi["loss_avoided_usd"] == overview["this_month"]["food_usd"] == 4000
    assert roi["quantified_saves"] == 1
    assert roi["return_multiple"] == round(4000 / roi["subscription_cost_usd"], 1)
    whys = {row["sensor_id"]: row["why"] for row in roi["detail"]}
    assert whys == {demo_account.HANDLED: "closed, and the food was recorded as kept",
                    demo_account.STILL_OPEN: "still open"}
    saved = _function(CONSOLE_JS, "renderBilling")
    assert "Food kept · 30 days" in saved and "real failure" not in saved


def test_roi_never_counts_food_thrown_away_or_an_alert_nobody_answered_for(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="WALKIN-1", vertical="restaurant", location="Walk-in fridge")
    from store import utc_now

    sensor = STORE.get_sensor("WALKIN-1")
    now = utc_now()
    for fate in ("discarded", ""):
        incident = STORE.open_incident(tenant_id=sensor.tenant_id, sensor=sensor, temperature_fahrenheit=48.0,
                                       breach_details="Too warm", sms_text="", sms_dispatch_source="fallback_template",
                                       opened_at=now - timedelta(hours=2))
        incident.out_since = incident.opened_at
        if fate:
            STORE.record_corrective_action(incident, "Jo <jo@example.com>", "Binned.", disposition=fate)
        STORE.resolve_incident(incident, "Jo <jo@example.com>")
    roi = api.get("/api/billing/roi", headers=headers).json()
    assert roi["loss_avoided_usd"] == 0 and roi["quantified_saves"] == 0
    assert sorted(row["why"] for row in roi["detail"]) == ["nobody has said the food was kept", "the food was thrown away"]


def test_the_demo_texts_are_pretend_and_say_so(demo, api):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    handled = _alert(demo["tenant_id"], demo_account.HANDLED)
    assert all(r["demo"] for r in still.sms_fanout + handled.sms_fanout)
    owner = _owner(api)
    pack = api.post(f"/api/claims/{still.incident_id}/packet", headers=owner).json()
    assert {d["outcome_words"] for d in pack["response"]["sms_deliveries"]} == {"pretend text, demo"}
    timeline = _function(CONSOLE_JS, "incidentTimeline")
    assert "(i.sms_fanout || []).every((f) => f.demo)" in timeline
    assert "pretend text to ${texted}" in timeline
    # A real receipt is still "delivered".
    assert claims._delivery_rows([{"channel": "sms", "delivered": True, "status": "delivered"}], None)[0]["outcome_words"] == "delivered"


def test_what_kryozyn_did_calls_the_demo_texts_pretend(demo, api):
    did = api.get("/api/console/did-for-you?period=week", headers=_owner(api)).json()
    assert did["figures"]["texts"] == 0 and did["figures"]["pretend_texts"] == 4
    assert "4 pretend alert texts (demo): nothing is really sent." in did["lines"]
    assert not any("delivered" in line for line in did["lines"])


def test_the_demo_numbers_are_spaced_as_a_trial_types_them(demo, api):
    for _, phone in demo_account.CONTACTS:
        assert re.fullmatch(r"\+1 555 010 01\d\d", phone), phone
    owner = _owner(api)
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    pack = api.post(f"/api/claims/{still.incident_id}/packet", headers=owner).json()
    assert pack["insured"]["contact_phone"] == "+1 555 010 0141"
    assert [d["to"] for d in pack["response"]["sms_deliveries"]] == ["+1 555 010 0141"]


# --- 3. /today says it once -------------------------------------------------------------


def test_today_never_repeats_the_banner_and_the_pill_says_back_in_range():
    needs = _function(SIMPLE_JS, "renderNeeds")
    assert "heading.hidden = !(data.account || {}).paused && live.length > 0;" in needs
    state = _function(SIMPLE_JS, "stateOf")
    assert 'word: c.back ? "Back in range" : c.word' in state
    assert "Back in range: press It's fixed\" :" not in state


def test_the_front_page_picture_has_no_repeated_heading():
    from PIL import Image

    picture = Image.open(STATIC / "hero-today.webp")
    assert picture.size == (480, 878)
    assert (STATIC / "hero-today.webp").stat().st_size < 60_000


# --- 4. a restaurant's order, a Money tab that fits ---------------------------------------


def test_meeting_notes_live_under_advanced_only():
    card = CONSOLE_HTML[CONSOLE_HTML.index('id="find-meeting-intelligence"') - 40:]
    card = card[:card.index(">")]
    assert 'data-advanced-only="1"' in card
    in_tab = _function(CONSOLE_JS, "inTab")
    assert 'card.getAttribute("data-advanced-only") === "1" && !searching' in in_tab
    assert "find-meeting-intelligence" in CONSOLE_JS[CONSOLE_JS.index("const ADV_SECTIONS"):CONSOLE_JS.index("const ADV_OF")]


def test_sector_lists_go_restaurant_food_service_pharmacy_then_a_to_z(api):
    rates = [r["vertical"] for r in pricing.calculator_rates()["rates"]]
    assert rates[:3] == ["restaurant", "country_club", "pharmacy"]
    names = [pricing.INDUSTRY_PROFILES[v]["name"] for v in rates[3:]]
    assert names == sorted(names)
    assert len(rates) == len(pricing.PRICE_BOOK), "every sector is still offered"
    page = api.get("/pricing").text
    select = page[page.index('<select class="calc-sector">'):]
    select = select[:select.index("</select>")]
    order = re.findall(r'<option value="([a-z_]+)"', select)
    assert order[:3] == ["restaurant", "country_club", "pharmacy"]
    assert select.index('value="pharmacy"') < select.index("<optgroup")
    assert sorted(order) == sorted(rates)
    assert 'const ADD_FIRST = ["restaurant", "country_club", "pharmacy"];' in CONSOLE_JS
    assert "$(\"tx-vertical\").innerHTML = plain;" in CONSOLE_JS and "const plain = ordered" in CONSOLE_JS


def test_for_puts_the_food_trades_ahead_of_the_niche_ones(api):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    body = api.get("/api/for").json()
    assert [r["vertical"] for r in body["industries"][:3]] == ["restaurant", "country_club", "pharmacy"]
    script = _function(INDUSTRY_JS, "indexOrder") + f"""
    const got = indexOrder({json.dumps(body["industries"])}, {json.dumps(body["trades"])});
    console.log(JSON.stringify(got));"""
    got = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    first = [c["href"] for c in got["first"]]
    assert first == ["/for/restaurant", "/for/country_club", "/for/butchers", "/for/grocery-stores",
                     "/for/ice-cream-shops", "/for/school-cafeterias", "/for/pharmacy", "/for/vaccine-clinics"]
    rest = [c["name"] for c in got["rest"]]
    assert rest == sorted(rest)
    assert len(first) + len(rest) == len(body["industries"]) + len(body["trades"]), "every page is still listed"


def test_the_pause_buttons_wrap_on_a_narrow_phone():
    assert ".pause-options .btn { flex-wrap: wrap; white-space: normal; max-width: 100%;" in THEME


def test_the_escalator_is_not_listed_with_the_add_ons(demo, api):
    owner = _owner(api)
    pipeline = api.get("/api/contracts/pipeline", headers=owner).json()
    # The rule itself is untouched: still signed, still in the pipeline.
    assert any(o["kind"] == "escalator" for o in pipeline["opportunities"])
    body = _function(CONSOLE_JS, "opportunities")
    assert '.filter((o) => o.kind !== "escalator")' in body
    assert "pipeline.identified_annual_usd" not in body


# --- 6. decimals, the robot, and the key in the wrong header ---------------------------------


def test_the_demo_log_and_the_pack_humidity_keep_their_tenth():
    assert "`(${cardReading(now)}°F). We wait" in DEMO_JS
    assert "has stayed too warm: ${cardReading(now)}°F" in DEMO_JS
    humid = _function(CONSOLE_JS, "humidityWords")
    assert "toFixed(1)" in humid
    assert CONSOLE_JS.count("esc(humidityWords(r.humidity))") == 2
    assert 'esc(r.humidity) + "%"' not in CONSOLE_JS


@pytest.mark.parametrize("hours,words", [(0.5, "30 minutes"), (1.1, "1 hour 6 minutes"), (1.0, "1 hour"),
                                         (2.25, "2 hours 15 minutes"), (1.99, "1 hour 59 minutes")])
def test_the_food_card_says_hours_and_minutes_never_1_1_hours(hours, words):
    assert disposition._span(hours) == words


def test_a_key_in_the_wrong_header_names_the_right_one(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    resp = api.post("/api/licenses/me/sensors", headers=headers,
                    json={"sensor_id": "KEY-1", "industry_vertical": "restaurant", "location_name": "Walk-in fridge"})
    assert resp.status_code == 201, resp.text
    sensor_key = resp.json()["ingest_key"]
    pulse = {"sensor_id": "KEY-1", "temperature_fahrenheit": 37.0}
    wrong = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Key": sensor_key}, json=pulse)
    assert wrong.status_code == 401
    assert "X-Kryozyn-Sensor-Key" in wrong.json()["detail"] and "sensor key" in wrong.json()["detail"]
    bearer = api.post("/api/sensor-pulse", headers={"Authorization": f"Bearer {sensor_key}"}, json=pulse)
    assert bearer.status_code == 401 and "X-Kryozyn-Sensor-Key" in bearer.json()["detail"]
    master = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": headers["X-Kryozyn-Key"]}, json=pulse)
    assert master.status_code == 401 and "X-Kryozyn-Key header" in master.json()["detail"]
    unknown = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Key": "not-a-key"}, json=pulse)
    assert unknown.status_code == 401
    assert unknown.json()["detail"].startswith("Unrecognised API key.") and "X-Kryozyn-Sensor-Key" in unknown.json()["detail"]
    right = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": sensor_key}, json=pulse)
    assert right.status_code == 200, right.text


def test_the_app_installs_from_the_browser_as_pilot_says(api):
    manifest = api.get("/manifest.webmanifest").json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/console"
    assert api.get("/sw.js").status_code == 200
    assert 'navigator.serviceWorker.register("/sw.js"' in CONSOLE_JS
