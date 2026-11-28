"""Round 15 (3.38.0): a demo that reads as a settled business and agrees
with itself, a pack that calls a prep fridge a prep fridge, no forecast
for a fridge that has just recovered, /for led by restaurants, even tiles,
and a Simple view that says what is left to set up and that texts are off.

Pilot's new answers with no key (nobody answering, a contract, an
electrician, what's wrong, who is on call, the bill, a pause, a limit, and
"8 fridges each") are in tests/js/kryozyn-pilot.test.js. The browser
checks at 320px, 375px and desktop were run by hand against the demo
account (CHANGELOG 3.38.0).
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
import console_api
import demo_account
import firstday
import forecaster
import legal
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
SIMPLE_HTML = (STATIC / "simple.html").read_text()
DEMO_JS = (STATIC / "demo.js").read_text()
THEME = (STATIC / "theme.css").read_text()
INDUSTRY_CSS = (STATIC / "industry.css").read_text()
PILOT_JS = (STATIC / "pilot-kryozyn.js").read_text()
PASSWORD = "demo-password-for-tests"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start) + 2]


def _sign_in_demo(api):
    resp = api.post("/api/accounts/login", json={"email": demo_account.OWNER[1], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def demo():
    firstday.forget()
    return demo_account.seed(password=PASSWORD)


def _open_alert(tenant_id):
    return next(i for i in STORE.incidents_for(tenant_id) if i.sensor_id == demo_account.STILL_OPEN)


# --- 2. the demo agrees with itself --------------------------------------------------


def test_the_demo_numbers_are_full_length_made_up_ones():
    for _, phone in demo_account.CONTACTS:
        # +1 555 010 01xx: ten digits after the +1, in the 555-01xx range,
        # spaced as a trial's are since 3.39.0.
        assert re.fullmatch(r"\+1 555 010 01\d\d", phone), phone


def test_the_open_alert_was_texted_to_the_person_on_call_as_the_handled_one_was(demo, api):
    tid = demo["tenant_id"]
    still = _open_alert(tid)
    tenant = STORE.get_tenant(tid)
    on_call = [c.full_name for c in STORE.sms_recipients(tenant)]
    assert [r["contact_name"] for r in still.sms_fanout] == on_call and on_call
    assert all(r["delivered"] and re.fullmatch(r"\+1 555 010 \d{4}", r["to"]) for r in still.sms_fanout)
    # The contacts existed before the alert opened.
    assert STORE.contacts_for(tid)
    owner = _sign_in_demo(api)
    pack = api.post(f"/api/claims/{still.incident_id}/packet", headers=owner).json()
    told = claims.told_words(pack)
    assert "nobody was on the list" not in told
    assert told.startswith("The alert was texted to 1 person at "), told
    assert "nobody was on the list" not in pack["cover_letter"]
    assert pack["insured"]["contact_phone"] == demo_account.CONTACTS[0][1]
    # Who was told: the texts and the person texted agree.
    assert [d["recipient"] for d in pack["response"]["sms_deliveries"]] == on_call


def test_the_demo_has_done_its_first_day_and_accepted_the_terms(demo, api):
    owner = _sign_in_demo(api)
    overview = api.get("/api/console/overview", headers=owner).json()
    assert overview["first_day"]["complete"] is True, overview["first_day"]
    status = api.get("/api/legal/acceptance/status", headers=owner).json()
    assert status["all_current"] is True, status
    # Accepted the week it signed, and the test alert on day one: neither
    # stops the owner sending a fresh test now.
    tid = demo["tenant_id"]
    assert not firstday.test_alerts_since(tid, utc_now() - timedelta(days=1))
    accepted = [e for e in STORE.audit_for(tid) if e.action == "legal.accepted"]
    assert len(accepted) == 1 and utc_now() - accepted[0].at > timedelta(days=demo_account.DAYS)
    assert f"v{legal.TERMS_VERSION}" in accepted[0].detail


# --- 3. a prep fridge is not a walk-in --------------------------------------------------


def test_a_name_that_says_what_it_is_gets_no_kind_after_it():
    for name in ("Prep fridge, bakery", "Commissary / Prep fridge, bakery", "Ice-cream freezer", "Dairy cooler",
                 "Blast chiller", "Dessert display"):
        assert claims.asset_label(name, "walk-in") == name
    assert claims.asset_label("Unit 4", "fridge") == "Unit 4 (fridge)"
    assert claims.asset_label("Unit 4", "walk-in") == "Unit 4 (walk-in)"


def test_the_pack_uses_the_units_own_kind(demo, api):
    owner = _sign_in_demo(api)
    still = _open_alert(demo["tenant_id"])
    pack = api.post(f"/api/claims/{still.incident_id}/packet", headers=owner).json()
    assert pack["location"]["asset_label"] == "Commissary / Prep fridge, bakery"
    assert pack["location"]["asset_type"] == "fridge"
    assert "(walk-in)" not in json.dumps(pack["location"])
    sensor = STORE.get_sensor("HBR-PREP-1")
    sensor.location_name = "Unit 4"
    from store import INDUSTRY_PROFILES
    assert claims.unit_kind_noun(sensor, INDUSTRY_PROFILES["restaurant"]) == "fridge"


# --- 4. no forecast for a fridge that has just recovered ----------------------------------


def _fridge_that_warmed_and_recovered(minutes_since_back: float):
    tenant = STORE.create_tenant(company_name="Recovery Cafe", contact_name="Sam Lee",
                                 contact_phone="+15550100150", contact_email="sam@recovery.example",
                                 plan="growth", industry_vertical="restaurant")
    sensor = STORE.register_sensor(sensor_id="REC-1", tenant_id=tenant.tenant_id,
                                   industry_vertical="restaurant", location_name="Prep fridge")
    STORE.set_sensor_overrides(sensor, 41.0, 32.0)
    now = utc_now()
    back_at = now - timedelta(minutes=minutes_since_back)
    # Ten hours steady, then an hour and a half warm, then back at 37.8F.
    for n in range(20):
        STORE.record_reading(sensor, 37.5, 50.0, False, at=back_at - timedelta(minutes=120 + 30 * (20 - n)))
    for n, temp in enumerate((40.0, 43.5, 45.0, 46.5)):
        STORE.record_reading(sensor, temp, 50.0, temp > 41.0, at=back_at - timedelta(minutes=120 - 30 * n))
    STORE.record_reading(sensor, 37.8, 50.0, False, at=back_at)
    t = back_at
    while t + timedelta(minutes=30) <= now:
        t += timedelta(minutes=30)
        STORE.record_reading(sensor, 37.6, 50.0, False, at=t)
    return sensor


def test_a_fridge_just_back_in_range_is_not_forecast_to_go_warm():
    sensor = _fridge_that_warmed_and_recovered(minutes_since_back=1)
    got = forecaster.forecast_sensor(sensor.sensor_id, window_hours=12.0)
    assert got["forecast"] == "recovering"
    assert got["risk_level"] == "stable" and got["hours_until_breach"] is None
    assert "settled" in got["summary"]


def test_once_settled_only_the_readings_since_the_return_are_fitted():
    sensor = _fridge_that_warmed_and_recovered(minutes_since_back=60 * forecaster.SETTLE_HOURS + 31)
    got = forecaster.forecast_sensor(sensor.sensor_id, window_hours=12.0)
    assert got["risk_level"] not in {"critical", "high", "elevated"}, got
    # Fitted through the warm spell, the same readings said "too warm in
    # about 7.5h"; since the return they drift down, nowhere near a limit.
    assert got["trend_f_per_hour"] is not None and got["trend_f_per_hour"] <= 0.05
    assert got["hours_until_breach"] is None or got["hours_until_breach"] > 24


def test_a_fridge_warming_now_is_still_forecast():
    tenant = STORE.create_tenant(company_name="Drift Cafe", contact_name="Sam Lee",
                                 contact_phone="+15550100151", contact_email="sam@drift.example",
                                 plan="growth", industry_vertical="restaurant")
    sensor = STORE.register_sensor(sensor_id="DRIFT-1", tenant_id=tenant.tenant_id,
                                   industry_vertical="restaurant", location_name="Walk-in fridge")
    STORE.set_sensor_overrides(sensor, 41.0, 32.0)
    now = utc_now()
    for n in range(12):
        STORE.record_reading(sensor, 36.0 + 0.4 * n, 50.0, False, at=now - timedelta(minutes=30 * (12 - n)))
    got = forecaster.forecast_sensor(sensor.sensor_id, window_hours=12.0)
    assert got["forecast"] == "breach_projected" and got["hours_until_breach"] > 0


def test_the_card_says_about_8_hours_never_7_54h():
    note = _function(CONSOLE_JS, "forecastNote")
    assert "h</span>" not in note and "forecastWhen(s.hours_until_breach)" in note
    assert '"too cold" : "too warm"' in note
    when = _function(CONSOLE_JS, "forecastWhen")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = when + "\nconsole.log(JSON.stringify([forecastWhen(7.54), forecastWhen(0.4), forecastWhen(1.2), forecastWhen(20)]));"
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out[:3] == ["in about 8 hours", "in under an hour", "in about 1 hour"]
    assert re.fullmatch(r"by about \d{1,2}:\d\d\s?[AP]M", out[3]), out[3]


# --- 5. layout and naming -------------------------------------------------------------------


def test_for_lists_restaurants_then_food_service_then_pharmacies(api):
    rows = api.get("/api/for").json()["industries"]
    assert [r["vertical"] for r in rows[:3]] == ["restaurant", "country_club", "pharmacy"]
    assert rows[0]["name"] == "Restaurants & Cafés"
    assert len(rows) == 12
    assert "minmax(min(280px, 100%), 1fr)" in INDUSTRY_CSS


def test_the_console_tiles_come_out_in_even_rows_with_grouped_numbers():
    assert 'tile("Readings logged", Number(s.readings_logged || 0).toLocaleString("en-US"),' in CONSOLE_JS
    balance = _function(CONSOLE_JS, "balanceTiles")
    assert "Math.ceil(n / Math.ceil(n / fits))" in balance
    assert 'box.style.setProperty("--per-row", String(perRow));' in balance
    assert "new ResizeObserver(() => balanceTiles()).observe($(\"tiles\"));" in CONSOLE_JS
    assert "#tiles > .tile { flex-basis: max(168px, calc((100% - (var(--per-row, 12) - 1) * 14px) / var(--per-row, 12) - 1px)); }" in THEME
    # "6,723" still counts up, and lands with its comma.
    assert "/^[0-9.,]+%?$/" in _function(CONSOLE_JS, "tile")
    assert 'toLocaleString("en-US")' in _function(CONSOLE_JS, "animateFigures")


def test_todays_loss_headline_names_the_unit_as_the_rest_of_the_page(demo, api):
    tid = demo["tenant_id"]
    handled = next(i for i in STORE.incidents_for(tid) if i.sensor_id == demo_account.HANDLED)
    handled.product_disposition = "discarded"
    handled.food_answered_at = utc_now() - timedelta(hours=1)
    STORE._save_incident(handled)
    loss = console_api.loss_headline(STORE.get_tenant(tid), utc_now())
    assert loss["sensor_id"] == demo_account.HANDLED
    assert loss["unit"] == "Old Mill / Walk-in freezer"
    verdict = _function(SIMPLE_JS, "verdict")
    assert "const lost = whichOne(byId[loss.sensor_id]" in verdict
    assert "Food thrown away from ${esc(lost)}: make the insurance pack" in verdict


# --- 6. the Simple view, the robot, the decimals, the duplicate heading ------------------------


def test_a_new_trial_hears_what_is_left_and_that_texts_are_off():
    verdict = _function(SIMPLE_JS, "verdict")
    assert "One thing left to set up" in verdict and "Open Getting started" in verdict
    assert '" There is nothing for you to do."' in verdict
    left = _function(SIMPLE_JS, "setupLeft")
    assert "!step.done" in left and "day.complete" in left
    assert 'id="texts-off" hidden' in SIMPLE_HTML
    assert 'textsOff = (await got.json()).message_delivery !== "twilio";' in _function(SIMPLE_JS, "loadHealth")
    assert "setupLeft(data)) === true;" in SIMPLE_JS


def test_a_trial_overview_lists_the_test_alert_as_left(api):
    made = api.post("/api/signup", json={
        "company_name": "Corner Bistro", "full_name": "Sam Reyes", "email": "sam@bistro15.example",
        "password": "correct-horse-battery", "contact_phone": "+15550100160", "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    owner = {"Authorization": f"Bearer {made.json()['token']}"}
    added = api.post("/api/licenses/me/sensors", headers=owner, json={
        "location_name": "Walk-in cooler", "sensor_id": "BISTRO-1", "industry_vertical": "restaurant"})
    api.post("/api/contacts", headers=owner, json={"full_name": "Sam Reyes", "phone": "+15550100160"}).raise_for_status()
    api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": added.json()["ingest_key"]},
             json={"sensor_id": "BISTRO-1", "temperature_fahrenheit": 37.0}).raise_for_status()
    steps = api.get("/api/console/overview", headers=owner).json()["first_day"]["steps"]
    assert [s["label"] for s in steps if not s["done"]] == ["Send a test alert"]
    assert api.get("/api/health").json()["message_delivery"] == "dry_run"


def test_the_robot_keeps_out_of_the_consoles_header():
    avoid = PILOT_JS[PILOT_JS.index("avoid: ["):PILOT_JS.index("],", PILOT_JS.index("avoid: ["))]
    assert '"#app > .nav"' in avoid
    for sel in (".top", ".demo-top"):
        assert f'"{sel}"' not in avoid


def test_temperatures_side_by_side_all_have_one_decimal():
    assert "esc(degreesOf(unit.last_temperature_display))" in SIMPLE_JS
    degrees = _function(SIMPLE_JS, "degreesOf")
    assert "toFixed(1)" in degrees
    assert "esc(cardReading(now))" in DEMO_JS
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    out = json.loads(subprocess.run([node, "-e", degrees + "\nconsole.log(JSON.stringify([degreesOf(35), degreesOf(36.4), degreesOf(-0.04), degreesOf(null)]))"],
                                    capture_output=True, text=True, check=True).stdout)
    assert out == ["35.0°", "36.4°", "0.0°", "—"]


def test_the_alert_card_drops_the_heading_the_hero_already_says():
    needs = _function(SIMPLE_JS, "renderNeeds")
    # 3.39.0: in every state the banner shows, not only while nobody has answered.
    assert 'heading.hidden = !(data.account || {}).paused && live.length > 0;' in needs
    assert "An alert needs" in _function(SIMPLE_JS, "verdict")
