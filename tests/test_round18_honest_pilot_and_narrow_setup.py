"""Round 18 (3.41.0): Pilot's confident wrong answers put right and more
answered with no AI key, the Setup tab inside a 320px phone, and the
proof report, the console and the demo saying the same thing.

Pilot's answers themselves (the bakery alarm, who can see the readings,
the bills, a count in one restaurant, the limit's list, the sectors,
after the trial, a second location, the alert wait, taking someone off,
a new number, the one-line "Nothing needs you" and the miss's
suggestions) are in tests/js/kryozyn-pilot.test.js. The browser checks
at 320px, 375px and desktop were run by hand against the demo account
(CHANGELOG 3.41.0).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import claims
import demo_account
import firstday
import main
import proofreports
from store import STORE, GAP_RECORD_MINUTES, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
PROOF_JS = (STATIC / "proof-report.js").read_text()
THEME = (STATIC / "theme.css").read_text()
PILOT = (STATIC / "pilot-kryozyn.js").read_text()
PASSWORD = "demo-password-for-tests"


@pytest.fixture()
def demo():
    firstday.forget()
    return demo_account.seed(password=PASSWORD)


def _owner(api):
    resp = api.post("/api/accounts/login", json={"email": demo_account.OWNER[1], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _alert(tenant_id, sensor_id):
    return next(i for i in STORE.incidents_for(tenant_id) if i.sensor_id == sensor_id)


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start) + 2]


# --- 1. what Pilot is told ---------------------------------------------------------------


def test_pilot_is_told_every_sector_with_a_page_and_the_trials_grace():
    import industries
    import trades
    from auth import LICENCE_GRACE_DAYS

    facts = main.pilot_facts()
    assert facts["grace-days"] == str(LICENCE_GRACE_DAYS)
    rows = [row.split("|") for row in facts["sectors"].split(";")]
    assert {r[0] for r in rows} == set(industries.known()) | set(trades.known())
    assert all(len(r) == 4 for r in rows)
    by_slug = {r[0]: r for r in rows}
    assert by_slug["florists"][1:] == ["Florists", "", ""]
    assert by_slug["restaurant"][2:] == ["149", "location"]
    assert by_slug["vaccine-clinics"][2:] == ["199", "pharmacy"]
    # Bare numbers, as the other figures: no page shows a price of its own.
    assert "$" not in facts["sectors"]


def test_this_rounds_answers_live_in_the_apps_own_config():
    # The app's own config holds this round's answers.
    for answer in ("function alarmAnswer", "function sectorAnswer", "function trialAfterAnswer", "function addSiteAnswer",
                   "function waitAnswer", "function removeAnswer", "function numberAnswer", "function billsNow"):
        assert answer in PILOT, answer
    assert "Spending limits" not in PILOT.split("const TOPICS", 1)[0].split("function callsLeftAnswer", 1)[1].split("\n  }\n", 1)[0]


# --- 2. the Setup tab at 320px -------------------------------------------------------------


def test_setup_modules_and_pairs_shrink_to_a_phone():
    assert "grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));" in THEME
    assert ".pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));" in THEME
    assert "minmax(300px, 1fr)" not in THEME


# --- 3. the console has what Pilot's answers name -------------------------------------------


def test_the_console_hands_pilot_its_bills_and_follows_the_bills_links():
    assert "bills: () => (state.token && state.bills ? state.bills : null)," in CONSOLE_JS
    assert "state.bills = { ledger: got.invoices || null, contract: (got.contract && got.contract.contract) || null };" in CONSOLE_JS
    follow = _function(CONSOLE_JS, "followBillsLink")
    assert "#invoice=" in follow and '"#bills"' in follow and '"#people"' in follow
    assert "openInvoice(bill[1])" in follow
    assert 'bills: { card: "find-invoices", field: "" },' in CONSOLE_JS
    assert 'people: { card: "find-team", field: "" },' in CONSOLE_JS


def test_change_number_keeps_the_place_and_remove_is_for_owners(demo, api):
    roster = _function(CONSOLE_JS, "renderRoster")
    assert ">Change number</button>" in roster and "data-contact-phone=" in roster
    team = _function(CONSOLE_JS, "renderTeam")
    assert 'state.user && state.user.role === "owner"' in team and "data-disable-user=" in team and ">Remove</button>" in team
    assert 'method: "PATCH", body: JSON.stringify({ phone: phone.trim() })' in CONSOLE_JS
    assert "/disable`, {" in CONSOLE_JS
    # The routes they call do what the answers say.
    owner = _owner(api)
    contacts = api.get("/api/contacts", headers=owner).json()["contacts"]
    priya = next(c for c in contacts if c["full_name"] == "Priya Lund")
    changed = api.patch(f"/api/contacts/{priya['contact_id']}", headers=owner, json={"phone": "+1 555 010 0199"})
    assert changed.status_code == 200, changed.text
    after = api.get("/api/contacts", headers=owner).json()["contacts"]
    assert [c["full_name"] for c in after] == [c["full_name"] for c in contacts]
    assert next(c for c in after if c["full_name"] == "Priya Lund")["phone"] == "+1 555 010 0199"
    users = api.get("/api/accounts/users", headers=owner).json()["users"]
    theo = next(u for u in users if u["full_name"].startswith("Theo"))
    assert api.post(f"/api/accounts/users/{theo['user_id']}/disable", headers=owner).status_code == 200
    assert next(u for u in api.get("/api/accounts/users", headers=owner).json()["users"]
                if u["user_id"] == theo["user_id"])["disabled"] is True


# --- 4. the proof report, the console and the demo agree ------------------------------------


def test_the_demo_week_has_a_reading_every_five_minutes_so_the_gap_line_is_true(demo):
    assert demo_account.EVERY_MINUTES < GAP_RECORD_MINUTES
    assert demo_account.EVERY_MINUTES == demo_account.LIVE_EVERY_MINUTES
    readings = STORE.readings_for(demo_account.HANDLED, complete=True)
    gaps = {round((b.recorded_at - a.recorded_at).total_seconds() / 60) for a, b in zip(readings, readings[1:])}
    assert gaps == {demo_account.EVERY_MINUTES}
    assert demo["readings"] == len(demo_account.UNITS) * demo_account.DAYS * 24 * 60 // demo_account.EVERY_MINUTES
    tenant = STORE.get_tenant(demo["tenant_id"])
    first = readings[0].recorded_at
    report = proofreports.build(tenant, None, date(first.year, first.month, 1))
    assert report["longest_gap"] is None
    assert "No sensor went ${r.gap_threshold_minutes} minutes or more without sending data." in PROOF_JS


def test_the_demos_alerts_keep_their_story_at_the_new_cadence(demo):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    readings = STORE.readings_for(demo_account.STILL_OPEN, complete=True)
    at_open = next(r for r in readings if r.recorded_at == still.opened_at)
    assert at_open.temperature_fahrenheit == 43.5 and still.peak_f == 46.5 == readings[-1].temperature_fahrenheit
    before = [r for r in readings if r.recorded_at < still.opened_at]
    assert not any(r.breached for r in before)
    handled = _alert(demo["tenant_id"], demo_account.HANDLED)
    out = [r for r in STORE.readings_for(demo_account.HANDLED, complete=True) if r.breached]
    assert out[0].recorded_at == handled.opened_at and out[0].temperature_fahrenheit == 12.5
    assert max(r.temperature_fahrenheit for r in out) == 21.0 == handled.peak_f
    assert out[-1].recorded_at < handled.resolved_at
    assert (out[-1].recorded_at - out[0].recorded_at) < timedelta(minutes=90)


def test_said_fixed_the_warm_fridge_comes_down_over_several_readings(demo):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    who = f"{demo_account.OPERATOR[0]} <{demo_account.OPERATOR[1]}>"
    still, _ = STORE.acknowledge_incident(still, who)
    STORE.report_fixed(still, who)
    start = utc_now()
    temps = [STORE.get_sensor(demo_account.STILL_OPEN).last_temperature]
    for step in range(1, 8):
        demo_account.feed(now=start + timedelta(minutes=6 * step))
        temps.append(STORE.get_sensor(demo_account.STILL_OPEN).last_temperature)
    drops = [a - b for a, b in zip(temps, temps[1:])]
    assert max(drops) <= demo_account.COOLS_PER_READING + 0.05, temps
    assert len([t for t in temps if t > 41]) >= 3, temps
    assert _alert(demo["tenant_id"], demo_account.STILL_OPEN).resolved_at is not None
    assert temps[-1] < 41


def test_an_every_location_report_counts_its_locations_and_shows_the_ladder(demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    first = min(r.recorded_at for r in STORE.readings_for(demo_account.HANDLED, complete=True))
    report = proofreports.build(tenant, None, date(first.year, first.month, 1))
    assert report["location"] == {"name": "Every location", "scope": "all", "locations": 3}
    steps = report["alerted"]["steps"]
    # 3.42.0: from the live list, as the Team tab: the demo's rotation
    # texts and phones whoever's turn it is, not all 3 (test_round19).
    assert steps[0] == "At once: a text to the person whose turn it is in the on-call rotation."
    assert steps[1] == "After 10 minutes with nobody acknowledging: a phone call to the person on call."
    assert len(steps) == 2
    site = STORE.sites_for(tenant.tenant_id)[0]
    one = proofreports.build(tenant, site.site_id, date(first.year, first.month, 1))
    assert one["location"]["scope"] == "one" and one["location"]["locations"] == 1
    if report["coverage"]["note"]:
        assert not re.search(r"\d{2} [A-Z][a-z]+ \d{4}", report["coverage"]["note"]), report["coverage"]["note"]
    assert "across ${place.locations} locations" in PROOF_JS
    assert "of ${num(s.hours_watched)} sensor-hours watched" in PROOF_JS
    assert '<ol class="people">' in PROOF_JS


def test_a_starter_reports_ladder_has_no_calls(api, tenant_factory):
    _, made = tenant_factory()
    tenant_id = made["tenant_id"]
    tenant = STORE.get_tenant(tenant_id)
    STORE.add_contact(tenant_id, "Sam Example", "+1 555 010 0150", escalation_order=1)
    tenant.plan = "starter"
    who = proofreports._who_is_alerted(tenant, None)
    assert who["calls"] is False
    assert who["steps"] == ["At once: a text to the one person on call.", "No phone calls: this plan sends texts only."]


def test_the_console_says_how_an_alert_closed_as_the_pack_does(demo, api):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    who = f"{demo_account.OWNER[0]} <{demo_account.OWNER[1]}>"
    still, _ = STORE.acknowledge_incident(still, who)
    STORE.report_fixed(still, who)
    start = utc_now()
    for step in range(1, 9):
        demo_account.feed(now=start + timedelta(minutes=6 * step))
    closed = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    assert claims.closed_how(closed) == "fixed_then_itself"
    assert "closed by itself" in claims.closed_words(closed, "F")
    row = next(i for i in api.get("/api/console/overview", headers=_owner(api)).json()["incidents"]
               if i["incident_id"] == closed.incident_id)
    assert row["closed_how"] == "fixed_then_itself"
    assert row["close_display"] is not None and row["close_display"] < 41
    handled = next(i for i in api.get("/api/console/overview", headers=_owner(api)).json()["incidents"]
                   if i["sensor_id"] == demo_account.HANDLED)
    assert handled["closed_how"] == "person"
    timeline = _function(CONSOLE_JS, "incidentTimeline")
    assert 'how === "fixed_then_itself" ? `Closed by itself${back}`' in timeline


def test_an_open_alerts_line_is_the_reading_now_and_the_worst_labelled():
    live = _function(CONSOLE_JS, "liveWords")
    assert '`${cold ? "Too cold" : "Too warm"} now: ${oneDecimal(now)}${u}${limitWords}.${worst}`' in live
    assert "Worst so far" in live
    assert 'i.state === "resolved" ? i.breach_details : liveWords(i)' in CONSOLE_JS


# --- 5. plain words ----------------------------------------------------------------------


def test_whole_dollars_one_date_style_and_clock_words_for_the_hand_over():
    assert "That is about ${wholeDollars(offer.saving_usd)} off." in CONSOLE_JS
    assert "${wholeDollars(price.monthly_for_you_usd)} a month for you while connected." in CONSOLE_JS
    assert "i.food_answered_at ? CLXWords.dateTime(i.food_answered_at)" in CONSOLE_JS
    assert "${esc(handoverWords(r.handover, r.timezone))}" in CONSOLE_JS
    assert "${esc(r.handover)} ${esc(r.timezone)}" not in CONSOLE_JS


def test_setup_and_the_activity_log_in_plain_words():
    for jargon in ("3rd-party gateway", ">Payload<", "Unattended watchdog", "Run sweep now", "Wire real hardware"):
        assert jargon not in CONSOLE_HTML, jargon
    assert "open endpoint" not in CONSOLE_JS
    assert "escalator</option>" not in CONSOLE_JS
    plain = _function(CONSOLE_JS, "plainAudit")
    assert "The alert on ${named(id)}" in plain and "prices rise $1% a year" in plain
    import contracts
    assert "% escalator" not in Path(contracts.__file__).read_text().split("def sign_contract", 1)[-1].split("write_audit", 2)[1]


def test_the_legal_date_reads_as_a_date_and_the_terms_hash_is_unchanged():
    import legal

    legal_js = (STATIC / "legal.js").read_text()
    assert 'toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })' in legal_js
    assert legal.EFFECTIVE_DATE == "2026-11-23"   # the hashed text is what customers accepted


def test_for_counts_its_own_cards():
    industry = (STATIC / "industry.js").read_text()
    assert "Twelve industries" not in industry
    assert "${esc(String(first.length + rest.length))} kinds of business" in industry


def test_version_is_3_41_0():
    # 3.42.0 followed; its entry stays in the changelog.
    assert "## 3.41.0" in (ROOT / "CHANGELOG.md").read_text()
