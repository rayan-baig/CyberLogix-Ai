"""Round 20 (3.43.0): Pilot tells the past from the present and a change
from a status; the proof report and the insurance pack say how an alert
really closed; one story on who is alerted; plain words.

Pilot's answers themselves (round 20's table of new phrasings, its unseen
batch and the reviewer's named wrong answers) are in
tests/js/kryozyn-pilot.test.js. The browser checks at 320px, 375px and
desktop were run against the demo account (CHANGELOG 3.43.0).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import claims
import demo_account
import firstday
import main
import proofreports
from console_api import texted_by_rotation
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
PROOF_JS = (STATIC / "proof-report.js").read_text()
PILOT = (STATIC / "pilot-kryozyn.js").read_text()
PASSWORD = "demo-password-for-tests"


@pytest.fixture()
def demo():
    firstday.forget()
    return demo_account.seed(password=PASSWORD)


def _owner(api, email=demo_account.OWNER[1], password=PASSWORD):
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


def _warm_and_closed_anyway(api, headers, rosa, note="Moved the food to another fridge"):
    for temp in (37.0, 46.4):
        resp = api.post("/api/sensor-pulse", headers=headers,
                        json={"sensor_id": "WALK-IN", "temperature_fahrenheit": temp})
        assert resp.status_code == 200, resp.text
    incident_id = resp.json()["incident_id"]
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={"close_anyway": True, "note": note})
    assert closed.status_code == 200, closed.text
    return STORE.get_incident(incident_id)


# --- 2. the proof report says how an alert closed ----------------------------------


def test_the_proof_report_says_an_alert_was_closed_by_hand_while_still_out(api, cafe):
    headers, rosa, tenant = cafe
    incident = _warm_and_closed_anyway(api, headers, rosa)
    assert incident.closed_out_of_range and incident.close_temperature_f == 46.4
    first = incident.opened_at.date().replace(day=1)
    alert = proofreports.build(tenant, None, first)["alerts"][0]
    assert alert["closed_how"] == "by_hand_out_of_range"
    assert alert["closed_at_temperature"] == 46.4
    # The page says so, and "back to normal" only when it was.
    assert "closed by hand while still out of range${temp}" in PROOF_JS
    assert 'if (a.closed_how === "on_its_own") return `back to normal ${at}`;' in PROOF_JS
    assert "back to normal ${esc(stamp(a.closed_at))}" not in PROOF_JS
    assert "return `closed ${at}`;" in PROOF_JS  # a report made before 3.43.0


def test_new_and_old_proof_reports_still_check_as_genuine(api, cafe):
    headers, rosa, tenant = cafe
    incident = _warm_and_closed_anyway(api, headers, rosa)
    first = incident.opened_at.date().replace(day=1)
    row = proofreports.make(tenant, None, first)
    assert proofreports.check(row) == "genuine"
    assert row["body"]["alerts"][0]["closed_how"] == "by_hand_out_of_range"
    page = api.get(f"/verify/{row['code']}")
    assert page.status_code == 200 and "genuine" in page.text.lower()
    # A report made before 3.43.0 has no closed_how: its own seal still
    # checks, and the page says "closed" for it.
    old = proofreports.make(tenant, None, first)
    for alert in old["body"]["alerts"]:
        alert.pop("closed_how", None)
        alert.pop("closed_at_temperature", None)
    old["content_hash"] = proofreports.content_hash(old["body"])
    old["seal"] = proofreports.seal(old["report_id"], old["code"], old["content_hash"],
                                    proofreports._key(old["key_source"]))
    STORE._db.put(proofreports.KIND, old["report_id"], old)
    assert proofreports.check(old) == "genuine"
    resp = api.get(f"/api/proof-reports/verify/{old['code']}")
    assert resp.status_code == 200 and resp.json()["state"] == "genuine"


def test_an_alert_that_came_back_by_itself_is_back_to_normal(demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    first = min(r.recorded_at for r in STORE.readings_for(demo_account.HANDLED, complete=True))
    report = proofreports.build(tenant, None, date(first.year, first.month, 1))
    handled = next(a for a in report["alerts"] if a.get("closed_at"))
    assert handled["closed_how"] in ("by_hand", "on_its_own")
    assert "closed_at_temperature" not in handled


# --- 3. the insurance pack: what went, why it was closed, a call --------------------


def test_the_close_reason_is_why_it_was_closed_never_what_went(api, cafe):
    headers, rosa, _ = cafe
    incident = _warm_and_closed_anyway(api, headers, rosa)
    food = claims.food_record(incident)
    assert food["note"] is None
    assert food["close_reason"] == "Moved the food to another fridge"
    assert food["closed_out_of_range"] is True
    # The note added with "Add what was thrown away" is what went.
    resp = api.post(f"/api/voice/corrective-action/{incident.incident_id}", headers=rosa,
                    json={"action": "Two trays of chicken", "product_disposition": "discarded"})
    assert resp.status_code == 200, resp.text
    food = claims.food_record(STORE.get_incident(incident.incident_id))
    assert food["note"] == "Two trays of chicken" and food["thrown"]
    assert food["close_reason"] == "Moved the food to another fridge"
    pack = api.post(f"/api/claims/{incident.incident_id}/packet", headers=rosa)
    assert pack.status_code == 200, pack.text
    story = pack.json()["food"]
    assert story["close_reason"] == "Moved the food to another fridge"
    # The console's pack shows each on its own line.
    assert '<span class="k">Why it was closed</span>' in CONSOLE_JS
    assert '${food.thrown ? "What went" : "Note"}' in CONSOLE_JS


def test_a_pretend_call_is_called_a_call():
    rows = claims._delivery_rows([
        {"channel": "sms", "to": "+1", "delivered": True, "status": "delivered", "demo": True},
        {"channel": "voice", "to": "+1", "delivered": False, "status": "not_configured", "demo": True},
    ], None)
    assert rows[0]["outcome_words"] == "pretend text, demo"
    assert rows[1]["outcome_words"] == "pretend call, demo, no call placed"


def test_the_demos_call_rows_say_no_call_was_placed(api, demo):
    still = next(i for i in STORE.incidents_for(demo["tenant_id"]) if i.sensor_id == demo_account.STILL_OPEN)
    rows = claims._delivery_rows(still.voice_fanout, None)
    assert rows and all(r["outcome_words"] == "pretend call, demo, no call placed" for r in rows)


# --- 4. who gets alerted, one story ---------------------------------------------


def test_the_overview_says_when_a_rotation_decides_who_is_texted(api, demo, tenant_factory):
    tenant = STORE.get_tenant(demo["tenant_id"])
    assert texted_by_rotation(tenant) is True
    overview = api.get("/api/console/overview", headers=_owner(api)).json()
    assert overview["texted_by_rotation"] is True
    _, made = tenant_factory()
    plain = STORE.get_tenant(made["tenant_id"])
    STORE.add_contact(plain.tenant_id, "Person A", "+1 555 010 0150")
    assert texted_by_rotation(plain) is False
    # Pilot and the Team tab read it.
    assert "dd.texted_by_rotation" in PILOT
    assert "With your rotation, whoever is on call in it is texted" in PILOT


def test_the_team_tabs_badge_counts_people_and_names_who_is_on_call_now():
    note = CONSOLE_JS[CONSOLE_JS.index("function rosterNote()"):]
    note = note[:note.index("\n}\n")]
    assert 'CLXWords.count(roster.count, "person", "people")' in note
    assert "state.escalation.sms_recipients" in note
    assert "on call now" in note
    assert "`${roster.count} on call`" not in CONSOLE_JS
    assert "state.escalation = preview;\n  rosterNote();" in CONSOLE_JS


# --- 1. Pilot is told whether calls are placed ------------------------------------


def test_pilot_is_told_whether_this_server_places_calls():
    from notifications import delivery_ready

    assert main.pilot_facts()["calls-live"] == ("yes" if delivery_ready() else "no")
    assert 'callsLive: d.callsLive || ""' in PILOT


# --- 5. smaller items -------------------------------------------------------------


def test_a_sensor_key_reading_with_no_serial_is_that_sensors(api, tenant_factory):
    headers, _ = tenant_factory()
    made = api.post("/api/licenses/me/sensors", headers=headers,
                    json={"sensor_id": "ANY-1", "industry_vertical": "restaurant", "location_name": "Walk-in fridge"})
    assert made.status_code == 201, made.text
    key = made.json()["ingest_key"]
    resp = api.post("/api/v1/bridge/any", headers={"X-Kryozyn-Sensor-Key": key}, json={"temperature_f": 38.5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["understood_as"]["serial"] == "ANY-1"
    assert STORE.get_sensor("ANY-1").last_temperature == 38.5
    # The business key does not say which sensor: a plain refusal.
    whole = api.post("/api/v1/bridge/any", headers={"X-Kryozyn-Key": headers["X-Kryozyn-Key"]},
                     json={"temperature_f": 38.5})
    assert whole.status_code == 422
    detail = whole.json()["detail"]
    assert detail["message"].startswith("This reading has no serial number")
    assert "X-Kryozyn-Sensor-Key" in detail["next"]
    # A sensor key still reports for its own sensor only.
    other = api.post("/api/licenses/me/sensors", headers=headers,
                     json={"sensor_id": "ANY-2", "industry_vertical": "restaurant", "location_name": "Bar fridge"})
    assert other.status_code == 201
    wrong = api.post("/api/v1/bridge/any", headers={"X-Kryozyn-Sensor-Key": key},
                     json={"serial": "ANY-2", "temperature_f": 38.5})
    assert wrong.status_code == 403


def test_money_says_what_the_contract_really_does():
    block = CONSOLE_JS[CONSOLE_JS.index('esc(isYearly ? `Billed yearly'):]
    block = block[:block.index("Next invoice")]
    assert "% each year after the first." in block
    assert "a new one starts by itself." in block
    assert "Cancel any time: nothing more is billed after you cancel, with no fee" in block
    assert '"renews automatically"' not in block
    # Pilot says the same.
    assert "A signed term sets your price and renews by itself, but cancelling still stops the bills" in PILOT
    assert "a signed term's price rises 5% a year after its first year" in PILOT


def test_plain_words_on_setup_and_proof():
    assert "Google AI Studio" not in CONSOLE_HTML + CONSOLE_JS
    assert 'placeholder="AI helper key (optional)"' in CONSOLE_HTML
    assert "// system terminal output log" not in CONSOLE_HTML
    assert "<span>Recent activity</span>" in CONSOLE_HTML
    for jargon in ("Issue attestation", "Estate digest", "links re-derived", "Attestation issued"):
        assert jargon not in CONSOLE_HTML + CONSOLE_JS, jargon
    assert 'placeholder="Mobile, +1 555…"' in CONSOLE_HTML and 'placeholder="Mobile, e.g.' not in CONSOLE_HTML
    assert "#contact-form .row > input { flex: 1 1 150px; }" in (STATIC / "theme.css").read_text()


def test_us_spelling_where_people_read_it():
    import domain
    import industries
    import sensor_guides
    import trades

    text = repr(domain.INDUSTRY_PROFILES) + repr(industries.__dict__.get("PAGES", "")) + sensor_guides.render_markdown()
    for british in ("Vapour", "vapour-phase", "recognise"):
        assert british not in text, british
    assert "Inspection authorization" in (ROOT / "industries.py").read_text()
    assert "Programmes" not in (ROOT / "trades.py").read_text()
    assert trades and industries


def test_the_industry_pages_have_pricing_and_sign_in_on_every_screen(api):
    for path in ("/for", "/for/restaurant", "/for/florists"):
        page = api.get(path)
        assert page.status_code == 200, path
        assert '<a href="/pricing">Pricing</a>' in page.text, path
        assert '<a class="btn btn-sm" href="/console">Sign in</a>' in page.text, path
        assert "/static/signedin.js" in page.text, path


def test_the_changelog_and_version_say_3_43_0():
    assert (ROOT / "VERSION").read_text().strip() == "3.43.0"
    assert (ROOT / "CHANGELOG.md").read_text().split("## ")[1].startswith("3.43.0")
