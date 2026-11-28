"""Round 8 (3.28.0): honest "Food protected", honest calls, a calmer café.

1. "Food protected" only when an answered alert was back in range within
   two hours, or somebody said "Food kept". Eight hours at 62.5°F said
   "Food protected: about $3,000"; it now says "Food at risk: check it".
   "It's fixed" asks "Food kept / Thrown away". The monthly email adds up
   by the same rule.
2. "We have phoned" / "Called the list" only when a call really went out.
3. Pilot keeps off /today's fridge rows and the demo cards (Node suite).
4. The repair offer: more than one warm spell before "failing", gone once
   the unit recovered and was marked fixed, never on the TV screen, never
   "failing" beside "Fine".
5. A small café's screens: one toast at a time, three tiles on a phone,
   operator cards under Advanced, the header's extras in the business
   menu, the Simple view's explainer one line for a returning owner.
6. Pilot's "Log for the inspector" and "It's fixed now" (Node suite).
7. "This month: 1 alert, answered in 4 minutes, food kept." on /today.
8. The pricing page says which plan a small café gets, first.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import food_value
import monthly
import repairs
from records import Incident
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
THEME = (STATIC / "theme.css").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
ANSWER_JS = (STATIC / "answer.js").read_text()
WORDS_JS = (STATIC / "words.js").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _sign_up(api, email="rosa@cafe.example"):
    made = api.post("/api/signup", json={
        "company_name": "Rosa's Cafe", "full_name": "Rosa Diaz", "email": email,
        "password": "flour-and-butter-22", "contact_phone": "+15550104478",
        "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _sensor(api, owner, sensor_id="WALK-IN-1", where="Walk-in fridge", kind="fridge"):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": sensor_id, "industry_vertical": "restaurant",
        "location_name": where, "unit_kind": kind})
    assert made.status_code == 201, made.text
    return made.json()["ingest_key"]


def _pulse(api, key, temp, sensor_id="WALK-IN-1"):
    got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert got.status_code == 200, got.text
    return got.json()


def _backdate(incident_id, hours):
    """Make the alert's warm spell start `hours` ago."""
    incident = STORE.get_incident(incident_id)
    then = utc_now() - timedelta(hours=hours)
    incident.out_since = then
    incident.opened_at = then
    STORE._save_incident(incident)


def _row(api, owner, incident_id):
    return next(i for i in api.get("/api/console/overview", headers=owner).json()["incidents"]
                if i["incident_id"] == incident_id)


def _long_answered_alert(api, owner, hours=8.0):
    """62.5°F for `hours`, answered, back to 38°F and closed with It's fixed."""
    key = _sensor(api, owner)
    _pulse(api, key, 38.0)
    opened = _pulse(api, key, 62.5)
    assert opened["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    incident_id = opened["incident_id"]
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _backdate(incident_id, hours)
    _pulse(api, key, 38.0)
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={})
    assert closed.status_code == 200, closed.text
    return incident_id, closed.json()


# --- 1. honest "Food protected" -------------------------------------------------

def test_eight_hours_warm_is_food_at_risk_not_food_protected(api):
    """The reviewer's case: 8 hours at 62.5°F claimed $3,000."""
    owner = _sign_up(api)
    incident_id, _ = _long_answered_alert(api, owner, hours=8.0)
    row = _row(api, owner, incident_id)
    assert row["food_protected"] is None
    assert row["food_at_risk"]["words"] == "Food at risk: check it"
    assert "more than 2 hours" in row["food_at_risk"]["note"]


def test_answered_and_back_within_two_hours_asks_rather_than_counts(api):
    """3.30.0: back in time is not "Food protected" until somebody says so
    (tests/test_round9_food_answer_and_small_cafe.py has the rest)."""
    owner = _sign_up(api)
    incident_id, _ = _long_answered_alert(api, owner, hours=1.5)
    row = _row(api, owner, incident_id)
    assert row["food_protected"] is None
    assert row["food_at_risk"] is None
    assert row["food_question"]["words"] == "Cold again after 1 hour 30 minutes out"
    assert api.post(f"/api/voice/food/{incident_id}", headers=owner,
                    json={"food": "kept"}).status_code == 200
    row = _row(api, owner, incident_id)
    assert row["food_protected"]["usd"] == food_value.STOCK_USD["food"]["walk-in fridge"]
    assert row["food_question"] is None


def test_its_fixed_asks_food_kept_or_thrown_and_the_answer_decides(api):
    owner = _sign_up(api)
    incident_id, closed = _long_answered_alert(api, owner, hours=8.0)
    assert closed["ask_food"] == {"word": "food"}
    kept = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "kept"})
    assert kept.status_code == 200, kept.text
    assert kept.json()["message"] == "Noted: food kept."
    row = _row(api, owner, incident_id)
    assert row["food_protected"]["words"] == "Food protected: about $3,000"
    assert row["food_at_risk"] is None
    assert row["product_disposition"] == "unaffected"
    thrown = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"})
    assert thrown.status_code == 200
    row = _row(api, owner, incident_id)
    assert row["food_protected"] is None and row["food_at_risk"] is None
    assert row["product_disposition"] == "discarded"
    # Once said, it is not asked again.
    assert api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={}).json()["status"] \
        == "ALREADY_RESOLVED"


def test_the_food_answer_takes_only_kept_or_thrown_and_only_on_a_unit_alert(api):
    owner = _sign_up(api)
    incident_id, _ = _long_answered_alert(api, owner, hours=3.0)
    bad = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "eaten"})
    assert bad.status_code == 422
    assert api.post("/api/voice/food/INC-NOPE", headers=owner, json={"food": "kept"}).status_code == 404
    assert api.post(f"/api/voice/food/{incident_id}", json={"food": "kept"}).status_code in (401, 403)


def test_closed_while_still_out_of_range_is_at_risk_until_somebody_says(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=50.0,
        breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
    incident.acknowledged_at = utc_now()
    incident.resolved_at = utc_now()
    incident.closed_out_of_range = True
    assert food_value.protected(incident, sensor) is None
    assert "closed while still out of range" in food_value.at_risk(incident, sensor)["note"]
    incident.product_disposition = "moved"
    assert food_value.protected(incident, sensor) is not None
    assert food_value.at_risk(incident, sensor) is None


def test_the_monthly_email_uses_the_same_rule_and_names_the_risk(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")

    def closed(hours):
        incident, _ = STORE.open_incident_once(
            tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=50.0,
            breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
        now = utc_now()
        incident.out_since = incident.opened_at = now - timedelta(hours=hours)
        incident.acknowledged_at = incident.opened_at + timedelta(minutes=4)
        incident.resolved_at = now
        return incident

    kept = closed(1.0)
    kept.product_disposition = "unaffected"
    total = food_value.month_total([(kept, sensor), (closed(8.0), sensor), (closed(1.0), sensor)])
    assert total == {"usd": food_value.STOCK_USD["food"]["fridge"], "alerts": 1, "at_risk": 1,
                     "thrown": 0, "unanswered": 1, "auto_safe": 0}
    report = {"company_name": "Rosa's Cafe", "month_label": "October 2026", "units": 1,
              "readings": 100, "in_range_percent": 99.0, "breach_count": 0,
              "food_protected_usd": 800, "food_protected_alerts": 1, "food_at_risk_alerts": 1,
              "open_problems": {"alerts": [], "offline": [], "never_reported": [], "low_battery": []},
              "inspector_log": "", "period_start": "2026-10-01", "period_end": "2026-10-31",
              "caveat": "", "temperature_unit": "F"}
    text = " ".join(monthly.render_text(report).split())
    assert "Food at risk: 1 alert ran over 2 hours with nobody saying what happened to the food." in text


def test_the_pages_ask_after_its_fixed_and_offer_the_answer_on_the_alert():
    assert "Closed. What about the food?" in ANSWER_JS
    assert '"Food kept"' in ANSWER_JS and '"Thrown away"' in ANSWER_JS and "Not sure yet" in ANSWER_JS
    assert "/api/voice/food/" in ANSWER_JS
    # Shown only when the server asks (a site-wide alert, or a sector with
    # no food, is not asked).
    assert "body && body.ask_food" in ANSWER_JS
    risk = _function(CONSOLE_JS, "foodAtRisk")
    assert "data-food-kept" in risk and "data-food-thrown" in risk and "state.wall" in risk
    assert "i.food_question ? foodAtRisk(i)" in CONSOLE_JS


# --- 2. never "phoned" when no call was placed ---------------------------------

def _incident(tenant_id, sensor, voice=None):
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant_id, sensor=sensor, temperature_fahrenheit=50.0,
        breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
    if voice is not None:
        incident.voice_escalated_at = utc_now()
        incident.voice_fanout = voice
        incident.voice_delivery = voice[0] if voice else None
    return incident


def test_calls_off_says_would_have_phoned_never_called(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")
    off = _incident(tenant["tenant_id"], sensor, [
        {"channel": "voice", "to": "+15550100", "delivered": False, "status": "not_configured",
         "contact_name": "Dana Reyes"}])
    public = off.public()
    assert public["phoned"] is False and public["calls_off"] is True
    assert public["call_words"] == "Would have phoned the list: calls are off on this server"


def test_a_call_that_went_out_and_one_that_failed(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")
    went = _incident(tenant["tenant_id"], sensor, [
        {"channel": "voice", "to": "+15550100", "delivered": True, "status": "queued",
         "contact_name": "Dana Reyes"}])
    assert went.phoned and went.call_words() == "Phoned Dana Reyes"
    STORE.resolve_incident(went, "test")
    failed = _incident(tenant["tenant_id"], sensor, [
        {"channel": "voice", "to": "+15550100", "delivered": False, "status": "send_failed"}])
    assert not failed.phoned and not failed.calls_off
    assert failed.call_words() == "Tried to phone the list: no call went through"
    STORE.resolve_incident(failed, "test")
    assert _incident(tenant["tenant_id"], sensor).call_words() is None


def test_no_page_says_phoned_or_called_without_checking():
    assert "Called the list" not in CONSOLE_JS
    assert "and we have phoned." not in CONSOLE_JS
    assert "and we have phoned." not in SIMPLE_JS
    start = WORDS_JS.index("function nobodyYet")
    nobody = WORDS_JS[start:WORDS_JS.index("\n  }\n", start)]
    assert 'if (x.phoned) return "Nobody has answered yet, and we have phoned."' in nobody
    assert "calls are off on this server" in nobody
    assert "CLXWords.nobodyYet(i)" in CONSOLE_JS and "CLXWords.nobodyYet(i)" in SIMPLE_JS
    timeline = _function(CONSOLE_JS, "incidentTimeline")
    assert "i.call_words" in timeline
    # The call's script and badge say a call was not placed.
    assert "What the phone call would have said (no call was placed)" in CONSOLE_JS
    assert '"calls are off on this server"' in _function(CONSOLE_JS, "deliveryLine")
    pack = (STATIC / "pilot-kryozyn-pack.js").read_text()
    assert "phone_call_made: Boolean(i.phoned)" in pack


def test_the_insurance_pack_timeline_is_honest_about_calls():
    claims = (ROOT / "claims.py").read_text()
    assert "No one on the ladder answered." not in claims
    assert "Calls are off on this server, so no call was placed." in claims


# --- 4. the repair offer --------------------------------------------------------

def _seed(sensor_id, temps, spacing_minutes=5):
    from domain import evaluate_breach

    sensor = STORE.get_sensor(sensor_id)
    now = utc_now()
    for index, temp in enumerate(temps):
        STORE.record_reading(
            sensor=sensor, temperature_fahrenheit=temp, humidity_percent=50.0,
            breached=evaluate_breach(sensor.industry_vertical, temp) is not None,
            at=now - timedelta(minutes=(len(temps) - 1 - index) * spacing_minutes))


def _cards(api, headers):
    return {s["sensor_id"]: s for s in
            api.get("/api/console/overview", headers=headers).json()["sensors"]}


def test_one_warm_spell_is_not_failing(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory(company_name="Harbor Grill")
    sensor_factory(headers, "ONE", "restaurant", "Walk-in freezer", above=32.0)
    _seed("ONE", [0.0] * 6 + [38.0, 40.0, 42.0, 44.0, 46.0, 48.0, 50.0, 51.0])
    # Its alert is the thing to deal with; no repair offer on one spell, and
    # not "heading for its limit" when it is already over it.
    assert _cards(api, headers)["ONE"]["repair"] is None


def test_a_unit_that_recovered_and_was_marked_fixed_is_not_offered_a_repair(
        api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(company_name="Harbor Grill")
    sensor_factory(headers, "TWO", "restaurant", "Walk-in freezer", above=32.0)
    # Two warm spells, the last 40 minutes (longer than a door), and back
    # to 0°F now.
    _seed("TWO", [0.0] * 4 + [40.0] * 3 + [0.0] * 4 + [40.0] * 9 + [0.0] * 3)
    offer = _cards(api, headers)["TWO"]["repair"]
    assert offer == {"offer": True, "kind": "repeated"}           # never "failing"
    sensor = STORE.get_sensor("TWO")
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=40.0,
        breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
    STORE.resolve_incident(incident, "Dana Reyes", note="Fixed the door seal.")
    assert _cards(api, headers)["TWO"]["repair"] is None


def test_the_offer_is_never_on_the_tv_screen_and_never_failing_beside_fine():
    block = _function(CONSOLE_JS, "repairBlock")
    assert "if (!r || state.wall) return" in block
    assert 'r.kind === "failing" && !fine' in block
    assert "This unit keeps going too warm." in block
    assert "def _marked_fixed" in (ROOT / "repairs.py").read_text()
    assert repairs.repair_signal.__code__.co_varnames.count("latest") == 1


# --- 5. a calmer small café -----------------------------------------------------

def test_one_toast_at_a_time_and_the_first_day_merges_with_the_last():
    toast = _function(CONSOLE_JS, "toast")
    assert "old.remove()" in toast
    assert "state.lastToast" in toast
    first = _function(CONSOLE_JS, "renderFirstDay")
    assert "const last = state.lastToast;" in first


def test_a_small_account_on_a_phone_sees_three_tiles_not_filler():
    # On a desktop too since 3.30.0.
    assert "const calm = smallAccount(d) && !state.wall;" in CONSOLE_JS
    for label in ("Readings in range", "Flat batteries", "Readings logged"):
        at = CONSOLE_JS.index(f'tile("{label}"')
        assert "calm" in CONSOLE_JS[at - 120:at], label


def test_a_small_accounts_setup_keeps_operator_cards_under_advanced():
    for card in ("find-autopilot", "find-ai-key", "find-sector-shortcuts"):
        assert re.search(r'id="%s" data-small-advanced="1"' % card, CONSOLE_HTML), card
    assert "Store 118 Boca Raton" not in CONSOLE_HTML
    adv = _function(CONSOLE_JS, "isAdvanced")
    assert 'data-small-advanced") === "1" && smallAccount()' in adv
    assert "const advanced = isAdvanced(card);" in _function(CONSOLE_JS, "inTab")


def test_a_small_accounts_header_extras_are_in_the_business_menu():
    assert 'document.body.classList.toggle("acct-small", smallAccount(d));' in CONSOLE_JS
    assert "body.acct-small #term-toggle, body.acct-small #wall-mode" in THEME
    box = _function(CONSOLE_JS, "thisAccountBox")
    # "Recent activity" since 3.44.0, the name the panel itself has.
    assert 'id="biz-log">Recent activity' in box and 'id="biz-tv">TV screen' in box
    assert '"biz-log": "term-toggle", "biz-tv": "wall-mode"' in CONSOLE_JS


def test_a_returning_owners_explainer_is_one_line():
    # Folded by foldIntro, from the person's saved choice (3.30.0).
    fold = _function(SIMPLE_JS, "foldIntro")
    assert 'classList.toggle("intro-back", !open)' in fold and '"How this works"' in fold
    assert "foldIntro(open);" in _function(SIMPLE_JS, "introFor")
    assert ".intro.intro-back:not([open])" in (STATIC / "simple.css").read_text()


# --- 7. the month on the Simple view --------------------------------------------

def test_this_month_says_the_alerts_how_fast_and_the_food(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    _pulse(api, key, 38.0)
    month = api.get("/api/console/overview", headers=owner).json()["this_month"]
    assert month["words"] == "This month: no alerts. Every fridge and freezer stayed in range."
    opened = _pulse(api, key, 62.0)
    incident_id = opened["incident_id"]
    incident = STORE.get_incident(incident_id)
    # Past a short blip, so the kept food has a figure (3.35.0).
    incident.opened_at = incident.out_since = utc_now() - timedelta(minutes=40)
    STORE._save_incident(incident)
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _pulse(api, key, 38.0)
    assert api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={}).status_code == 200
    # Until somebody says, "food not answered yet", never "food kept" (3.30.0).
    month = api.get("/api/console/overview", headers=owner).json()["this_month"]
    assert month["words"] == "This month: 1 alert · 1 food answer missing"  # two items since 3.35.0
    assert month["food_kept"] == 0 and month["food_usd"] == 0
    assert api.post(f"/api/voice/food/{incident_id}", headers=owner,
                    json={"food": "kept"}).status_code == 200
    month = api.get("/api/console/overview", headers=owner).json()["this_month"]
    assert month["alerts"] == 1 and month["food_kept"] == 1
    assert month["words"] == "This month: 1 alert · answered in 40 minutes"
    assert month["food_usd"] == food_value.STOCK_USD["food"]["walk-in fridge"]
    assert "monthLine(data, units);" in SIMPLE_JS
    assert 'id="month-line"' in (STATIC / "simple.html").read_text()


# --- 8. the pricing page --------------------------------------------------------

def test_the_pricing_page_leads_with_the_small_cafe_plan(api):
    page = api.get("/pricing").text
    h1 = re.search(r"<h1>(.*?)</h1>", page).group(1)
    assert h1 == "Small caf&eacute;? Starter is $59 a month."
    picker = page[page.index('class="plan-pick"'):page.index("</nav>", page.index('class="plan-pick"'))]
    assert "Starter &middot; $59 a month" in picker
    assert "Growth &middot; $149 a month a location" in picker
    assert picker.index("Starter") < picker.index("Growth")
    assert "%%" not in page


def test_dataclass_has_the_new_fields():
    assert "phoned" in dir(Incident) and "calls_off" in dir(Incident)
