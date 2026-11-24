"""Round 10 (3.32.0): short blips, the loss flow, the week on one screen.

1. A food unit's alert out of range for under CYBERLOGIX_FOOD_SAFE_MINUTES
   (30) and never far past its limit records "Food safe: out for N
   minutes" by itself, with Change: nobody is asked, no "One thing to do",
   no dollars. Long, severe, closed-while-warm and pharmacy alerts still
   ask.
2. Pilot's built-in answers on every page (Node:
   tests/js/kryozyn-pilot.test.js).
3. "Cold again after 2 minutes out (since 9:10 PM)" everywhere, and the
   short month line "3 alerts · all answered · 1 food answer missing".
4. After "Thrown away": the insurance pack and the discard note; any
   answer can be changed for 24 hours, each change in the history.
5. What Kryozyn did for you: GET /api/console/did-for-you, with a
   sample in a trial's first week.
6. The TV screen's bar, phone tiles 1 + 2, Advanced in sections, the
   "What this is" label (Node: tests/js/today.test.js), referral words
   that follow the plan.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

import brand
import food_value
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
THEME = (STATIC / "theme.css").read_text()
ANSWER_JS = (STATIC / "answer.js").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _inner(source: str, name: str) -> str:
    """A function inside answer.js's wrapper, indented two spaces."""
    start = source.index(f"function {name}")
    return source[start:source.index("\n  }\n", start)]


def _sign_up(api, vertical="restaurant", email="maya@bakery.example"):
    made = api.post("/api/signup", json={
        "company_name": "Maya's Bakery", "full_name": "Maya Lin", "email": email,
        "password": "croissant-dough-31", "contact_phone": "+15550107788",
        "industry_vertical": vertical})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _unit(api, owner, sensor_id="WALK-IN-1", where="Walk-in fridge", vertical="restaurant", wait=0):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": sensor_id, "industry_vertical": vertical,
        "location_name": where, "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    if wait is not None:
        rule = api.post(f"/api/licenses/me/sensors/{sensor_id}/alert-rule", headers=owner,
                        json={"alert_after_minutes": wait})
        assert rule.status_code == 200, rule.text
    return made.json()["ingest_key"]


def _pulse(api, key, temp, sensor_id="WALK-IN-1"):
    got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert got.status_code == 200, got.text
    return got.json()


def _overview(api, owner):
    return api.get("/api/console/overview", headers=owner).json()


def _row(api, owner, incident_id):
    return next(i for i in _overview(api, owner)["incidents"] if i["incident_id"] == incident_id)


def _blip(api, owner, key, warm=48.5, minutes=None, sensor_id="WALK-IN-1"):
    """Out of range (alerting at once, the wait is 0), then back."""
    _pulse(api, key, 37.5, sensor_id)
    opened = _pulse(api, key, warm, sensor_id)
    incident_id = opened["incident_id"]
    assert incident_id, opened
    if minutes is not None:
        incident = STORE.get_incident(incident_id)
        incident.opened_at = incident.out_since = utc_now() - timedelta(minutes=minutes)
        STORE._save_incident(incident)
    _pulse(api, key, 37.9, sensor_id)
    return incident_id


# --- 1. short blips record themselves -----------------------------------------------

def test_a_short_blip_records_food_safe_by_itself_with_no_question_and_no_dollars(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, owner, key, minutes=12)
    row = _row(api, owner, incident_id)
    assert row["state"] == "resolved" and row["back_to_normal_on_its_own"] is True
    assert row["food_question"] is None, "nobody is asked"
    assert row["food_auto"] is True and row["food_auto_minutes"] == pytest.approx(12, abs=0.5)
    assert row["food_answer_words"] == "Food safe: out for 12 minutes"
    assert row["food_answered_by"] == "Kryozyn (automatic)"
    assert row["food_protected"] is None, "no dollars for a short blip"
    assert row["food_change_until"], "Change shows for 24 hours"
    assert [h["auto"] for h in row["food_history"]] == [True]
    month = _overview(api, owner)["this_month"]
    assert month["words"] == "This month: 1 alert · cold again by itself · food safe"
    assert month["food_usd"] == 0 and month["food_auto_safe"] == 1
    answer = month["food_answers"][0]
    assert answer["auto"] is True and answer["changeable"] is True and answer["by_name"] is None
    # The record says so too: the inspector's log reads it as food kept.
    assert STORE.get_incident(incident_id).product_disposition == "unaffected"


@pytest.mark.parametrize("why, warm, minutes", [
    ("long", 48.5, 40),            # past the 30 minutes
    ("severe", 62.5, 5),           # 21.5°F past its 41°F limit
    ("hot", 71.0, 2),              # room temperature
])
def test_long_or_severe_alerts_keep_the_question(api, why, warm, minutes):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, owner, key, warm=warm, minutes=minutes)
    row = _row(api, owner, incident_id)
    assert row["food_auto"] is False, why
    assert row["food_answer_words"] is None, why
    q = row["food_question"]
    assert q is not None and q["ask_on_today"] is True, why
    assert q["words"] == f"Cold again after {food_value._minutes_words(minutes)} out", why
    assert q["out_since"], "the page adds (since 9:10 PM)"


def test_closed_while_still_warm_keeps_the_question(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    _pulse(api, key, 37.5)
    incident_id = _pulse(api, key, 48.5)["incident_id"]
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=owner,
                      json={"close_anyway": True, "note": "Moved the food to the freezer"})
    assert closed.status_code == 200, closed.text
    row = _row(api, owner, incident_id)
    assert row["food_auto"] is False
    assert row["food_question"]["at_risk"] is True


def test_a_pharmacy_fridge_always_asks(api):
    owner = _sign_up(api, vertical="pharmacy")
    key = _unit(api, owner, where="Vaccine fridge", vertical="pharmacy")
    incident_id = _blip(api, owner, key, warm=12.0, minutes=5)
    row = _row(api, owner, incident_id)
    assert row["food_auto"] is False
    assert row["food_question"]["word"] == "stock"


def test_its_fixed_on_a_blip_says_it_was_recorded(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    _pulse(api, key, 37.5)
    incident_id = _pulse(api, key, 48.5)["incident_id"]
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _pulse(api, key, 38.0)
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={})
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["ask_food"] is None
    assert body["incident"]["food_auto"] is True
    assert body["message"].startswith("Closed. Food safe: out for")
    assert "Change it on the alert" in body["message"]
    assert "body.incident.food_auto" in _inner(ANSWER_JS, "afterClose")


def test_the_threshold_is_a_setting(monkeypatch, api):
    for raw, want in (("", 30.0), ("45", 45.0), ("500", 120.0), ("x", 30.0), ("0", 0.0), ("-3", 0.0)):
        monkeypatch.setenv("CYBERLOGIX_FOOD_SAFE_MINUTES", raw)
        assert food_value._short_minutes() == want, raw
    monkeypatch.setattr(food_value, "SHORT_MINUTES", 0.0)
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, owner, key, minutes=3)
    assert _row(api, owner, incident_id)["food_question"] is not None, "0 turns it off"
    assert _overview(api, owner)["food_safe_minutes"] == 0.0
    assert "CYBERLOGIX_FOOD_SAFE_MINUTES" in (ROOT / ".env.example").read_text()
    assert "CYBERLOGIX_FOOD_SAFE_MINUTES" in (ROOT / "docs" / "ALERTS.md").read_text()


# --- 4. the loss flow and the 24-hour change ---------------------------------------

def test_an_answer_can_be_changed_for_24_hours_and_each_change_is_kept(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, owner, key, minutes=8)
    changed = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"] is True
    assert changed.json()["message"].startswith("Changed: food thrown away")
    row = _row(api, owner, incident_id)
    assert row["food_auto"] is False and row["food_answer_words"] == "Thrown away"
    assert [h["words"] for h in row["food_history"]] == ["Food safe: out for 8 minutes", "Thrown away"]
    assert [h["auto"] for h in row["food_history"]] == [True, False]
    assert row["food_history"][1]["by_name"] == "Maya"
    audit = [e.detail for e in STORE.audit_for(STORE.get_incident(incident_id).tenant_id)]
    assert any("changed from Food safe: out for 8 minutes" in d for d in audit), audit
    month = _overview(api, owner)["this_month"]
    assert month["food_answers"][0]["thrown"] is True and month["food_answers"][0]["has_note"] is False
    # A discard note: the corrective action, with what went.
    note = api.post(f"/api/voice/corrective-action/{incident_id}", headers=owner,
                    json={"action": "Thrown away from Walk-in fridge: two trays of chicken",
                          "product_disposition": "discarded"})
    assert note.status_code == 200, note.text
    assert _overview(api, owner)["this_month"]["food_answers"][0]["has_note"] is True
    # A day later it stands.
    incident = STORE.get_incident(incident_id)
    incident.food_answered_at = utc_now() - timedelta(hours=25)
    STORE._save_incident(incident)
    late = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "kept"})
    assert late.status_code == 409
    assert "24 hours" in late.json()["detail"]
    assert _overview(api, owner)["this_month"]["food_answers"][0]["changeable"] is False


def test_the_pages_offer_the_pack_and_the_note_after_thrown_away():
    # answer.js: the dialog after "Thrown away", the prefilled note, and the pack.
    food = _inner(ANSWER_JS, "food(")
    assert 'if (answerWord === "thrown") lossNext(id);' in food
    assert 'text: "Make the insurance pack"' in ANSWER_JS
    assert 'text: "Add what was thrown away"' in ANSWER_JS
    assert "box.value = cfg.discardNote(d.dataset.id)" in ANSWER_JS
    assert '{ action: text, product_disposition: "discarded" }' in ANSWER_JS
    # The console opens the pack itself, and follows /today's link to it.
    assert "packet: (id) => openPacket(id)," in CONSOLE_JS
    assert "function followPacketLink()" in CONSOLE_JS
    said = _function(CONSOLE_JS, "foodAnswered")
    assert "CLXWords.changeable(i.food_change_until)" in said
    assert "data-loss-pack" in said and "data-loss-note" in said and "data-food-change" in said


# --- 3. the month line ------------------------------------------------------------

def test_the_month_line_is_short(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    key2 = _unit(api, owner, "PREP-1", "Prep fridge")
    _blip(api, owner, key, minutes=4)
    severe = _blip(api, owner, key2, warm=62.5, minutes=6, sensor_id="PREP-1")
    month = _overview(api, owner)["this_month"]
    assert month["words"] == "This month: 2 alerts · all cold again by themselves · 1 food answer missing"
    api.post(f"/api/voice/food/{severe}", headers=owner, json={"food": "thrown"})
    month = _overview(api, owner)["this_month"]
    assert month["words"] == "This month: 2 alerts · all cold again by themselves · thrown away on 1"


# --- 5. What Kryozyn did for you --------------------------------------------------

def test_what_cyberlogix_did_for_you(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    _blip(api, owner, key, minutes=4)
    week = api.get("/api/console/did-for-you", headers=owner)
    assert week.status_code == 200, week.text
    got = week.json()
    assert got["period"] == "week" and got["label"] == "This week"
    f = got["figures"]
    assert f["units"] == 1 and f["readings"] == 3 and f["alerts"] == 1
    assert f["by_itself"] == 1 and f["food_auto_safe"] == 1 and f["texts"] == 0, "texts off: none delivered"
    assert got["lines"][0] == f"Checked 3 readings from 1 fridge or freezer, {f['in_range_percent']:g}% in range."
    assert "1 alert, 1 cold again by itself." in got["lines"]
    assert "Food: 1 short blip recorded safe." in got["lines"]
    assert got["lines"][-1] == "Every sensor is reporting."
    assert not any("delivered" in line for line in got["lines"]), "no text line when none went"
    assert re.fullmatch(r"/console\?view=full#inspector-log\?from=\d{4}-\d\d-\d\d&to=\d{4}-\d\d-\d\d&open=1", got["log_link"])
    # A trial's first week: a sample, labelled.
    assert got["sample"]["note"].startswith("A sample of what this shows")
    assert got["sample"]["lines"][0] == "Checked 6,048 readings from 3 fridges and freezers, 99.6% in range."
    month = api.get("/api/console/did-for-you?period=month", headers=owner).json()
    assert month["label"] == "This month"
    assert api.get("/api/console/did-for-you?period=year", headers=owner).status_code == 422
    assert api.get("/api/console/did-for-you").status_code in (401, 403)
    # After the first week, no sample.
    tenant_id = _overview(api, owner)["tenant"]["tenant_id"]
    tenant = STORE.get_tenant(tenant_id)
    tenant.activated_at = utc_now() - timedelta(days=8)
    assert api.get("/api/console/did-for-you", headers=owner).json()["sample"] is None


def test_today_shows_it_with_the_log_link():
    html = brand.fill((STATIC / "simple.html").read_text())
    assert 'id="did"' in html and "What Kryozyn did for you" in html
    assert 'id="did-log"' in html and 'id="did-sample"' in html
    js = (STATIC / "simple.js").read_text()
    assert "/api/console/did-for-you?period=${didPeriod}" in js
    assert '$("did-log").href = got.log_link;' in js


# --- 6. the TV screen, tiles, Advanced, referral words ---------------------------

def test_the_tv_screen_bar_has_no_buttons_and_room_for_leave():
    assert "body.wall .needs-bar-acts { display: none !important; }" in THEME
    assert "body.wall .needs-bar-in { min-height: 56px; padding-right: 190px; max-width: none; }" in THEME
    assert "body.wall #wall-exit { top: 9px; }" in THEME
    wall = _function(CONSOLE_JS, "setWall")
    assert "if (state.data) renderNeedsBar(state.data);" in wall, "the bar is drawn again on the TV screen"


def test_advanced_is_in_sections_that_cover_every_advanced_card():
    advanced = set(re.findall(r'id="(find-[a-z0-9-]+)"[^>]*data-(?:small-)?advanced="1"', CONSOLE_HTML))
    advanced |= set(re.findall(r'data-(?:small-)?advanced="1"[^>]*id="(find-[a-z0-9-]+)"', CONSOLE_HTML))
    assert len(advanced) >= 10
    listed = set(re.findall(r'"(find-[a-z0-9-]+)"', CONSOLE_JS[CONSOLE_JS.index("const ADV_SECTIONS"):CONSOLE_JS.index("const ADV_OF")]))
    assert advanced <= listed, advanced - listed
    assert 'id="adv-sections"' in CONSOLE_HTML
    tab = _function(CONSOLE_JS, "inTab")
    assert "return searching || !sec || advOpen.has(sec);" in tab, "a search still finds every card"


def test_referral_words_follow_the_plan():
    words = _function(CONSOLE_JS, "referralReward")
    assert 'if (plan === "starter")' in words and "a month of Starter free" in words
    assert 'if (plan === "trial")' in words
    render = _function(CONSOLE_JS, "renderReferrals")
    assert "referralReward(seen)" in render
    assert "off, one month on one location,\n      when they pay" not in render


@pytest.mark.parametrize("suite", ["today.test.js", "kryozyn-pilot.test.js"])
def test_the_pages_in_node(suite):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    run = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / suite)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
