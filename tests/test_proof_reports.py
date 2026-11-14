"""Proof of monitoring: a monthly report a business hands its insurer.

What it says (sensors and time online, every alert and how fast it was
acknowledged, the longest gap, who is alerted by role), that only the
owner makes and sees it, and the public check: an unguessable code, an
edited report that fails, a forged code that finds nothing, a link that
stops working when the owner switches it off, and a page that shows the
business name and the numbers and nothing personal.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

import pytest

import proofreports
from store import GAP_KIND, STORE, evaluate_breach, utc_now

PASSWORD = "correct-horse-battery"


def _reading(sensor_id, temp, when):
    sensor = STORE.get_sensor(sensor_id)
    breached = evaluate_breach(sensor.industry_vertical, temp) is not None
    STORE.record_reading(sensor, temp, None, breached, at=when)


def _alert(tenant_id, sensor_id, temp, opened, acked_by=None, ack_after=None,
           closed_after=None):
    sensor = STORE.get_sensor(sensor_id)
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant_id, sensor=sensor, temperature_fahrenheit=temp,
        breach_details="too warm", sms_text="too warm", sms_dispatch_source="test")
    incident.opened_at = opened
    if acked_by:
        incident.acknowledged_at = opened + timedelta(minutes=ack_after)
        incident.acknowledged_by = acked_by
    if closed_after is not None:
        incident.resolved_at = opened + timedelta(minutes=closed_after)
    STORE._db.put("incident", incident.incident_id, incident.to_row())
    return incident


def _start_of_month(now):
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@pytest.fixture()
def grill(api, operator_factory, sensor_factory):
    """Harbor Grill this month: one site, two units, an on-call list, an alert.

    Gaps are tracked from the start of the month; the walk-in reports every
    five minutes from then except for one two-hour silence, and the bar
    freezer has been silent for the last three hours.
    """
    headers, tenant, _ = operator_factory(company_name="Harbor Grill",
                                          email="dana@grill.example",
                                          full_name="Dana Reyes")
    tid = tenant["tenant_id"]
    site = api.post("/api/sites", headers=headers,
                    json={"name": "Harbor Street"}).json()
    sensor_factory(headers, "HG-WALKIN", "restaurant", "Kitchen walk-in")
    sensor_factory(headers, "HG-BAR", "restaurant", "Bar freezer")
    for sensor_id in ("HG-WALKIN", "HG-BAR"):
        assert api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
                        json={"sensor_id": sensor_id}).status_code == 200
    for name, phone, sms, voice, where in (
            ("Sam Cook", "+1-555-0171", True, True, site["site_id"]),
            ("Lee Park", "+1-555-0172", True, False, None)):
        made = api.post("/api/contacts", headers=headers, json={
            "full_name": name, "phone": phone, "receives_sms": sms,
            "receives_voice": voice, "site_id": where})
        assert made.status_code == 201, made.text

    now = utc_now()
    start = _start_of_month(now)
    if now - start < timedelta(hours=8):           # the 1st, just after midnight
        start = now - timedelta(hours=8)
    STORE._db.put("meta", "gap_log_since", {"at": start.isoformat()})
    STORE._gap_since = None
    for sensor_id in ("HG-WALKIN", "HG-BAR"):
        sensor = STORE.get_sensor(sensor_id)
        sensor.registered_at = start
        sensor.last_seen = None
        STORE._db.put("sensor", sensor_id, sensor.to_row())
    STORE._db.delete_many(GAP_KIND, [i for i, _ in STORE._db.rows(GAP_KIND)])
    # The walk-in: every five minutes, but silent from 3h to 5h after start.
    silence = (start + timedelta(hours=3), start + timedelta(hours=5))
    t = start
    while t <= now:
        if not silence[0] < t < silence[1]:
            _reading("HG-WALKIN", 36.0, t)
        t += timedelta(minutes=5)
    # The bar freezer: every five minutes until three hours ago.
    t = start
    while t <= now - timedelta(hours=3):
        _reading("HG-BAR", 5.0, t)
        t += timedelta(minutes=5)
    alert = _alert(tid, "HG-WALKIN", 48.0, start + timedelta(hours=1),
                   acked_by="Dana Reyes <dana@grill.example>", ack_after=7,
                   closed_after=40)
    return {"headers": headers, "tenant_id": tid, "site": site, "start": start,
            "now": now, "silence": silence, "alert": alert}


def _make(api, grill, month=None, site=True):
    body = {"month": month or grill["now"].strftime("%Y-%m")}
    if site:
        body["site_id"] = grill["site"]["site_id"]
    made = api.post("/api/proof-reports", headers=grill["headers"], json=body)
    assert made.status_code == 201, made.text
    return made.json()


# ---- what it says ------------------------------------------------------------


def test_the_report_counts_sensors_time_online_alerts_and_the_longest_gap(api, grill):
    made = _make(api, grill)
    report = made["report"]
    s = report["summary"]
    assert report["business"] == "Harbor Grill"
    assert report["location"] == {"name": "Harbor Street", "scope": "one"}
    assert s["sensors_watched"] == 2
    assert s["alerts"] == 1 and s["alerts_acknowledged"] == 1
    assert s["median_minutes_to_acknowledge"] == 7

    [alert] = report["alerts"]
    assert alert["unit"] == "Kitchen walk-in"
    assert alert["minutes_to_acknowledge"] == 7
    assert alert["acknowledged_by_role"] == "Owner"   # a role, never the name

    # The walk-in's two-hour silence: 120 minutes long; offline for 90 of
    # them (the first 30 count as online, the product's own rule).
    walk = next(u for u in report["sensors"] if u["name"] == "Kitchen walk-in")
    watched = (grill["now"] - grill["start"]).total_seconds()
    expected = math.floor(100 * (watched - 90 * 60) / watched * 10) / 10
    assert walk["online_percent"] == pytest.approx(expected, abs=0.1)
    assert walk["longest_gap_minutes"] == 120

    # The bar freezer has been silent for three hours and still is.
    bar = next(u for u in report["sensors"] if u["name"] == "Bar freezer")
    assert bar["longest_gap_minutes"] == pytest.approx(180, abs=6)
    gap = report["longest_gap"]
    assert gap["unit"] == "Bar freezer" and gap["still_going"] is True
    assert s["longest_gap_minutes"] == gap["minutes"]
    assert report["coverage"]["covers_the_whole_period"] is True


def test_who_gets_alerted_is_roles_only(api, grill):
    report = _make(api, grill)["report"]
    alerted = report["alerted"]
    assert [p["gets"] for p in alerted["on_call"]] == ["texts and phone calls", "texts"]
    assert [p["covers"] for p in alerted["on_call"]] == ["this location", "every location"]
    assert {p["role"] for p in alerted["on_call"]} == {"On-call person"}
    assert {"role": "Owners", "count": 1} in alerted["team"]
    text = str(report)
    for personal in ("Sam Cook", "Lee Park", "Dana", "Reyes", "555-017",
                     "5550171", "@grill.example", "ops@example.com"):
        assert personal not in text, personal


def test_a_unit_named_after_a_person_is_not_printed(api, grill, sensor_factory):
    sensor_factory(grill["headers"], "HG-DANA", "restaurant", "Dana's fridge")
    api.post(f"/api/sites/{grill['site']['site_id']}/sensors", headers=grill["headers"],
             json={"sensor_id": "HG-DANA"})
    report = _make(api, grill)["report"]
    assert "Dana" not in str(report["sensors"])
    assert any(u["name"].startswith("Unit ") for u in report["sensors"])


def test_every_location_covers_the_whole_business(api, grill):
    made = _make(api, grill, site=False)
    assert made["report"]["location"] == {"name": "Every location", "scope": "all"}
    assert made["location"] == "Every location"


def test_a_gap_is_written_down_when_it_ends_and_split_by_a_late_reading(api, grill):
    rows = [r for _, r in STORE._db.rows(GAP_KIND) if r["sensor_id"] == "HG-WALKIN"]
    assert len(rows) == 1 and rows[0]["minutes"] == pytest.approx(120, abs=0.1)
    # A gateway catches up: a reading from the middle of the silence.
    middle = grill["silence"][0] + timedelta(minutes=55)
    _reading("HG-WALKIN", 36.0, middle)
    rows = sorted((r for _, r in STORE._db.rows(GAP_KIND) if r["sensor_id"] == "HG-WALKIN"),
                  key=lambda r: r["start"])
    assert [r["minutes"] for r in rows] == [pytest.approx(55, abs=0.1),
                                             pytest.approx(65, abs=0.1)]


def test_time_before_gaps_were_recorded_is_said_not_counted(api, grill):
    later = grill["start"] + timedelta(hours=6)
    STORE._db.put("meta", "gap_log_since", {"at": later.isoformat()})
    STORE._gap_since = None
    report = _make(api, grill)["report"]
    assert report["coverage"]["covers_the_whole_period"] is False
    assert "not counted" in report["coverage"]["note"]
    walk = next(u for u in report["sensors"] if u["name"] == "Kitchen walk-in")
    assert walk["longest_gap_minutes"] is None   # the silence came before


# ---- who may make and see it ------------------------------------------------


def test_only_an_owner_makes_or_lists_reports(api, grill):
    made = api.post("/api/accounts/users", headers=grill["headers"], json={
        "email": "sam@grill.example", "full_name": "Sam", "password": PASSWORD,
        "role": "operator"})
    assert made.status_code == 201
    token = api.post("/api/accounts/login", json={
        "email": "sam@grill.example", "password": PASSWORD}).json()["token"]
    operator = {"Authorization": f"Bearer {token}"}
    month = grill["now"].strftime("%Y-%m")
    assert api.post("/api/proof-reports", headers=operator,
                    json={"month": month}).status_code == 403
    assert api.get("/api/proof-reports", headers=operator).status_code == 403
    assert api.get("/api/proof-reports").status_code == 401


def test_another_business_cannot_open_or_switch_off_a_report(api, grill, operator_factory):
    made = _make(api, grill)
    other, _, _ = operator_factory(company_name="Rival Diner", email="boss@rival.example")
    rid = made["report_id"]
    assert api.get(f"/api/proof-reports/{rid}", headers=other).status_code == 404
    assert api.post(f"/api/proof-reports/{rid}/revoke", headers=other).status_code == 404
    assert api.get("/api/proof-reports", headers=other).json()["reports"] == []
    assert api.get(f"/api/proof-reports/{rid}", headers=grill["headers"]).status_code == 200


def test_the_owner_lists_reports_and_locations(api, grill):
    _make(api, grill)
    seen = api.get("/api/proof-reports", headers=grill["headers"]).json()
    [row] = seen["reports"]
    assert row["state"] == "genuine" and "report" not in row
    assert seen["locations"] == [{"site_id": grill["site"]["site_id"], "name": "Harbor Street"}]


def test_a_month_that_has_not_started_or_a_strange_location_is_refused(api, grill):
    nxt = (grill["now"].replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
    assert api.post("/api/proof-reports", headers=grill["headers"],
                    json={"month": nxt}).status_code == 422
    assert api.post("/api/proof-reports", headers=grill["headers"],
                    json={"month": "2001-01"}).status_code == 422
    assert api.post("/api/proof-reports", headers=grill["headers"],
                    json={"month": grill["now"].strftime("%Y-%m"),
                          "site_id": "SITE-999999"}).status_code == 404


def test_a_location_with_no_sensors_makes_no_report(api, grill):
    empty = api.post("/api/sites", headers=grill["headers"], json={"name": "Pier Kiosk"}).json()
    resp = api.post("/api/proof-reports", headers=grill["headers"],
                    json={"month": grill["now"].strftime("%Y-%m"), "site_id": empty["site_id"]})
    assert resp.status_code == 422
    assert STORE._db.all(proofreports.KIND) == []


# ---- the code ----------------------------------------------------------------


def test_the_verify_code_is_eighty_random_bits():
    codes = {proofreports.new_code() for _ in range(500)}
    assert len(codes) == 500
    for code in codes:
        assert re.fullmatch(r"[A-Z2-7]{16}", code)
    # 16 characters of base32 are 80 bits, over the 64 asked for.
    assert proofreports.CODE_BYTES * 8 == 80 >= 64
    assert len(proofreports.new_code()) * 5 == 80


def test_a_made_up_code_finds_nothing(api, grill):
    made = _make(api, grill)
    real = made["verify_code"].replace("-", "")
    for fake in ("AAAA-BBBB-CCCC-DDDD", real[:-1] + ("A" if real[-1] != "A" else "B"),
                 "not a code", real[:12]):
        resp = api.get(f"/api/proof-reports/verify/{fake}")
        assert resp.status_code == 404, fake
        assert resp.json()["state"] == "unknown"
        assert "business" not in resp.json()


def test_a_forged_report_with_its_own_code_fails_the_seal(api, grill):
    made = _make(api, grill)
    real = STORE._db.get(proofreports.KIND, made["report_id"])
    forged = dict(real)
    forged["report_id"] = "PRF-999999"
    forged["code"] = proofreports.new_code()
    forged["body"] = dict(real["body"], business="Somebody Else Ltd")
    forged["content_hash"] = proofreports.content_hash(forged["body"])
    STORE._db.put(proofreports.KIND, forged["report_id"], forged)
    resp = api.get(f"/api/proof-reports/verify/{forged['code']}")
    assert resp.status_code == 409 and resp.json()["state"] == "changed"
    assert "Somebody Else" not in resp.text


def test_moving_a_real_seal_onto_another_code_fails(api, grill):
    made = _make(api, grill)
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    row["code"] = proofreports.new_code()
    STORE._db.put(proofreports.KIND, row["report_id"], row)
    assert api.get(f"/api/proof-reports/verify/{row['code']}").json()["state"] == "changed"


# ---- the check ---------------------------------------------------------------


def test_a_genuine_report_verifies_with_its_numbers_and_nothing_personal(api, grill):
    made = _make(api, grill)
    resp = api.get(f"/api/proof-reports/verify/{made['verify_code']}")
    assert resp.status_code == 200
    seen = resp.json()
    assert seen["state"] == "genuine"
    assert seen["business"] == "Harbor Grill"
    assert seen["fingerprint"] == made["fingerprint"]
    assert seen["summary"]["sensors_watched"] == 2
    assert seen["summary"]["alerts"] == 1
    text = resp.text
    for private in ("Harbor Street", "Kitchen walk-in", "Bar freezer", "On-call",
                    "Dana", "Sam", "555", "@", "HG-", grill["tenant_id"]):
        assert private not in text, private
    # Typed in lower case, with spaces instead of dashes, it still checks.
    loose = made["verify_code"].lower().replace("-", " ")
    assert api.get(f"/api/proof-reports/verify/{loose}").json()["state"] == "genuine"


def test_an_edited_report_fails_verification(api, grill):
    made = _make(api, grill)
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    row["body"]["summary"]["online_percent"] = 100.0
    row["body"]["summary"]["alerts"] = 0
    STORE._db.put(proofreports.KIND, row["report_id"], row)
    resp = api.get(f"/api/proof-reports/verify/{made['verify_code']}")
    assert resp.status_code == 409
    assert resp.json()["state"] == "changed"
    assert "summary" not in resp.json() and "business" not in resp.json()
    # The owner is told too.
    assert api.get(f"/api/proof-reports/{made['report_id']}",
                   headers=grill["headers"]).json()["state"] == "changed"


def test_an_edit_with_a_fresh_hash_still_fails_without_the_key(api, grill):
    made = _make(api, grill)
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    row["body"]["summary"]["alerts"] = 0
    row["content_hash"] = proofreports.content_hash(row["body"])
    STORE._db.put(proofreports.KIND, row["report_id"], row)
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").json()["state"] == "changed"


def test_a_configured_key_seals_it_and_a_missing_key_cannot_check(api, grill, monkeypatch):
    monkeypatch.setattr(proofreports, "PROOF_REPORT_KEY", "a-long-random-test-value")
    made = _make(api, grill)
    row = STORE._db.get(proofreports.KIND, made["report_id"])
    assert row["key_source"] == "setting"
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").json()["state"] == "genuine"
    monkeypatch.setattr(proofreports, "PROOF_REPORT_KEY", "a-different-value")
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").json()["state"] == "changed"
    monkeypatch.setattr(proofreports, "PROOF_REPORT_KEY", "")
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").json()["state"] == "unchecked"


def test_a_revoked_link_stops_working(api, grill):
    made = _make(api, grill)
    code = made["verify_code"]
    assert api.get(f"/verify/{code}").status_code == 200
    off = api.post(f"/api/proof-reports/{made['report_id']}/revoke", headers=grill["headers"])
    assert off.status_code == 200 and off.json()["state"] == "revoked"
    resp = api.get(f"/api/proof-reports/verify/{code}")
    assert resp.status_code == 410
    assert resp.json()["state"] == "revoked" and "summary" not in resp.json()
    page = api.get(f"/verify/{code}")
    assert page.status_code == 410
    assert "switched this link off" in page.text and "Harbor Grill" not in page.text
    # Switching it off twice is the same as once.
    assert api.post(f"/api/proof-reports/{made['report_id']}/revoke",
                    headers=grill["headers"]).status_code == 200


def test_the_public_page_shows_the_business_name_and_the_numbers(api, grill):
    made = _make(api, grill)
    page = api.get(f"/verify/{made['verify_code']}")
    assert page.status_code == 200
    text = page.text
    assert "Genuine" in text and "Harbor Grill" in text
    assert made["fingerprint"] in text
    assert "Sensors watched" in text and "Longest gap in data" in text
    for private in ("Harbor Street", "Kitchen walk-in", "Dana", "555"):
        assert private not in text, private
    assert "not an insurer" in text
    assert page.headers["X-Robots-Tag"].startswith("noindex")


def test_the_page_escapes_a_hostile_business_name(api, grill):
    tenant = STORE.get_tenant(grill["tenant_id"])
    tenant.company_name = '<img src=x onerror="alert(1)"> & Sons'
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())
    made = _make(api, grill)
    text = api.get(f"/verify/{made['verify_code']}").text
    assert "<img src=x" not in text and "&lt;img src=x" in text


def test_the_code_entry_page_and_its_redirect(api):
    assert api.get("/verify").status_code == 200
    assert 'name="code"' in api.get("/verify").text
    moved = api.get("/verify", params={"code": "abcd efgh ijkl mnop"},
                    follow_redirects=False)
    assert moved.status_code == 303
    assert moved.headers["location"] == "/verify/ABCD-EFGH-IJKL-MNOP"
    assert api.get("/verify/AAAA-BBBB-CCCC-DDDD").status_code == 404


def test_the_check_is_rate_limited(api, grill):
    made = _make(api, grill)
    limit = proofreports.VERIFY_LIMIT.per_caller
    for _ in range(limit):
        assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").status_code == 200
    refused = api.get(f"/api/proof-reports/verify/{made['verify_code']}")
    assert refused.status_code == 429 and "Retry-After" in refused.headers
    # The page shares the same window, and says so in words.
    page = api.get(f"/verify/{made['verify_code']}")
    assert page.status_code == 429 and "Too many report checks" in page.text
    assert "Retry-After" in page.headers


def test_the_printable_page_is_served_and_holds_nothing(api):
    page = api.get("/proof-report/PRF-000001")
    assert page.status_code == 200
    assert "/static/proof-report.js" in page.text
    assert "@media print" in api.get("/static/proof-report.css").text


def test_reports_pause_for_an_account_far_behind(api, grill, monkeypatch):
    import contracts

    monkeypatch.setattr(contracts, "delinquency", lambda tid: {
        "delinquent": True, "licence_state": "lapsed", "days_overdue": 60,
        "oldest_unpaid": None, "owed_usd": 100.0})
    resp = api.post("/api/proof-reports", headers=grill["headers"],
                    json={"month": grill["now"].strftime("%Y-%m")})
    assert resp.status_code in (402, 403), resp.text


def test_a_report_goes_with_the_business_when_it_deletes_itself(api, grill):
    made = _make(api, grill)
    gone = api.post("/api/accounts/business/erase", headers=grill["headers"],
                    json={"company_name": "Harbor Grill", "password": PASSWORD})
    assert gone.status_code == 200, gone.text
    assert STORE._db.get(proofreports.KIND, made["report_id"]) is None
    assert api.get(f"/api/proof-reports/verify/{made['verify_code']}").status_code == 404
    assert not [r for _, r in STORE._db.rows(GAP_KIND) if r["tenant_id"] == grill["tenant_id"]]


def test_the_console_has_the_card(api):
    from pages import served_page

    page = served_page(api, "/console")
    assert 'id="find-proof-reports"' in page
    assert "/api/proof-reports" in page
    assert "renderProofReports" in page


def test_build_on_a_past_month_reads_only_that_month(api, grill):
    """Straight through build(): last month, with the gap log from before it."""
    tenant = STORE.get_tenant(grill["tenant_id"])
    first_of_this = grill["start"].date().replace(day=1)
    last_month = (first_of_this - timedelta(days=1)).replace(day=1)
    begin = datetime.combine(last_month, datetime.min.time(), tzinfo=timezone.utc)
    STORE._db.put("meta", "gap_log_since", {"at": (begin - timedelta(days=1)).isoformat()})
    STORE._gap_since = None
    for sensor_id in ("HG-WALKIN", "HG-BAR"):
        sensor = STORE.get_sensor(sensor_id)
        sensor.registered_at = begin
        STORE._db.put("sensor", sensor_id, sensor.to_row())
    # A four-hour silence on the walk-in on the 10th of last month.
    STORE._db.put(GAP_KIND, "HG-WALKIN:x", {
        "tenant_id": tenant.tenant_id, "sensor_id": "HG-WALKIN",
        "start": (begin + timedelta(days=9)).isoformat(),
        "end": (begin + timedelta(days=9, hours=4)).isoformat(), "minutes": 240.0})
    body = proofreports.build(tenant, grill["site"]["site_id"], last_month)
    assert body["month_complete"] is True
    assert body["summary"]["alerts"] == 0          # this month's alert is not in it
    walk = next(u for u in body["sensors"] if u["name"] == "Kitchen walk-in")
    assert walk["longest_gap_minutes"] == 240


def test_the_page_rounds_minutes_as_the_printed_report_does():
    assert proofreports._mins(8.5) == "9 min"
    assert proofreports._mins(7.4) == "7 min"
    assert proofreports._mins(150) == "2 h 30 min"
    assert proofreports._mins(None) == "—"
