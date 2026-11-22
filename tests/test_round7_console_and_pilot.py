"""Round 7 (3.26.0): a calmer console for a small café, honest words, and a
value line on a closed alert.

1. Pilot's robot never sits on the logo (tests/js/cyberlogix-pilot.test.js
   runs the core's own placement on a new business's phone screen).
2. Simple tabs: one or two fridges, three tabs and More, no search; one
   scrolling row on a phone, not pinned; the account bar in two rows.
3. The operator's tools are out of the customer console; server settings
   are never named to a customer; the partner extra is not sold during
   setup; the activity log says what was agreed to in words.
4. A tab with nothing in one column gives the other the width.
5. Three visitor pills work without a key (the Node suite; core links
   since 3.27.0).
6. Texts off: a test says it was recorded, not texted; after "Received!"
   the wizard offers the next step.
7. "Food protected: about $X", an estimate, from food_value.py.
8. Small things: minutes not seconds, "insurance packs", a sensor's reply
   always has words, TV screen, the trades list.
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
from ratelimit import wait_words
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
THEME = (STATIC / "theme.css").read_text()
PILOT_APP = (STATIC / "pilot-cyberlogix.js").read_text()


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
    got = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": key},
                   json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert got.status_code == 200, got.text
    return got.json()


# --- 1. the robot and the logo ---------------------------------------------------

def test_the_robot_avoids_only_the_checklist_buttons_and_no_header_bar():
    """3.26.0 listed every header bar; core 1.4.0 (3.27.0) keeps the robot
    out of a phone's top corners and off links itself, so the bars are no
    longer listed (tests/js/cyberlogix-pilot.test.js runs the placement)."""
    avoid = PILOT_APP[PILOT_APP.index("avoid: ["):PILOT_APP.index("],", PILOT_APP.index("avoid: ["))]
    for sel in (".checklist-acts", ".btn-go"):
        assert f'"{sel}"' in avoid, sel
    for sel in (".top", ".demo-top", "#app > .nav", "#first-day"):
        assert f'"{sel}"' not in avoid, sel


def test_the_core_stays_untouched():
    """The fix is the app's config; the core is copied from MTRX Horizyn."""
    out = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", "static/pilot/"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    assert out.stdout.strip() == ""


# --- 2. simple tabs -----------------------------------------------------------

def test_a_small_account_with_one_or_two_fridges_gets_three_tabs_and_more():
    assert "const SIMPLE_TABS_BELOW = 3;" in CONSOLE_JS
    assert 'const SIMPLE_TABS = new Set(["fridges", "alerts", "people"]);' in CONSOLE_JS
    simple = _function(CONSOLE_JS, "simpleTabs")
    assert "smallAccount(d)" in simple and "finder.expanded" in simple and "state.wall" in simple
    count = _function(CONSOLE_JS, "countTheTabs")
    assert "(simpleTabs() && !SIMPLE_TABS.has(key))" in count
    finder = _function(CONSOLE_JS, "applyFinder")
    assert '$("finder-search").hidden = simple;' in finder
    assert '$("tabs-more").hidden = !simple;' in finder
    assert '<button class="f-more" id="tabs-more" type="button" hidden>More</button>' in CONSOLE_HTML
    assert 'data-tab="people" aria-pressed="false" type="button">Team</button>' in CONSOLE_HTML
    # More shows every tab and the search for the rest of the visit.
    assert 'sessionStorage.setItem("cyberlogix.tabs", "all")' in CONSOLE_JS


def test_day_one_opens_on_fridges_or_alerts_not_everything():
    land = _function(CONSOLE_JS, "landOnATab")
    assert "(dayOne(d) && !simpleTabs(d))" in land
    assert 'finder.tab = live ? "alerts" : "fridges";' in land
    # A checklist button keeps the tab and shows its card there.
    go = _function(CONSOLE_JS, "goTo")
    assert 'if (!simpleTabs()) finder.tab = "all";' in go
    assert '(finder.tab === "all" || simpleTabs()) && finder.reveal.has(card.id)' in CONSOLE_JS


def test_on_a_phone_the_tabs_are_one_scrolling_row_and_not_pinned():
    phone = THEME[THEME.index("Its tabs are one row that scrolls sideways"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".finder { top: 0; position: relative; }" in phone
    assert "flex-wrap: nowrap; overflow-x: auto;" in phone
    assert ".f-tab, .f-more { flex: none; }" in phone


def test_on_a_phone_the_account_bar_is_two_rows():
    narrow = THEME[THEME.index("The account bar in two rows, not three"):]
    narrow = narrow[:narrow.index("}\n\n") if "}\n\n" in narrow[:2000] else 2000]
    assert "#wall-mode { display: none; }" in narrow
    assert "#chip-company { max-width: 9.5em;" in narrow


# --- 3. out of the customer console ---------------------------------------------

def test_the_operators_tools_are_not_in_the_customer_console():
    assert "find-enterprise-tier-calculator" not in CONSOLE_HTML
    assert "Volume matrix" not in CONSOLE_HTML
    assert "calculateBilling" not in CONSOLE_JS and "calc-branches" not in CONSOLE_JS
    assert "Simulate hardware pulse" not in CONSOLE_HTML
    assert 'id="byod-form"' not in CONSOLE_HTML and '$("byod-form")' not in CONSOLE_JS
    # The quote still works for whoever uses the API.
    assert "/api/v1/enterprise-billing" in (ROOT / "enterprise_billing.py").read_text()


def test_an_unpublished_comparison_is_not_shown():
    assert 'id="find-sector-standing" data-empty="1"' in CONSOLE_HTML
    bench = _function(CONSOLE_JS, "renderBenchmark")
    assert 'card.dataset.empty = body.available ? "" : "1";' in bench
    assert "esc(body.reason)" not in bench


def test_the_reason_reads_right_for_one_business(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, "W-1", "restaurant", "Walk-in")
    STORE.record_reading(STORE.get_sensor("W-1"), 38.0, None, False)
    body = api.get("/api/benchmarks/restaurant", headers=headers).json()
    assert body["available"] is False
    assert "Only 1 operator in this sector has reported" in body["reason"]


def test_no_server_setting_is_named_to_a_customer(api, operator_factory, monkeypatch):
    import alertkey

    monkeypatch.delenv(alertkey.SETTING, raising=False)
    assert alertkey.why_not() == ("Not switched on for this server yet. "
                                  "Ask your provider to switch this on.")
    js = _function(CONSOLE_JS, "renderOmni")
    assert "CYBERLOGIX_" not in js and "Ask your provider to switch this on." in js
    omni = (ROOT / "omniarmor.py").read_text()
    assert "CYBERLOGIX_CREDENTIAL_KEY is not set. Ask whoever runs it." not in omni
    mail_src = (ROOT / "mail.py").read_text()
    assert "(CYBERLOGIX_REMIT_TO or CYBERLOGIX_PAY_URL). Reply" not in mail_src


def test_the_partner_extra_is_not_sold_during_setup():
    line = _function(CONSOLE_JS, "omniPriceLine")
    assert 'if (!seen.connected && (trial || dayOne())) return "";' in line
    assert "${omniPriceLine(seen, price)}" in _function(CONSOLE_JS, "renderOmni")


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the page's own code")
def test_the_activity_log_says_what_was_agreed_to_in_words():
    script = "\n".join([
        CONSOLE_JS[CONSOLE_JS.index("const AGREEMENTS = {"):CONSOLE_JS.index("function renderAudit")],
        "const e = { action: 'legal.accepted', detail: 'Rosa Diaz accepted v2.9 at sign-up: "
        "acceptable-use@3b702ac19048, privacy@6ffc872c6b63, sla@9cd811d090fe, terms@a354d90401e1' };",
        "console.log(auditDetail(e));",
        "console.log(auditDetail({ action: 'sensor.added', detail: 'WALK-IN-1 added.' }));",
    ])
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    first, second = run.stdout.strip().splitlines()
    assert first == ("Agreed to the acceptable-use rules, the privacy notice, the service "
                     "levels and the subscription agreement (version 2.9) at sign-up.")
    assert "@" not in first
    assert second == "WALK-IN-1 added."
    # The exact record stays, on hover.
    assert '` title="${esc(e.detail)}"`' in CONSOLE_JS


def test_the_alerts_card_offers_slack_as_optional_and_never_says_nothing_connected():
    assert "Also send alerts to Slack or Teams (optional)" in CONSOLE_HTML
    hooks = _function(CONSOLE_JS, "renderHooks")
    assert "Nothing connected" not in hooks and "paintReach();" in hooks
    reach = _function(CONSOLE_JS, "paintReach")
    assert "state.textsOff" in reach and "We text the people under Who gets called" in reach


# --- 4. an even grid ------------------------------------------------------------

def test_a_tab_with_one_empty_column_gives_the_other_the_width():
    balance = _function(CONSOLE_JS, "balanceLayout")
    assert 'layout.classList.toggle("solo-main", main && !side);' in balance
    assert 'layout.classList.toggle("solo-side", side && !main);' in balance
    assert "balanceLayout();" in _function(CONSOLE_JS, "applyFinder")
    assert ".layout.solo-main, .layout.solo-side { grid-template-columns: minmax(0, 1fr); }" in THEME
    assert "grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));" in THEME
    assert ".pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));" in THEME


# --- 6. texts off ---------------------------------------------------------------

def test_with_texts_off_a_test_says_recorded_not_texted(api):
    owner = _sign_up(api)
    body = api.post("/api/contacts/test-alert", headers=owner).json()
    assert body["dry_run"] is True
    assert body["message"] == "Recorded, not texted: texts are off on this server."
    send = _function(CONSOLE_JS, "sendTestAlert")
    assert 'out.dry_run ? "Recorded, not texted: texts are off on this server."' in send
    assert "written, not sent (dry run)" not in send
    trail = api.get("/api/audit", headers=owner)
    if trail.status_code == 200:
        assert "dry run" not in trail.text


def test_after_received_the_wizard_offers_the_next_step():
    nxt = _function(CONSOLE_JS, "connectNext")
    assert '["test", "Next: send a test"]' in nxt and '["oncall", "Next: add who gets alerts"]' in nxt
    assert "${connectNext()}" in _function(CONSOLE_JS, "paintConnectWait")


# --- 7. food protected ----------------------------------------------------------

def test_the_estimate_is_by_kind_and_only_where_a_sector_has_one(operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor_factory(headers, "Z-1", "restaurant", "Walk-in freezer", kind="freezer")
    sensor_factory(headers, "P-1", "pharmacy", "Vaccine fridge", kind="fridge")
    sensor_factory(headers, "R-1", "cybersecurity", "Rack")
    assert food_value.estimate(STORE.get_sensor("F-1"))["usd"] == food_value.STOCK_USD["food"]["fridge"]
    assert food_value.estimate(STORE.get_sensor("Z-1"))["usd"] == food_value.STOCK_USD["food"]["walk-in freezer"]
    assert food_value.estimate(STORE.get_sensor("P-1"))["label"] == "Stock protected"
    assert food_value.estimate(STORE.get_sensor("R-1")) is None


def test_a_closed_answered_alert_says_food_protected_as_an_estimate(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    _pulse(api, key, 38.0)
    opened = _pulse(api, key, 64.0)
    assert opened["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    incident_id = opened["incident_id"]
    row = lambda: next(i for i in api.get("/api/console/overview", headers=owner).json()["incidents"]
                       if i["incident_id"] == incident_id)
    assert row()["food_protected"] is None             # not while it is open
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=owner, json={}).status_code == 200
    _pulse(api, key, 38.0)
    assert api.post(f"/api/voice/resolve/{incident_id}", headers=owner, json={}).status_code == 200
    saved = row()["food_protected"]
    usd = food_value.STOCK_USD["food"]["walk-in fridge"]
    assert saved["usd"] == usd
    assert saved["words"] == f"Food protected: about ${usd:,}"
    assert saved["note"].startswith("An estimate:")
    assert "i.food_protected.words" in CONSOLE_JS and "i.food_protected.note" in CONSOLE_JS


def test_an_alert_nobody_answered_or_whose_food_was_binned_claims_nothing(operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, "F-1", "restaurant", "Prep fridge", kind="fridge")
    sensor = STORE.get_sensor("F-1")
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant["tenant_id"], sensor=sensor, temperature_fahrenheit=50.0,
        breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
    incident.resolved_at = utc_now()
    assert food_value.protected(incident, sensor) is None
    incident.acknowledged_at = utc_now() - timedelta(minutes=5)
    assert food_value.protected(incident, sensor)["usd"] == food_value.STOCK_USD["food"]["fridge"]
    incident.product_disposition = "discarded"
    assert food_value.protected(incident, sensor) is None


def test_the_monthly_email_adds_it_up_as_an_estimate():
    report = {"company_name": "Rosa's Cafe", "month_label": "October 2026", "units": 1,
              "readings": 100, "in_range_percent": 99.0, "breach_count": 0,
              "food_protected_usd": 3800, "food_protected_alerts": 2,
              "open_problems": {"alerts": [], "offline": [], "never_reported": [], "low_battery": []},
              "inspector_log": "", "period_start": "2026-10-01", "period_end": "2026-10-31",
              "caveat": "", "temperature_unit": "F"}
    text = monthly.render_text(report)
    # 3.28.0: the rule is said as it is (food_value.protected).
    assert ("Food protected: about $3,800 (2 alerts answered and back in range within "
            "2 hours, or marked Food kept).") in " ".join(text.split())
    assert "An estimate: what a typical fridge or freezer of each kind holds" in " ".join(text.split())
    report["food_protected_usd"] = 0
    assert "Food protected" not in monthly.render_text(report)


def test_the_owner_tunes_it_in_one_place_and_it_is_written_down():
    assert "STOCK_USD" in (ROOT / "food_value.py").read_text()
    alerts = (ROOT / "docs/ALERTS.md").read_text()
    assert '"Food protected": an estimate on a closed alert' in alerts
    assert "`food_value.py`" in alerts


# --- 8. small things ------------------------------------------------------------

def test_waits_are_said_in_minutes():
    assert wait_words(45) == "45 seconds"
    assert wait_words(61) == "about 2 minutes"
    assert wait_words(2832) == "about 48 minutes"
    assert wait_words(60) == "about a minute"


def test_the_sign_up_limit_says_minutes(api):
    for n in range(3):
        _sign_up(api, email=f"cafe{n}@cafe.example")
    refused = api.post("/api/signup", json={
        "company_name": "One More", "full_name": "Rosa Diaz", "email": "more@cafe.example",
        "password": "flour-and-butter-22", "contact_phone": "+15550104478",
        "industry_vertical": "restaurant"})
    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert re.search(r"Try again in about \d+ minutes", detail), detail
    assert "seconds" not in detail
    assert int(refused.headers["Retry-After"]) > 60


def test_insurance_packs_not_claim_packets_on_the_public_pages(api):
    for path in ("/pricing", "/"):
        page = api.get(path).text
        assert "claim packet" not in page.lower(), path
    assert "Tamper-evident readings and insurance packs" in api.get("/pricing").text


def test_a_live_reading_is_not_called_awaiting_readings():
    spark = _function(CONSOLE_JS, "sparkline")
    assert "awaiting readings</div>" not in spark
    assert "the line starts with the next reading" in spark


def test_a_far_too_warm_reading_gets_words_back(api):
    owner = _sign_up(api)
    key = _sensor(api, owner)
    back = _pulse(api, key, 70.0)
    assert back["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert isinstance(back["message"], str) and back["message"]
    assert "Alert opened" in back["message"] and "Texts are off on this server" in back["message"]
    assert "undefined" not in back["message"] and "None" not in back["message"]


def test_the_tv_screen_hides_the_robot_and_the_hint_and_leave_has_its_own_strip():
    assert "body.wall main { padding-top: 64px; }" in THEME
    assert "body.wall .pilot-root, body.wall #adv-note, body.wall #tab-empty { display: none !important; }" in THEME
    assert "|| dayOne() || state.wall;" in CONSOLE_JS


def test_the_cafe_trades_come_first_and_the_rest_are_other_industries(api):
    page = api.get("/pricing").text
    select = page[page.index('<select class="calc-sector">'):]
    select = select[:select.index("</select>")]
    assert select.index('value="restaurant"') < select.index('<optgroup label="Other industries (priced by quote)">')
    assert select.index('<optgroup label="Other industries') < select.index('value="superyacht"')
