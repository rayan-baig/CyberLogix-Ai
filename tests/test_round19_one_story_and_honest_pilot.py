"""Round 19 (3.42.0): Pilot misses honestly rather than guessing, and the
proof report, the insurance pack, the demo and the console agree on who
is alerted, when things happened, and what things are called.

Pilot's answers themselves (the five named misroutes, the new answers,
and the adversarial table of over 300 questions) are in
tests/js/kryozyn-pilot.test.js. The browser checks at 320px, 375px and
desktop were run against the demo account (CHANGELOG 3.42.0).
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
from domain import VOICE_ESCALATION_GRACE_MINUTES, minutes_words
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
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


def _month_of_demo(tenant):
    first = min(r.recorded_at for r in STORE.readings_for(demo_account.HANDLED, complete=True))
    return date(first.year, first.month, 1)


# --- 1. Pilot is told the figures its new answers quote ------------------------------


def test_pilot_is_told_the_setup_fee_the_yearly_offer_and_how_long_data_is_kept():
    from pricing import SETUP_FEE_PER_SITE_USD, YEARLY_MONTHS_CHARGED, YEARLY_MONTHS_COVERED
    from store import KEEP_DETAIL_DAYS, KEEP_READINGS_MONTHS

    facts = main.pilot_facts()
    assert facts["setup-fee"] == f"{SETUP_FEE_PER_SITE_USD:g}"
    assert facts["yearly-charged"] == str(YEARLY_MONTHS_CHARGED)
    assert facts["yearly-covered"] == str(YEARLY_MONTHS_COVERED)
    assert facts["keep-readings-months"] == str(KEEP_READINGS_MONTHS)
    assert facts["keep-detail-days"] == str(KEEP_DETAIL_DAYS)
    # Each new answer points at something the console really has.
    for label in ("Quiet hours", "No quiet hours", '"Mute" : "Unmute"', "Thrown away", "Add what was thrown away",
                  "Add someone", "Remove", "Make the report", "Proof of monitoring"):
        assert label in CONSOLE_HTML + CONSOLE_JS, label


# --- 2. who gets alerted: one source, one name for each role ---------------------------


def test_the_proof_report_names_whoever_the_rotation_texts_as_the_team_tab_does(demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    report = proofreports.build(tenant, None, _month_of_demo(tenant))
    who = report["alerted"]
    texted = [c for c in STORE.sms_recipients(tenant) if c.contact_id != "fallback"]
    assert len(texted) == 1  # the demo's rotation: one person a week
    assert who["rotation"] is True
    assert who["steps"][0] == "At once: a text to the person whose turn it is in the on-call rotation."
    assert who["steps"][1] == ("After 10 minutes with nobody acknowledging: a phone call to the "
                               "person on call.")
    assert not any("all 3" in step for step in who["steps"])
    assert "rotation decides who" in who["rule"]
    # Roles only, as ever: nobody's name or number.
    text = str(who)
    for name, phone in demo_account.CONTACTS:
        assert name.split()[0] not in text and phone not in text


def test_all_of_them_only_when_there_is_no_rotation(api, tenant_factory):
    _, made = tenant_factory()
    tenant = STORE.get_tenant(made["tenant_id"])
    for n in range(3):
        STORE.add_contact(tenant.tenant_id, f"Person {n}", f"+1 555 010 01{50 + n}", escalation_order=n + 1)
    who = proofreports._who_is_alerted(tenant, None)
    assert who["rotation"] is False
    assert who["steps"][0] == "At once: a text to all 3 people on call."


def test_the_report_and_the_team_tab_use_one_name_for_each_role():
    assert proofreports._TEAM_WORDS == {"owner": "Owners", "operator": "Operators", "viewer": "Viewers"}
    assert proofreports._ROLE_WORDS["manager"] == "Operator"
    assert "Manager" not in proofreports._ROLE_WORDS.values()


# --- 3. the demo's audit trail and its call step -----------------------------------------


def test_the_demos_audit_rows_are_at_their_own_moments(demo):
    tid = demo["tenant_id"]
    rows = {e.action: e for e in STORE.audit_for(tid, limit=500)}
    sub = STORE.active_subscription(tid)
    handled = _alert(tid, demo_account.HANDLED)
    assert rows["contract.signed"].at == sub.started_at
    assert rows["incident.resolved"].at == handled.resolved_at
    # As stored, too: a restart reads the same moments back.
    stored = {r["action"]: r for _, r in STORE._db.rows("audit") if r.get("tenant_id") == tid}
    assert stored["incident.resolved"]["at"].startswith(handled.resolved_at.strftime("%Y-%m-%dT%H:%M"))


def test_would_have_phoned_is_stamped_when_the_ladder_says(demo):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    assert still.voice_escalated_at == still.opened_at + timedelta(minutes=VOICE_ESCALATION_GRACE_MINUTES)
    assert still.calls_off and not still.phoned
    assert still.call_words() == "Would have phoned the list: calls are off on this server"


# --- 4. times on the business's clock; hours with separators -------------------------------


def test_the_proof_report_carries_the_businesss_clock_and_keeps_its_times_utc(demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    report = proofreports.build(tenant, None, _month_of_demo(tenant))
    assert report["timezone"] == "America/Chicago"
    for key in ("period_start", "period_end", "issued_at"):
        assert report[key].endswith("Z") or report[key].endswith("+00:00"), report[key]
    for alert in report["alerts"]:
        assert alert["opened_at"].endswith("Z") or alert["opened_at"].endswith("+00:00")
    # The page writes them in that zone, labelled, and says which.
    assert 'timeZone: CLOCK.zone, timeZoneName: "short"' in PROOF_JS
    assert 'CLOCK.zone = r.timezone || "UTC";' in PROOF_JS
    assert "Times are on the business's clock" in PROOF_JS


def test_a_saved_proof_report_still_checks_as_genuine_with_its_clock(api, demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    row = proofreports.make(tenant, None, _month_of_demo(tenant))
    assert proofreports.check(row) == "genuine"
    assert row["body"]["timezone"] == "America/Chicago"


def test_the_insurance_pack_is_on_the_businesss_clock_whoever_reads_it(api, demo):
    owner = _owner(api)
    handled = _alert(demo["tenant_id"], demo_account.HANDLED)
    pack = api.post(f"/api/claims/{handled.incident_id}/packet?tz=Europe/London", headers=owner).json()
    assert pack["clock"] == "America/Chicago"
    zone = claims.business_zone(STORE.get_tenant(demo["tenant_id"]), handled)
    assert re.search(r"\b(?:CST|CDT)$", claims.when_words(handled.opened_at, zone))
    # Shown in that clock, labelled, on the console's pack.
    assert "const at = (iso) => zonedStamp(iso, packet.clock);" in CONSOLE_JS
    assert 'timeZone: zone || "UTC", timeZoneName: "short"' in CONSOLE_JS


def test_a_business_with_no_clock_of_its_own_gets_the_readers(api, tenant_factory):
    _, made = tenant_factory()
    tenant = STORE.get_tenant(made["tenant_id"])
    assert proofreports._zone_name(tenant, None) == "UTC"


def test_the_verify_page_writes_hours_as_the_report_does():
    assert proofreports.online_words({"online_percent": 99.9, "hours_watched": 3353.6}) == "99.9% of 3,353.6 hours watched"
    assert proofreports.online_words({"online_percent": None, "hours_watched": 1200.0}) == "Not enough yet: 1,200 hours watched so far"


# --- 4 (continued). plain US English, plain words in Setup, the proof report link ---------


def test_the_licences_tab_is_staff_cards_in_us_english():
    assert 'type="button">Staff cards</button>' in CONSOLE_HTML
    assert ">Licences<" not in CONSOLE_HTML and "Staff licences" not in CONSOLE_HTML
    for british in ("} analysed`", "} modelled", "Licence suspended", "no licences recorded"):
        assert british not in CONSOLE_JS, british


def test_setups_jargon_is_in_plain_words_or_under_advanced():
    assert "Fetch from a sensor cloud" not in CONSOLE_HTML + CONSOLE_JS
    assert "Sensor maker's cloud" in CONSOLE_HTML
    key = CONSOLE_HTML[CONSOLE_HTML.index('id="find-business-key"'):]
    key = key[:key.index(">")]
    assert 'data-advanced-only="1"' in key and 'data-label="Key for other software"' in key
    # Under Advanced for every account, as "Spending limits" went in 3.40.0.
    for card in ("find-vendor-clouds", "find-omniarmor"):
        head = CONSOLE_HTML[CONSOLE_HTML.index(f'id="{card}"'):]
        assert 'data-advanced-only="1"' in head[:head.index(">")], card
    assert "%%CHECKLIST_APP%% connection</span>" not in CONSOLE_HTML
    assert "Send the daily record to %%CHECKLIST_APP%%" in CONSOLE_HTML
    assert '<option value="breaching">Out of range</option>' in CONSOLE_HTML
    assert "breaching` : \"\"" not in CONSOLE_JS


def test_make_a_proof_report_opens_proof_of_monitoring():
    assert 'proof: { card: "find-proof-reports", field: "proof-report-month" }' in CONSOLE_JS
    assert 'hash === "#proof-report" ? "proof"' in CONSOLE_JS
    assert '#proof-report`' in PILOT


def test_an_owner_can_add_someone_who_can_sign_in(api, demo):
    form = CONSOLE_HTML[CONSOLE_HTML.index('id="user-add"'):]
    form = form[:form.index("</form>")]
    for field in ('id="ua-name"', 'id="ua-email"', 'id="ua-role"', 'id="ua-pw"', ">Add someone</button>"):
        assert field in form, field
    assert '$("user-add").hidden = !(state.user && state.user.role === "owner");' in CONSOLE_JS
    # What the form sends is what the endpoint takes.
    resp = api.post("/api/accounts/users", headers=_owner(api), json={
        "full_name": "Jo Example", "email": "jo@example.com", "role": "operator", "password": "first-pass-123"})
    assert resp.status_code == 201, resp.text


# --- 5. polish --------------------------------------------------------------------------


def test_how_long_says_hours_and_minutes_under_three_hours():
    assert minutes_words(88) == "1 hour 28 minutes"
    assert minutes_words(60) == "1 hour"
    assert minutes_words(121) == "2 hours 1 minute"
    assert minutes_words(200) == "about 3 hours"
    assert minutes_words(45) == "45 minutes"


def test_today_starts_a_sentence_with_a_capital():
    body = SIMPLE_JS[SIMPLE_JS.index("function nameOf"):]
    body = body[:body.index("\n}\n")]
    assert "name.charAt(0).toUpperCase() + name.slice(1)" in body


def test_the_pinned_alert_bar_is_compact_on_a_small_phone():
    rule = THEME[THEME.index("@media (max-width: 480px) {\n  .needs-bar-in"):]
    rule = rule[:rule.index("\n}\n")]
    assert ".needs-bar-who { display: none; }" in rule
    assert ".needs-bar-what { font-size: 13px;" in rule


def test_on_today_the_robot_keeps_off_the_alerts_words_not_its_whole_cards():
    assert 'const TODAY_EDGE = new Set(["#verdict", "#needs-wrap", "#needs", ".need"]);' in PILOT
    assert 'const TODAY_WORDS = ["#verdict > *", ".need-what", ".need-when"];' in PILOT
    assert ".filter((sel) => !(onToday && TODAY_EDGE.has(sel))).concat(onToday ? TODAY_WORDS : [])" in PILOT


def test_the_changelog_and_version_say_3_42_0():
    assert (ROOT / "VERSION").read_text().strip() == "3.42.0"
    assert (ROOT / "CHANGELOG.md").read_text().split("## ")[1].startswith("3.42.0")
    assert claims.business_zone  # the pack's clock comes from the business
