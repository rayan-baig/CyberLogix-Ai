"""The monthly compliance summary: right figures, right people, once.

Each test builds last month in the store directly (readings and alerts at
known times inside it, and some just outside it) and then reads the
report the way an owner gets it: through the mail transport, as it would
leave the building.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

import mail
import monthly
from digest import DIGEST_HOUR_UTC
from store import STORE, evaluate_breach, utc_now

PASSWORD = "correct-horse-battery"


def _now():
    """The second of this month, after the digest hour: the report is due."""
    today = utc_now()
    return today.replace(day=2, hour=min(23, DIGEST_HOUR_UTC + 1), minute=5,
                         second=0, microsecond=0)


NOW = _now()
FIRST, LAST = monthly.previous_month(NOW)


def at(day, hour=12, minute=0):
    """A moment on `day` of last month."""
    return datetime.combine(FIRST + timedelta(days=day - 1), time(hour, minute),
                            tzinfo=timezone.utc)


def _reading(sensor_id, temp, when):
    sensor = STORE.get_sensor(sensor_id)
    breached = evaluate_breach(sensor.industry_vertical, temp) is not None
    STORE.record_reading(sensor, temp, None, breached, at=when)
    sensor = STORE.get_sensor(sensor_id)
    sensor.last_seen = max(sensor.last_seen or when, when)
    STORE._db.put("sensor", sensor_id, sensor.to_row())


def _reporting_now(*sensor_ids):
    """Heard from a minute before the report runs, so none is offline."""
    for sensor_id in sensor_ids:
        sensor = STORE.get_sensor(sensor_id)
        sensor.last_seen = NOW - timedelta(minutes=1)
        STORE._db.put("sensor", sensor_id, sensor.to_row())


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


@pytest.fixture()
def grill(api, operator_factory, sensor_factory):
    """Harbor Grill: two units, a month of readings, three alerts.

    The walk-in went warm once on the 4th (Dana answered in 6 minutes);
    the bar freezer on the 10th (Sam, the cook, in 20) and again on the
    20th, which nobody has answered and is still open.
    """
    headers, tenant, _ = operator_factory(company_name="Harbor Grill",
                                          email="dana@grill.example")
    tid = tenant["tenant_id"]
    sensor_factory(headers, "HG-WALKIN", "restaurant", "Kitchen walk-in")
    sensor_factory(headers, "HG-BAR", "restaurant", "Bar freezer")
    for email, name, role in (("lee@grill.example", "Lee Park", "owner"),
                              ("sam@grill.example", "Sam Cook", "operator"),
                              ("val@grill.example", "Val Moss", "viewer")):
        made = api.post("/api/accounts/users", headers=headers, json={
            "email": email, "full_name": name, "password": PASSWORD, "role": role})
        assert made.status_code == 201, made.text

    # Outside the month on both sides: never counted.
    _reading("HG-WALKIN", 50.0, at(1) - timedelta(days=3))
    _alert(tid, "HG-WALKIN", 50.0, at(1) - timedelta(days=3),
           acked_by="Dana Reyes <dana@grill.example>", ack_after=1, closed_after=30)

    # The walk-in: 3 days, 4 readings a day, two warm ones on the 4th.
    for day in (3, 4, 5):
        for hour in (6, 12, 18, 23):
            temp = 40.0 if (day == 4 and hour in (12, 18)) else 10.0
            _reading("HG-WALKIN", temp, at(day, hour))
    _alert(tid, "HG-WALKIN", 40.0, at(4, 12),
           acked_by="Dana Reyes <dana@grill.example>", ack_after=6, closed_after=90)

    # The bar freezer: 5 readings, warm on the 10th and on the 20th.
    for day, temp in ((9, 5.0), (10, 45.0), (11, 4.0), (20, 39.0), (21, 6.0)):
        _reading("HG-BAR", temp, at(day))
    _alert(tid, "HG-BAR", 45.0, at(10), acked_by="Sam Cook <sam@grill.example>",
           ack_after=20, closed_after=60)
    _alert_open = _alert(tid, "HG-BAR", 39.0, at(20))

    # This month already: not last month's business.
    _reading("HG-BAR", 7.0, NOW - timedelta(hours=2))
    _reporting_now("HG-WALKIN", "HG-BAR")
    return {"headers": headers, "tenant_id": tid, "open": _alert_open}


@pytest.fixture()
def bakery(api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(company_name="Harbor Bakery",
                                          email="kim@bakery.example",
                                          full_name="Kim Rowe")
    tid = tenant["tenant_id"]
    sensor_factory(headers, "HB-PROOF", "restaurant", "Proving fridge")
    for day in (2, 3):
        _reading("HB-PROOF", 50.0, at(day))
    _alert(tid, "HB-PROOF", 50.0, at(2), acked_by="Kim Rowe <kim@bakery.example>",
           ack_after=3, closed_after=10)
    _reporting_now("HB-PROOF")
    return {"headers": headers, "tenant_id": tid}


@pytest.fixture()
def public_url(monkeypatch):
    monkeypatch.setattr(mail, "PUBLIC_BASE_URL", "https://cyberlogix.example.com")


def _messages(mailbox, address):
    return [m for m in mailbox if m["To"] == address
            and "compliance summary" in m["Subject"]]


def _text(message):
    return message.get_body(preferencelist=("plain",)).get_content()


# ---- the figures -------------------------------------------------------------


def test_the_figures_are_last_months_and_add_up(grill):
    report = monthly.summary(STORE.get_tenant(grill["tenant_id"]), FIRST, LAST, NOW)

    assert report["month"] == FIRST.strftime("%Y-%m")
    assert report["units"] == 2
    # 12 walk-in readings and 5 bar readings inside the month; the one
    # three days before it and the one this month are not.
    assert report["readings"] == 17
    assert report["out_of_range_readings"] == 4
    assert report["in_range_percent"] == round(13 / 17 * 100, 1)
    assert report["breach_count"] == 3
    assert report["acknowledged_count"] == 2
    assert report["unanswered_count"] == 1
    assert report["median_minutes_to_acknowledge"] == 13
    assert report["who_acknowledged"] == [
        {"name": "Dana Reyes", "acknowledged": 1, "median_minutes": 6,
         "slowest_minutes": 6},
        {"name": "Sam Cook", "acknowledged": 1, "median_minutes": 20,
         "slowest_minutes": 20},
    ]
    [still_open] = report["open_problems"]["alerts"]
    assert still_open["unit"] == "Bar freezer" and not still_open["acknowledged"]
    assert report["open_problem_count"] == 1


def test_the_email_says_the_same_things(grill, mailbox, public_url):
    result = monthly.run_monthly_reports(NOW)
    assert result["business_count"] == 1

    [message] = _messages(mailbox, "dana@grill.example")
    text = _text(message)
    assert FIRST.strftime("%B %Y") in message["Subject"]
    assert "3 times out of range" in message["Subject"] and "1 open" in message["Subject"]
    assert "Units watched: 2" in text
    assert "Readings kept: 17" in text
    assert "Out of range: 3 times, 2 acknowledged (half within 13 min), " \
           "1 never acknowledged" in text
    assert "Dana Reyes: 1, typically in 6 min" in text
    assert "Sam Cook: 1, typically in 20 min" in text
    assert "Bar freezer: out of range since" in text
    assert "nobody has acknowledged it" in text
    # Names, not the addresses they signed in with.
    assert "sam@grill.example" not in text and "dana@grill.example" not in text


def test_it_links_to_the_inspector_log_for_that_month_and_business(
    grill, mailbox, public_url
):
    monthly.run_monthly_reports(NOW)
    text = _text(_messages(mailbox, "dana@grill.example")[0])
    link = (f"https://cyberlogix.example.com/console#inspector-log?business="
            f"{grill['tenant_id']}&from={FIRST.isoformat()}&to={LAST.isoformat()}")
    assert link in text


def test_it_has_a_plain_text_part_an_unsubscribe_link_and_the_footer(
    grill, mailbox, public_url
):
    monthly.run_monthly_reports(NOW)
    [message] = _messages(mailbox, "dana@grill.example")

    assert message.get_content_type() == "multipart/alternative"
    plain = message.get_body(preferencelist=("plain",))
    rich = message.get_body(preferencelist=("html",))
    assert plain.get_content_type() == "text/plain"
    assert rich.get_content_type() == "text/html"
    for part in (plain.get_content(), rich.get_content()):
        assert "/api/mail/unsubscribe?address=dana%40grill.example" in part
        assert "CyberLogix is a product of MTRX Horizyn." in part
    assert message["List-Unsubscribe"].startswith(
        "<https://cyberlogix.example.com/api/mail/unsubscribe")
    assert "Horizon" not in plain.get_content()


def test_the_html_part_escapes_what_a_customer_typed(
    api, operator_factory, sensor_factory, mailbox
):
    headers, tenant, _ = operator_factory(company_name="<b>Grill</b> & Co",
                                          email="esc@grill.example")
    sensor_factory(headers, "ESC-1", "restaurant", "<script>x</script>")
    _reading("ESC-1", 10.0, at(3))
    monthly.run_monthly_reports(NOW)
    [message] = _messages(mailbox, "esc@grill.example")
    rich = message.get_body(preferencelist=("html",)).get_content()
    assert "<b>Grill</b>" not in rich and "&lt;b&gt;Grill&lt;/b&gt; &amp; Co" in rich


# ---- who, and how often --------------------------------------------------------


def test_owners_only(grill, mailbox):
    monthly.run_monthly_reports(NOW)
    assert len(_messages(mailbox, "dana@grill.example")) == 1
    assert len(_messages(mailbox, "lee@grill.example")) == 1
    assert _messages(mailbox, "sam@grill.example") == []
    assert _messages(mailbox, "val@grill.example") == []


def test_a_removed_owner_is_not_sent_it(api, grill, mailbox):
    users = api.get("/api/accounts/users", headers=grill["headers"]).json()["users"]
    lee = next(u for u in users if u["email"] == "lee@grill.example")
    api.post(f"/api/accounts/users/{lee['user_id']}/disable", headers=grill["headers"])
    monthly.run_monthly_reports(NOW)
    assert _messages(mailbox, "lee@grill.example") == []
    assert len(_messages(mailbox, "dana@grill.example")) == 1


def test_once_a_month_however_often_the_pass_runs(grill, mailbox):
    monthly.run_monthly_reports(NOW)
    for hours in (1, 5, 30, 100):
        again = monthly.run_monthly_reports(NOW + timedelta(hours=hours))
        assert again.get("sent_count", 0) == 0
    assert len(_messages(mailbox, "dana@grill.example")) == 1


def test_once_a_month_through_a_restart(grill, mailbox):
    monthly.run_monthly_reports(NOW)
    STORE._forget_locked()
    STORE.load()
    monthly.run_monthly_reports(NOW + timedelta(hours=2))
    assert len(_messages(mailbox, "dana@grill.example")) == 1


def test_once_a_month_even_if_the_mark_was_lost(grill, mailbox):
    """The business's mark is belt; each owner's message key is braces."""
    monthly.run_monthly_reports(NOW)
    STORE._db.delete(monthly.KIND, f"{grill['tenant_id']}:{monthly.month_key(FIRST)}")
    monthly.run_monthly_reports(NOW + timedelta(hours=2))
    assert len(_messages(mailbox, "dana@grill.example")) == 1


def test_an_owner_added_after_it_went_is_not_sent_last_month(api, grill, mailbox):
    monthly.run_monthly_reports(NOW)
    api.post("/api/accounts/users", headers=grill["headers"], json={
        "email": "new@grill.example", "full_name": "New Owner",
        "password": PASSWORD, "role": "owner"})
    monthly.run_monthly_reports(NOW + timedelta(hours=2))
    assert _messages(mailbox, "new@grill.example") == []


def test_one_pass_at_a_time(grill, mailbox):
    """A second pass that finds one running steps aside."""
    assert monthly._pass_lock.acquire(blocking=False)
    try:
        result = monthly.run_monthly_reports(NOW)
    finally:
        monthly._pass_lock.release()
    assert result["skipped"] == "another pass is still running"
    assert _messages(mailbox, "dana@grill.example") == []


def test_only_in_the_first_week_of_the_month(grill, mailbox):
    late = NOW.replace(day=9)
    assert monthly.run_monthly_reports(late)["sent_count"] == 0
    early = NOW.replace(day=1, hour=max(0, DIGEST_HOUR_UTC - 1))
    if DIGEST_HOUR_UTC > 0:
        assert monthly.run_monthly_reports(early)["sent_count"] == 0
    assert _messages(mailbox, "dana@grill.example") == []
    assert monthly.run_monthly_reports(NOW)["sent_count"] == 2


def test_the_money_pass_sends_it(grill, mailbox, monkeypatch):
    import digest

    sent = digest.run_digests(NOW)
    assert sent["monthly_reports"]["business_count"] == 1
    assert len(_messages(mailbox, "dana@grill.example")) == 1


# ---- only that business ------------------------------------------------------


def test_each_business_gets_its_own_and_nothing_of_the_others(grill, bakery, mailbox):
    monthly.run_monthly_reports(NOW)
    grill_text = _text(_messages(mailbox, "dana@grill.example")[0])
    bakery_text = _text(_messages(mailbox, "kim@bakery.example")[0])

    assert "Harbor Grill" in grill_text and "Harbor Bakery" not in grill_text
    assert "Proving fridge" not in grill_text and "Kim Rowe" not in grill_text
    assert "Harbor Bakery" in bakery_text and "Harbor Grill" not in bakery_text
    for word in ("Kitchen walk-in", "Bar freezer", "Dana Reyes", "Sam Cook"):
        assert word not in bakery_text
    assert "Readings kept: 2" in bakery_text
    assert _messages(mailbox, "kim@grill.example") == []


def test_a_person_who_owns_two_businesses_gets_two_reports(api, grill, mailbox):
    """One person, two businesses: one report about each, each about its own."""
    added = api.post("/api/accounts/businesses", headers=grill["headers"],
                     json={"company_name": "Harbor Deli",
                           "industry_vertical": "restaurant"})
    assert added.status_code == 201, added.text
    deli = {"X-CyberLogix-Key": added.json()["api_key"]}
    assert api.post("/api/licenses/me/sensors", headers=deli, json={
        "sensor_id": "HD-1", "industry_vertical": "restaurant",
        "location_name": "Deli counter"}).status_code == 201
    _reading("HD-1", 8.0, at(6))

    monthly.run_monthly_reports(NOW)
    reports = _messages(mailbox, "dana@grill.example")
    assert sorted(m["Subject"].split(":")[0] for m in reports) == [
        "Harbor Deli", "Harbor Grill"]
    deli_text = _text(next(m for m in reports if m["Subject"].startswith("Harbor Deli")))
    assert "Bar freezer" not in deli_text and "Readings kept: 1" in deli_text


def test_withheld_while_reports_are_paused(grill, mailbox):
    tenant = STORE.get_tenant(grill["tenant_id"])
    invoice = STORE.create_invoice(tenant, [{"description": "Month",
                                             "amount_usd": 100.0}], period_days=30)
    invoice.due_at = utc_now() - timedelta(days=60)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())

    result = monthly.run_monthly_reports(NOW)
    assert result["business_count"] == 0
    assert _messages(mailbox, "dana@grill.example") == []
    # Settled inside the week: it goes.
    STORE.settle_invoice(invoice, "WIRE-1")
    assert monthly.run_monthly_reports(NOW + timedelta(hours=1))["business_count"] == 1


def test_a_business_with_no_units_is_not_sent_an_empty_report(
    api, operator_factory, mailbox
):
    operator_factory(company_name="Empty Shop", email="empty@shop.example")
    monthly.run_monthly_reports(NOW)
    assert _messages(mailbox, "empty@shop.example") == []


# ---- routes ---------------------------------------------------------------------


def test_an_owner_can_preview_it(api, grill):
    resp = api.get("/api/digest/monthly", headers=grill["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["readings"] == 17 and body["breach_count"] == 3
    assert body["sent"] is False
    assert sorted(body["goes_to"]) == ["Dana Reyes", "Lee Park"]
    assert body["text"].startswith("Harbor Grill: ")

    named = api.get("/api/digest/monthly", headers=grill["headers"],
                    params={"month": FIRST.strftime("%Y-%m")})
    assert named.json()["readings"] == 17
    assert api.get("/api/digest/monthly", headers=grill["headers"],
                   params={"month": "2026-13"}).status_code == 422


def test_the_operator_can_send_it_now(api, grill, mailbox, admin_headers):
    assert api.post("/api/digest/monthly").status_code in (401, 403)
    resp = api.post("/api/digest/monthly", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert len(_messages(mailbox, "dana@grill.example")) == 1
    assert api.post("/api/digest/monthly", headers=admin_headers).json()["sent_count"] == 0


def test_the_console_follows_the_link_in_the_email():
    """The link opens the console on the log card with the month filled in,
    in the business it is about. Driven in a browser by
    tools/console_smoke.js; this pins the wiring."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "console.js").read_text()
    enter = js[js.index("async function enter(token, user)"):]
    enter = enter[:enter.index("\n}\n")]
    assert "followInspectorLink()" in enter
    follow = js[js.index("async function followInspectorLink()"):]
    follow = follow[:follow.index("\n}\n")]
    assert "/api/accounts/businesses/" in follow and "/switch" in follow
    assert 'finder.tab = "proof"' in follow
    assert "history.replaceState" in follow
    # Dates are checked before they reach the form.
    assert "DAY.test(from)" in js and "DAY.test(to)" in js
