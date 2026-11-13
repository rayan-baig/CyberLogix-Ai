"""The Kitchen Safety Bundle: each day's report, sent to OmniArmor exactly
as its contract (OmniArmor docs/CYBERLOGIX-CONNECTION.md) says.

Every report here goes through tests/omniarmor_contract.FakeOmniArmor,
which validates with OmniArmor's own validation code, ported: a field
built wrong is a 422 here, as it would be there.
"""

import ast
import json
import re
import threading
import time
from datetime import datetime, time as clock, timedelta, timezone
from pathlib import Path

import pytest

import omniarmor
import pricing
from omniarmor_contract import ACTIONS as CONTRACT_ACTIONS
from omniarmor_contract import ROLES as CONTRACT_ROLES
from omniarmor_contract import FakeOmniArmor, validate
import omniarmor_contract as contract
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
OMNIARMOR_SOURCE = ROOT.parent / "omniarmor.ai" / "omniarmor_app" / "connections.py"
KEY = "clx_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v"
ADDRESS = "https://omniarmor.example.com"
assert len(KEY) == 47


def _today_utc():
    return utc_now().astimezone(timezone.utc).date()


def _at(day, hour, minute=0):
    return datetime.combine(day, clock(hour, minute), tzinfo=timezone.utc)


@pytest.fixture()
def kitchen(api, operator_factory, sensor_factory):
    """A restaurant with two units, connected to OmniArmor, on UTC."""
    owner, tenant, user = operator_factory(plan="growth", company_name="Harbor Grill",
                                           email="robin@harborgrill.example",
                                           full_name="Robin Vale")
    key = {"X-CyberLogix-Key": api.post("/api/licenses/me/rotate-key",
                                        headers=owner).json()["api_key"]}
    sensor_factory(key, sensor_id="HG-WALKIN", vertical="restaurant", location="Walk-in freezer")
    sensor_factory(key, sensor_id="HG-BAR", vertical="restaurant", location="Bar fridge")
    for sensor in STORE.sensors_for(tenant["tenant_id"]):    # installed a while ago
        sensor.registered_at = utc_now() - timedelta(days=20)
        STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    saved = api.put("/api/connections/omniarmor", headers=owner, json={
        "address": ADDRESS, "key": KEY, "timezone": "UTC", "site_label": "Main kitchen"})
    assert saved.status_code == 200, saved.text
    return {"owner": owner, "tenant": STORE.get_tenant(tenant["tenant_id"]), "key": key,
            "user": user}


@pytest.fixture()
def fake(monkeypatch):
    server = FakeOmniArmor(KEY, _today_utc())
    monkeypatch.setattr(omniarmor, "TRANSPORT", server)
    return server


def _clean_day(day, temps=((2.0, 6.5, 4.0), (35.0 - 2, 30.5, 28.0))):
    walkin, bar = STORE.get_sensor("HG-WALKIN"), STORE.get_sensor("HG-BAR")
    for sensor, values in ((walkin, temps[0]), (bar, temps[1])):
        for n, value in enumerate(values):
            STORE.record_reading(sensor, value, None, False, at=_at(day, 3 + n * 6))


def _breach(day, start=(2, 10), end=(2, 45), peak=41.0):
    walkin = STORE.get_sensor("HG-WALKIN")
    opened = _at(day, *start)
    STORE.record_reading(walkin, 5.0, None, False, at=opened - timedelta(minutes=30))
    STORE.record_reading(walkin, 36.0, None, True, at=opened)
    STORE.record_reading(walkin, peak, None, True, at=opened + timedelta(minutes=15))
    STORE.record_reading(walkin, 6.0, None, False, at=_at(day, *end))
    STORE.record_reading(walkin, 7.5, None, False, at=_at(day, 12))
    incident = STORE.open_incident(
        tenant_id=walkin.tenant_id, sensor=walkin, temperature_fahrenheit=36.0,
        breach_details="Walk-in freezer above 32F", sms_text="x",
        sms_dispatch_source="template", opened_at=opened)
    return incident


def _now_after(day, hours=1):
    return _at(day + timedelta(days=1), 0) + timedelta(hours=hours)


def _sent(fake):
    return [json.loads(call["body"]) for call in fake.calls]


# ---- the contract, and drift -------------------------------------------------


def test_the_lists_and_limits_are_the_contracts():
    assert omniarmor.ROLES == CONTRACT_ROLES
    assert omniarmor.ACTIONS == tuple(CONTRACT_ACTIONS)
    assert omniarmor.MAX_BODY == contract.MAX_BODY
    assert omniarmor.MAX_UNITS == contract.MAX_UNITS
    assert omniarmor.MAX_BREACHES == contract.MAX_BREACHES
    assert omniarmor.MAX_TEXT == contract.MAX_TEXT
    assert omniarmor.MAX_URL == contract.MAX_URL
    assert (omniarmor.LOWEST, omniarmor.HIGHEST) == (contract.LOWEST_TEMP, contract.HIGHEST_TEMP)
    assert omniarmor.LONGEST_BREACH == contract.LONGEST_BREACH
    assert omniarmor.KEY_RE.match(KEY)


@pytest.mark.skipif(not OMNIARMOR_SOURCE.exists(),
                    reason="OmniArmor's code is not checked out beside this repository")
def test_the_ported_validation_is_still_omniarmors():
    """Where OmniArmor's code is beside this one, read its lists and limits
    and its validate() and hold the port to them."""
    tree = ast.parse(OMNIARMOR_SOURCE.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names = ([target.id] if isinstance(target, ast.Name) else
                         [e.id for e in getattr(target, "elts", []) if isinstance(e, ast.Name)])
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    continue
                if len(names) == 1:
                    values[names[0]] = value
                elif isinstance(value, tuple):
                    values.update(zip(names, value))
    assert values["ROLES"] == contract.ROLES
    assert values["ACTIONS"] == contract.ACTIONS
    assert values["UNITS_OF_MEASURE"] == contract.UNITS_OF_MEASURE
    for name in ("MAX_BREACHES", "MAX_UNITS", "MAX_TEXT", "MAX_URL", "OLDEST_DAYS",
                 "LOWEST_TEMP", "HIGHEST_TEMP", "KEY_PREFIX"):
        assert values[name] == getattr(contract, name), name
    def body_of(module):
        func = next(n for n in module.body
                    if isinstance(n, ast.FunctionDef) and n.name == "validate")
        return [ast.dump(statement) for statement in func.body[1:]]   # past the docstring

    assert body_of(tree) == body_of(ast.parse(Path(contract.__file__).read_text()))


# ---- a clean day and a day with a breach -------------------------------------


def test_a_clean_day_is_exactly_the_contracts_shape(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    result = omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    assert result["sent"] == 1
    [body] = _sent(fake)
    assert set(body) == {"date", "site", "units_total", "units_in_range_all_day",
                         "breaches", "warmest_reading", "unit_of_measure", "log_url"}
    assert body["date"] == day.isoformat()
    assert body["site"] == "Main kitchen"
    assert (body["units_total"], body["units_in_range_all_day"]) == (2, 2)
    assert body["breaches"] == []
    assert body["warmest_reading"] == 33.0 and body["unit_of_measure"] == "°F"
    assert body["log_url"].startswith("https://hub.example/console#inspector-log?")
    assert f"from={day.isoformat()}&to={day.isoformat()}" in body["log_url"]
    validate(body, _today_utc())                   # OmniArmor's own check
    assert fake.accepted[0]["units_total"] == 2
    row = omniarmor.day_row(kitchen["tenant"].tenant_id, day)
    assert row["state"] == "sent" and row["answer"]["ticked"] == 1


def test_a_breach_day_carries_the_breach_mapped_to_the_lists(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    incident = _breach(day)
    STORE.acknowledge_incident(incident, "Ash, by text reply (+15550142)")
    STORE.record_corrective_action(incident, "Ash", "Door found open and closed", "")
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    [body] = _sent(fake)
    assert (body["units_total"], body["units_in_range_all_day"]) == (2, 1)
    [breach] = body["breaches"]
    assert breach == {
        "unit": "Walk-in freezer",
        "started_at": f"{day.isoformat()}T02:10:00+00:00",
        "ended_at": f"{day.isoformat()}T02:45:00+00:00",
        "peak": 41.0, "limit": 32.0, "unit_of_measure": "°F",
        "acknowledged_by_role": "on-call staff",
        "action": "closed_door",
    }
    assert body["warmest_reading"] == 33.0            # in range, from the other unit
    validate(body, _today_utc())
    assert omniarmor.day_row(kitchen["tenant"].tenant_id, day)["answer"]["noted"] == 1


def test_celsius_and_the_sites_own_clock(api, kitchen, fake, mailbox):
    api.post("/api/licenses/me/temperature-unit", headers=kitchen["owner"],
             json={"temperature_unit": "C"})
    api.put("/api/connections/omniarmor", headers=kitchen["owner"],
            json={"address": ADDRESS, "timezone": "America/New_York"})
    day = _today_utc() - timedelta(days=2)
    # 02:10 in New York is 07:10 UTC in November.
    walkin = STORE.get_sensor("HG-WALKIN")
    _clean_day(day + timedelta(days=0))
    incident = _breach(day, start=(7, 10), end=(7, 45))
    assert incident.sensor_id == walkin.sensor_id
    tenant = STORE.get_tenant(kitchen["tenant"].tenant_id)
    report = omniarmor.build_report(tenant, omniarmor.connection(tenant.tenant_id), day,
                                    now=_now_after(day, 12))
    [breach] = report["breaches"]
    assert breach["started_at"].endswith("-05:00") and "T02:10:00" in breach["started_at"]
    assert breach["unit_of_measure"] == "°C" and report["unit_of_measure"] == "°C"
    assert breach["limit"] == 0.0 and breach["peak"] == 5.0
    validate(report, _today_utc())


def test_a_unit_that_sent_nothing_is_not_counted_in_range(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    walkin = STORE.get_sensor("HG-WALKIN")
    STORE.record_reading(walkin, 3.0, None, False, at=_at(day, 9))
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    [body] = _sent(fake)
    assert (body["units_total"], body["units_in_range_all_day"]) == (2, 1)
    assert body["breaches"] == []
    validate(body, _today_utc())


def test_a_day_with_no_readings_at_all_is_not_sent_as_clean(kitchen, fake):
    day = _today_utc() - timedelta(days=1)
    tenant = kitchen["tenant"]
    report = omniarmor.build_report(tenant, omniarmor.connection(tenant.tenant_id), day)
    assert report["units_in_range_all_day"] == 0      # not confirmed: a note, not a tick
    validate(report, _today_utc())


def test_breaches_are_capped_and_the_body_fits(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    walkin = STORE.get_sensor("HG-WALKIN")
    for n in range(60):
        at = _at(day, 0) + timedelta(minutes=20 * n)
        STORE.record_reading(walkin, 40.0, None, True, at=at)
        STORE.record_reading(walkin, 5.0, None, False, at=at + timedelta(minutes=5))
        incident = STORE.open_incident(tenant_id=walkin.tenant_id, sensor=walkin,
                                       temperature_fahrenheit=40.0, breach_details="x",
                                       sms_text="x", sms_dispatch_source="t", opened_at=at)
        incident.resolved_at = at + timedelta(minutes=5)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    [call] = fake.calls
    body = json.loads(call["body"])
    assert len(body["breaches"]) == omniarmor.MAX_BREACHES
    assert len(call["body"]) <= omniarmor.MAX_BODY
    validate(body, _today_utc())


# ---- roles and actions ----------------------------------------------------------


def test_people_become_roles(api, kitchen):
    tenant = kitchen["tenant"]
    owner = kitchen["user"]
    made = api.post("/api/accounts/users", headers=kitchen["owner"], json={
        "email": "kit@harborgrill.example", "full_name": "Kit Moreno",
        "password": "correct-horse-battery", "role": "operator"})
    assert made.status_code in (200, 201)
    viewer = api.post("/api/accounts/users", headers=kitchen["owner"], json={
        "email": "lee@harborgrill.example", "full_name": "Lee Park",
        "password": "correct-horse-battery", "role": "viewer"})
    assert viewer.status_code in (200, 201)
    role = omniarmor.role_for
    assert role(tenant, f"{owner['full_name']} <{owner['email']}>") == "owner"
    assert role(tenant, "Kit Moreno <kit@harborgrill.example>") == "manager"
    assert role(tenant, "Lee Park") == "kitchen staff"
    assert role(tenant, "phone keypad (+15550100)") == "on-call staff"
    assert role(tenant, "Night Cook, by text reply (+15550101)") == "on-call staff"
    assert role(tenant, "Kit Moreno <kit@harborgrill.example>, with site-wide alert INC-1") == "manager"
    assert role(tenant, "API key") == "on-call staff"
    assert role(tenant, None) is None
    assert set(omniarmor.USER_ROLES.values()) <= set(CONTRACT_ROLES)


def test_what_was_done_becomes_an_action_code():
    from voice_dispatch import CORRECTIVE_ACTIONS, DEFAULT_ACTIONS, DISPOSITIONS

    class Incident:
        def __init__(self, action=None, fate=None, acked=True):
            self.corrective_action = action
            self.product_disposition = fate
            self.acknowledged_at = utc_now() if acked else None

    expected = {
        "Moved product to another unit": "moved_food",
        "Discarded affected product": "discarded_food",
        "Door found open and closed": "closed_door",
        "Called refrigeration engineer": "called_technician",
        "Unit defrosted and recovered": "defrost_cycle",
        "Fault corrected on site": "adjusted_unit",
        "Called an engineer": "called_technician",
        "Load moved elsewhere": "moved_food",
        "No action needed; reading was transient": "monitoring",
        "Stock destroyed and recorded": "discarded_food",
        "Samples quarantined pending review": "moved_food",
    }
    for text, code in expected.items():
        assert omniarmor.action_for(Incident(text)) == code, text
    every = [a for options in CORRECTIVE_ACTIONS.values() for a in options] + DEFAULT_ACTIONS
    for text in every:
        assert omniarmor.action_for(Incident(text)) in CONTRACT_ACTIONS, text
    for fate in DISPOSITIONS:
        assert omniarmor.action_for(Incident(None, fate)) in CONTRACT_ACTIONS, fate
    assert omniarmor.action_for(Incident(None, "discarded")) == "discarded_food"
    assert omniarmor.action_for(Incident(None, None, acked=True)) == "none"
    assert omniarmor.action_for(Incident(None, None, acked=False)) is None


# ---- no names, ever -----------------------------------------------------------------


def test_no_persons_name_email_or_phone_leaves(api, kitchen, fake, mailbox):
    api.post("/api/contacts", headers=kitchen["owner"],
             json={"full_name": "Ash Okafor", "phone": "+15550142"})
    # A unit named after a person, and a site label with a phone number.
    walkin = STORE.get_sensor("HG-WALKIN")
    walkin.location_name = "Ash's freezer"
    STORE._db.put("sensor", walkin.sensor_id, walkin.to_row())
    api.put("/api/connections/omniarmor", headers=kitchen["owner"],
            json={"address": ADDRESS, "site_label": "Call 555 010 0142"})
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    incident = _breach(day)
    STORE.acknowledge_incident(incident, f"{kitchen['user']['full_name']} <{kitchen['user']['email']}>")
    STORE.record_corrective_action(incident, "Robin Vale", "Moved product to another unit", "moved")
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    [call] = fake.calls
    raw = call["body"].decode("utf-8")
    for leak in ("Robin", "Vale", "Ash", "Okafor", "harborgrill", "@", "0142", "Dana", "Reyes"):
        assert leak not in raw, leak
    body = json.loads(raw)
    assert body["site"] is None
    assert body["breaches"][0]["unit"].startswith("Unit ")
    assert body["breaches"][0]["acknowledged_by_role"] == "owner"
    validate(body, _today_utc())


# ---- reliability: retries and backoff ---------------------------------------------


def test_429_and_5xx_are_retried_with_backoff_and_retry_after(kitchen, fake, mailbox):
    fake.script = [(503, {}), (429, {"Retry-After": "7"}), (500, {})]
    waits = []
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    result = omniarmor.run_daily(now=_now_after(day), sleep=waits.append)
    assert result["sent"] == 1
    assert waits == [2.0, 7.0, 8.0]                 # backoff, Retry-After when longer, backoff
    assert len(fake.calls) == 4
    assert omniarmor.day_row(kitchen["tenant"].tenant_id, day)["attempts"] == 4


def test_a_long_retry_after_is_left_for_a_later_pass(kitchen, fake, mailbox):
    fake.script = [(429, {"Retry-After": "3600"})]
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    now = _now_after(day)
    omniarmor.run_daily(now=now, sleep=lambda s: pytest.fail("must not sleep an hour"))
    row = omniarmor.day_row(kitchen["tenant"].tenant_id, day)
    assert row["last_outcome"] == "retry_later" and row["next_attempt_at"]
    omniarmor.run_daily(now=now + timedelta(minutes=30), sleep=lambda s: None)
    assert len(fake.calls) == 1                      # still waiting
    omniarmor.run_daily(now=now + timedelta(minutes=61), sleep=lambda s: None)
    assert len(fake.calls) == 2 and fake.accepted


def test_a_retry_after_given_as_a_date_is_understood():
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    headers = {"retry-after": "Fri, 13 Nov 2026 12:00:30 GMT"}
    assert omniarmor.retry_after_seconds(headers, now) == 30.0
    assert omniarmor.retry_after_seconds({"Retry-After": "12"}) == 12.0
    assert omniarmor.retry_after_seconds({}) is None


def test_a_network_failure_is_retried(kitchen, monkeypatch, mailbox):
    server = FakeOmniArmor(KEY, _today_utc())
    calls = []

    def flaky(url, key, body):
        calls.append(1)
        if len(calls) < 3:
            raise OSError("connection reset")
        return server(url, key, body)

    monkeypatch.setattr(omniarmor, "TRANSPORT", flaky)
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    assert len(calls) == 3 and server.accepted


def test_giving_up_records_the_failure_per_day(kitchen, fake, mailbox):
    fake.script = [(503, {})] * omniarmor.MAX_ATTEMPTS
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    result = omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    assert result["failed"] == 1
    row = omniarmor.day_row(kitchen["tenant"].tenant_id, day)
    assert row["state"] == "failed" and row["http_status"] == 503
    seen = omniarmor.overview(kitchen["tenant"])
    assert f"not sent since {day.isoformat()}" in seen["status"]


def test_401_stops_sending_until_a_new_key(api, kitchen, fake, mailbox):
    fake.key = "clx_" + "Z" * 43                     # OmniArmor regenerated its key
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    assert len(fake.calls) == 1                      # no retry on 401
    seen = api.get("/api/connections/omniarmor", headers=kitchen["owner"]).json()
    assert seen["key_rejected"] and "paste a new one" in seen["status"]
    omniarmor.run_daily(now=_now_after(day, 3), sleep=lambda s: None)
    assert len(fake.calls) == 1                      # stopped
    api.put("/api/connections/omniarmor", headers=kitchen["owner"],
            json={"address": ADDRESS, "key": fake.key})
    omniarmor.run_daily(now=_now_after(day, 4), sleep=lambda s: None)
    assert fake.accepted


def test_422_is_our_bug_logged_as_a_fault_and_not_resent_as_it_is(kitchen, fake, mailbox):
    import faults

    fake.script = [(422, {})]
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    assert any("omniarmor" in (row.get("source") or "") for row in faults.recent())
    omniarmor.run_daily(now=_now_after(day, 2), sleep=lambda s: None)
    assert len(fake.calls) == 1


# ---- never twice, under the claim ----------------------------------------------


def test_the_same_day_is_never_sent_twice(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    omniarmor.run_daily(now=_now_after(day, 2), sleep=lambda s: None)
    omniarmor.run_daily(now=_now_after(day, 30), sleep=lambda s: None)
    dates = [json.loads(c["body"])["date"] for c in fake.calls]
    assert dates.count(day.isoformat()) == 1


def test_a_day_another_pass_holds_is_left_alone(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    now = _now_after(day)
    tenant_id = kitchen["tenant"].tenant_id
    STORE._db.put(omniarmor.DAY_KIND, f"{tenant_id}:{day.isoformat()}", {
        "tenant_id": tenant_id, "date": day.isoformat(), "state": "sending",
        "claimed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sent_fingerprint": None})
    omniarmor.run_daily(now=now, sleep=lambda s: None)
    assert fake.calls == []
    # A claim from a pass that died is taken over after the lease.
    omniarmor.run_daily(now=now + omniarmor.CLAIM_LEASE + timedelta(minutes=1),
                        sleep=lambda s: None)
    assert len(fake.calls) == 1


def test_two_passes_at_once_send_once(kitchen, monkeypatch, mailbox):
    server = FakeOmniArmor(KEY, _today_utc())

    def slow(url, key, body):
        time.sleep(0.2)
        return server(url, key, body)

    monkeypatch.setattr(omniarmor, "TRANSPORT", slow)
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    now = _now_after(day)
    tenant = kitchen["tenant"]
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        omniarmor.send_day(tenant, day, now, sleep=lambda s: None))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(server.calls) == 1
    assert sorted(r["status"] for r in results).count("ok") == 1


def test_the_pass_itself_runs_one_at_a_time(kitchen, fake):
    assert omniarmor._pass_lock.acquire(blocking=False)
    try:
        assert omniarmor.run_daily()["skipped"]
    finally:
        omniarmor._pass_lock.release()


# ---- corrections ------------------------------------------------------------------


def test_a_day_that_changed_after_it_was_sent_is_sent_again_as_a_correction(
        kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=1)
    _clean_day(day)
    incident = _breach(day)
    omniarmor.run_daily(now=_now_after(day), sleep=lambda s: None)
    first = _sent(fake)[0]["breaches"][0]
    assert first["acknowledged_by_role"] is None and first["action"] is None

    # The next morning somebody signs the alarm off and writes it up.
    STORE.acknowledge_incident(incident, "phone keypad (+15550100)")
    STORE.record_corrective_action(incident, "x", "Discarded affected product", "discarded")
    omniarmor.run_daily(now=_now_after(day, 9), sleep=lambda s: None)

    assert len(fake.calls) == 2
    second = _sent(fake)[1]
    assert second["date"] == day.isoformat()
    assert second["breaches"][0]["acknowledged_by_role"] == "on-call staff"
    assert second["breaches"][0]["action"] == "discarded_food"
    row = omniarmor.day_row(kitchen["tenant"].tenant_id, day)
    assert row["corrections"] == 1 and row["state"] == "sent"
    # And nothing more once it stands.
    omniarmor.run_daily(now=_now_after(day, 10), sleep=lambda s: None)
    assert len(fake.calls) == 2


def test_an_old_day_is_not_reopened(kitchen, fake, mailbox):
    day = _today_utc() - timedelta(days=4)
    _clean_day(day)
    tenant_id = kitchen["tenant"].tenant_id
    conn = omniarmor.connection(tenant_id)
    conn["connected_at"] = (utc_now() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put(omniarmor.CONNECTION_KIND, tenant_id, conn)
    omniarmor.run_daily(sleep=lambda s: None)
    sent_for_day = [c for c in _sent(fake) if c["date"] == day.isoformat()]
    assert len(sent_for_day) == 1
    STORE.record_reading(STORE.get_sensor("HG-BAR"), 31.5, None, False, at=_at(day, 22))
    omniarmor.run_daily(sleep=lambda s: None)
    assert len([c for c in _sent(fake) if c["date"] == day.isoformat()]) == 1


# ---- the owners hear about two days of failure, once ---------------------------------


def test_two_days_of_failure_email_the_owners_once(kitchen, monkeypatch, mailbox):
    monkeypatch.setattr(omniarmor, "TRANSPORT",
                        lambda url, key, body: (503, {}, b'{"ok": false}'))
    tenant_id = kitchen["tenant"].tenant_id
    conn = omniarmor.connection(tenant_id)
    conn["connected_at"] = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put(omniarmor.CONNECTION_KIND, tenant_id, conn)
    first = _today_utc() - timedelta(days=2)
    _clean_day(first)
    omniarmor.run_daily(now=_now_after(first), sleep=lambda s: None)
    assert not [m for m in mailbox if "OmniArmor" in m["Subject"]]

    second = first + timedelta(days=1)
    _clean_day(second)
    omniarmor.run_daily(now=_now_after(second), sleep=lambda s: None)
    omniarmor.run_daily(now=_now_after(second, 5), sleep=lambda s: None)
    emails = [m for m in mailbox if "OmniArmor" in m["Subject"]]
    assert len(emails) == 1
    assert emails[0]["To"] == kitchen["user"]["email"]
    body = emails[0].get_body(preferencelist=("plain",)).get_content()
    assert "2 days" in body and "alarms are not affected" in body


def test_a_success_ends_the_run_and_a_new_run_emails_again(kitchen, monkeypatch, mailbox):
    tenant = kitchen["tenant"]
    tid = tenant.tenant_id
    for back in (6, 5):
        d = (_today_utc() - timedelta(days=back)).isoformat()
        STORE._db.put(omniarmor.DAY_KIND, f"{tid}:{d}", {"tenant_id": tid, "date": d,
                      "state": "failed", "last_outcome": "failed", "sent_fingerprint": None})
    assert omniarmor.maybe_email_owners(tenant)
    assert not omniarmor.maybe_email_owners(tenant)
    d = (_today_utc() - timedelta(days=4)).isoformat()
    STORE._db.put(omniarmor.DAY_KIND, f"{tid}:{d}", {"tenant_id": tid, "date": d,
                  "state": "sent", "last_outcome": "ok", "sent_fingerprint": "f"})
    assert omniarmor.failing_days(tid) == []
    for back in (3, 2):
        d = (_today_utc() - timedelta(days=back)).isoformat()
        STORE._db.put(omniarmor.DAY_KIND, f"{tid}:{d}", {"tenant_id": tid, "date": d,
                      "state": "failed", "last_outcome": "failed", "sent_fingerprint": None})
    assert omniarmor.maybe_email_owners(tenant)
    assert len([m for m in mailbox if "OmniArmor" in m["Subject"]]) == 2


# ---- the key is kept encrypted --------------------------------------------------------


def test_the_key_is_stored_sealed_and_never_shown(api, kitchen):
    tenant_id = kitchen["tenant"].tenant_id
    row = omniarmor.connection(tenant_id)
    assert KEY not in json.dumps(row)
    assert row["key_ciphertext"].startswith("v1.") and row["key_hint"] == KEY[-4:]
    assert omniarmor.unseal(tenant_id, row["key_ciphertext"]) == KEY
    with pytest.raises(Exception):                   # bound to its own business
        omniarmor.unseal("TEN-OTHER", row["key_ciphertext"])
    for kind in STORE._db.kinds():
        assert KEY not in json.dumps(STORE._db.all(kind)), kind
    seen = api.get("/api/connections/omniarmor", headers=kitchen["owner"]).json()
    assert KEY not in json.dumps(seen) and seen["key_hint"] == KEY[-4:]
    exported = api.get("/api/accounts/business/export", headers=kitchen["owner"]).json()
    assert "key_ciphertext" not in json.dumps(exported)


def test_without_the_server_secret_nothing_is_connected(api, kitchen, monkeypatch):
    monkeypatch.setattr(omniarmor, "CREDENTIAL_KEY", "")
    refused = api.put("/api/connections/omniarmor", headers=kitchen["owner"],
                      json={"address": ADDRESS, "key": KEY})
    assert refused.status_code == 503
    assert "CYBERLOGIX_CREDENTIAL_KEY" in refused.json()["detail"]


def test_a_changed_server_secret_cannot_open_an_old_key(kitchen, monkeypatch):
    row = omniarmor.connection(kitchen["tenant"].tenant_id)
    monkeypatch.setattr(omniarmor, "CREDENTIAL_KEY", "a-different-secret")
    with pytest.raises(Exception):
        omniarmor.unseal(kitchen["tenant"].tenant_id, row["key_ciphertext"])


def test_the_key_and_address_are_checked(api, kitchen):
    owner = kitchen["owner"]
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": ADDRESS, "key": "clx_short"}).status_code == 422
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": "http://omniarmor.example.com"}).status_code == 422
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": "https://127.0.0.1"}).status_code == 422
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": "https://user:pw@omniarmor.example.com"}).status_code == 422
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": ADDRESS, "timezone": "Mars/Base"}).status_code == 422
    assert api.put("/api/connections/omniarmor", headers=owner,
                   json={"address": ADDRESS, "site_ids": ["SITE-NOPE"]}).status_code == 404


# ---- owner only ----------------------------------------------------------------------


def test_only_an_owner_can_see_or_change_the_connection(api, kitchen):
    for role, email in (("operator", "op@harborgrill.example"),
                        ("viewer", "view@harborgrill.example")):
        made = api.post("/api/accounts/users", headers=kitchen["owner"], json={
            "email": email, "full_name": role.title(),
            "password": "correct-horse-battery", "role": role})
        assert made.status_code in (200, 201)
        token = api.post("/api/accounts/login", json={
            "email": email, "password": "correct-horse-battery"}).json()["token"]
        them = {"Authorization": f"Bearer {token}"}
        assert api.get("/api/connections/omniarmor", headers=them).status_code == 403
        assert api.put("/api/connections/omniarmor", headers=them,
                       json={"address": ADDRESS, "key": KEY}).status_code == 403
        assert api.post("/api/connections/omniarmor/test", headers=them).status_code == 403
        assert api.delete("/api/connections/omniarmor", headers=them).status_code == 403
    assert api.get("/api/connections/omniarmor", headers=kitchen["key"]).status_code == 401


# ---- the console, the test button and the status line -------------------------------


def test_the_test_button_sends_yesterday_and_says_so(api, kitchen, fake, mailbox):
    tz_day = _today_utc() - timedelta(days=1)
    _clean_day(tz_day)
    out = api.post("/api/connections/omniarmor/test", headers=kitchen["owner"]).json()
    assert out["status"] == "ok" and "OmniArmor took it" in out["message"]
    assert out["connection"]["status"] == f"Last sent {tz_day.isoformat()} · OK"
    again = api.post("/api/connections/omniarmor/test", headers=kitchen["owner"]).json()
    assert again["status"] == "already_sent"
    assert len(fake.calls) == 1
    # The daily job does not send it a second time either.
    omniarmor.run_daily(sleep=lambda s: None)
    assert [json.loads(c["body"])["date"] for c in fake.calls].count(tz_day.isoformat()) == 1


def test_the_console_card_is_owner_only_and_never_holds_the_key():
    page = (ROOT / "static" / "console.html").read_text()
    script = (ROOT / "static" / "console.js").read_text()
    assert 'id="find-omniarmor"' in page and "Send a test report" in page
    assert "/api/connections/omniarmor/test" in script
    assert 'id="find-referrals"' in page and "/api/referrals/me" in script
    assert "notOwner" in script and "omni-not-owner" in page
    assert not re.search(r"style=", page)


def test_disconnecting_deletes_the_key_and_stops_the_bundle(api, kitchen, fake):
    resp = api.delete("/api/connections/omniarmor", headers=kitchen["owner"])
    assert resp.status_code == 200
    assert omniarmor.connection(kitchen["tenant"].tenant_id) is None
    assert not omniarmor.bundle_active(kitchen["tenant"].tenant_id)
    assert api.delete("/api/connections/omniarmor", headers=kitchen["owner"]).status_code == 404
    assert api.post("/api/connections/omniarmor/test",
                    headers=kitchen["owner"]).status_code == 404


# ---- the price -------------------------------------------------------------------------


def test_the_add_on_price_and_the_bundle_page_string():
    entry = pricing.ADD_ONS[pricing.BUNDLE_ADD_ON]
    assert entry["monthly_usd"] == 29.0 and entry["basis"] == "per location"
    assert entry["included_in_trial"] is True
    assert pricing.bundle_price_label() == "+$29 a month per location"


def test_connected_is_billed_per_location_and_the_trial_is_not(
        api, kitchen, admin_headers, tenant_factory, owner_headers, sensor_factory):
    tenant_id = kitchen["tenant"].tenant_id
    for name in ("High Street", "Market Square"):
        api.post("/api/sites", headers=kitchen["owner"], json={"name": name})
    invoice = api.post("/api/invoices", params={"tenant_id": tenant_id},
                       headers=admin_headers, json={}).json()
    [line] = [l for l in invoice["lines"] if l["kind"] == "add_on"]
    assert line["description"].startswith("Kitchen Safety Bundle (per location)")
    assert line["quantity"] == 2 and line["amount_usd"] == 58.0

    headers, trial = tenant_factory(plan="trial", company_name="Trial Cafe")
    both = {**headers, **owner_headers(headers, email="t@trialcafe.example")}
    sensor_factory(headers, sensor_id="TC-1", vertical="restaurant", location="Fridge")
    assert api.put("/api/connections/omniarmor", headers=both,
                   json={"address": ADDRESS, "key": KEY}).status_code == 200
    assert pricing.auto_add_ons(STORE.get_tenant(trial["tenant_id"])) == []
    seen = api.get("/api/connections/omniarmor", headers=both).json()
    assert seen["price"]["free_now"] is True


def test_the_pricing_page_and_calculator_show_the_bundle(api):
    page = api.get("/pricing").text
    assert 'value="kitchen_safety_bundle" data-rate="29.0"' in page
    assert 'data-basis="per location"' in page
    assert "+$29 a month per location" in page
    assert "free on the trial" in page


def test_it_runs_in_the_hourly_money_pass(monkeypatch):
    import scheduler

    called = []
    monkeypatch.setattr(omniarmor, "run_daily", lambda: called.append(1) or {"sent": 0})
    out = scheduler.run_money_pass()
    assert called and out["kitchen_safety_bundle"] == {"sent": 0}
