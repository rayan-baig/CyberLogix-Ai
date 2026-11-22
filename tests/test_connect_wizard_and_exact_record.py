"""3.25.0: "How to connect it" as a wizard, and an exact record of an alert.

The first-day checklist's "How to connect it" was a link to the long
/help/sensors page. It opens a short wizard now: pick the sensor's brand,
see only its steps with the address, key and serial filled in, and a live
"Waiting... Received!" tick when the first reading lands. A unit added
without its device's serial can be given one from there
(POST /api/licenses/me/sensors/{id}/serial).

An alert's timeline said "Went out of range: 60.5°F, 1:16 AM" when the
reading at 1:16 was 47.2°F and 60.5 was the peak: the incident kept only
its latest reading. The first reading out of range and the worst one are
kept apart now. On a server with texts switched off, an alert said
"texted 1 person · 0 delivered / ALERT TEXTS FAILED"; it says "Not sent:
texts are off on this server".

And a florist or a shop ("Other / not listed", "Shop") is told its fridges
get "Food fridge and freezer limits", not "For restaurants & cafés".
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

import sensor_guides
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_JS = (ROOT / "static" / "console.js").read_text()
CONSOLE_HTML = (ROOT / "static" / "console.html").read_text()


# --- the wizard's words ------------------------------------------------------

def test_the_wizard_route_gives_each_brand_with_this_address(api):
    body = api.get("/api/sensor-guides/wizard").json()
    ids = [b["id"] for b in body["brands"]]
    assert ids == ["monnit", "logger", "sensorpush", "gateway", "cloud", "other"]
    for brand in body["brands"]:
        assert brand["label"] and brand["hint"] and 2 <= len(brand["steps"]) <= 4
        assert "{base}" not in brand["address"] and "{base}" not in " ".join(brand["steps"])
        if brand["address"]:
            assert brand["address"].startswith("http://testserver/api/")
        assert brand["needs_key"] is bool(brand["address"])
        assert brand["needs_serial"] is bool(brand["serial"])
    monnit = body["brands"][0]
    assert monnit["address"] == "http://testserver/api/v1/bridge/any?k={key}"


@pytest.mark.parametrize("brand", sensor_guides.WIZARD, ids=lambda b: b["id"])
def test_every_route_and_header_in_a_brand_is_its_guides(brand):
    """Held to the guide it points at, which tests/test_sensor_guides.py
    holds to the code: a path or header the guide does not declare is a
    step that sends somebody somewhere that does not take it."""
    guide = next(g for g in sensor_guides.GUIDES if g["id"] == brand["guide"])
    words = " ".join(brand["steps"]) + " " + brand["address"]
    declared_paths = {path for _, path in guide["routes"]}
    for path in re.findall(r"/api/[a-z0-9/_\-{}]+", words):
        assert path.split("?")[0] in declared_paths, (brand["id"], path)
    declared_headers = {h for hs in guide["headers"].values() for h in hs}
    for header in re.findall(r"X-CyberLogix-[A-Za-z-]+", words):
        assert header in declared_headers, (brand["id"], header)
    for name in re.findall(r"\?(\w+)=", words):
        assert name in {q for qs in guide["query"].values() for q in qs}, (brand["id"], name)


def test_the_logger_steps_name_only_the_webhook_models_fields():
    from hardware_bridge import GenericWebhookPayload

    logger = next(b for b in sensor_guides.WIZARD if b["id"] == "logger")
    words = " ".join(logger["steps"])
    for field in ("device_sn", "api_key_token", "reading_value", "metric_type"):
        assert field in words and field in GenericWebhookPayload.model_fields
    assert "temperature_c" in words and "temperature_f" in words


# --- the console's side ------------------------------------------------------

def test_how_to_connect_it_opens_the_wizard_not_a_long_page():
    """It was <a href="/help/sensors">How to connect it</a>."""
    assert '<a class="btn btn-sm" href="/help/sensors">How to connect it</a>' not in CONSOLE_JS
    assert 'data-connect="1">How to connect it</button>' in CONSOLE_JS
    assert "if (btn.dataset.connect) openConnect(" in CONSOLE_JS
    assert '<dialog id="connect" class="confirm-dialog connect-dialog"' in CONSOLE_HTML
    # The key box's link opens it for that unit, with the key in it.
    assert 'data-connect-issued="1"' in CONSOLE_HTML
    assert "state.issuedKeys[sensorId] = key;" in CONSOLE_JS


def test_the_wizard_fills_in_the_key_and_serial_and_waits_for_a_reading():
    body = CONSOLE_JS[CONSOLE_JS.index("function connectStepsHtml"):
                      CONSOLE_JS.index("/* Asks only for the overview")]
    assert 'b.address.replace("{key}"' in body
    assert '.replace("{serial}"' in body and '.replace("{sensor_id}"' in body
    assert "Make a new key" in body and "/rotate-key" in CONSOLE_JS
    assert "Waiting for the first reading…" in body and "<b>Received!</b>" in body
    watch = CONSOLE_JS[CONSOLE_JS.index("function watchConnect"):]
    watch = watch[:watch.index("\n}\n")]
    assert 'api("/api/console/overview")' in watch and "3000" in watch
    assert "15 * 60 * 1000" in watch
    # The key is kept in memory for this page only, never stored.
    assert not re.search(r"(localStorage|sessionStorage)\.setItem\([^)]*issued", CONSOLE_JS)


# --- giving a unit its serial ------------------------------------------------

def _monnit_reading(serial: str) -> dict:
    return {"SensorID": serial, "DataValues": "3.4", "DataTypes": "TemperatureC",
            "MessageDate": "2026-11-03 06:00:00"}


def test_a_unit_added_without_a_serial_can_be_given_one(api, operator_factory):
    headers, _, _ = operator_factory(plan="trial")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in fridge", "unit_kind": "fridge"}).json()
    key = made["ingest_key"]
    # Before: the sensor key refuses a serial that is on no unit of its own.
    before = api.post(f"/api/v1/bridge/any?k={key}", json=_monnit_reading("554433"))
    assert before.status_code == 403

    saved = api.post("/api/licenses/me/sensors/WALKIN-1/serial", headers=headers,
                     json={"external_device_sn": " 554433 "})
    assert saved.status_code == 200, saved.text
    assert saved.json()["sensor"]["external_device_sn"] == "554433"

    after = api.post(f"/api/v1/bridge/any?k={key}", json=_monnit_reading("554433"))
    assert after.status_code == 200, after.text
    assert after.json()["bound_sensor_id"] == "WALKIN-1"
    # Kept on disk: the serial survives a reload of the store.
    row = STORE._db.get("sensor", "WALKIN-1")
    assert row["external_device_sn"] == "554433"


def test_a_serial_on_another_unit_is_refused(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory(plan="trial")
    sensor_factory(headers, sensor_id="A-1", vertical="restaurant", location="Walk-in")
    sensor_factory(headers, sensor_id="B-1", vertical="restaurant", location="Freezer")
    assert api.post("/api/licenses/me/sensors/A-1/serial", headers=headers,
                    json={"external_device_sn": "SN-9"}).status_code == 200
    taken = api.post("/api/licenses/me/sensors/B-1/serial", headers=headers,
                     json={"external_device_sn": "SN-9"})
    assert taken.status_code == 409
    # Another unit's id would capture its readings, so it is refused too.
    as_id = api.post("/api/licenses/me/sensors/B-1/serial", headers=headers,
                     json={"external_device_sn": "A-1"})
    assert as_id.status_code == 409
    # Changing it frees the old one; clearing it takes it away.
    assert api.post("/api/licenses/me/sensors/A-1/serial", headers=headers,
                    json={"external_device_sn": "SN-10"}).status_code == 200
    assert STORE.sensor_by_device("SN-9") is None
    cleared = api.post("/api/licenses/me/sensors/A-1/serial", headers=headers,
                       json={"external_device_sn": ""})
    assert cleared.json()["sensor"]["external_device_sn"] is None
    assert STORE.sensor_by_device("SN-10") is None


def test_only_this_business_and_not_a_viewer_can_set_a_serial(api, operator_factory,
                                                              sensor_factory):
    headers, _, _ = operator_factory(plan="trial")
    sensor_factory(headers, sensor_id="A-1", vertical="restaurant", location="Walk-in")
    other, _, _ = operator_factory(plan="trial", company_name="Other Cafe",
                                   email="other@example.com")
    assert api.post("/api/licenses/me/sensors/A-1/serial", headers=other,
                    json={"external_device_sn": "X"}).status_code == 404
    invited = api.post("/api/accounts/users", headers=headers, json={
        "email": "viewer@example.com", "full_name": "Val Viewer",
        "password": "viewer-password-123", "role": "viewer"})
    assert invited.status_code == 201, invited.text
    token = api.post("/api/accounts/login", json={
        "email": "viewer@example.com", "password": "viewer-password-123"}).json()["token"]
    refused = api.post("/api/licenses/me/sensors/A-1/serial",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"external_device_sn": "X"})
    assert refused.status_code == 403
    assert api.post("/api/licenses/me/sensors/A-1/serial",
                    json={"external_device_sn": "X"}).status_code == 401


# --- the exact record --------------------------------------------------------

def _pulse(api, key, temp):
    r = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": key},
                 json={"sensor_id": "WALKIN-1", "temperature_fahrenheit": temp})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def walkin(api, operator_factory):
    headers, _, _ = operator_factory(plan="trial")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in fridge", "unit_kind": "fridge"}).json()
    return headers, made["ingest_key"]


def _backdate_readings(minutes_each: float):
    """Spread the unit's readings out, oldest first, so a wait can pass."""
    readings = list(STORE.readings_for("WALKIN-1"))
    for n, reading in enumerate(readings):
        reading.recorded_at -= timedelta(minutes=minutes_each * (len(readings) - n))


def test_the_first_reading_out_and_the_worst_are_kept_apart(api, walkin):
    headers, key = walkin
    # A 20-minute wait: the first reading out (47.2) comes well before the
    # alert opens (at 55.1), and it gets worse (60.5) after that.
    _pulse(api, key, 47.2)
    _pulse(api, key, 52.0)
    _backdate_readings(15)
    opened = _pulse(api, key, 55.1)
    assert opened["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", opened
    _pulse(api, key, 60.5)
    _pulse(api, key, 51.0)

    incident = STORE.latest_unresolved_incident("WALKIN-1", STORE.get_sensor("WALKIN-1").tenant_id)
    assert incident.first_out_f == 47.2
    assert incident.peak_f == 60.5
    assert incident.temperature_fahrenheit == 51.0  # the latest, as before
    first_at = STORE.readings_for("WALKIN-1")[-5].recorded_at
    assert incident.out_since == first_at

    row = next(i for i in api.get("/api/console/overview", headers=headers).json()["incidents"])
    assert row["first_out_fahrenheit"] == 47.2 and row["first_out_display"] == 47.2
    assert row["peak_fahrenheit"] == 60.5 and row["peak_display"] == 60.5
    assert row["peak_at"]
    # Survives a restart: both are on the stored row.
    stored = STORE._db.get("incident", incident.incident_id)
    assert stored["first_out_f"] == 47.2 and stored["peak_f"] == 60.5


def test_the_worst_reading_keeps_rising_while_the_alert_is_open(api, walkin):
    _, key = walkin
    STORE.set_alert_rule(STORE.get_sensor("WALKIN-1"), 0.0, None)
    _pulse(api, key, 45.0)
    _pulse(api, key, 49.5)
    _pulse(api, key, 46.0)
    incident = STORE.incidents_for(STORE.get_sensor("WALKIN-1").tenant_id)[0]
    assert (incident.first_out_f, incident.peak_f, incident.temperature_fahrenheit) == (45.0, 49.5, 46.0)


def test_too_cold_counts_colder_as_worse(api, operator_factory):
    headers, _, _ = operator_factory(plan="trial")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "pharmacy",
        "location_name": "Vaccine fridge"}).json()
    STORE.set_alert_rule(STORE.get_sensor("WALKIN-1"), 0.0, None)
    for t in (34.0, 30.0, 33.0):
        _pulse(api, made["ingest_key"], t)
    incident = STORE.incidents_for(STORE.get_sensor("WALKIN-1").tenant_id)[0]
    assert incident.first_out_f == 34.0 and incident.peak_f == 30.0


def test_an_old_alert_without_the_two_still_loads():
    from records import Incident

    row = {"incident_id": "INC-1", "tenant_id": "T", "sensor_id": "S",
           "industry_vertical": "restaurant", "catastrophe": "x",
           "temperature_fahrenheit": 55.0, "breach_details": "", "sms_text": "",
           "sms_dispatch_source": "template", "opened_at": "2026-01-01T00:00:00Z"}
    old = Incident.from_row(row)
    assert old.first_out_f is None and old.peak_f is None
    assert old.public()["first_out_fahrenheit"] is None


def test_the_console_timeline_says_the_first_reading_and_the_worst_apart():
    body = CONSOLE_JS[CONSOLE_JS.index("function incidentTimeline"):]
    body = body[:body.index("\n}\n")]
    assert "Went out of range: ${deg}" not in body
    assert "`Went out of range: ${first}${u}`" in body
    assert '"At its worst"' in body and '"Worst so far"' in body
    assert "steps.sort(" in body


# --- texts switched off --------------------------------------------------------

def test_an_alert_on_a_server_with_texts_off_is_not_a_failure(api, walkin):
    headers, key = walkin
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Rosa Diaz", "phone": "+15550104478"})
    STORE.set_alert_rule(STORE.get_sensor("WALKIN-1"), 0.0, None)
    _pulse(api, key, 50.0)
    row = api.get("/api/console/overview", headers=headers).json()["incidents"][0]
    assert row["sms_fanout"] and all(f["status"] == "not_configured" for f in row["sms_fanout"])
    assert row["texts_off"] is True
    assert row["alert_texts_failed"] is False
    assert row["next_sms_retry_at"] is None


def test_a_real_failure_is_still_one(api, walkin):
    from records import Incident

    incident = Incident.from_row({
        "incident_id": "INC-1", "tenant_id": "T", "sensor_id": "S",
        "industry_vertical": "restaurant", "catastrophe": "x",
        "temperature_fahrenheit": 55.0, "breach_details": "", "sms_text": "",
        "sms_dispatch_source": "template", "opened_at": "2026-01-01T00:00:00Z",
        "sms_fanout": [{"delivered": False, "status": "send_failed"},
                       {"delivered": False, "status": "not_configured"}]})
    assert incident.alert_texts_failed is True and incident.texts_off is False


def test_the_console_says_not_sent_texts_are_off():
    note = CONSOLE_JS[CONSOLE_JS.index("function textsFailedNote"):]
    note = note[:note.index("\n}\n")]
    assert note.index("if (i.texts_off)") < note.index("alert_texts_failed")
    assert "Texts are off on this server" in note
    assert "Alert opened. Not sent: texts are off on this server" in CONSOLE_JS


def test_the_insurance_pack_says_the_first_reading_at_its_time(api, walkin):
    headers, key = walkin
    _pulse(api, key, 47.2)
    _pulse(api, key, 60.5)
    _backdate_readings(15)
    _pulse(api, key, 55.1)
    incident = STORE.incidents_for(STORE.get_sensor("WALKIN-1").tenant_id)[0]
    packet = api.post(f"/api/claims/{incident.incident_id}/packet", headers=headers)
    assert packet.status_code == 200, packet.text
    body = packet.json()
    assert body["event"]["detected_reading"] == 47.2
    assert body["event"]["peak_reading"] == 60.5
    detected = next(e for e in body["response"]["timeline"] if e["event"] == "Breach detected")
    assert detected["detail"].startswith("47.2°F")
    assert detected["at"].startswith(incident.started_at.isoformat()[:16])
    assert any(e["event"] == "Worst reading" and e["detail"].startswith("60.5")
               for e in body["response"]["timeline"])


# --- what a florist's limits are called ----------------------------------------

def _sign_up(api, kind, email):
    r = api.post("/api/signup", json={
        "company_name": "Petal & Stem", "full_name": "Flo Green",
        "contact_phone": "+1 555 010 9988", "email": email,
        "password": "tulips-and-roses-44", "industry_vertical": kind})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("kind,words", [
    ("other", "Food fridge and freezer limits"),
    ("shop", "Food fridge and freezer limits"),
    ("clinic", "Vaccine and medicine fridge limits"),
    ("restaurant", None),
])
def test_the_kind_picked_at_sign_up_names_its_limits(api, kind, words):
    headers = _sign_up(api, kind, f"{kind}@petal.example")
    tenant = api.get("/api/console/overview", headers=headers).json()["tenant"]
    assert tenant["business_kind"] == kind
    assert tenant["limits_words"] == words
    # Watched as the sector it shares, as before.
    assert tenant["industry_vertical"] in ("restaurant", "pharmacy")


def test_the_add_form_uses_those_words():
    body = CONSOLE_JS[CONSOLE_JS.index("function preferSector"):]
    body = body[:body.index("\n}\n")]
    assert "overview.tenant.limits_words" in body
    assert "own ? `${own}.` : `For ${trade.name.toLowerCase()}.`" in body
