"""The possible attack or site-wide failure alert.

On 11 October 2026 attackers took over the refrigeration controllers of
hundreds of supermarkets and switched them to defrost overnight. A unit
warming on its own is a door or a compressor and has its own alert. Many
units at one site, or across a business's sites, warming at about the same
time is a power cut, a cold-room door, or somebody at the controls, and it
gets one urgent alert on top: no grace before the call, the whole on-call
list and the owner, quiet hours or not, claimed once.

What these hold to:

* it is raised at one site and across sites, and not below the threshold;
* a learned defrost is respected, and sensors gone silent together are an
  outage, not warming;
* quiet hours do not silence it, and two sweeps at once send it once;
* "1" and the button acknowledge it (and the units in it);
* the console, the inspector's log and the monthly report show it;
* another business's units never count, and its people are never told.
"""

from __future__ import annotations

import threading
import time as clock
from datetime import datetime, timedelta, timezone

import pytest

import automation
import monthly
import notifications
import scheduler
import sitewide
import telemetry
import voice_dispatch
import wording
from records import SITE_WIDE_KIND
from store import STORE, utc_now

OWNER_PHONE = "+1-555-0100"  # tenant_factory's contact_phone


# ---- an estate to break --------------------------------------------------


@pytest.fixture(autouse=True)
def daytime(monkeypatch):
    """Day thresholds unless a test asks for night: the suite runs at any hour."""
    monkeypatch.setenv("CYBERLOGIX_GROUP_ALERT_NIGHT", "00:00-00:00")


@pytest.fixture()
def phones(monkeypatch):
    """Every text and call, captured where the send path is called."""
    sent = {"texts": [], "calls": []}
    lock = threading.Lock()

    def text(to, body, tenant_id=None):
        with lock:
            sent["texts"].append((to, body))
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "SM-1", "detail": "ok"}

    def call(to, script, tenant_id=None, action_url=None):
        with lock:
            sent["calls"].append((to, script))
        return {"channel": "voice", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "CA-1", "detail": "ok"}

    monkeypatch.setattr(telemetry, "send_sms", text)
    monkeypatch.setattr(automation, "send_sms", text)
    monkeypatch.setattr(voice_dispatch, "place_voice_call", call)
    return sent


def site_wide_texts(sent):
    return [(to, body) for to, body in sent["texts"] if "site-wide" in body
            or "Possible problem across" in body]


def _estate(tenant_factory, sensor_factory, sites, company="Harbor Grill",
            plan="enterprise", prefix=""):
    """A business with `sites` = {"Main Street": 7, ...} restaurant units.

    Returns (tenant, {site name: site_id}, {site name: [sensor ids]}).
    """
    headers, tenant = tenant_factory(plan=plan, company_name=company)
    site_ids, units = {}, {}
    for s_index, (name, count) in enumerate(sites.items()):
        site = STORE.create_site(tenant["tenant_id"], name)
        site_ids[name] = site.site_id
        units[name] = []
        for n in range(count):
            sensor_id = f"{prefix}S{s_index}-U{n + 1}"
            sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant",
                           location=f"{name} freezer {n + 1}")
            STORE.assign_sensor_to_site(STORE.get_sensor(sensor_id), site.site_id)
            units[name].append(sensor_id)
    return STORE.get_tenant(tenant["tenant_id"]), site_ids, units


def _readings(sensor_id, points):
    """(minutes ago, °F) pairs, oldest first."""
    sensor = STORE.get_sensor(sensor_id)
    now = utc_now()
    for ago, temp in sorted(points, key=lambda p: -p[0]):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=temp,
                             humidity_percent=None, breached=temp > 32.0,
                             at=now - timedelta(minutes=ago))


def cold(*sensor_ids):
    for sensor_id in sensor_ids:
        _readings(sensor_id, [(m, 0.0) for m in range(120, 0, -10)])


def warming(*sensor_ids, started=15, peak=40.0):
    """Cold for two hours, then warming from `started` minutes ago."""
    for sensor_id in sensor_ids:
        steps = list(range(started, 0, -5)) + [1]
        rise = [(m, round(peak * (started - m + 5) / (started + 4), 1)) for m in steps]
        _readings(sensor_id, [(m, 0.0) for m in range(120, started, -10)] + rise)


def silent(*sensor_ids, since=45, temp=0.0):
    for sensor_id in sensor_ids:
        _readings(sensor_id, [(m, temp) for m in range(120, since - 1, -10)])


def contact(tenant, name, phone, site_id=None, quiet=None):
    made = STORE.add_contact(tenant.tenant_id, name, phone, site_id=site_id)
    if quiet:
        made.quiet_hours = quiet
        STORE.save_contact(made)
    return made


def sweep(tenant):
    return automation.sweep_tenant(STORE.get_tenant(tenant.tenant_id))


def alerts(tenant):
    return STORE.site_wide_alerts(tenant.tenant_id)


# ---- raised at one site --------------------------------------------------


def test_three_units_warming_together_at_one_site_raise_one_urgent_alert(
        tenant_factory, sensor_factory, phones):
    tenant, sites, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    here = units["Main Street"]
    cook = contact(tenant, "Night cook", "+1-555-0111", site_id=sites["Main Street"])
    boss = contact(tenant, "Area manager", "+1-555-0112")
    warming(*here[:3])
    cold(*here[3:])

    result = sweep(tenant)

    raised = [a for a in result["actions"] if a["action"] == "site_wide_alert_raised"]
    assert len(raised) == 1
    (alert,) = alerts(tenant)
    assert alert.kind == SITE_WIDE_KIND and alert.site_wide
    assert sorted(alert.group_sensor_ids) == sorted(here[:3])
    assert alert.group_scope == "site" and alert.group_total_units == 7
    assert alert.breach_details == ("Possible site-wide problem at Main Street: "
                                    "3 of 7 units warming at once.")
    assert alert.sms_text == ("Possible site-wide problem at Main Street: 3 of 7 "
                              "units warming at once. Power cut, open door or "
                              "tampered controls? Check now. Reply 1 if you are on it.")

    # Everyone on call for the site, the estate-wide people, and the owner.
    texted = {to for to, _ in site_wide_texts(phones)}
    assert texted == {cook.phone, boss.phone, OWNER_PHONE}

    # Called in the same sweep, without the ten-minute grace, everybody.
    assert result["voice_calls_placed"] >= 1
    assert alert.voice_escalated_at is not None
    called = [to for to, script in phones["calls"] if "3 of 7 units" in script]
    assert sorted(called) == sorted([cook.phone, boss.phone, OWNER_PHONE])
    script = phones["calls"][0][1]
    assert "someone tampering with the fridge controls" in script
    assert "Press 1" in script or "reply 1" in script


def test_half_of_a_small_site_is_enough(tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Kiosk": 4})
    warming(*units["Kiosk"][:2])
    cold(*units["Kiosk"][2:])

    sweep(tenant)

    (alert,) = alerts(tenant)
    assert "2 of 4 units" in alert.sms_text


def test_below_the_threshold_there_is_no_site_wide_alert(
        tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory,
                               {"Main Street": 7, "Pier": 2})
    warming(*units["Main Street"][:2])            # 2 of 7, by day
    cold(*units["Main Street"][2:])
    warming(units["Pier"][0])                     # 1 of 2: one faulty unit
    cold(units["Pier"][1])

    result = sweep(tenant)

    assert alerts(tenant) == []
    assert not [a for a in result["actions"] if a["action"] == "site_wide_alert_raised"]
    assert site_wide_texts(phones) == []


def test_units_that_started_warming_far_apart_are_not_one_event(
        tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    here = units["Main Street"]
    warming(here[0], started=55)
    warming(here[1], started=30)
    warming(here[2], started=5)
    cold(*here[3:])

    sweep(tenant)

    assert alerts(tenant) == []


def test_an_old_fault_is_not_part_of_something_new(tenant_factory, sensor_factory, phones):
    """A unit warm since yesterday is known about; it does not make a third."""
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    here = units["Main Street"]
    _readings(here[0], [(m, 45.0) for m in range(180, 0, -10)])
    warming(*here[1:3])
    cold(*here[3:])

    sweep(tenant)

    assert alerts(tenant) == []


def test_at_night_two_units_are_enough(tenant_factory, sensor_factory, phones,
                                       monkeypatch):
    monkeypatch.setattr(sitewide, "is_night", lambda *a, **k: True)
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    warming(*units["Main Street"][:2])
    cold(*units["Main Street"][2:])

    sweep(tenant)

    (alert,) = alerts(tenant)
    assert alert.group_night is True


def test_night_is_read_on_the_sites_own_clock(tenant_factory, sensor_factory,
                                             monkeypatch):
    import oncall

    monkeypatch.setenv("CYBERLOGIX_GROUP_ALERT_NIGHT", "22:00-06:00")
    tenant, sites, _ = _estate(tenant_factory, sensor_factory, {"Lyon": 1})
    rotation = oncall.Rotation(tenant_id=tenant.tenant_id, site_id=sites["Lyon"],
                               timezone="Europe/Paris", handover="08:00",
                               shift_days=7, starts_on="2026-11-02")
    STORE._db.put(oncall.ROTATION_KIND, rotation.rec_id, rotation.to_row())

    # 21:30 UTC in November is 22:30 in Paris: night there, not in London.
    late = datetime(2026, 11, 10, 21, 30, tzinfo=timezone.utc)
    assert sitewide.site_timezone(tenant, sites["Lyon"], "UTC") == "Europe/Paris"
    assert sitewide.is_night(tenant, sites["Lyon"], late) is True
    assert sitewide.is_night(tenant, None, late) is False  # no rotation: UTC
    assert sitewide.is_night(tenant, sites["Lyon"], late.replace(hour=12)) is False

    monkeypatch.setenv("CYBERLOGIX_GROUP_ALERT_NIGHT", "not a window")
    assert sitewide.is_night(tenant, sites["Lyon"], late) is False


def test_the_thresholds_are_settings(tenant_factory, sensor_factory, phones,
                                     monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_GROUP_ALERT_MIN_UNITS", "4")
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 9})
    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])
    sweep(tenant)
    assert alerts(tenant) == []

    monkeypatch.setenv("CYBERLOGIX_GROUP_ALERT_MIN_UNITS", "0")  # off
    warming(*units["Main Street"][:6])
    sweep(tenant)
    assert alerts(tenant) == []


# ---- raised across sites -------------------------------------------------


def test_warming_at_several_sites_of_one_business_raises_one_alert_for_all(
        tenant_factory, sensor_factory, phones):
    tenant, sites, units = _estate(tenant_factory, sensor_factory,
                                   {"Main Street": 7, "Harbour": 7})
    north = contact(tenant, "Main cook", "+1-555-0121", site_id=sites["Main Street"])
    south = contact(tenant, "Harbour cook", "+1-555-0122", site_id=sites["Harbour"])
    # Two of seven at each: neither site alone is enough.
    warming(*units["Main Street"][:2])
    warming(*units["Harbour"][:2], started=10)
    cold(*units["Main Street"][2:], *units["Harbour"][2:])

    sweep(tenant)

    (alert,) = alerts(tenant)
    assert alert.group_scope == "business"
    assert sorted(alert.group_site_ids) == sorted(sites.values())
    assert alert.sms_text.startswith("Possible problem across 2 sites: 4 units warming at once.")
    texted = {to for to, _ in site_wide_texts(phones)}
    assert {north.phone, south.phone, OWNER_PHONE} <= texted

    # Sweeping again raises nothing more for the same warming.
    phones["texts"].clear()
    sweep(tenant)
    assert len(alerts(tenant)) == 1 and site_wide_texts(phones) == []


# ---- what does not count -------------------------------------------------


HOUR = 60


def _defrosting(sensor_id, spell_started_ago=10, interval=8 * HOUR, warm=20,
                early=0):
    """Three earlier defrosts on an 8-hour timer, and the current one.

    `early` minutes moves the current spell that much ahead of its timer.
    """
    points = []
    start = spell_started_ago + 30 * HOUR
    for n in range(3):
        begins = spell_started_ago + (3 - n) * interval - early
        points += [(m, 0.0) for m in range(start, begins, -30) if m > begins]
        points += [(begins - k, 40.0) for k in range(0, warm + 1, 10)]
        start = begins - warm - 10
    points += [(m, 0.0) for m in range(start, spell_started_ago, -30)
               if m > spell_started_ago]
    points += [(m, 40.0) for m in range(spell_started_ago, 0, -5)] + [(1, 40.0)]
    _readings(sensor_id, points)


def test_a_planned_defrost_across_the_site_is_respected(
        tenant_factory, sensor_factory, phones):
    """Many stores time a row of cases to defrost together. That is the plan."""
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 5})
    for sensor_id in units["Main Street"][:4]:
        _defrosting(sensor_id)
    cold(units["Main Street"][4])

    result = sweep(tenant)

    assert alerts(tenant) == []
    assert not [a for a in result["actions"] if a["action"] == "site_wide_alert_raised"]


def test_a_defrost_switched_on_at_the_wrong_time_is_not_excused(
        tenant_factory, sensor_factory, phones):
    """The attack: defrost switched on hours early, at every case at once."""
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 5})
    for sensor_id in units["Main Street"][:4]:
        _defrosting(sensor_id, early=3 * HOUR)
    cold(units["Main Street"][4])

    sweep(tenant)

    assert len(alerts(tenant)) == 1


def test_sensors_gone_silent_together_are_an_outage_not_an_attack(
        tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    here = units["Main Street"]
    # The gateway lost power: five went quiet at once, two still report a rise.
    silent(*here[:5], temp=36.0)
    warming(*here[5:])

    result = sweep(tenant)

    assert alerts(tenant) == []
    outage = [a for a in result["actions"] if a["action"] == "site_wide_outage_not_warming"]
    assert len(outage) == 1 and "outage" in outage[0]["detail"]
    # The quiet-sensor path is what tells people, as it always has.
    assert any(a["action"] == "sensor_offline_flagged" for a in result["actions"])


def test_a_unit_that_went_silent_while_warm_does_not_count_as_warming(
        tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    here = units["Main Street"]
    silent(here[0], since=40, temp=45.0)          # last heard warm, now silent
    warming(*here[1:3])
    cold(*here[3:])

    sweep(tenant)

    assert alerts(tenant) == []


def test_another_business_never_counts_and_is_never_told(
        tenant_factory, sensor_factory, phones):
    grill, _, grill_units = _estate(tenant_factory, sensor_factory,
                                    {"Main Street": 7}, company="Harbor Grill")
    cafe, _, cafe_units = _estate(tenant_factory, sensor_factory,
                                  {"Main Street": 7}, company="Pier Cafe", prefix="X")
    cafe_cook = contact(cafe, "Cafe cook", "+1-555-0199")
    # Two each, at a site of the same name, at the same moment: four units,
    # but two businesses, so neither has a site-wide problem.
    warming(*grill_units["Main Street"][:2])
    warming(*cafe_units["Main Street"][:2])
    cold(*grill_units["Main Street"][2:], *cafe_units["Main Street"][2:])

    sweep(grill)
    sweep(cafe)
    assert alerts(grill) == [] and alerts(cafe) == []

    # And when one of them does have one, only its own people hear.
    warming(grill_units["Main Street"][2])
    sweep(grill)
    (alert,) = alerts(grill)
    assert all(s.startswith("S0-") for s in alert.group_sensor_ids)
    assert cafe_cook.phone not in {to for to, _ in site_wide_texts(phones)}
    assert alerts(cafe) == []


# ---- quiet hours, claims, the send path ------------------------------------


def _quiet_now():
    now = utc_now()
    return {"start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "timezone": "UTC"}


def test_quiet_hours_do_not_silence_it(tenant_factory, sensor_factory, phones):
    tenant, sites, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    awake = contact(tenant, "Day cook", "+1-555-0131")
    asleep = contact(tenant, "Night manager", "+1-555-0132", quiet=_quiet_now())
    # A normal unit alert would skip the person asleep while someone is awake.
    assert asleep.contact_id not in {
        c.contact_id for c in STORE.sms_recipients(tenant, sites["Main Street"])}

    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])
    sweep(tenant)

    texted = {to for to, _ in site_wide_texts(phones)}
    assert {awake.phone, asleep.phone, OWNER_PHONE} <= texted
    called = [to for to, script in phones["calls"] if "units started warming" in script]
    # Rung too: after the people awake, never left out.
    assert asleep.phone in called
    assert called.index(asleep.phone) > called.index(awake.phone)


def test_the_spend_caps_still_apply(tenant_factory, sensor_factory, monkeypatch,
                                    configured_twilio):
    """It goes through send_sms, so the daily cap holds it like any text."""
    import costs

    monkeypatch.setattr(costs, "MAX_SMS_PER_DAY", 1)
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    contact(tenant, "Cook", "+1-555-0141")
    contact(tenant, "Manager", "+1-555-0142")
    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])

    sweep(tenant)

    (alert,) = alerts(tenant)
    statuses = [d["status"] for d in alert.sms_fanout]
    assert len(statuses) == 3
    assert statuses.count("budget_exceeded") == 2


def test_two_sweeps_at_once_open_and_send_it_once(tenant_factory, sensor_factory,
                                                  monkeypatch):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    contact(tenant, "Cook", "+1-555-0151")
    warming(*units["Main Street"][:4])
    cold(*units["Main Street"][4:])

    sent, lock = [], threading.Lock()

    def slow_text(to, body, tenant_id=None):
        clock.sleep(0.2)   # wide enough for the second sweep to arrive
        with lock:
            sent.append((to, body))
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued"}

    def call(to, script, tenant_id=None, action_url=None):
        with lock:
            sent.append((to, script))
        return {"channel": "voice", "to": to, "delivered": True, "status": "queued"}

    monkeypatch.setattr(telemetry, "send_sms", slow_text)
    monkeypatch.setattr(automation, "send_sms", slow_text)
    monkeypatch.setattr(voice_dispatch, "place_voice_call", call)

    threads = [threading.Thread(target=sweep, args=(tenant,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert len(alerts(tenant)) == 1
    texts = [to for to, body in sent if body.startswith("Possible site-wide")]
    assert sorted(texts) == sorted({"+1-555-0151", OWNER_PHONE})
    calls = [to for to, body in sent if "units started warming" in body]
    assert sorted(calls) == sorted({"+1-555-0151", OWNER_PHONE})


def test_the_fleet_sweep_lock_still_holds(tenant_factory, sensor_factory, phones,
                                          monkeypatch):
    import watchdog

    monkeypatch.setattr(watchdog, "ping", lambda: None)
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])

    assert scheduler._sweep_lock.acquire(blocking=False)
    try:
        assert scheduler.run_one_pass()["skipped"] == "another sweep is still running"
        assert alerts(tenant) == []
    finally:
        scheduler._sweep_lock.release()
    scheduler.run_one_pass()
    scheduler.run_one_pass()
    assert len(alerts(tenant)) == 1
    assert len([1 for to, _ in site_wide_texts(phones) if to == OWNER_PHONE]) == 1


def test_failed_texts_are_sent_again_to_the_same_list(
        tenant_factory, sensor_factory, monkeypatch, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    asleep = contact(tenant, "Night manager", "+1-555-0161", quiet=_quiet_now())
    contact(tenant, "Day cook", "+1-555-0162")
    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])

    def down(to, body, tenant_id=None):
        return {"channel": "sms", "to": to, "delivered": False,
                "status": "send_failed", "detail": "Twilio is down"}

    monkeypatch.setattr(telemetry, "send_sms", down)
    sweep(tenant)
    (alert,) = alerts(tenant)
    assert alert.alert_texts_failed

    alert.opened_at -= timedelta(minutes=2)
    result = sweep(tenant)
    retried = [a for a in result["actions"] if a["action"] == "alert_text_retried"
               and a["incident_id"] == alert.incident_id]
    assert retried
    assert asleep.phone in {d["to"] for d in retried[0]["deliveries"]}


def test_the_text_always_fits_one_segment():
    long_name = "The Very Long Named Flagship Superstore and Bakery on Riverside Avenue"
    text = wording.site_wide_sms(None, "site", 12, 40, [long_name])
    assert wording._septets(text) <= wording.SMS_LIMIT
    assert "12 of 40 units warming at once" in text and "Reply 1" in text
    text = wording.site_wide_sms(None, "business", 9, 30, ["A", "B", "C"])
    assert text.startswith("Possible problem across 3 sites: 9 units warming at once.")
    assert wording._septets(text) <= wording.SMS_LIMIT


def test_a_plan_without_calls_still_texts(tenant_factory, sensor_factory, phones):
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 5},
                               plan="trial")
    warming(*units["Main Street"][:3])
    cold(*units["Main Street"][3:])

    result = sweep(tenant)

    assert len(alerts(tenant)) == 1
    assert {to for to, _ in site_wide_texts(phones)} == {OWNER_PHONE}
    assert phones["calls"] == []
    assert any(a["action"] == "voice_escalation_blocked" for a in result["actions"])


# ---- acknowledging -------------------------------------------------------


def _sign(url, form, token="token"):
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).compute_signature(url, form)


def _reply(api, phone, body):
    route = "/api/voice/sms-reply"
    form = {"From": phone, "To": "+15550000", "Body": body, "MessageSid": "SM1"}
    return api.post(route, data=form, headers={
        "X-Twilio-Signature": _sign(f"http://testserver{route}", form)})


@pytest.fixture()
def raised(tenant_factory, sensor_factory, phones, monkeypatch):
    """A site-wide alert with its units' own alerts open beside it."""
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    tenant, _, units = _estate(tenant_factory, sensor_factory, {"Main Street": 7})
    cook = contact(tenant, "Night cook", "+1-555-0171")
    here = units["Main Street"]
    warming(*here[:3])
    cold(*here[3:])
    for sensor_id in here[:3]:   # the units' own alerts, as ingest opens them
        telemetry.process_reading(tenant, STORE.get_sensor(sensor_id), 41.0, None)
    sweep(tenant)
    (alert,) = alerts(tenant)
    return tenant, cook, alert, here[:3]


def test_reply_1_acknowledges_the_site_wide_alert_and_its_units(api, raised):
    tenant, cook, alert, unit_ids = raised
    units = [STORE.latest_unresolved_incident(s, tenant.tenant_id) for s in unit_ids]
    assert all(u is not None and u.open for u in units)

    resp = _reply(api, "+15550171", "1")

    assert resp.status_code == 200
    assert "site-wide alert is yours" in resp.text
    assert alert.acknowledged_by == "Night cook, by text reply (+1-555-0171)"
    assert all(not u.open for u in units)
    assert all(alert.incident_id in u.acknowledged_by for u in units)


def test_the_owner_can_reply_1_too(api, raised):
    _, _, alert, _ = raised
    resp = _reply(api, "+15550100", "ok")
    assert resp.status_code == 200 and "No more calls" in resp.text
    assert alert.acknowledged_at is not None


def test_the_button_acknowledges_it(api, raised, tenant_factory):
    tenant, _, alert, unit_ids = raised
    key = STORE.rotate_api_key(tenant)

    resp = api.post(f"/api/voice/acknowledge/{alert.incident_id}",
                    headers={"X-CyberLogix-Key": key}, json={"acknowledged_by": "Dana"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ACKNOWLEDGED"
    assert alert.acknowledged_at is not None
    assert STORE.latest_unresolved_incident(unit_ids[0], tenant.tenant_id).acknowledged_at
    # Nothing left for the sweep to call about.
    assert alert.incident_id not in {
        i.incident_id for i in STORE.open_incidents(tenant.tenant_id, include_site_wide=True)}


def test_pressing_1_on_the_call_acknowledges_it(api, raised, monkeypatch):
    from twilio.request_validator import RequestValidator

    _, _, alert, _ = raised
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://hub.example")
    token = STORE.issue_ack_token(alert)
    url = notifications.acknowledgement_url(alert.incident_id, token)
    form = {"Digits": "1", "To": "+15550171"}

    pressed = api.post(f"/api/voice/keypress/{alert.incident_id}/{token}", data=form,
                       headers={"X-Twilio-Signature":
                                RequestValidator("token").compute_signature(url, form)})

    assert pressed.status_code == 200 and "Acknowledged" in pressed.text
    assert alert.acknowledged_by == "phone keypad (+15550171)"


def test_it_survives_a_restart(raised):
    from records import Incident

    _, _, alert, unit_ids = raised
    again = Incident.from_row(STORE._db.get("incident", alert.incident_id))
    assert again.site_wide and again.group_scope == "site"
    assert sorted(again.group_sensor_ids) == sorted(unit_ids)
    assert again.group_total_units == 7 and len(again.group_units) == 3
    # An incident written before there were site-wide alerts is a unit's.
    old = alert.to_row()
    for key in ("kind", "group_sensor_ids", "group_site_ids", "group_units",
                "group_scope", "group_total_units", "group_night"):
        old.pop(key)
    assert not Incident.from_row(old).site_wide


def test_it_never_folds_into_a_units_own_incident(raised):
    """A unit's next breach finds its own incident, never the site-wide one."""
    tenant, _, alert, unit_ids = raised
    found = STORE.latest_unresolved_incident(unit_ids[0], tenant.tenant_id)
    assert found is not None and not found.site_wide
    assert STORE.latest_unresolved_incident(alert.sensor_id) is None


# ---- where it is shown ---------------------------------------------------


def test_the_console_overview_carries_it_apart_from_unit_alarms(api, operator_factory,
                                                                  sensor_factory, phones):
    headers, tenant_body, _ = operator_factory()
    tenant = STORE.get_tenant(tenant_body["tenant_id"])
    site = STORE.create_site(tenant.tenant_id, "Main Street")
    for n in range(5):
        sensor_factory(headers, sensor_id=f"U{n}", vertical="restaurant",
                       location=f"Freezer {n}")
        STORE.assign_sensor_to_site(STORE.get_sensor(f"U{n}"), site.site_id)
    warming("U0", "U1", "U2")
    cold("U3", "U4")
    sweep(tenant)

    body = api.get("/api/console/overview", headers=headers).json()

    (row,) = body["site_wide_alerts"]
    assert row["site_wide"] is True and row["kind"] == "site_wide"
    assert row["sites"] == ["Main Street"]
    assert sorted(u["location_name"] for u in row["group_units"]) == [
        "Freezer 0", "Freezer 1", "Freezer 2"]
    assert all(u["temperature_display"] is not None for u in row["group_units"])
    # Not counted as a unit's incident.
    assert all(not i.get("site_wide") for i in body["incidents"])


def test_the_console_draws_a_distinct_banner_and_card():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "static"
    html = (root / "console.html").read_text()
    js = (root / "console.js").read_text()
    css = (root / "theme.css").read_text()
    assert 'id="site-wide-banner"' in html
    assert "function renderSiteWide" in js and "function siteWideCard" in js
    assert "renderSiteWide(d.site_wide_alerts" in js
    assert "Site-wide alerts" in js           # the inspector's log section
    assert ".banner.sitewide-banner" in css and ".incident.sitewide-card" in css
    assert "style=" not in html


def test_the_inspectors_log_lists_it(api, raised):
    tenant, _, alert, _ = raised
    key = STORE.rotate_api_key(tenant)

    log = api.get("/api/autopilot/inspector-log",
                  headers={"X-CyberLogix-Key": key}).json()

    (entry,) = log["site_wide_alerts"]
    assert entry["incident_id"] == alert.incident_id
    assert entry["unit_count"] == 3 and entry["total_units"] == 7
    assert entry["sites"] == ["Main Street"]
    # The units' own breaches are still each in their own section, once.
    assert log["breaches"] == 3


def test_the_monthly_report_lists_it(raised):
    tenant, _, alert, _ = raised
    first = alert.opened_at.date().replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    report = monthly.summary(tenant, first, last)
    text = monthly.render_text(report)

    assert report["site_wide_count"] == 1
    assert "Site-wide alerts (many units warming at once): 1" in text
    assert "Possible site-wide problem at Main Street" in text
    # Still open, so it is listed under what is open now as well.
    assert "site-wide alert since" in text
    # And the units are counted as three times out of range, not four.
    assert report["breach_count"] == 3


def test_the_help_page_answers_it():
    import helpdesk

    item = next(i for i in helpdesk.faq() if i["id"] == "site-wide")
    answer = " ".join(item["a"])
    assert "same time" in answer and "power cut" in answer
    assert "never control a fridge" in answer and "cannot stop an attack" in answer
