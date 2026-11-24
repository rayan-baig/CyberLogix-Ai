"""Round 12 (3.35.0): the insurance pack as evidence, Pilot at the loss
moment, the phone layout, and what it is worth.

1. Humidity is optional: a sensor that sends none has none stored (it used
   to be 50.0), the pack shows no humidity column when no reading has one,
   and the old stand-in 50% from before 3.35.0 is left sealed as stored but
   not shown as a measurement. The pack names its issuer (the product and
   the company, from brand.py), says "Summary" (never "(template)"), puts
   channels and outcomes in words ("Text", "not switched on"), starts its
   window at the first reading, keeps the IT note to the chain head and a
   link, and carries the site address the business gives (asked for in the
   Insurance packs card and on a pack that has none). Printed, every page
   is numbered.
2. Pilot (Node: tests/js/kryozyn-pilot.test.js): "Who gets the texts?"
   names the people by first name from the overview here.
3. The food dialog: "Not sure yet" on its own line, the answers with a gap;
   the Pilot handle wholly on the screen.
4. A sample pack with pretend data, linked from the front page and the
   pricing page; no dollars for an alert shorter than a short blip; the
   month line in two items; alert texts that start with the product's
   name; the export named after the product.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import brand
import claims
import food_value
import wording
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
THEME = (STATIC / "theme.css").read_text()


def _sign_up(api, email="maya@bakery.example"):
    made = api.post("/api/signup", json={
        "company_name": "Maya's Bakery", "full_name": "Maya Okafor", "email": email,
        "password": "croissant-dough-31", "contact_phone": "+15550104477",
        "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _unit(api, owner, sensor_id="WALK-IN-1"):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": sensor_id, "industry_vertical": "restaurant",
        "location_name": "Walk-in fridge", "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    assert api.post(f"/api/licenses/me/sensors/{sensor_id}/alert-rule", headers=owner,
                    json={"alert_after_minutes": 0}).status_code == 200
    return made.json()["ingest_key"]


def _pulse(api, key, temp, sensor_id="WALK-IN-1", **extra):
    got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp, **extra})
    assert got.status_code == 200, got.text
    return got.json()


def _alert(api, owner, key, **extra):
    _pulse(api, key, 37.5, **extra)
    incident_id = _pulse(api, key, 62.5, **extra)["incident_id"]
    assert incident_id
    _pulse(api, key, 38.0, **extra)
    return incident_id


def _pack(api, owner, incident_id):
    got = api.post(f"/api/claims/{incident_id}/packet", headers=owner)
    assert got.status_code == 200, got.text
    return got.json()


# --- 1. the pack as evidence ----------------------------------------------------------

def test_a_reading_without_humidity_stores_none_not_fifty(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    _pulse(api, key, 37.5)
    readings = STORE.readings_for("WALK-IN-1")
    assert readings and readings[-1].humidity_percent is None, "nobody measured 50%"
    _pulse(api, key, 37.6, humidity_percent=61.5)
    assert STORE.readings_for("WALK-IN-1")[-1].humidity_percent == 61.5


def test_the_pack_hides_humidity_when_no_reading_has_one(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key))
    evidence = pack["evidence"]
    assert evidence["humidity_shown"] is False
    assert all(r["humidity"] is None for r in evidence["readings"])
    assert "humid" in CONSOLE_JS and "evidence.humidity_shown" in CONSOLE_JS
    assert '${humid ? `<th class="num">Humidity</th>` : ""}' in CONSOLE_JS


def test_the_pack_shows_humidity_a_sensor_really_measured(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key, humidity_percent=58.0))
    assert pack["evidence"]["humidity_shown"] is True
    assert {r["humidity"] for r in pack["evidence"]["readings"]} == {58.0}


def test_the_old_fifty_percent_stand_in_stays_sealed_but_is_not_shown(api, break_gemini):
    """Readings from before 3.35.0 that say exactly 50% (what a missing
    humidity was stored as) are not rewritten: the chain covers them. The
    pack shows them as not measured and says why; the verifiable list keeps
    the stored value, so the chain head still checks."""
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _alert(api, owner, key, humidity_percent=50.0)
    # As if this database first ran 3.35.0 after these readings.
    later = (utc_now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put("meta", "humidity_optional", {"at": later})
    pack = _pack(api, owner, incident_id)
    evidence = pack["evidence"]
    assert evidence["humidity_shown"] is False
    assert all(r["humidity"] is None for r in evidence["readings"])
    assert "50%" in evidence["humidity_note"] and "probably not measured" in evidence["humidity_note"]
    assert {r["humidity_percent"] for r in evidence["verifiable_readings"]} == {50.0}
    checked = api.post("/api/vault/verify", json={
        "readings": evidence["verifiable_readings"],
        "chain_head": pack["attestation"]["chain_head"]})
    assert checked.status_code == 200 and checked.json()["matches"] is True, checked.text


def test_the_database_records_when_humidity_became_optional(tmp_path):
    import db

    fresh = db.Database(str(tmp_path / "a.db"))
    first = fresh.humidity_optional_since()
    assert first, "stamped the first time 3.35.0 opens it"
    fresh.close()
    again = db.Database(str(tmp_path / "a.db"))
    assert again.humidity_optional_since() == first, "never moved later"
    again.close()


def test_the_pack_names_its_issuer_and_says_summary(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key))
    assert pack["issuer"] == {"product": brand.APP_NAME, "company": brand.COMPANY_NAME, "url": brand.APP_URL}
    assert "Issued by ${esc(issuer.product)}, a product of" in CONSOLE_JS
    assert '<div class="quote-label">Summary</div>' in CONSOLE_JS
    assert "(template)" not in CONSOLE_JS and "Covering summary" not in CONSOLE_JS


def test_channels_and_outcomes_are_in_words(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key))
    rows = pack["response"]["sms_deliveries"]
    assert rows, "the alert text was written for the owner"
    assert rows[0]["channel_words"] == "Text"
    assert rows[0]["outcome_words"] == "not switched on", rows[0]
    assert claims.outcome_words(False, "not_configured") == "not switched on"
    assert claims.outcome_words(True, "queued") == "delivered"
    assert claims.channel_words("voice") == "Phone call"
    assert "esc(d.channel_words || d.channel)" in CONSOLE_JS
    assert "esc(d.outcome_words" in CONSOLE_JS


def test_the_window_starts_at_the_first_reading(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key))
    evidence = pack["evidence"]
    assert evidence["window_start"] == evidence["readings"][0]["at"], \
        "not twelve hours before a sensor that was added minutes ago"


def test_the_it_note_is_the_chain_head_and_a_link(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    pack = _pack(api, owner, _alert(api, owner, key))
    assert pack["verification_url"] == f"{brand.APP_URL}/developers#post-api-vault-verify"
    assert "POST" not in pack["verification"] and "verifiable_readings" not in pack["verification"]
    assert len(pack["verification"]) < 260
    block = CONSOLE_JS[CONSOLE_JS.index('<div class="doc-it">'):CONSOLE_JS.index("showDrawer();", CONSOLE_JS.index('<div class="doc-it">'))]
    assert "signing.note" not in block and "chain_head" in block and "verification_url" in block
    reference = api.get("/developers").text
    assert 'id="post-api-vault-verify"' in reference, "the link lands on the verifier's entry"


def test_the_site_address_is_asked_for_and_goes_on_every_pack(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _alert(api, owner, key)
    assert _pack(api, owner, incident_id)["location"]["address"] is None
    saved = api.post("/api/claims/site-address", headers=owner,
                     json={"address": "  12 Harbor   Street, Springfield "})
    assert saved.status_code == 200 and saved.json() == {"address": "12 Harbor Street, Springfield"}
    assert _pack(api, owner, incident_id)["location"]["address"] == "12 Harbor Street, Springfield"
    assert _overview(api, owner)["tenant"]["site_address"] == "12 Harbor Street, Springfield"
    cleared = api.post("/api/claims/site-address", headers=owner, json={"address": ""})
    assert cleared.json() == {"address": None}
    assert 'id="packet-add-address">Add the site address</button>' in CONSOLE_JS
    assert 'id="claims-address-form"' in (STATIC / "console.html").read_text()


def test_a_viewer_cannot_change_the_site_address(api, operator_factory):
    owner, _, _ = operator_factory(email="owner@example.com")
    made = api.post("/api/accounts/users", headers=owner, json={
        "email": "viewer@example.com", "full_name": "Viewer", "password": "correct-horse-battery",
        "role": "viewer"})
    assert made.status_code in (200, 201), made.text
    token = api.post("/api/accounts/login", json={
        "email": "viewer@example.com", "password": "correct-horse-battery"}).json()["token"]
    got = api.post("/api/claims/site-address", headers={"Authorization": f"Bearer {token}"},
                   json={"address": "1 Main St"})
    assert got.status_code == 403
    assert api.post("/api/claims/site-address", headers=owner, json={"address": "1 Main St"}).status_code == 200


def test_every_printed_page_is_numbered():
    assert '@bottom-right { content: "Page " counter(page) " of " counter(pages);' in THEME


def _overview(api, owner):
    return api.get("/api/console/overview", headers=owner).json()


# --- 2. Pilot's facts from the overview ------------------------------------------------

def test_the_overview_names_who_gets_texts_by_first_name_only(api):
    owner = _sign_up(api)
    _unit(api, owner)
    assert api.post("/api/contacts", headers=owner, json={
        "full_name": "Sam Lee", "phone": "+15550109999"}).status_code in (200, 201)
    names = _overview(api, owner)["texted_first_names"]
    assert "Sam" in names and all(" " not in n and "@" not in n and "+" not in n for n in names)


# --- 3. the phone layout -----------------------------------------------------------------

def test_not_sure_yet_has_its_own_line_and_the_answers_a_gap():
    assert ".food-dialog .food-acts .linkish { flex: 1 0 100%;" in THEME
    assert ".food-dialog .food-acts { gap: 10px; }" in THEME


def test_the_pilot_handle_is_wholly_on_the_screen():
    assert "html .pilot-handle-r .pilot-handle-robot { left: 1px; width: 42px; }" in THEME
    pilot = (STATIC / "pilot-kryozyn.js").read_text()
    assert '".answer-note"' in pilot, "the robot keeps off the quoted note of what went"


# --- 4. value ------------------------------------------------------------------------------

def test_the_sample_pack_is_public_labelled_and_linked(api):
    page = api.get("/sample-insurance-pack")
    assert page.status_code == 200
    text = page.text
    assert "Sample, with pretend data." in text and "Summary" in text
    assert "%%" not in text and brand.APP_NAME in text
    assert 'href="/sample-insurance-pack"' in api.get("/").text
    assert 'href="/sample-insurance-pack"' in api.get("/pricing").text
    assert "/sample-insurance-pack" in api.get("/sitemap.xml").text
    assert "style=" not in text and "<script>" not in text


def test_no_dollars_for_an_alert_shorter_than_a_short_blip(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")

    def kept(minutes):
        incident, _ = STORE.open_incident_once(
            tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=50.0,
            breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
        now = utc_now()
        incident.out_since = incident.opened_at = now - timedelta(minutes=minutes)
        incident.acknowledged_at = incident.opened_at + timedelta(minutes=1)
        incident.resolved_at = now
        incident.product_disposition = "unaffected"
        return incident

    short = kept(food_value.SHORT_MINUTES / 3)
    assert food_value.protected(short, sensor) is None, "a door held open is not $800 saved"
    long = kept(food_value.SHORT_MINUTES + 20)
    assert food_value.protected(long, sensor)["usd"] == food_value.STOCK_USD["food"]["fridge"]
    total = food_value.month_total([(short, sensor), (long, sensor)])
    assert total["usd"] == food_value.STOCK_USD["food"]["fridge"] and total["alerts"] == 2


def test_the_month_line_is_two_items(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _alert(api, owner, key)
    incident = STORE.get_incident(incident_id)
    incident.opened_at = incident.out_since = utc_now() - timedelta(minutes=45)
    STORE._save_incident(incident)
    assert api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"}).status_code == 200
    words = _overview(api, owner)["this_month"]["words"]
    assert words == "This month: 1 alert · food thrown away", words
    assert words.count("·") == 1


def test_alert_texts_start_with_the_products_name(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _alert(api, owner, key)
    text = STORE.get_incident(incident_id).sms_text
    assert text.startswith(f"{brand.APP_SHORT}: "), text
    assert wording.SMS_HEAD == f"{brand.APP_SHORT}: "


def test_the_export_is_named_after_the_product(api, operator_factory):
    headers, tenant, _ = operator_factory()
    got = api.get("/api/accounts/business/export", headers=headers)
    assert got.status_code == 200
    stem = re.sub(r"[^a-z0-9]+", "-", brand.APP_SHORT.lower()).strip("-")
    assert f'filename="{stem}-{tenant["tenant_id"]}.json"' in got.headers["content-disposition"]
    assert got.json()["file_name"] == f"{stem}-{tenant['tenant_id']}.json"
    assert "link.download = body.file_name" in CONSOLE_JS
