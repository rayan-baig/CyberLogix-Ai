"""Round 14 (3.37.0): dates on times that are not today, a way out of the
note of what was thrown away, a calmer console on a phone, a pack that
says what happened once and plainly, and a demo that agrees with itself.

Pilot's matching (a price for a number, the night, the Wi-Fi dropping, a
metal walk-in, and every miss answered with the two closest answers) is
in tests/js/kryozyn-pilot.test.js; the clock in tests/js/words.test.js.
The browser checks at a phone's width are at the end of this file.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import claims
import console_api
import demo_account
import food_value
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
THEME = (STATIC / "theme.css").read_text()
ANSWER_JS = (STATIC / "answer.js").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
WORDS_JS = (STATIC / "words.js").read_text()
PILOT_JS = (STATIC / "pilot-kryozyn.js").read_text()
SAMPLE = (STATIC / "sample-pack.html").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _sign_up(api, email="maya@bakery.example"):
    made = api.post("/api/signup", json={
        "company_name": "Maya's Bakery", "full_name": "Maya Okafor", "email": email,
        "password": "croissant-dough-31", "contact_phone": "+15550104477",
        "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _unit(api, owner):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": "WALK-IN-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in fridge", "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    assert api.post("/api/licenses/me/sensors/WALK-IN-1/alert-rule", headers=owner,
                    json={"alert_after_minutes": 0}).status_code == 200
    key = made.json()["ingest_key"]

    def pulse(temp):
        got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                       json={"sensor_id": "WALK-IN-1", "temperature_fahrenheit": temp})
        assert got.status_code == 200, got.text
        return got.json()
    return pulse


# --- 2. a date on any time that is not today --------------------------------------------


def test_the_clock_says_the_day_whenever_it_is_not_today():
    clock = WORDS_JS[WORDS_JS.index("  function clock(iso)"):WORDS_JS.index("\n  }\n", WORDS_JS.index("  function clock(iso)"))]
    assert 'weekday: "short"' in clock and 'month: "short", day: "numeric"' in clock
    assert "if (days === 0) return timeOf(d);" in clock
    # Pilot's own "today or else the date" is the clock's now.
    assert "const saidAt = (iso) => clock(iso);" in PILOT_JS
    assert "20 * 3600000" not in PILOT_JS


# --- 3. a way out of the note -------------------------------------------------------------


def test_the_note_of_what_was_thrown_away_has_back_or_cancel_and_the_backdrop_closes_it():
    assert 'id: "loss-back", text: "Cancel"' in ANSWER_JS
    assert '[save, back]' in ANSWER_JS
    assert 'd.querySelector("#loss-back").textContent = fromActs ? "Back" : "Cancel";' in ANSWER_JS
    assert "if (event.target !== lossDialog) return;" in ANSWER_JS
    # Opened from "What next?" it goes back there; from the alert, it closes.
    assert 'if (lossDialog.dataset.from === "acts") showActs(lossDialog);' in ANSWER_JS


# --- 4. a calmer console ----------------------------------------------------------------


def test_readings_in_range_never_round_up_to_a_hundred_with_a_reading_out():
    assert console_api.in_range_percent(2000, 1) == 99.9          # 99.95 rounded up
    assert console_api.in_range_percent(20000, 3) == 99.9
    assert console_api.in_range_percent(1000, 0) == 100.0
    assert console_api.in_range_percent(1000, 0, live=True) == 99.9  # an alert open now
    assert console_api.in_range_percent(100, 10) == 90.0
    assert console_api.in_range_percent(0, 0) is None


def test_the_tile_says_what_is_out_now_beside_the_figure(api):
    owner = _sign_up(api)
    pulse = _unit(api, owner)
    for _ in range(30):
        pulse(37.5)
    pulse(62.0)
    s = api.get("/api/console/overview", headers=owner).json()["summary"]
    assert s["open_incidents"] == 1 and s["compliance_percent"] < 100
    note = _function(CONSOLE_JS, "inRangeNote")
    assert 'open now`' in note and "out of range now" in note
    assert "s.compliance_ready ? inRangeNote(s)" in CONSOLE_JS


def test_the_notices_are_one_dismissible_line_and_getting_started_one_line_once_readings_arrive():
    line = CONSOLE_HTML[CONSOLE_HTML.index('<div id="notice-line"'):CONSOLE_HTML.index('<div id="offline-banner"')]
    for part in ('id="notice-words"', 'id="notice-more"', 'id="notice-close"', 'id="role-banner"',
                 'id="delivery-banner"', 'id="terms-banner"', '<div id="notices" class="notices folded">'):
        assert part in line, part
    merge = _function(CONSOLE_JS, "mergeNotices")
    assert 'const away = !shown.length || (key === gone && !state.noticesOpen);' in merge
    assert ".notices.folded > .banner { display: none; }" in THEME
    first = _function(CONSOLE_JS, "renderFirstDay")
    assert 'step.key === "reading" && step.done' in first and '"first-day-line"' in first
    assert ".first-day-line .card-plain, .first-day-line .checklist" in THEME
    chip = _function(CONSOLE_JS, "companyChip")
    assert 'words.className = "chip-name"' in chip
    assert "#chip-company .chip-name { min-width: 0; overflow: hidden; text-overflow: ellipsis;" in THEME


# --- 5. the pack ----------------------------------------------------------------------


def test_closed_by_itself_after_someone_said_fixed_is_said_so_never_closed_by_them(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    pulse = _unit(api, owner)
    pulse(37.5)
    incident_id = pulse(62.0)["incident_id"]
    said = api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={})
    assert said.status_code == 409, said.text            # still warm: "It's fixed" noted
    pulse(38.2)                                           # back: closes by itself
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_at and incident.resolved_note and incident.resolved_by  # theirs, in the log
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    closed = [e for e in pack["response"]["timeline"] if e["event"].startswith("Closed")]
    assert len(closed) == 1 and closed[0]["event"] == "Closed by itself"
    assert re.fullmatch(r"Maya Okafor said it was fixed at \d{1,2}:\d\d [AP]M; it closed by itself at "
                        r"\d{1,2}:\d\d [AP]M, back to 38\.2°F\.", closed[0]["detail"]), closed[0]["detail"]
    assert "Closed by Maya" not in str(pack)
    assert pack["event"]["closed_by_itself"] is True
    letter = pack["cover_letter"]
    assert re.search(r"Maya Okafor said it was fixed at [^;]+; the alert closed by itself at [^,]+, back to 38\.2°F\.", letter), letter


def test_the_summary_is_three_short_paragraphs_about_the_unit(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    pulse = _unit(api, owner)
    pulse(37.5)
    incident_id = pulse(62.0)["incident_id"]
    letter = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()["cover_letter"]
    paras = letter.split("\n\n")
    assert len(paras) == 3, letter
    assert paras[0].startswith("Maya's Bakery reports that the Walk-in fridge went out of its safe range at ")
    assert "probable cause" in paras[0]
    assert "texted" in paras[1] or "text" in paras[1]
    assert paras[2].startswith("Continuous monitoring recorded ") and paras[2].endswith("independently verifiable.")
    assert claims.unit_phrase("Walk-in fridge") == "the Walk-in fridge"
    assert claims.unit_phrase("The bar fridge") == "The bar fridge"
    # The console shows each paragraph as one.
    packet = _function(CONSOLE_JS, "openPacket")
    assert "split(/\\n\\s*\\n/)" in packet and "<p>${esc(para)}</p>" in packet


def test_thrown_away_is_said_once_and_the_texts_table_is_who_was_told(api, break_gemini):
    break_gemini("missing")
    assert claims.note_repeats("Thrown away", "Thrown away from Walk-in fridge: two trays")
    assert claims.note_repeats("Thrown away", "thrown away")
    assert not claims.note_repeats("Thrown away", "Two trays of chicken")
    assert not claims.note_repeats("Thrown away", "Thrown awayish")
    packet = _function(CONSOLE_JS, "openPacket")
    assert "esc(repeats ? food.note : food.answer)" in packet
    assert "food.note && !repeats" in packet
    assert '<div class="doc-sub">Who was told</div>\n    <table class="doc-channels">' in packet
    assert '<div class="doc-sub">Who was told</div>\n    <table class="doc-channels">' in SAMPLE
    # New notes no longer start with "Thrown away".
    assert 'return "From the " + unitNameFor(i.sensor_id)' in CONSOLE_JS
    assert 'return "From the " + nameOf(unit)' in SIMPLE_JS


def test_every_box_of_the_pack_prints_whole():
    printing = THEME[THEME.index("@media print {"):]
    assert ".readout, .doc-letter p, .timeline li { break-inside: avoid; }" in printing
    sample_css = (STATIC / "sample-pack.css").read_text()
    assert ".sample-doc .readout, .sample-doc .doc-letter p, .sample-doc .timeline li { break-inside: avoid; }" in sample_css


# --- 6. the demo, /today's top alert, Canada ---------------------------------------------


def test_the_demo_records_the_kept_answer_its_note_gives():
    made = demo_account.seed(password="demo-password-for-tests")
    handled = next(i for i in STORE.incidents_for(made["tenant_id"]) if i.sensor_id == demo_account.HANDLED)
    assert handled.corrective_action.endswith("Kept.")
    assert handled.product_disposition == "unaffected"
    assert handled.food_answered_at == handled.opened_at + timedelta(minutes=50)
    assert utc_now() - handled.food_answered_at > timedelta(days=2)
    sensor = STORE.get_sensor(handled.sensor_id)
    assert food_value.question(handled, sensor) is None, "nobody is asked about food already answered"


def test_todays_top_alert_names_the_site_on_an_account_with_several():
    which = _function(SIMPLE_JS, "whichOne")
    assert "const sites = new Set(all.map((other) => other.site_name).filter(Boolean)).size;" in which
    assert "(shared || sites > 1) && unit.site_name" in which


def test_the_canada_answer_has_no_provider_jargon():
    canada = PILOT_JS[PILOT_JS.index("const CANADA = () => {"):PILOT_JS.index("// When it was said")]
    assert "Twilio" not in canada
    assert "reach any mobile number written with its country code" in canada


# --- in a real browser, at a phone's width ------------------------------------------------

# The real server and browser of the round-13 checks, shared here.
from test_round13_dialogs_fit import Cafe, _page, browser, server  # noqa: E402

FIXTURES = (browser, server)


def test_the_note_closes_with_back_cancel_the_backdrop_and_escape(browser, server):
    cafe = Cafe(server)
    incident = cafe.warm()
    assert cafe.post(f"/api/voice/acknowledge/{incident}", {}, cafe.auth).status_code == 200
    cafe.pulse(38.0)
    ctx, page = _page(browser, server, 375, 812, cafe.token)
    try:
        page.goto(server + "/today")
        page.locator("[data-fixed]").first.click(timeout=15000)
        page.click("#food-thrown")
        page.wait_for_selector("#loss-dialog[open] #loss-acts:not([hidden])")
        page.click("#loss-note")
        assert page.locator("#loss-back").inner_text() == "Back"
        page.click("#loss-back")
        assert page.is_visible("#loss-acts") and not page.is_visible("#loss-write")
        assert page.evaluate("document.getElementById('loss-dialog').open")
        # A tap on the dimmed page around it.
        page.mouse.click(4, 4)
        page.wait_for_timeout(150)
        assert not page.evaluate("document.getElementById('loss-dialog').open")
        # From the alert's own "Add what was thrown away": Cancel, then Escape.
        page.wait_for_selector("[data-loss-note]", timeout=15000)
        page.locator("[data-loss-note]").first.click()
        page.wait_for_selector("#loss-dialog[open] #loss-write:not([hidden])")
        assert page.locator("#loss-back").inner_text() == "Cancel"
        assert page.input_value("#loss-text").startswith("From the Walk-in fridge")
        page.click("#loss-back")
        assert not page.evaluate("document.getElementById('loss-dialog').open")
        page.locator("[data-loss-note]").first.click()
        page.wait_for_selector("#loss-dialog[open]")
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        assert not page.evaluate("document.getElementById('loss-dialog').open")
        # A tap inside the box never closes it.
        page.locator("[data-loss-note]").first.click()
        page.wait_for_selector("#loss-dialog[open]")
        page.click("#loss-title")
        assert page.evaluate("document.getElementById('loss-dialog').open")
    finally:
        ctx.close()


def test_a_returning_phone_console_is_calm(browser, server):
    cafe = Cafe(server)
    long_name = cafe.post("/api/signup", {
        "company_name": "Juniper and Vine Kitchens Harbourside", "full_name": "Sam Lee",
        "email": f"sam{utc_now().timestamp()}@cafe.example", "password": "espresso-shots-44",
        "contact_phone": "+15550103300", "industry_vertical": "restaurant"})
    assert long_name.status_code == 201, long_name.text
    token = long_name.json()["token"]
    auth = {"Authorization": "Bearer " + token}
    added = cafe.post("/api/licenses/me/sensors", {"sensor_id": "LONG-1", "industry_vertical": "restaurant",
                                                   "location_name": "Walk-in fridge", "unit_kind": "fridge"}, auth)
    assert added.status_code == 201, added.text
    got = cafe.post("/api/sensor-pulse", {"sensor_id": "LONG-1", "temperature_fahrenheit": 37.4},
                    {"X-CyberLogix-Sensor-Key": added.json()["ingest_key"]})
    assert got.status_code < 300, got.text
    ctx, page = _page(browser, server, 375, 812, token)
    try:
        page.goto(server + "/console")
        page.wait_for_selector("#notice-line:not([hidden])", timeout=15000)
        # Texts are off on a test server: one line, the box itself folded.
        assert "Texts are off" in page.inner_text("#notice-words")
        assert not page.is_visible("#delivery-banner")
        page.click("#notice-more")
        assert page.is_visible("#delivery-banner")
        page.click("#notice-close")
        assert not page.is_visible("#notice-line") and not page.is_visible("#delivery-banner")
        page.reload()
        page.wait_for_selector("#chip-company .chip-name", timeout=15000)
        page.wait_for_timeout(800)
        assert not page.is_visible("#notice-line"), "dismissed stays dismissed until it changes"
        # Getting started: one line once a reading has arrived.
        page.wait_for_selector("#first-day:not([hidden])")
        assert "first-day-line" in page.get_attribute("#first-day", "class")
        assert "next:" in page.inner_text("#first-day-note")
        assert not page.is_visible("#first-day-list")
        # One line: the card no taller than its heading row and padding.
        assert page.evaluate("document.getElementById('first-day').getBoundingClientRect().height") < 64
        page.click("#first-day-more")
        assert page.is_visible("#first-day-list")
        # The name cut with an ellipsis inside the chip, the caret kept.
        cut = page.evaluate("""() => { const n = document.querySelector('#chip-company .chip-name');
          const c = document.getElementById('chip-company').getBoundingClientRect();
          const caret = document.querySelector('#chip-company .chip-caret').getBoundingClientRect();
          return { over: n.scrollWidth > n.clientWidth, ellipsis: getComputedStyle(n).textOverflow,
                   caretIn: caret.right <= c.right + 0.5 && caret.width > 0 }; }""")
        assert cut == {"over": True, "ellipsis": "ellipsis", "caretIn": True}, cut
    finally:
        ctx.close()
