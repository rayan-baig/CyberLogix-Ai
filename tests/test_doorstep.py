"""Sensors that turn up uninvited, and how to let them in.

Connecting a sensor used to require getting a serial exactly right
before anything worked. Get one character wrong -- or not know it,
which is normal, because it is printed inside a battery compartment
bolted inside a freezer -- and every reading was answered with a 404
and thrown away. The console stayed empty and said nothing about why.

That was the whole difficulty of setting this product up, and it was
backwards: a device that is sending us readings has already done the
hard part.
"""

import pytest

import doorstep
from store import STORE


@pytest.fixture()
def account(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    return {**headers, **owner_headers(headers)}, tenant


def report(api, headers, serial="NEW-BOX-1", value=38.0, unit="temperature_f"):
    return api.post("/api/v1/bridge/sensor-webhook-ingest", headers=headers,
                    json={"device_sn": serial, "reading_value": value,
                          "metric_type": unit,
                          "api_key_token": headers["X-CyberLogix-Key"]})


# --- the hassle this removes ----------------------------------------------


def test_an_unknown_device_is_held_rather_than_thrown_away(api, account):
    headers, _ = account

    resp = report(api, headers)

    assert resp.status_code == 202, resp.text
    assert "held rather than watched" in str(resp.json()["detail"])
    waiting = api.get("/api/doorstep", headers=headers).json()
    assert waiting["count"] == 1
    assert waiting["devices"][0]["serial"] == "NEW-BOX-1"


def test_you_do_not_need_the_serial_in_advance(api, account):
    """The console says so, because the old way was to find it first."""
    headers, _ = account

    note = api.get("/api/doorstep", headers=headers).json()["note"]

    assert "do not need its serial in advance" in note


def test_adopting_takes_the_serial_and_nothing_else_typed(api, account):
    headers, _ = account
    report(api, headers)

    added = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": "NEW-BOX-1", "industry_vertical": "restaurant",
        "location_name": "Back kitchen"})

    assert added.status_code == 201, added.text
    body = added.json()
    assert body["sensor"]["external_device_sn"] == "NEW-BOX-1"
    assert body["ingest_key"], "the device still gets its own key"
    assert api.get("/api/doorstep", headers=headers).json()["count"] == 0


def test_the_readings_it_sent_while_waiting_are_kept(api, account):
    """Its history starts when the sensor did, not when somebody got
    round to the paperwork."""
    headers, _ = account
    for temp in (30.0, 31.0, 33.0, 36.0):
        report(api, headers, value=temp)

    added = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": "NEW-BOX-1", "industry_vertical": "restaurant",
        "location_name": "Back kitchen"}).json()

    assert added["replayed_readings"] == 4
    kept = STORE.readings_for(added["sensor"]["sensor_id"])
    assert [r.temperature_fahrenheit for r in kept] == [30.0, 31.0, 33.0, 36.0]


def test_a_replayed_breach_still_opens_an_incident(api, account):
    """Through the same scoring path a live reading takes. Storing the
    numbers without the alarms would give the shape of the history
    without its meaning."""
    headers, tenant = account
    report(api, headers, value=55.0)          # a restaurant freezer at 55F

    added = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": "NEW-BOX-1", "industry_vertical": "restaurant",
        "location_name": "Back kitchen"}).json()

    assert added["replayed_readings"] == 1
    incidents = STORE.incidents_for(tenant["tenant_id"])
    assert any(i.sensor_id == added["sensor"]["sensor_id"] for i in incidents)


def test_seeing_it_again_updates_rather_than_duplicates(api, account):
    headers, _ = account
    for _ in range(5):
        report(api, headers)

    waiting = api.get("/api/doorstep", headers=headers).json()

    assert waiting["count"] == 1
    assert waiting["devices"][0]["times_seen"] == 5


# --- what it refuses to do ------------------------------------------------


def test_a_device_that_never_says_its_unit_is_not_guessed(api, account):
    """Four degrees is a fridge in Celsius and a disaster in Fahrenheit."""
    headers, _ = account
    api.post("/api/v1/bridge/any", headers=headers,
             json={"serial": "NO-UNIT-1", "reading": 4.0,
                   "api_key_token": headers["X-CyberLogix-Key"]})
    waiting = api.get("/api/doorstep", headers=headers).json()
    assert waiting["count"] == 1, (
        "a device that does not state its unit is still a real device "
        "asking to be set up, and losing it is the hassle this removes")

    device = waiting["devices"][0]
    assert device["needs_unit"] is True
    refused = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": device["serial"], "industry_vertical": "restaurant",
        "location_name": "Kitchen"})

    assert refused.status_code == 400
    assert "never said" in refused.json()["detail"]


def test_nothing_is_adopted_automatically(api, account):
    """A seat costs money and an adopted sensor starts being billed. The
    typing is what this removes, not the decision."""
    headers, tenant = account
    report(api, headers)

    fleet = api.get("/api/licenses/me/sensors", headers=headers).json()

    assert not any(s.get("external_device_sn") == "NEW-BOX-1"
                   for s in fleet.get("sensors", []))


def test_one_account_never_sees_anothers_doorstep(api, account,
                                                  tenant_factory,
                                                  owner_headers):
    headers, _ = account
    report(api, headers)
    other, _ = tenant_factory(plan="enterprise", company_name="Other")
    other = {**other, **owner_headers(other, email="other@ex.com")}

    assert api.get("/api/doorstep", headers=other).json()["count"] == 0
    stolen = api.post("/api/doorstep/adopt", headers=other, json={
        "serial": "NEW-BOX-1", "industry_vertical": "restaurant",
        "location_name": "Mine now"})
    assert stolen.status_code == 404


def test_a_gateway_inventing_serials_cannot_fill_the_database(api, account,
                                                              monkeypatch):
    """A misconfigured device can invent a new serial on every request,
    and without a ceiling that is an unbounded write endpoint wearing a
    friendly name."""
    headers, _ = account
    monkeypatch.setattr(doorstep, "MAX_WAITING", 6)

    for n in range(40):
        report(api, headers, serial=f"JUNK-{n}")

    assert api.get("/api/doorstep", headers=headers).json()["count"] <= 6


def test_only_the_most_recent_readings_are_held(api, account, monkeypatch):
    headers, _ = account
    monkeypatch.setattr(doorstep, "KEPT_READINGS", 5)

    for temp in range(20, 40):
        report(api, headers, value=float(temp))

    device = api.get("/api/doorstep", headers=headers).json()["devices"][0]
    assert device["held_readings"] <= 5
    assert device["last_value"] == 39.0


def test_turning_one_away_stops_holding_it(api, account):
    headers, _ = account
    report(api, headers)

    gone = api.delete("/api/doorstep/NEW-BOX-1", headers=headers)

    assert gone.status_code == 204
    assert api.get("/api/doorstep", headers=headers).json()["count"] == 0


def test_adopting_still_respects_the_seat_limit(api, tenant_factory,
                                                owner_headers):
    """One tap is not a way around what the plan was sold as."""
    headers, tenant = tenant_factory(plan="trial", company_name="Small")
    headers = {**headers, **owner_headers(headers)}
    cap = api.get("/api/licenses/me", headers=headers).json()
    seats = cap.get("seats_total") or cap.get("tenant", {}).get("seats_total")
    for n in range(int(seats or 5)):
        api.post("/api/licenses/me/sensors", headers=headers, json={
            "sensor_id": f"S{n}", "industry_vertical": "restaurant",
            "location_name": "X"})
    report(api, headers, serial="ONE-TOO-MANY")

    refused = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": "ONE-TOO-MANY", "industry_vertical": "restaurant",
        "location_name": "Kitchen"})

    assert refused.status_code == 409


def test_a_unitless_device_can_be_adopted_by_saying_which(api, account):
    """The question has a one-tap answer. It is asked, not guessed."""
    headers, _ = account
    api.post("/api/v1/bridge/any", headers=headers,
             json={"serial": "NO-UNIT-2", "reading": 4.0,
                   "api_key_token": headers["X-CyberLogix-Key"]})

    added = api.post("/api/doorstep/adopt", headers=headers, json={
        "serial": "NO-UNIT-2", "industry_vertical": "restaurant",
        "location_name": "Walk-in", "unit": "temperature_c"})

    assert added.status_code == 201, added.text
    kept = STORE.readings_for(added.json()["sensor"]["sensor_id"])
    # 4C is 39.2F. Had it been read as Fahrenheit it would have been 4.
    assert kept[0].temperature_fahrenheit == 39.2


def test_a_broken_doorstep_never_breaks_the_device_talking_to_us(
        api, account, monkeypatch):
    """This runs while answering a sensor, not a person.

    A device that gets a 500 because our holding area had a problem is
    a device whose vendor firmware may back off, retry badly, or stop.
    The reading is still not watched -- it is not set up -- but the
    failure has to be ours to see, not theirs to handle.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("the doorstep is on fire")

    monkeypatch.setattr(STORE._db, "put", explode)
    headers, _ = account

    resp = report(api, headers, serial="WHILE-BROKEN")

    assert resp.status_code < 500, resp.text
