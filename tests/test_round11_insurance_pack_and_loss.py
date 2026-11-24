"""Round 11 (3.33.0): the insurance pack around the food, the loss flow
seen through, Pilot's new answers, the front page's picture, plurals.

1. The pack has "What happened to the food" (the answer, who, when, the
   saved note, every change); "closed at ..." once the alert closed, never
   "remains open"; dates in words, in the reader's clock; an evidence
   window that ends when the pack is issued; "Walk-in fridge", never
   "Walk-in fridge (walk-in)"; the verifier's instructions under a small
   "For the adjuster's IT team".
2. The note shows under the food's answer (card, history, /today, pack);
   saving it with the same answer adds no "Changed to Thrown away" and
   leaves the answer's time alone; for a day after a loss /today leads
   with "Food thrown away from Walk-in fridge: make the insurance pack"
   (Node: tests/js/today.test.js).
3. Pilot (Node: tests/js/kryozyn-pilot.test.js).
4. The front page shows a real /today on a phone beside "See it live".
5. Plurals everywhere; the Proof list by unit name; no "probably the door"
   after a reading far past the limit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import claims
import wording
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _sign_up(api, vertical="restaurant", email="maya@bakery.example"):
    made = api.post("/api/signup", json={
        "company_name": "Maya's Bakery", "full_name": "Maya Okafor", "email": email,
        "password": "croissant-dough-31", "contact_phone": "+15550104477",
        "industry_vertical": vertical})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _unit(api, owner, sensor_id="WALK-IN-1", where="Walk-in fridge"):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": sensor_id, "industry_vertical": "restaurant",
        "location_name": where, "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    rule = api.post(f"/api/licenses/me/sensors/{sensor_id}/alert-rule", headers=owner,
                    json={"alert_after_minutes": 0})
    assert rule.status_code == 200, rule.text
    return made.json()["ingest_key"]


def _pulse(api, key, temp, sensor_id="WALK-IN-1"):
    got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert got.status_code == 200, got.text
    return got.json()


def _blip(api, key, warm=48.5, minutes=None, sensor_id="WALK-IN-1"):
    _pulse(api, key, 37.5, sensor_id)
    incident_id = _pulse(api, key, warm, sensor_id)["incident_id"]
    assert incident_id
    if minutes is not None:
        incident = STORE.get_incident(incident_id)
        incident.opened_at = incident.out_since = utc_now() - timedelta(minutes=minutes)
        STORE._save_incident(incident)
    _pulse(api, key, 37.9, sensor_id)
    return incident_id


def _overview(api, owner):
    return api.get("/api/console/overview", headers=owner).json()


def _thrown_with_note(api, owner, key, note="Thrown away from Walk-in fridge: two trays of chicken"):
    incident_id = _blip(api, key, warm=62.5, minutes=40)
    assert api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"}).status_code == 200
    if note:
        saved = api.post(f"/api/voice/corrective-action/{incident_id}", headers=owner,
                         json={"action": note, "product_disposition": "discarded"})
        assert saved.status_code == 200, saved.text
    return incident_id


# --- 1. the pack around the food ----------------------------------------------------

def test_the_pack_says_what_happened_to_the_food(api, break_gemini):
    break_gemini("missing")  # the plain letter, not an AI's
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _thrown_with_note(api, owner, key)
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    food = pack["food"]
    assert food["answer"] == "Thrown away" and food["thrown"] is True
    assert food["by"] == "Maya Okafor", "the whole name, never the address"
    assert food["at"] and food["note"] == "Thrown away from Walk-in fridge: two trays of chicken"
    assert food["note_by"] == "Maya Okafor" and food["note_at"]
    assert [(h["words"], h["changed"]) for h in food["history"]] == [("Thrown away", False)]
    letter = pack["cover_letter"]
    assert "The food was recorded as \u201cThrown away\u201d by Maya Okafor at " in letter
    assert "The note of what went: \u201cThrown away from Walk-in fridge: two trays of chicken\u201d." in letter
    # Nothing answered yet: said so, in the letter and the section.
    other = _blip(api, key, warm=62.5, minutes=40)
    bare = api.post(f"/api/claims/{other}/packet", headers=owner).json()
    assert bare["food"]["answer"] is None and bare["food"]["history"] == []
    assert "Nobody had recorded what happened to the food when this pack was issued." in bare["cover_letter"]
    # The console draws the section, with the note and the change history.
    packet = _function(CONSOLE_JS, "openPacket")
    assert '<div class="doc-section">What happened to the food</div>' in packet
    assert "Change history" in packet and "What went" in packet


def test_a_closed_alert_says_closed_at_never_remains_open(api, break_gemini):
    break_gemini("missing")  # the plain letter, not an AI's
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, key, minutes=12)       # back in range by itself
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    assert pack["event"]["state"] == "resolved" and pack["event"]["closed_by_itself"] is True
    letter = pack["cover_letter"]
    assert "remains open" not in letter and "still open" not in letter
    # 3.36.0: the date and the zone once, on when it went out; the same
    # night's later times as a clock ("4:41 AM").
    assert re.search(r"The alert closed at (?:[A-Z][a-z]{2} \d{1,2}, \d{4}, )?\d{1,2}:\d\d [AP]M(?: UTC)?, when the unit "
                     r"came back in range by itself\.", letter), letter
    # Open, it says so.
    _pulse(api, key, 37.5)
    live = _pulse(api, key, 50.0)["incident_id"]
    open_pack = api.post(f"/api/claims/{live}/packet", headers=owner).json()
    assert "The alert was still open when this pack was issued." in open_pack["cover_letter"]
    assert "Closed" in [e["event"] for e in pack["response"]["timeline"]]
    # 3.36.0: "Closed at 4:41 AM", the key saying it once.
    assert 'const closedKey = ev.resolved_at ? "Closed at" : "Closed";' in _function(CONSOLE_JS, "openPacket")


def test_dates_in_words_in_the_readers_clock_and_plurals(api, break_gemini):
    break_gemini("missing")  # the plain letter, not an AI's
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, key, minutes=12)
    pack = api.post(f"/api/claims/{incident_id}/packet?tz=America/New_York", headers=owner).json()
    letter = pack["cover_letter"]
    assert not ISO.search(letter), letter
    assert re.search(r"went out of its safe range at [A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d\d [AP]M E[SD]T\.", letter), letter
    assert "Continuous monitoring recorded 3 readings from " in letter
    assert pack["clock"] == "America/New_York"
    # An unknown zone is UTC, never an error.
    assert api.post(f"/api/claims/{incident_id}/packet?tz=Mars/Olympus", headers=owner).status_code == 200
    # The words and the plurals themselves.
    moment = datetime(2026, 11, 23, 1, 58, 35, tzinfo=timezone.utc)
    assert claims.when_words(moment) == "Nov 23, 2026, 1:58 AM UTC"
    assert claims.when_words(moment, claims.reader_zone("America/New_York")) == "Nov 22, 2026, 8:58 PM EST"
    assert claims.plural(1, "reading") == "1 reading" and claims.plural(6, "reading") == "6 readings"
    assert claims._outside(1, 1) == "it was outside" and claims._outside(6, 1) == "1 of them was outside"
    assert claims._outside(6, 3) == "3 of them were outside" and claims._outside(6, 0) == "none of them were outside"


def test_the_evidence_window_ends_when_the_pack_is_issued(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, key, minutes=12)
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    end = datetime.fromisoformat(pack["evidence"]["window_end"].replace("Z", "+00:00"))
    issued = datetime.fromisoformat(pack["issued_at"].replace("Z", "+00:00"))
    assert end <= issued, (end, issued)
    # An old alert keeps its 12 hours after it closed.
    incident = STORE.get_incident(incident_id)
    incident.resolved_at = utc_now() - timedelta(days=3)
    incident.opened_at = incident.out_since = incident.resolved_at - timedelta(minutes=12)
    STORE._save_incident(incident)
    old = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    end = datetime.fromisoformat(old["evidence"]["window_end"].replace("Z", "+00:00"))
    assert abs((end - incident.resolved_at).total_seconds() - 12 * 3600) < 2


def test_the_unit_is_named_once_and_the_verifier_is_for_the_it_team(api, break_gemini):
    break_gemini("missing")  # the plain letter, not an AI's
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _blip(api, key, minutes=12)
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    assert pack["location"]["asset_label"] == "Walk-in fridge"
    assert "(walk-in)" not in pack["cover_letter"]
    assert claims.asset_label("Unit 4", "walk-in") == "Unit 4 (walk-in)"
    assert claims.asset_label("Bar Walk In", "walk-in") == "Bar Walk In"
    assert pack["verification_heading"] == "For the adjuster's IT team"
    packet = _function(CONSOLE_JS, "openPacket")
    assert "esc(loc.asset)} (${esc(loc.asset_type)})" not in packet
    assert "loc.asset_label || loc.asset" in packet
    assert '<div class="doc-it">' in packet and '<div class="doc-section">Verification</div>' not in packet
    assert packet.index("doc-it") > packet.index("Prior incidents on this asset")
    # The browser sends its own clock.
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in packet
    theme = (STATIC / "theme.css").read_text()
    assert ".doc-it {" in theme and ".doc-it-head {" in theme


# --- 2. the loss flow seen through --------------------------------------------------

def test_saving_the_note_with_the_same_answer_changes_nothing_about_the_answer(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _thrown_with_note(api, owner, key, note=None)
    before = STORE.get_incident(incident_id)
    answered_at = before.food_answered_at
    saved = api.post(f"/api/voice/corrective-action/{incident_id}", headers=owner,
                     json={"action": "Thrown away from Walk-in fridge: 20 lb chicken",
                           "product_disposition": "discarded"})
    assert saved.status_code == 200, saved.text
    after = STORE.get_incident(incident_id)
    assert after.food_answered_at == answered_at, "its 24 hours don't start over"
    assert [h["disposition"] for h in after.food_history] == ["discarded"], "no 'Changed to Thrown away'"
    row = next(i for i in _overview(api, owner)["incidents"] if i["incident_id"] == incident_id)
    assert [h["words"] for h in row["food_history"]] == ["Thrown away"]
    assert row["corrective_action"] == "Thrown away from Walk-in fridge: 20 lb chicken"
    assert row["corrective_action_by_name"] == "Maya"
    # Pressing "Thrown away" again: nothing changes either.
    again = api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"})
    assert again.status_code == 200 and again.json()["changed"] is False
    assert again.json()["message"] == "Already recorded: food thrown away."
    assert len(STORE.get_incident(incident_id).food_history) == 1
    # A real change is still a change, and the note doesn't undo it.
    assert api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "kept"}).json()["changed"] is True
    assert len(STORE.get_incident(incident_id).food_history) == 2


def test_the_note_shows_under_the_food_line_everywhere(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    incident_id = _thrown_with_note(api, owner, key)
    answer = _overview(api, owner)["this_month"]["food_answers"][0]
    assert answer["note"] == "Thrown away from Walk-in fridge: two trays of chicken"
    # The console's card and the alert's history.
    card = _function(CONSOLE_JS, "foodAnswered")
    assert 'i.corrective_action ? `<span class="sub food-note">' in card
    assert "Note: \u201c${i.corrective_action}\u201d" in CONSOLE_JS
    # /today (tests/js/today.test.js renders it).
    assert 'class="answer-note"' in (STATIC / "simple.js").read_text()
    assert incident_id


def test_for_a_day_after_a_loss_today_leads_with_the_pack(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    assert _overview(api, owner)["recent_loss"] is None
    incident_id = _thrown_with_note(api, owner, key, note=None)
    loss = _overview(api, owner)["recent_loss"]
    assert loss["incident_id"] == incident_id and loss["unit"] == "Walk-in fridge"
    assert loss["by_name"] == "Maya" and loss["note"] is None and loss["others"] == 0
    # Kept instead: no loss.
    api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "kept"})
    assert _overview(api, owner)["recent_loss"] is None
    api.post(f"/api/voice/food/{incident_id}", headers=owner, json={"food": "thrown"})
    assert _overview(api, owner)["recent_loss"]["incident_id"] == incident_id
    # A day on, the page is calm again.
    incident = STORE.get_incident(incident_id)
    incident.food_answered_at = utc_now() - timedelta(hours=25)
    STORE._save_incident(incident)
    assert _overview(api, owner)["recent_loss"] is None
    simple = (STATIC / "simple.js").read_text()
    assert "Food thrown away from ${esc(loss.unit)}: make the insurance pack" in simple
    assert ".verdict.calm" in (STATIC / "simple.css").read_text()


# --- 4. the front page's picture -------------------------------------------------------

def test_the_front_page_shows_a_real_phone_screenshot_beside_see_it_live(api):
    html = api.get("/").text
    shot = re.search(r'<img[^>]+src="(/static/hero-today[^"]*\.webp)[^"]*"[^>]*>', html)
    assert shot, "the picture is on the page"
    tag = shot.group(0)
    assert 'alt="' in tag and "example" in tag.lower()
    assert 'width="' in tag and 'height="' in tag, "no jump as it loads"
    assert "style=" not in tag
    path = STATIC / shot.group(1).split("/static/", 1)[1].split("?", 1)[0]
    assert path.exists() and path.stat().st_size < 60_000, "small"
    assert path.read_bytes()[:4] == b"RIFF" and path.read_bytes()[8:12] == b"WEBP"
    assert re.search(r">\s*Example[^<]*<", html), "labelled as an example"
    # Right after "See it live", inside the first section.
    hero = html[html.index('<section class="hero">'):]
    hero = hero[:hero.index("</section>")]
    assert hero.index('href="/demo">See it live</a>') < hero.index("hero-today")
    assert ".hero-shot" in (STATIC / "landing.css").read_text()


# --- 5. plurals, the Proof list, the door ----------------------------------------------

def test_the_proof_list_names_the_unit_and_counts_properly(api):
    owner = _sign_up(api)
    key = _unit(api, owner)
    _blip(api, key, minutes=12)
    got = api.get("/api/claims/eligible", headers=owner).json()
    row = got["incidents"][0]
    assert row["unit"] == "Walk-in fridge" and row["food"] == "Food safe: out for 12 minutes"
    render = _function(CONSOLE_JS, "renderClaims")
    assert "alert(s)" not in render and 'CLXWords.count(eligible.count, "alert")' in render
    assert '<span class="fc-name">${esc(i.unit || i.sensor_id)}</span>' in render
    assert "fc-id" not in render


def test_no_naive_plurals_left_on_the_customer_pages():
    for name in ("console.js", "simple.js", "proof.js"):
        source = (STATIC / name).read_text()
        assert not re.search(r"\b(alert|reading|sensor|incident|excursion|unit|day|minute|asset|report|hour)\(s\)", source), name
    simple = (STATIC / "simple.js").read_text()
    assert "readings checked" not in simple.replace('CLXWords.count(f.readings, "reading")} checked', "")


def test_the_week_summary_counts_properly():
    import didforyou

    figures = dict(didforyou.SAMPLE, units=1, readings=1, alerts=1, answered=1, by_itself=0,
                   typical_answer_minutes=1.0, fastest_answer_minutes=1.0, texts=1, calls=0,
                   food_kept=1, food_auto_safe=0, in_range_percent=100.0)
    lines = didforyou.lines(figures)
    assert lines[0] == "Checked 1 reading from 1 fridge or freezer, 100% in range."
    assert "1 alert, 1 answered in 1 minute." in lines
    assert "1 alert text delivered, 0 phone calls." in lines
    assert "Food: 1 kept." in lines


def test_no_door_guess_after_a_reading_far_past_the_limit(api):
    assert "probably the door" in wording.back_to_normal_note(3, over_by_f=4.0)
    far = wording.back_to_normal_note(3, over_by_f=21.5)
    assert "door" not in far and "far past its limit" in far
    assert "probably the door" in wording.back_to_normal_note(3)
    owner = _sign_up(api)
    key = _unit(api, owner)
    mild = _blip(api, key, warm=48.5, minutes=3)
    assert "probably the door" in STORE.get_incident(mild).resolved_note
    severe = _blip(api, key, warm=62.5, minutes=3)
    note = STORE.get_incident(severe).resolved_note
    assert "door" not in note and "far past its limit" in note, note


def test_pilot_quotes_the_cancellation_rule_checkout_shows():
    import main
    from pricing import CANCELLATION_RULE

    assert main.pilot_facts()["cancel-rule"] == CANCELLATION_RULE
    assert CANCELLATION_RULE in (STATIC / "pilot-kryozyn.js").read_text()


@pytest.mark.parametrize("suite", ["today.test.js", "kryozyn-pilot.test.js"])
def test_the_pages_in_node(suite):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    run = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / suite)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
