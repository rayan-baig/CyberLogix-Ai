"""Round 9 (3.30.0): the food question on every close, a calmer small café.

1. /today never says "Everything is fine... nothing for you to do" while
   the food is at risk: "One thing to do: check the food in Walk-in
   fridge" with Food kept / Thrown away right there, and the same in the
   console's "Needs you now" bar (Node: tests/js/today.test.js).
2. Every way an alert closes asks about the food ("It's fixed", "It's
   fixed" while warm and then cold, closed by itself): until somebody taps
   it is "food unanswered" ("Back in range in 12 minutes"), never "food
   kept" or "$800 protected". The month line and the monthly email follow
   the saved answer only, and the alert's history says "Thrown away, Rosa".
3. An open alert whose reading is back in range: "Back in range: press
   It's fixed", not "Too warm" beside 38°.
4. "No more calls for this alert" only once a call really went out; the
   demo asks about the food after "Mark it fixed".
5. A small account's Setup folds the OmniArmor connection, "Things to do"
   and the activity log under Advanced; the plan buttons say what
   /pricing says; three figures on a desktop too.
6. Pilot's "Log for the inspector" links straight to the log (Node).
7. "What this is" is folded or open per account, saved on the server.
8. A toast never sits on a new sensor key and its instructions.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

import food_value
import monthly
from store import STORE, utc_now
from voice_dispatch import ack_reply_text

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
DEMO_JS = (STATIC / "demo.js").read_text()
THEME = (STATIC / "theme.css").read_text()
PILOT_JS = (STATIC / "pilot-cyberlogix.js").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _sign_up(api, email="rosa@cafe.example", password="flour-and-butter-22"):
    made = api.post("/api/signup", json={
        "company_name": "Rosa's Cafe", "full_name": "Rosa Diaz", "email": email,
        "password": password, "contact_phone": "+15550104478",
        "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _sensor(api, owner, sensor_id="WALK-IN-1", where="Walk-in fridge"):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": sensor_id, "industry_vertical": "restaurant",
        "location_name": where, "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    return made.json()["ingest_key"]


def _pulse(api, key, temp, sensor_id="WALK-IN-1"):
    got = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert got.status_code == 200, got.text
    return got.json()


def _overview(api, owner):
    return api.get("/api/console/overview", headers=owner).json()


def _row(api, owner, incident_id):
    return next(i for i in _overview(api, owner)["incidents"] if i["incident_id"] == incident_id)


def _warm(api, key, minutes=12):
    """62.5°F (far past the limit, so it alerts at once), out for `minutes`."""
    _pulse(api, key, 38.0)
    opened = _pulse(api, key, 62.5)
    assert opened["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    incident = STORE.get_incident(opened["incident_id"])
    incident.opened_at = incident.out_since = utc_now() - timedelta(minutes=minutes)
    STORE._save_incident(incident)
    return opened["incident_id"]


def _assert_unanswered(row, minutes_words="12 minutes"):
    assert row["state"] == "resolved"
    assert row["food_protected"] is None, "no claim before an answer"
    assert row["food_answer_words"] is None
    assert row["food_question"]["words"] == f"Back in range in {minutes_words}"
    assert row["food_question"]["at_risk"] is False
    assert row["food_question"]["ask_on_today"] is True


# --- 2. the food question on every way an alert closes ---------------------------

def test_its_fixed_while_warm_then_closing_by_itself_asks_and_never_says_kept(api):
    """The reviewer's case: "It's fixed" at 60.5°F, closed once cold, and
    /today said "food kept" and "About $3,000 of food protected"."""
    owner = _sign_up(api)
    key = _sensor(api, owner)
    incident_id = _warm(api, key)
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    still = api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={})
    assert still.status_code == 409
    _pulse(api, key, 38.0)                      # closes by itself, on Rosa's word
    _assert_unanswered(_row(api, owner, incident_id))
    month = _overview(api, owner)["this_month"]
    assert month["words"] == "This month: 1 alert, answered in 12 minutes, food not answered yet."
    assert month["food_usd"] == 0 and month["food_kept"] == 0
    assert "food kept" not in month["words"]


def test_an_alert_that_closed_by_itself_asks_too(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    incident_id = _warm(api, key)
    _pulse(api, key, 38.0)                      # nobody answered; back to normal
    row = _row(api, owner, incident_id)
    assert row["back_to_normal_on_its_own"] is True
    _assert_unanswered(row)


def test_its_fixed_in_range_asks_and_the_answer_is_saved_with_who_and_when(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    incident_id = _warm(api, key)
    _pulse(api, key, 45.0)                      # still warm, but not far: stays open
    STORE.get_incident(incident_id)
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _pulse(api, key, 38.0)
    closed = api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={})
    assert closed.status_code == 200, closed.text
    assert closed.json()["ask_food"] == {"word": "food"}
    _assert_unanswered(_row(api, owner, incident_id))

    thrown = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"})
    assert thrown.status_code == 200, thrown.text
    row = _row(api, owner, incident_id)
    assert row["food_question"] is None
    assert row["food_answer_words"] == "Thrown away"
    assert row["food_answered_by_name"] == "Rosa"
    assert row["food_answered_at"]
    assert row["food_protected"] is None
    # Kept across a restart: the answer and who gave it are on the record.
    again = STORE.get_incident(incident_id)
    assert again.food_answered_by and again.food_answered_at is not None
    month = _overview(api, owner)["this_month"]
    assert month["words"] == "This month: 1 alert, answered in 12 minutes, food thrown away."
    assert month["food_answers"][0]["words"] == "Thrown away"
    assert month["food_answers"][0]["by_name"] == "Rosa"
    assert month["food_answers"][0]["unit"] == "Walk-in fridge"

    # The inspector's log says it in words, with who and when.
    today = utc_now().date().isoformat()
    log = api.get(f"/api/autopilot/inspector-log?start={today}&end={today}", headers=owner)
    assert log.status_code == 200, log.text
    breach = log.json()["units"][0]["breaches"][0]
    assert breach["food_answer"] == "Thrown away"
    assert breach["food_answered_by"].startswith("Rosa") and breach["food_answered_at"]

    kept = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "kept"})
    assert kept.status_code == 200
    row = _row(api, owner, incident_id)
    assert row["food_answer_words"] == "Food kept"
    assert row["food_protected"]["words"] == "Food protected: about $3,000"
    month = _overview(api, owner)["this_month"]
    assert month["words"].endswith("food kept.")
    assert month["food_usd"] == food_value.STOCK_USD["food"]["walk-in fridge"]


def test_a_corrective_action_that_says_what_happened_answers_it_too(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    incident_id = _warm(api, key)
    _pulse(api, key, 38.0)
    done = api.post(f"/api/voice/corrective-action/{incident_id}", headers=owner, json={
        "action": "Moved to the reach-in", "product_disposition": "moved"})
    assert done.status_code == 200, done.text
    row = _row(api, owner, incident_id)
    assert row["food_question"] is None
    assert row["food_answer_words"] == "Moved and kept"
    assert row["food_answered_by_name"] == "Rosa"


def test_a_long_one_is_at_risk_and_the_rule_is_the_saved_answer_only(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")

    def closed(hours, acked=True):
        incident, _ = STORE.open_incident_once(
            tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=50.0,
            breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
        now = utc_now()
        incident.out_since = incident.opened_at = now - timedelta(hours=hours)
        if acked:
            incident.acknowledged_at = incident.opened_at + timedelta(minutes=4)
        incident.resolved_at = now
        return incident

    quick = closed(0.2)
    assert food_value.protected(quick, sensor) is None, "answered and quick is not kept"
    assert food_value.question(quick, sensor)["words"] == "Back in range in 12 minutes"
    long = closed(8.0)
    q = food_value.question(long, sensor)
    assert q["at_risk"] is True and q["words"] == "Food at risk: check it"
    thrown = closed(0.5)
    thrown.product_disposition = "discarded"
    assert food_value.question(thrown, sensor) is None
    kept = closed(0.5, acked=False)
    kept.product_disposition = "unaffected"
    assert food_value.protected(kept, sensor)["usd"] == food_value.STOCK_USD["food"]["fridge"]
    old = closed(0.2)
    old.resolved_at = utc_now() - timedelta(days=food_value.ASK_ON_TODAY_DAYS + 1)
    assert food_value.question(old, sensor)["ask_on_today"] is False
    total = food_value.month_total([(quick, sensor), (long, sensor), (thrown, sensor), (kept, sensor)])
    assert total == {"usd": food_value.STOCK_USD["food"]["fridge"], "alerts": 1, "thrown": 1,
                     "at_risk": 1, "unanswered": 1}


def test_the_monthly_email_follows_the_saved_answers():
    report = {"company_name": "Rosa's Cafe", "month_label": "November 2026", "units": 1,
              "readings": 100, "in_range_percent": 99.0, "breach_count": 0,
              "food_protected_usd": 0, "food_protected_alerts": 0, "food_at_risk_alerts": 0,
              "food_thrown_alerts": 1, "food_unanswered_alerts": 2,
              "open_problems": {"alerts": [], "offline": [], "never_reported": [], "low_battery": []},
              "inspector_log": "", "period_start": "2026-11-01", "period_end": "2026-11-30",
              "caveat": "", "temperature_unit": "F"}
    text = " ".join(monthly.render_text(report).split())
    assert "Food protected" not in text
    assert "Food thrown away: 1 alert, as recorded in the log." in text
    assert "Food not answered: 2 alerts came back in range with nobody saying whether the food was kept." in text


def test_the_console_shows_the_question_the_answer_and_the_history():
    ask = _function(CONSOLE_JS, "foodAtRisk")
    assert "i.food_question" in ask and "data-food-kept" in ask and "data-food-thrown" in ask
    said = _function(CONSOLE_JS, "foodAnswered")
    assert "i.food_answer_words, i.food_answered_by_name" in said
    assert "i.food_protected ?" in said, "the estimate only beside food kept"
    timeline = _function(CONSOLE_JS, "incidentTimeline")
    assert "${i.food_answer_words}, ${by}" in timeline
    # "Needs you now" asks about the food once nothing is live.
    bar = _function(CONSOLE_JS, "renderNeedsBar")
    assert "One thing to do: check the ${esc(q.word)} in" in bar
    assert "data-food-kept" in bar and "food_question.ask_on_today" in bar


# --- 3. back in range with the alert still open ----------------------------------

def test_an_open_alert_back_in_range_says_back_in_range(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    incident_id = _warm(api, key)
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _pulse(api, key, 38.0)                      # acknowledged: stays open until It's fixed
    data = _overview(api, owner)
    unit = data["sensors"][0]
    assert unit["condition"]["state"] == "alert"
    assert unit["condition"]["back_in_range"] is True
    assert unit["condition"]["too"] is None
    row = _row(api, owner, incident_id)
    assert row["state"] == "acknowledged"
    bar = _function(CONSOLE_JS, "renderNeedsBar")
    assert "is back in range at ${esc(deg)}: press <b>It's fixed</b>." in bar
    _pulse(api, key, 50.0)
    assert _overview(api, owner)["sensors"][0]["condition"]["back_in_range"] is False


# --- 4. honest calls in the reply and the demo -----------------------------------

def test_the_reply_says_no_more_calls_only_after_a_call():
    assert ack_reply_text("Sam", "walk-in cooler").endswith("Nobody else will be called.")
    assert ack_reply_text("Sam", "walk-in cooler", phoned=True).endswith("No more calls for this alert.")
    assert ack_reply_text("Sam", "walk-in cooler", calls=False).endswith(
        "Everyone on the list can see you have it.")


def test_the_demo_is_honest_about_calls_and_asks_about_the_food():
    assert '"The calls have stopped."' in DEMO_JS and '"Nobody else will be called."' in DEMO_JS
    assert 'run.events.has("call-0") ? "The calls have stopped."' in DEMO_JS
    assert "S.messages.ack_reply_called" in DEMO_JS
    ask = _function(DEMO_JS, "foodAsk")
    assert "What about the food?" in ask.replace("what about", "What about")
    assert 'data-act="food-kept"' in ask and 'data-act="food-thrown"' in ask
    assert 'answerFood("kept")' in DEMO_JS and 'answerFood("thrown")' in DEMO_JS
    assert 'timeline("food-asked"' in _function(DEMO_JS, "fix")


# --- 5. a small café's Setup, money words and figures ----------------------------

def test_a_small_accounts_setup_folds_omniarmor_tasks_and_the_log_under_advanced():
    for card in ("find-omniarmor", "find-tasks", "find-audit-trail"):
        assert re.search(r'id="%s" data-small-advanced="1"' % card, CONSOLE_HTML), card


def test_the_plan_buttons_say_what_the_pricing_page_says(api):
    owner = _sign_up(api)
    data = _overview(api, owner)
    assert data["plan_prices"] == {"growth_label": "$149", "units_per_location": 10}
    words = _function(CONSOLE_JS, "planWords")
    assert "a month a location, up to ${" in words and "units a location" in words
    assert "Growth · 50 units</button>" not in CONSOLE_JS
    assert "Enterprise · 1000 units</button>" not in CONSOLE_JS
    assert '${esc(planWords("growth", starter))}</button>' in CONSOLE_JS
    pricing = api.get("/pricing").text
    assert "Growth &middot; $149 a month a location" in pricing
    assert "Up to 10 units there" in pricing


def test_three_figures_for_a_small_account_on_a_desktop_too():
    assert "const calm = smallAccount(d) && !state.wall;" in CONSOLE_JS
    assert 'matchMedia("(max-width: 720px)").matches;\n  const calm' not in CONSOLE_JS


# --- 6. Pilot goes straight to the log -------------------------------------------

def test_pilot_log_for_the_inspector_is_a_link_to_the_log():
    assert '["Log for the inspector", link(LOG_HERE)]' in PILOT_JS
    assert "#inspector-log?open=1" in PILOT_JS
    follow = _function(CONSOLE_JS, "inspectorLink")
    assert r"(?:\?(.*))?$" in follow, "a link with no dates is the last week"
    assert "if (link.open) openInspectorLog(link.from, link.to, false);" in CONSOLE_JS


# --- 7. "What this is", saved per account -----------------------------------------

def test_the_explainer_choice_is_saved_on_the_account_not_the_device(api):
    owner = _sign_up(api)
    me = api.get("/api/accounts/me", headers=owner)
    assert me.status_code == 200 and me.json()["prefs"] == {}
    saved = api.post("/api/accounts/me/prefs", headers=owner, json={"today_intro": "closed"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["prefs"] == {"today_intro": "closed"}
    # Another device: a fresh sign-in sees the same choice.
    other = api.post("/api/accounts/login", json={"email": "rosa@cafe.example",
                                                  "password": "flour-and-butter-22"})
    assert other.status_code == 200, other.text
    there = {"Authorization": f"Bearer {other.json()['token']}"}
    assert api.get("/api/accounts/me", headers=there).json()["prefs"] == {"today_intro": "closed"}
    assert api.post("/api/accounts/me/prefs", headers=there,
                    json={"today_intro": "open"}).json()["prefs"] == {"today_intro": "open"}
    assert api.get("/api/accounts/me", headers=owner).json()["prefs"] == {"today_intro": "open"}
    # Kept on disk with the sign-in.
    person = next(u for u in STORE._users.values() if u.email == "rosa@cafe.example")
    assert STORE._db.get("user", person.user_id)["prefs"] == {"today_intro": "open"}


def test_only_known_settings_and_only_for_somebody_signed_in(api):
    owner = _sign_up(api)
    assert api.post("/api/accounts/me/prefs", headers=owner,
                    json={"today_intro": "sideways"}).status_code == 422
    assert api.post("/api/accounts/me/prefs", headers=owner,
                    json={"theme": "pink"}).status_code == 422
    assert api.post("/api/accounts/me/prefs", json={"today_intro": "open"}).status_code in (401, 403)


# --- 8. a toast is never on the sensor key -----------------------------------------

def test_a_toast_moves_off_the_new_sensor_key():
    assert re.search(r'<div id="issued-key"[^>]* data-toast-avoid', CONSOLE_HTML)
    place = _function(CONSOLE_JS, "placeToasts")
    assert "[data-toast-avoid]" in place and "TOAST_SPOTS" in place
    assert 'const TOAST_SPOTS = ["toasts-top", "toasts-left", "toasts-top-left"];' in CONSOLE_JS
    assert "placeToasts(box);" in _function(CONSOLE_JS, "toast")
    assert "#toasts.toasts-top, #toasts.toasts-top-left { top: 76px; bottom: auto; }" in THEME


# --- the pages, run in Node ------------------------------------------------------

def test_today_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here (CI has it)")
    for suite in ("today.test.js", "words.test.js"):
        result = subprocess.run([node, "--test", "--test-reporter=tap", str(ROOT / "tests/js" / suite)],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "# fail 0" in result.stdout, suite
