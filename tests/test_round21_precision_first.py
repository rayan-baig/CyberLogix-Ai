"""Round 21 (3.44.0): Pilot answers from the data only when the question is
plainly about it; the alert card and /today keep "Add what was thrown
away" after Close anyway; It's fixed stays on /today; a unit said beside
the reading is read; a set-up sensor is never back under Devices
knocking; Recent activity looks like the rest of the console; a
rotation's turns in its own clock; a shorter search box.

Pilot's answers (round 21's table of 262 new phrasings, its unseen batch
of 80 and the reviewer's named wrong answers) are in
tests/js/kryozyn-pilot.test.js.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import byod
import claims
import proofreports
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
THEME = (STATIC / "theme.css").read_text()
WORDS_JS = (STATIC / "words.js").read_text()
PILOT = (STATIC / "pilot-kryozyn.js").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _owner(api, email, password):
    resp = api.post("/api/accounts/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def cafe(api, tenant_factory, sensor_factory):
    """A café with one walk-in fridge that alerts on its first reading out
    of range, and its owner signed in."""
    headers, tenant = tenant_factory(plan="growth", company_name="Rosa's Corner Cafe")
    sensor_factory(headers, sensor_id="WALK-IN", vertical="restaurant",
                   location="Kitchen / Walk-in fridge", kind="fridge")
    STORE.add_contact(tenant["tenant_id"], "Mia Lopez", "+15550111")
    boot = api.post("/api/accounts/bootstrap", headers=headers, json={
        "email": "rosa@cafe.example", "full_name": "Rosa Test", "password": "correct-horse-battery"})
    assert boot.status_code in (200, 201), boot.text
    return headers, _owner(api, "rosa@cafe.example", "correct-horse-battery"), STORE.get_tenant(tenant["tenant_id"])


def _closed_anyway_then_thrown(api, headers, rosa, note="Called for a repair"):
    for temp in (37.0, 46.4):
        resp = api.post("/api/sensor-pulse", headers=headers,
                        json={"sensor_id": "WALK-IN", "temperature_fahrenheit": temp})
        assert resp.status_code == 200, resp.text
    incident_id = resp.json()["incident_id"]
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa,
                      json={"close_anyway": True, "note": note})
    assert closed.status_code == 200, closed.text
    # "Close anyway" copies its reason into the corrective action.
    assert STORE.get_incident(incident_id).corrective_action == note
    thrown = api.post(f"/api/voice/food/{incident_id}", headers=rosa, json={"food": "thrown"})
    assert thrown.status_code == 200, thrown.text
    return STORE.get_incident(incident_id)


# --- 1. after Close anyway and Thrown away: the note of what went ----------------


def test_the_close_reason_is_never_the_note_of_what_went(api, cafe):
    headers, rosa, tenant = cafe
    incident = _closed_anyway_then_thrown(api, headers, rosa)
    assert claims.close_reason(incident) == "Called for a repair"
    assert claims.went_note(incident) is None
    assert claims.food_record(incident)["note"] is None
    overview = api.get("/api/console/overview", headers=rosa).json()
    row = next(i for i in overview["incidents"] if i["incident_id"] == incident.incident_id)
    # The card has what it needs: the close note beside the corrective action.
    assert row["close_note"] == "Called for a repair"
    # /today's month answers and loss headline say the same.
    month = overview.get("this_month") or {}
    for answer in month.get("food_answers", []):
        if answer["incident_id"] == incident.incident_id:
            assert answer["note"] is None and answer["has_note"] is False
            assert answer["close_note"] == "Called for a repair"
    loss = overview.get("recent_loss")
    if loss and loss["incident_id"] == incident.incident_id:
        assert loss["note"] is None and loss["close_note"] == "Called for a repair"
    # The note added with "Add what was thrown away" is what went.
    resp = api.post(f"/api/voice/corrective-action/{incident.incident_id}", headers=rosa,
                    json={"action": "Two trays of chicken", "product_disposition": "discarded"})
    assert resp.status_code == 200, resp.text
    assert claims.went_note(STORE.get_incident(incident.incident_id)) == "Two trays of chicken"


def test_the_alert_card_quotes_what_went_only_when_it_is_not_the_close_reason():
    card = _function(CONSOLE_JS, "foodAnswered")
    assert 'const closeNote = i.resolved_by || i.resolved_by_name ? String(i.close_note || "").trim() : "";' in card
    assert 'const went = action && action !== closeNote ? action : "";' in card
    assert "went ? `<span class=\"sub food-note\">“${esc(went)}”</span>` : \"\"" in card
    assert "Why it was closed${i.closed_out_of_range ? \" while still out of range\" : \"\"}" in card
    assert 'went ? "" : `<button class="btn btn-sm" type="button" data-loss-note="${id}">Add what was thrown away</button>`' in card
    assert "i.corrective_action ?" not in card
    # The pack's "No note of what was thrown away yet" only when food was.
    assert "${food.note || !food.thrown ? \"\" : `<div class=\"card-note u-mt-8\">No note of what was thrown away yet." in CONSOLE_JS
    # /today: the reason on its own line too.
    assert "Why it was closed${a.closed_out_of_range ? \" while still out of range\" : \"\"}" in SIMPLE_JS


# --- 2. It's fixed stays on /today while the alert is open ------------------------


def test_its_fixed_stays_on_today_after_its_dialog_was_dismissed():
    buttons = _function(SIMPLE_JS, "needButtons")
    assert "if (fixSaid(i)) {" in buttons
    assert 'data-fixed="${esc(i.incident_id)}">It\'s fixed</button>' in buttons
    assert 'if (fixSaid(i)) return "";' not in buttons
    assert "press It's fixed again: Close anyway is there." in _function(SIMPLE_JS, "fixWaitingShort")


# --- 3. a unit said beside the reading ------------------------------------------


@pytest.mark.parametrize("payload, unit", [
    ({"serial": "A1", "temperature": 37.8, "unit": "F"}, "temperature_f"),
    ({"serial": "A1", "temperature": 3.2, "unit": "C"}, "temperature_c"),
    ({"serial": "A1", "temperature": 3.2, "units": "°C"}, "temperature_c"),
    ({"serial": "A1", "temperature": 37.8, "scale": "fahrenheit"}, "temperature_f"),
    ({"serial": "A1", "temperature": 3.2, "temp_unit": "Celsius"}, "temperature_c"),
    ({"serial": "A1", "temperature": 37.8, "temperature_unit": "°F"}, "temperature_f"),
    ({"device": {"serial": "A1"}, "data": {"temperature": 37.8, "unit": "f"}}, "temperature_f"),
])
def test_a_unit_in_its_own_field_beside_the_reading_is_read(payload, unit):
    verdict = byod.detect(payload)
    assert verdict["understood"], verdict
    assert verdict["unit"] == unit
    assert verdict["note"] == ""


@pytest.mark.parametrize("payload", [
    {"serial": "A1", "temperature": 37.8},
    {"serial": "A1", "temperature": 37.8, "unit": "kelvin"},
    {"serial": "A1", "temperature": 37.8, "unit": "%"},
    {"serial": "A1", "temperature": 37.8, "unit": 7},
])
def test_a_unit_still_never_guessed(payload):
    assert byod.detect(payload)["unit"] is None


# --- 4. a set-up sensor is never back under Devices knocking --------------------


@pytest.fixture()
def estate(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Harbor")
    owner = owner_headers(headers)
    made = api.post("/api/licenses/me/sensors", headers={**headers, **owner},
                    json={"sensor_id": "FRZ-1", "industry_vertical": "restaurant",
                          "location_name": "Walk-in", "external_device_sn": "556677"})
    assert made.status_code == 201, made.text
    return {"X-Kryozyn-Key": STORE.get_tenant(tenant["tenant_id"]).api_key}, {**headers, **owner}, tenant["tenant_id"]


def test_a_set_up_sensors_unit_less_reading_is_held_on_its_card_not_knocking(api, estate):
    import doorstep

    key, owner, tenant_id = estate
    sent = api.post("/api/v1/bridge/any", json={"serial": "556677", "temperature": 38.1}, headers=key)
    assert sent.status_code == 202, sent.text
    detail = sent.json()["detail"]
    assert detail["sensor_id"] == "FRZ-1"
    assert "Say the unit in your sensor's settings" in detail["next"]
    assert "Devices knocking" not in detail["next"]
    assert doorstep.waiting_for(tenant_id) == []
    overview = api.get("/api/console/overview", headers=owner).json()
    card = next(s for s in overview["sensors"] if s["sensor_id"] == "FRZ-1")
    assert card["unit_held"]["value"] == 38.1
    assert "Say the unit in your sensor's settings" in _function(CONSOLE_JS, "sensorCard")
    # Once a reading says its unit, the note goes.
    ok = api.post("/api/v1/bridge/any", json={"serial": "556677", "temperature": 38.1, "unit": "F"}, headers=key)
    assert ok.status_code == 200, ok.text
    overview = api.get("/api/console/overview", headers=owner).json()
    assert next(s for s in overview["sensors"] if s["sensor_id"] == "FRZ-1")["unit_held"] is None


def test_an_unknown_devices_unit_less_reading_still_knocks(api, estate):
    import doorstep

    key, _, tenant_id = estate
    sent = api.post("/api/v1/bridge/any", json={"serial": "NEW-9", "temperature": 38.1}, headers=key)
    assert sent.status_code == 202, sent.text
    assert "Devices knocking" in sent.json()["detail"]["next"]
    assert [r["serial"] for r in doorstep.waiting_for(tenant_id)] == ["NEW-9"]


# --- 5. Recent activity looks like the console ------------------------------------


def test_recent_activity_is_plain_rows_with_the_panels_own_name():
    assert 'title="Show what has happened on this page recently">Recent activity</button>' in CONSOLE_HTML
    assert ">Activity log</button>" not in CONSOLE_HTML
    assert 'id="biz-log">Recent activity' in CONSOLE_JS
    assert '"Hide recent activity" : "Recent activity"' in CONSOLE_JS
    log = _function(CONSOLE_JS, "logToConsole")
    assert "&gt;" not in log and 'line.className = "term-row";' in log
    assert "hour12: false" not in log
    terminal = THEME[THEME.index("#terminal {"):THEME.index("#toasts {")]
    assert "var(--mono)" not in terminal and "text-transform: uppercase" not in terminal
    # Pilot keeps off the panel's title.
    assert '".term-head",' in PILOT


# --- 6. a rotation's turns in its own clock ----------------------------------------


def test_a_rotations_turns_are_in_its_own_zone_and_labelled():
    assert "timeZone: opts.zone" in WORDS_JS[WORDS_JS.index("function dateTime"):WORDS_JS.index("function date(")]
    when = _function(CONSOLE_JS, "rotaWhen")
    assert "CLXWords.dateTime(iso, zone ? { zone } : undefined)" in when
    assert "zonePlace(zone)" in when
    assert "until ${esc(rotaWhen(schedule[0].ends_at, r.timezone))}" in CONSOLE_JS
    assert "to ${esc(rotaWhen(s.ends_at, r.timezone))}" in CONSOLE_JS
    assert "CLXWords.dateTime(s.starts_at))" not in CONSOLE_JS


# --- 7. the search box fits a 320px phone ------------------------------------------


def test_the_search_placeholders_are_short():
    assert 'placeholder="Search…">' in CONSOLE_HTML
    assert 'placeholder="Search units or sites">' in CONSOLE_HTML
    assert "try fridge, license, invoice" not in CONSOLE_HTML


# --- the sealed record still checks -------------------------------------------------


def test_a_proof_report_with_a_closed_anyway_alert_still_checks_as_genuine(api, cafe):
    headers, rosa, tenant = cafe
    incident = _closed_anyway_then_thrown(api, headers, rosa)
    row = proofreports.make(tenant, None, incident.opened_at.date().replace(day=1))
    assert proofreports.check(row) == "genuine"
    page = api.get(f"/verify/{row['code']}")
    assert page.status_code == 200 and "genuine" in page.text.lower()


def test_the_changelog_and_version_say_3_44_0():
    assert (ROOT / "VERSION").read_text().strip() == "3.44.0"
    assert (ROOT / "CHANGELOG.md").read_text().split("## ")[1].startswith("3.44.0")
