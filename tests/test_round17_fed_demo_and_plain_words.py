"""Round 17 (3.40.0): a demo that stays fed while it is served (readings
that are never billed and never alert or text), quiet sensors grouped on
/today, a proof of monitoring report that reads the demo's whole week,
pretend texts called pretend in the insurance pack, and plainer words on
the owner's screens.

Pilot's new and corrected answers with no key (a table of about a hundred
phrasings and the answer each gets) are in tests/js/kryozyn-pilot.test.js;
/today's grouped quiet card and its banner in tests/js/today.test.js. The
browser checks at 320px, 375px and desktop, with the demo served for over
35 real minutes, were run by hand against the demo account (CHANGELOG
3.40.0).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import claims
import demo_account
import firstday
import notifications
import proofreports
import scheduler
from store import GAP_KIND, STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
WORDS_JS = (STATIC / "words.js").read_text()
PROOF_JS = (STATIC / "proof-report.js").read_text()
THEME = (STATIC / "theme.css").read_text()
PASSWORD = "demo-password-for-tests"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start) + 2]


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


def _usage(tenant_id):
    return [row.to_row() if hasattr(row, "to_row") else row for row in STORE.usage_history(tenant_id, days=2)]


# --- 1. the demo stays fed --------------------------------------------------------------


def test_feed_writes_one_reading_per_unit_when_due_and_nothing_else(demo):
    tid = demo["tenant_id"]
    incidents = len(STORE.incidents_for(tid))
    invoices = len(STORE.invoices_for(tid))
    usage = _usage(tid)
    # The clock simulated: 40 minutes of the sweep's passes, a minute apart.
    start = utc_now()
    written = sum(demo_account.feed(now=start + timedelta(minutes=m)) for m in range(1, 41))
    assert written == len(demo_account.UNITS) * (40 // demo_account.LIVE_EVERY_MINUTES)
    later = start + timedelta(minutes=40)
    for _, sensor_id, _, _ in demo_account.UNITS:
        sensor = STORE.get_sensor(sensor_id)
        assert sensor.last_seen == later, sensor_id
        assert not sensor.offline(later + timedelta(minutes=1)), sensor_id
    # Not again until its next turn.
    assert demo_account.feed(now=later + timedelta(minutes=2)) == 0
    # No alert opened, no invoice, nothing counted, no gap in data.
    assert len(STORE.incidents_for(tid)) == incidents
    assert len(STORE.invoices_for(tid)) == invoices
    assert _usage(tid) == usage
    assert not STORE.gaps_for(tid)


def test_the_open_alerts_unit_stays_warm_at_or_under_its_worst_and_the_rest_stay_in_range(demo):
    start = utc_now()
    for step in range(1, 13):
        demo_account.feed(now=start + timedelta(minutes=5 * step))
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    warm = [r for r in STORE.readings_for(demo_account.STILL_OPEN) if r.recorded_at > start]
    assert len(warm) == 12
    assert all(45.6 <= r.temperature_fahrenheit <= 46.5 and r.breached for r in warm)
    assert still.peak_f == 46.5 == max(r.temperature_fahrenheit for r in STORE.readings_for(demo_account.STILL_OPEN))
    assert still.open and still.resolved_at is None
    for _, sensor_id, _, _ in demo_account.UNITS:
        if sensor_id == demo_account.STILL_OPEN:
            continue
        fresh = [r for r in STORE.readings_for(sensor_id) if r.recorded_at > start]
        assert fresh and not any(r.breached for r in fresh), sensor_id


def test_said_fixed_the_open_alerts_unit_cools_and_closes_by_itself_with_no_text(demo):
    still = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    who = f"{demo_account.OPERATOR[0]} <{demo_account.OPERATOR[1]}>"
    still, _ = STORE.acknowledge_incident(still, who)
    STORE.report_fixed(still, who)
    # It cools over several readings (3.41.0), not at once.
    start = utc_now()
    for step in range(1, 7):
        demo_account.feed(now=start + timedelta(minutes=6 * step))
    closed = _alert(demo["tenant_id"], demo_account.STILL_OPEN)
    assert closed.resolved_at is not None
    assert STORE.get_sensor(demo_account.STILL_OPEN).last_temperature < 41
    # Nothing new was opened by the cold reading or the warm ones before.
    assert len(STORE.incidents_for(demo["tenant_id"])) == 2


def test_feed_does_nothing_on_a_database_that_is_not_a_demo(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="REAL-1", vertical="restaurant")
    assert demo_account.feed(now=utc_now() + timedelta(hours=1)) == 0
    assert STORE.get_sensor("REAL-1").last_seen is None


def test_the_sweep_feeds_a_demo_that_went_quiet_before_it_looks_for_quiet_units(demo, api):
    # The clock simulated the other way round: every unit last heard from
    # 40 minutes ago, as the demo was half an hour after it was seeded.
    gone = utc_now() - timedelta(minutes=40)
    for _, sensor_id, _, _ in demo_account.UNITS:
        sensor = STORE.get_sensor(sensor_id)
        sensor.last_seen = gone
        STORE._db.put("sensor", sensor_id, sensor.to_row())
    owner = _owner(api)
    before = api.get("/api/console/overview", headers=owner).json()["summary"]
    assert before["sensors_online"] == 0
    incidents = len(STORE.incidents_for(demo["tenant_id"]))
    result = scheduler.run_one_pass()
    assert result["tenants_swept"] == 1
    after = api.get("/api/console/overview", headers=owner).json()
    assert after["summary"]["sensors_online"] == after["summary"]["sensors_total"] == len(demo_account.UNITS)
    assert len(STORE.incidents_for(demo["tenant_id"])) == incidents
    assert all(not s.get("breaching") or s["sensor_id"] == demo_account.STILL_OPEN for s in after["sensors"])


def test_a_demo_database_never_sends_a_text_or_places_a_call(demo, monkeypatch):
    sent = []

    class Client:
        class messages:  # noqa: N801 - the provider's own shape
            @staticmethod
            def create(**kw):
                sent.append(kw)
                raise AssertionError("a demo database sent a text")

        class calls:  # noqa: N801
            @staticmethod
            def create(**kw):
                sent.append(kw)
                raise AssertionError("a demo database placed a call")

    monkeypatch.setattr(notifications, "_configured", lambda: True)
    monkeypatch.setattr(notifications, "_get_client", lambda: Client())
    text = notifications.send_sms("+1 555 010 0141", "Test", demo["tenant_id"])
    call = notifications.place_voice_call("+1 555 010 0141", "Test", demo["tenant_id"])
    assert not sent
    for out in (text, call):
        assert out["delivered"] is False and out["status"] == "not_configured"
        assert "demo database" in out["detail"]


def test_a_real_database_still_sends(api, tenant_factory, monkeypatch):
    tenant_factory()
    assert notifications._demo_database() is False


# --- 1b. quiet sensors grouped on /today --------------------------------------------------


def test_today_groups_three_or_more_quiet_sensors_into_one_card():
    assert "const QUIET_TOGETHER = 3;" in SIMPLE_JS
    job = _function(SIMPLE_JS, "quietGroupJob")
    assert "stopped together</strong> — probably the internet or power." in job
    assert "together.grouped.has(unit.sensor_id)" in SIMPLE_JS


# --- 2. the proof of monitoring report reads the demo's week -------------------------------


def test_the_demo_is_watched_from_its_first_reading_with_no_made_up_gaps(demo):
    first = min(r.recorded_at for r in STORE.readings_for(demo_account.HANDLED, complete=True))
    for _, sensor_id, _, _ in demo_account.UNITS:
        # To the second: the record keeps readings' times to the second,
        # and at 5 minutes a reading (3.41.0) the first is read from it.
        assert abs((STORE.get_sensor(sensor_id).registered_at - first).total_seconds()) < 1, sensor_id
    assert abs((STORE.gap_log_since() - first).total_seconds()) < 1
    assert STORE._db.get("meta", "gap_log_since")["at"].startswith(first.strftime("%Y-%m-%dT%H:%M"))
    assert not [row for _, row in STORE._db.rows(GAP_KIND) if row.get("tenant_id") == demo["tenant_id"]]


def test_the_proof_report_reads_a_full_week_for_each_unit(demo):
    tenant = STORE.get_tenant(demo["tenant_id"])
    first = min(r.recorded_at for r in STORE.readings_for(demo_account.HANDLED, complete=True))
    month = date(first.year, first.month, 1)
    report = proofreports.build(tenant, None, month)
    in_month = max(first, proofreports._at(month))
    hours = (utc_now() - in_month).total_seconds() / 3600
    assert hours > 24
    assert len(report["sensors"]) == len(demo_account.UNITS)
    for unit in report["sensors"]:
        assert unit["watched_hours"] >= round(hours, 1) - 0.2, unit
        assert unit["online_percent"] == 100.0 and unit["longest_gap_minutes"] is None, unit
    assert report["summary"]["hours_watched"] >= round(hours * len(demo_account.UNITS), 1) - 4
    assert report["summary"]["online_percent"] == 100.0
    assert proofreports._floor1(99.97) == 99.9


# --- 4. pretend texts in the insurance pack -------------------------------------------------


def test_the_pack_says_pretend_texts_and_that_nothing_was_sent(demo, api):
    owner = _owner(api)
    handled = _alert(demo["tenant_id"], demo_account.HANDLED)
    pack = api.post(f"/api/claims/{handled.incident_id}/packet", headers=owner).json()
    assert all(d["demo"] for d in pack["response"]["sms_deliveries"])
    sent = next(t for t in pack["response"]["timeline"] if t["event"] == "Alert dispatched by SMS")
    assert sent["detail"] == "3 pretend texts (demo); nothing was sent."
    assert pack["response"]["notified"]["demo"] is True
    told = claims.cover_facts(pack)["told"]
    assert told.startswith("The alert went out as 3 pretend texts (demo) at "), told
    assert told.endswith("; nothing was sent. It was acknowledged 7 minutes after the alert opened."), told
    assert "texted to" not in told
    assert "delivered" not in sent["detail"]
    # The console's row says "Alert opened", not "Alert texted", for them.
    assert "told.delivered && !told.demo ? \"Alert texted\"" in CONSOLE_JS


def test_real_receipts_are_still_texted_and_delivered():
    rows = claims._delivery_rows([{"channel": "sms", "delivered": True, "status": "delivered", "to": "+15550100"},
                                  {"channel": "sms", "delivered": False, "status": "failed", "to": "+15550101"}], None)
    assert [r["demo"] for r in rows] == [False, False]
    assert claims._texted_words(rows) == "2 on the roster texted, 1 delivered."
    mixed = rows + claims._delivery_rows([{"channel": "sms", "delivered": True, "demo": True}], None)
    assert claims._texted_words(mixed) == "3 on the roster texted, 2 delivered."


# --- 5. plainer words on the owner's screens -------------------------------------------------


def test_todays_being_dealt_with_banner_agrees_with_the_dialog():
    verdict = _function(SIMPLE_JS, "verdict")
    assert "</b> once it is cold again" not in verdict
    assert verdict.count("when the job is done: it closes\n            by itself once it's cold.") == 2
    assert "it closes by itself once it is cold." in (STATIC / "answer.js").read_text()


def test_the_console_bar_says_being_dealt_with_once_somebody_has_it():
    bar = _function(CONSOLE_JS, "renderNeedsBar")
    assert 'const kicker = i.state === "open" ? "Needs you now" : "Being dealt with";' in bar
    assert '<span class="needs-bar-kicker">${kicker}</span>' in bar


def test_spending_limits_and_things_to_do_are_under_advanced_only():
    for card in ("find-spend-controls", "find-tasks"):
        tag = re.search(r'<section [^>]*id="%s"[^>]*>' % card, CONSOLE_HTML).group(0)
        assert 'data-advanced-only="1"' in tag, card
    assert 'card.getAttribute("data-advanced-only") === "1"\n    || (card.getAttribute("data-small-advanced")' in CONSOLE_JS


def test_no_provider_buttons_on_the_agreement_and_the_cancel_rule_once():
    assert ">Bill what is due<" not in CONSOLE_JS and "contract-bill" not in CONSOLE_JS
    assert ">Renew</button>" not in CONSOLE_JS
    assert "Start a new ${c.term_years}-year term now" in CONSOLE_JS
    contract = _function(CONSOLE_JS, "renderContract")
    signed = contract[contract.index("const rows = (contract.schedule"):]
    signed = signed[:signed.index("wireAddOnButtons(c);")]
    assert "yearly.cancellation" not in signed


def test_one_date_style_on_the_team_tab_the_agreement_and_the_pause(demo, api):
    assert "function date(iso)" in WORDS_JS and "date, person" in WORDS_JS
    assert "esc(s.starts_local)" not in CONSOLE_JS and "schedule[0].ends_local" not in CONSOLE_JS
    # A turn has its date: "Fri 2:00 PM to Fri 2:00 PM" said nothing.
    assert "CLXWords.dateTime(s.starts_at)" in CONSOLE_JS
    assert "until ${esc(CLXWords.clock(schedule[0].ends_at))}" in CONSOLE_JS
    assert "Next invoice ${esc(CLXWords.date(c.next_billing_at))}" in CONSOLE_JS
    assert "Ends ${esc(CLXWords.date(c.ends_at))}" in CONSOLE_JS
    assert ".slice(0, 10))}, when what you have paid" not in CONSOLE_JS
    options = api.get("/api/leaving", headers=_owner(api))
    assert options.status_code == 200, options.text
    assert options.json()["pause_options"]
    assert all(o["resume_at"] for o in options.json()["pause_options"])
    assert "CLXWords.date(o.resume_at)" in CONSOLE_JS


def test_whole_dollars_beside_whole_dollar_prices_and_readings_with_their_tenth():
    assert '" — $" + Math.round(yearly).toLocaleString() + " a year"' in CONSOLE_JS
    assert "Number.isInteger(reward)" in CONSOLE_JS
    timeline = _function(CONSOLE_JS, "incidentTimeline")
    assert '"Worst so far"}: ${oneDecimal(peak)}${u}' in timeline
    assert "${esc(tenth(a.worst))}°" in PROOF_JS


def test_the_agreement_table_stays_inside_its_card_and_the_hero_lines_match():
    assert '<div class="term-scroll"><table class="term-table">' in CONSOLE_JS
    assert ".term-scroll { max-width: 100%; overflow-x: auto; }" in THEME
    assert "A freezer fails at two." in (STATIC / "signup.html").read_text()
    assert "A fridge fails at two" not in (STATIC / "signup.html").read_text()
    assert "A freezer fails at two." in (STATIC / "index.html").read_text()


def test_version_is_3_40_0():
    # 3.41.0 followed; its entry stays in the changelog.
    assert "## 3.40.0" in (ROOT / "CHANGELOG.md").read_text()
