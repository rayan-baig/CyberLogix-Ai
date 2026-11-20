"""The long record: every reading for 30 days, then hourly, for 13 months.

/today says "when a health inspector asks what the walk-in was doing last
March, the answer is a record you can print", and the restaurant page says
every reading is kept. Until 3.21.0 only the newest 500 readings a sensor
were kept: under two days. Now the database keeps every reading for
KEEP_DETAIL_DAYS, and an hourly low, high and average after that, until
KEEP_READINGS_MONTHS; the reports, the daily history and the delete
button all cover both.
"""

from datetime import timedelta

import pytest

import store
from store import STORE, utc_now


@pytest.fixture()
def walk_in(api, tenant_factory, sensor_factory):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Harbor Grill")
    sensor_factory(headers, sensor_id="WALKIN-1", vertical="restaurant",
                   location="Walk-in cooler", kind="fridge")
    return headers, STORE.get_tenant(tenant["tenant_id"]), STORE.get_sensor("WALKIN-1")


def _old_hour(sensor, days_ago, temps, minute_step=5):
    """Readings an hour long, `days_ago` days back, at the top of an hour."""
    start = (utc_now() - timedelta(days=days_ago)).replace(minute=0, second=0, microsecond=0)
    for n, temp in enumerate(temps):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=temp,
                             humidity_percent=None, breached=temp > 41.0,
                             at=start + timedelta(minutes=n * minute_step))
    return start


def test_old_readings_are_folded_into_hours_and_kept(walk_in):
    _, _, sensor = walk_in
    hour = _old_hour(sensor, 90, [36.0, 38.0, 44.0, 37.0])
    _old_hour(sensor, 1, [37.0, 37.5])

    result = STORE.roll_up_old_readings()

    assert result["readings_folded"] == 4
    (row,) = STORE.hourly_for("WALKIN-1", hour - timedelta(hours=1), hour + timedelta(hours=1))
    assert row == {"hour": store.iso(hour), "count": 4, "breached": 1,
                   "min_f": 36.0, "max_f": 44.0, "avg_f": 38.75}
    # The recent ones are still there reading by reading.
    assert len(STORE.readings_for("WALKIN-1", complete=True)) == 2
    assert STORE.detail_floor("WALKIN-1") is not None
    # A second pass has nothing to do, and does not double the hour.
    assert STORE.roll_up_old_readings()["readings_folded"] == 0
    assert STORE.hourly_for("WALKIN-1")[0]["count"] == 4


def test_a_report_for_last_season_reads_the_hours(api, walk_in):
    headers, _, sensor = walk_in
    hour = _old_hour(sensor, 75, [36.0, 39.0, 45.0])
    STORE.roll_up_old_readings()
    day = hour.date().isoformat()

    log = api.get(f"/api/autopilot/inspector-log?start={day}&end={day}",
                  headers=headers).json()
    (unit,) = log["units"]
    assert unit["readings"] == 3
    assert unit["out_of_range_readings"] == 1
    assert (unit["lowest"], unit["highest"]) == (36.0, 45.0)
    assert unit["hours"][0]["low"] == 36.0 and unit["hours"][0]["high"] == 45.0
    assert log["covers_the_whole_period"] is True

    # The sensor's own daily history reaches back too.
    detail = api.get("/api/console/sensor/WALKIN-1", headers=headers).json()
    assert any(d["day"] == day and d["high"] == 45.0 for d in detail["daily"])
    assert detail["readings_total"] == 3


def test_the_compliance_figures_count_every_reading_kept(api, walk_in):
    headers, _, sensor = walk_in
    _old_hour(sensor, 40, [36.0, 50.0])                 # folded into an hour
    _old_hour(sensor, 3, [37.0, 37.0, 37.0])            # still in full
    STORE.roll_up_old_readings()

    report = api.get("/api/autopilot/compliance?days=60", headers=headers).json()
    row = report["per_sensor"][0]
    assert row["readings_logged"] == 5
    assert row["readings_breached"] == 1
    assert (row["min_temperature"], row["max_temperature"]) == (36.0, 50.0)


def test_nothing_is_kept_past_the_months(walk_in):
    _, _, sensor = walk_in
    _old_hour(sensor, 500, [36.0])
    _old_hour(sensor, 300, [37.0])
    result = STORE.roll_up_old_readings()
    assert result["hours_dropped"] == 1
    assert [h["min_f"] for h in STORE.hourly_for("WALKIN-1")] == [37.0]


def test_a_reading_is_copied_onto_its_alert_before_it_is_folded(api, walk_in):
    headers, tenant, sensor = walk_in
    start = _old_hour(sensor, 45, [38.0, 60.0, 61.0])
    incident, _ = STORE.open_incident_once(
        tenant_id=tenant.tenant_id, sensor=sensor, temperature_fahrenheit=60.0,
        breach_details="Too warm: 60°F (limit 41°F).", sms_text="x",
        sms_dispatch_source="test")
    incident.opened_at = start + timedelta(minutes=5)
    incident.evidence_readings = []
    STORE.roll_up_old_readings()
    kept = [r["temperature_fahrenheit"] for r in STORE.get_incident(incident.incident_id).evidence_readings]
    assert kept == [38.0, 60.0, 61.0]


def test_memory_holds_the_newest_and_a_restart_brings_back_only_those(
        walk_in, monkeypatch):
    _, _, sensor = walk_in
    monkeypatch.setattr(store, "MAX_READINGS_PER_SENSOR", 5)
    STORE._readings["WALKIN-1"] = store.deque(maxlen=5)
    for n in range(12):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=30.0 + n,
                             humidity_percent=None, breached=False,
                             at=utc_now() - timedelta(minutes=12 - n))
    assert len(STORE.readings_for("WALKIN-1")) == 5
    assert len(STORE.readings_for("WALKIN-1", complete=True)) == 12
    since = utc_now() - timedelta(minutes=10, seconds=30)
    assert [r.temperature_fahrenheit for r in
            STORE.readings_for("WALKIN-1", since=since, complete=True)][0] == 32.0
    STORE.forget()
    STORE.load()
    assert [r.temperature_fahrenheit for r in STORE.readings_for("WALKIN-1")] == [
        37.0, 38.0, 39.0, 40.0, 41.0]


def test_reading_queries_use_the_index():
    plan = STORE._db._conn.execute(
        "EXPLAIN QUERY PLAN SELECT data FROM records WHERE kind = 'reading' "
        "AND json_extract(data, '$.sensor_id') = ? "
        "AND json_extract(data, '$.recorded_at') >= ?", ("S", "2026")).fetchall()
    assert "records_reading_time" in str(plan)


# --- taking it away, all of it -----------------------------------------------


def test_retiring_a_sensor_removes_every_reading_it_sent(api, walk_in):
    headers, _, sensor = walk_in
    _old_hour(sensor, 60, [36.0, 37.0])
    _old_hour(sensor, 2, [37.0])
    STORE.roll_up_old_readings()
    STORE.remove_sensor("WALKIN-1")
    assert STORE._db.count("reading") == 0
    assert STORE._db.count("reading_hour") == 0
    assert STORE._db.count("reading_floor") == 0


def test_delete_everything_deletes_the_hours_too(api, owner_headers, walk_in):
    headers, tenant, sensor = walk_in
    _old_hour(sensor, 60, [36.0, 37.0])
    _old_hour(sensor, 2, [37.0])
    STORE.roll_up_old_readings()

    owner = owner_headers(headers)
    exported = api.get("/api/accounts/business/export", headers=owner).json()
    kinds = exported.get("records", exported)
    assert len(kinds["reading_hour"]) == 1 and len(kinds["reading"]) == 1

    removed = STORE.erase_tenant(tenant.tenant_id)
    assert removed["reading"] == 1 and removed["reading_hour"] == 1
    assert STORE._db.count("reading") == 0
    assert STORE._db.count("reading_hour") == 0
    assert STORE._db.count("reading_floor") == 0
