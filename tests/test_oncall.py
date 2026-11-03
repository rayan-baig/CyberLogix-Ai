"""Rotations and quiet hours, for real use.

Three promises:

* A critical breach still reaches somebody during quiet hours: with
  everybody asleep the text goes to all of them, and the call ladder ends
  with the people asleep rather than leaving them off.
* A rotation hands over at the local time it says, across midnight and on
  the nights the clocks change -- not an hour early, not twice.
* A rotation with nobody in it, or with a muted person taking turns, is
  named as a gap before an alert needs it.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import notifications
import oncall
from store import STORE, utc_now

NY = "America/New_York"
LONDON = "Europe/London"


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


def rota(members=("A", "B"), handover="08:00", days=1, starts="2026-10-30", tz=NY):
    return oncall.Rotation(tenant_id="T", timezone=tz, handover=handover,
                           shift_days=days, starts_on=starts, members=list(members))


# ---- handovers ---------------------------------------------------------------


def test_a_daily_handover_happens_at_the_local_time_across_the_clocks_going_back():
    r = rota()   # A from 30 Oct 08:00, B from 31 Oct 08:00, A from 1 Nov 08:00 ...
    # 31 Oct is still daylight time in New York: 08:00 EDT is 12:00 UTC.
    assert r.on_shift(utc(2026, 10, 31, 11, 59)) == "A"
    assert r.on_shift(utc(2026, 10, 31, 12, 0)) == "B"
    # The clocks go back at 02:00 on 1 Nov, so 08:00 EST is 13:00 UTC. A
    # handover an hour early (12:00 UTC) is the bug this guards against.
    assert r.on_shift(utc(2026, 11, 1, 12, 30)) == "B"
    assert r.on_shift(utc(2026, 11, 1, 13, 0)) == "A"
    assert r.next_handover(utc(2026, 11, 1, 12, 30)) == utc(2026, 11, 1, 13, 0)


def test_a_handover_at_midnight_happens_at_midnight():
    r = rota(handover="00:00", tz=LONDON, starts="2026-11-01")
    assert r.on_shift(utc(2026, 11, 1, 23, 59)) == "A"
    assert r.on_shift(utc(2026, 11, 2, 0, 0)) == "B"
    assert r.on_shift(utc(2026, 11, 3, 0, 0)) == "A"


def test_a_handover_in_the_repeated_hour_happens_once():
    """01:30 happens twice when the clocks go back. The first is the handover;
    the second must not hand the shift back for half an hour."""
    r = rota(handover="01:30", starts="2026-10-31")
    first_0130 = utc(2026, 11, 1, 5, 30)    # 01:30 EDT
    assert r.on_shift(first_0130 - timedelta(minutes=1)) == "A"
    assert r.on_shift(first_0130) == "B"
    assert r.on_shift(utc(2026, 11, 1, 6, 15)) == "B"   # 01:15 EST, the second time round
    assert r.on_shift(utc(2026, 11, 1, 6, 45)) == "B"   # 01:45 EST


def test_a_handover_in_the_missing_hour_happens_when_the_clock_jumps_past_it():
    """02:30 does not exist on 14 March 2027 in New York: the clock goes
    from 01:59 EST to 03:00 EDT. The handover is at that jump."""
    r = rota(handover="02:30", starts="2027-03-13")
    assert r.on_shift(utc(2027, 3, 14, 6, 59)) == "A"   # 01:59 EST
    assert r.on_shift(utc(2027, 3, 14, 7, 0)) == "B"    # 03:00 EDT
    # And the day after, back to 02:30 local: 06:30 UTC in daylight time.
    assert r.on_shift(utc(2027, 3, 15, 6, 29)) == "B"
    assert r.on_shift(utc(2027, 3, 15, 6, 30)) == "A"


def test_a_weekly_rotation_hands_over_on_the_right_day_through_a_clock_change():
    # Mondays 09:00 in London; the clocks go back on Sunday 25 Oct 2026.
    r = rota(members=("A", "B", "C"), handover="09:00", days=7, tz=LONDON,
             starts="2026-10-19")
    assert r.on_shift(utc(2026, 10, 19, 8, 0)) == "A"     # 09:00 BST
    assert r.on_shift(utc(2026, 10, 26, 8, 59)) == "A"    # 08:59 GMT, Monday
    assert r.on_shift(utc(2026, 10, 26, 9, 0)) == "B"     # 09:00 GMT
    assert r.on_shift(utc(2026, 11, 2, 9, 0)) == "C"
    assert r.on_shift(utc(2026, 11, 9, 9, 0)) == "A"
    # Before the first shift, turns run backwards rather than stopping.
    assert r.on_shift(utc(2026, 10, 18, 12, 0)) == "C"


def test_the_schedule_lists_handovers_in_local_time():
    r = rota(starts="2026-10-31")
    shifts = r.schedule(utc(2026, 10, 31, 20, 0), shifts=3)
    assert [s["contact_id"] for s in shifts] == ["A", "B", "A"]
    assert shifts[0]["starts_local"].endswith("08:00 EDT")
    assert shifts[1]["starts_local"].endswith("08:00 EST")
    assert rota(members=()).schedule(utc(2026, 10, 31)) == []
    assert rota(members=()).on_shift(utc(2026, 10, 31)) is None


def test_quiet_hours_over_midnight():
    quiet = {"start": "22:00", "end": "07:00", "timezone": LONDON}
    assert oncall.in_quiet_hours(quiet, utc(2026, 11, 3, 23, 0))
    assert oncall.in_quiet_hours(quiet, utc(2026, 11, 4, 6, 59))
    assert not oncall.in_quiet_hours(quiet, utc(2026, 11, 4, 7, 0))
    assert not oncall.in_quiet_hours(quiet, utc(2026, 11, 3, 21, 59))
    # Unreadable quiet hours never silence anybody.
    assert not oncall.in_quiet_hours({"start": "late", "end": "07:00",
                                      "timezone": LONDON}, utc(2026, 11, 3, 23, 0))
    assert not oncall.in_quiet_hours({"start": "22:00", "end": "07:00",
                                      "timezone": "Mars/Base"}, utc(2026, 11, 3, 23, 0))


# ---- through the roster ------------------------------------------------------


def _quiet_now():
    """Quiet hours that certainly cover the present moment, in UTC."""
    now = utc_now()
    return {"start": (now - timedelta(hours=2)).strftime("%H:%M"),
            "end": (now + timedelta(hours=2)).strftime("%H:%M"),
            "timezone": "UTC"}


@pytest.fixture()
def twilio(monkeypatch):
    sent = {"sms": [], "calls": []}

    class _Messages:
        def create(self, **kwargs):
            sent["sms"].append(kwargs["to"])
            return type("M", (), {"sid": "SM-1", "status": "queued"})()

    class _Calls:
        def create(self, **kwargs):
            sent["calls"].append(kwargs["to"])
            return type("C", (), {"sid": "CA-1", "status": "queued"})()

    client = type("Client", (), {"messages": _Messages(), "calls": _Calls()})()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", client)
    monkeypatch.setattr(notifications, "_client_error", None)
    return sent


def _person(api, headers, name, phone, order, **extra):
    resp = api.post("/api/contacts", headers=headers, json={
        "full_name": name, "phone": phone, "escalation_order": order, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_quiet_hours_leave_somebody_out_only_while_someone_else_is_awake(
        api, operator_factory, sensor_factory, twilio):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    manager = _person(api, headers, "Day Manager", "+15550201", 1,
                      quiet_hours=_quiet_now())
    night = _person(api, headers, "Night Engineer", "+15550202", 2)
    t = STORE.get_tenant(tenant["tenant_id"])

    assert [c.phone for c in STORE.sms_recipients(t)] == ["+15550202"]
    # The sleeping manager is still on the ladder: last, not gone.
    assert [c.phone for c in STORE.voice_ladder(t)] == ["+15550202", "+15550201"]

    # The engineer is muted for leave: now only the sleeper is left, and a
    # critical breach wakes them rather than reaching nobody.
    api.patch(f"/api/contacts/{night['contact_id']}", headers=headers,
              json={"active": False})
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 99.0})
    assert resp.json()["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert twilio["sms"] == ["+15550201"]
    assert manager["quiet_hours"] is not None

    preview = api.get("/api/contacts/preview", headers=headers).json()
    assert any("quiet hours never silence an alert" in w for w in preview["warnings"])


def test_quiet_hours_are_validated_and_can_be_cleared(api, operator_factory):
    headers, _, _ = operator_factory()
    bad = api.post("/api/contacts", headers=headers, json={
        "full_name": "X", "phone": "+15550301",
        "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Nowhere/Here"}})
    assert bad.status_code == 422
    bad = api.post("/api/contacts", headers=headers, json={
        "full_name": "X", "phone": "+15550301",
        "quiet_hours": {"start": "25:00", "end": "07:00", "timezone": LONDON}})
    assert bad.status_code == 422
    person = _person(api, headers, "X", "+15550301", 1, quiet_hours={
        "start": "22:00", "end": "7:00", "timezone": LONDON})
    assert person["quiet_hours"]["end"] == "07:00"
    cleared = api.patch(f"/api/contacts/{person['contact_id']}", headers=headers,
                        json={"quiet_hours": None})
    assert cleared.status_code == 200 and cleared.json()["quiet_hours"] is None


def test_only_the_person_on_shift_is_alerted(api, operator_factory, sensor_factory, twilio):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    a = _person(api, headers, "Alex", "+15550401", 1)
    b = _person(api, headers, "Blair", "+15550402", 1)
    _person(api, headers, "Supervisor", "+15550403", 2)

    today = utc_now().date()
    resp = api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": "UTC", "handover": "00:00", "shift_days": 1,
        "starts_on": today.isoformat(),
        "members": [a["contact_id"], b["contact_id"]]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["on_shift_now"]["full_name"] == "Alex"
    assert body["next_handover_at"].startswith((today + timedelta(days=1)).isoformat())
    assert [s["full_name"] for s in body["schedule"][:2]] == ["Alex", "Blair"]

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RACK-01", "temperature_fahrenheit": 99.0})
    assert sorted(twilio["sms"]) == ["+15550401", "+15550403"]

    listed = api.get("/api/contacts/rotations", headers=headers).json()
    assert listed["rotations"][0]["scope"] == "estate"

    # Started yesterday instead, it is Blair's turn.
    api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": "UTC", "handover": "00:00", "shift_days": 1,
        "starts_on": (today - timedelta(days=1)).isoformat(),
        "members": [a["contact_id"], b["contact_id"]]})
    t = STORE.get_tenant(tenant["tenant_id"])
    assert sorted(c.phone for c in STORE.sms_recipients(t)) == ["+15550402", "+15550403"]

    gone = api.delete("/api/contacts/rotations/estate", headers=headers)
    assert gone.status_code == 200
    assert len(STORE.sms_recipients(t)) == 3
    assert api.delete("/api/contacts/rotations/estate", headers=headers).status_code == 404


def test_an_empty_rotation_is_warned_about(api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    a = _person(api, headers, "Alex", "+15550501", 1)

    resp = api.put("/api/contacts/rotations", headers=headers,
                   json={"timezone": LONDON, "members": []})
    assert resp.status_code == 200, resp.text
    assert any("has nobody in it" in w for w in resp.json()["warnings"])

    # Becoming empty by removing its last member is warned about the same way.
    api.put("/api/contacts/rotations", headers=headers,
            json={"timezone": LONDON, "members": [a["contact_id"]]})
    gone = api.delete(f"/api/contacts/{a['contact_id']}", headers=headers)
    assert any("has nobody in it" in w for w in gone.json()["warnings"])
    row = STORE._db.get(oncall.ROTATION_KIND, f"{tenant['tenant_id']}:estate")
    assert row["members"] == []


def test_a_muted_person_with_turns_is_warned_about(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    a = _person(api, headers, "Alex", "+15550601", 1)
    b = _person(api, headers, "Blair", "+15550602", 1)
    _person(api, headers, "Supervisor", "+15550603", 2)
    api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "members": [a["contact_id"], b["contact_id"]]})
    muted = api.patch(f"/api/contacts/{b['contact_id']}", headers=headers,
                      json={"active": False})
    assert any("Blair is muted but has turns" in w for w in muted.json()["warnings"])


def test_rotation_members_must_be_on_the_roster_and_in_one_rotation(
        api, operator_factory):
    headers, _, _ = operator_factory(plan="enterprise")
    a = _person(api, headers, "Alex", "+15550701", 1)
    site = api.post("/api/sites", headers=headers, json={"name": "Depot"})
    assert site.status_code in (200, 201), site.text
    site_id = site.json()["site_id"]

    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "members": ["CON-999999"]}).status_code == 404
    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "members": [a["contact_id"], a["contact_id"]]}).status_code == 400
    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": "Not/AZone", "members": []}).status_code == 422
    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "members": [a["contact_id"]]}).status_code == 200
    clash = api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "site_id": site_id, "members": [a["contact_id"]]})
    assert clash.status_code == 409
    assert api.delete(f"/api/contacts/rotations/{site_id}", headers=headers).status_code == 404


def test_a_rotation_is_exported_and_erased_with_its_business(api, operator_factory):
    headers, tenant, _ = operator_factory(plan="enterprise")
    api.put("/api/contacts/rotations", headers=headers,
            json={"timezone": NY, "members": []})
    exported = STORE.export_tenant(tenant["tenant_id"])
    assert exported[oncall.ROTATION_KIND][0]["timezone"] == NY
    STORE.erase_tenant(tenant["tenant_id"])
    assert STORE._db.all(oncall.ROTATION_KIND) == []


def test_starts_on_defaults_to_the_last_handover_where_the_rotation_is(
        api, operator_factory):
    headers, _, _ = operator_factory(plan="enterprise")
    tz = oncall.zone("Pacific/Kiritimati")
    resp = api.put("/api/contacts/rotations", headers=headers,
                   json={"timezone": "Pacific/Kiritimati", "members": []})
    today = utc_now().astimezone(tz).date()
    handover = oncall.handover_at(today, oncall.clock("08:00"), tz)
    expected = today if handover <= utc_now() else today - timedelta(days=1)
    assert date.fromisoformat(resp.json()["starts_on"]) == expected


@pytest.mark.parametrize("hours_to_handover", [-3, 3])
def test_the_person_listed_first_is_on_call_from_now(
        api, operator_factory, hours_to_handover):
    """Made before the day's handover, the first turn had not begun, and
    the person listed last was on call until it did. Found by the browser
    walk, which failed at some hours of the day and not others."""
    headers, _, _ = operator_factory(plan="enterprise")
    a = _person(api, headers, "Alex", "+15550801", 1)
    b = _person(api, headers, "Blair", "+15550802", 1)
    c = _person(api, headers, "Casey", "+15550803", 1)
    handover = (utc_now() + timedelta(hours=hours_to_handover)).strftime("%H:%M")
    for days in (1, 7):
        made = api.put("/api/contacts/rotations", headers=headers, json={
            "timezone": "UTC", "handover": handover, "shift_days": days,
            "members": [a["contact_id"], b["contact_id"], c["contact_id"]]})
        assert made.status_code == 200, made.text
        listed = api.get("/api/contacts/rotations", headers=headers).json()
        (rotation,) = listed["rotations"]
        assert rotation["on_shift_now"]["full_name"] == "Alex", (days, handover)


def test_a_reply_after_the_handover_still_acknowledges(
        api, operator_factory, sensor_factory, twilio):
    """Texted at 07:55, replied at 08:02: the reply answers the text they
    were sent, though their shift has ended."""
    from twilio.request_validator import RequestValidator

    headers, _, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    a = _person(api, headers, "Alex", "+15550801", 1)
    b = _person(api, headers, "Blair", "+15550802", 1)
    today = utc_now().date()
    rotation = {"timezone": "UTC", "handover": "00:00", "shift_days": 1,
                "members": [a["contact_id"], b["contact_id"]]}
    api.put("/api/contacts/rotations", headers=headers,
            json={**rotation, "starts_on": today.isoformat()})
    incident_id = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "RACK-01", "temperature_fahrenheit": 99.0}).json()["incident_id"]
    assert twilio["sms"] == ["+15550801"]

    # The shift hands over to Blair.
    api.put("/api/contacts/rotations", headers=headers,
            json={**rotation, "starts_on": (today - timedelta(days=1)).isoformat()})
    form = {"From": "+15550801", "To": "+15550000", "Body": "1", "MessageSid": "SM9"}
    signature = RequestValidator("token").compute_signature(
        "http://testserver/api/voice/sms-reply", form)
    resp = api.post("/api/voice/sms-reply", data=form,
                    headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 200, resp.text
    assert STORE.get_incident(incident_id).acknowledged_by.startswith("Alex")


def test_removing_a_site_takes_its_rotation_with_it(api, operator_factory):
    """A site's rotation outlived the site. Its people had been released to
    the whole estate, but the rotation still held them to their turns, the
    warnings called it the estate's rotation, and it could not be stopped
    (DELETE answered "site not found") nor its people put in another one
    ("already in another rotation")."""
    headers, tenant, _ = operator_factory(plan="enterprise")
    site_id = api.post("/api/sites", headers=headers, json={"name": "Depot"}).json()["site_id"]
    a = _person(api, headers, "Alex", "+15551101", 1, site_id=site_id)
    b = _person(api, headers, "Blair", "+15551102", 1, site_id=site_id)
    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "site_id": site_id,
        "members": [a["contact_id"], b["contact_id"]]}).status_code == 200

    assert api.delete(f"/api/sites/{site_id}", headers=headers).status_code == 200

    assert api.get("/api/contacts/rotations", headers=headers).json()["rotations"] == []
    assert STORE._db.get(oncall.ROTATION_KIND, f"{tenant['tenant_id']}:{site_id}") is None
    # Both are on call all the time again, as the release promised...
    t = STORE.get_tenant(tenant["tenant_id"])
    assert sorted(c.full_name for c in STORE.sms_recipients(t)) == ["Alex", "Blair"]
    # ...and can take turns in the estate's rotation.
    assert api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "members": [a["contact_id"], b["contact_id"]]}).status_code == 200
