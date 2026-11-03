"""Backup, restore, and the app still works -- and sends nothing twice.

The rehearsal in docs/OPERATIONS.md, automated: take a snapshot with the
app's own backup, restore it with the app's own restore, and check that
the sensors, readings, incidents, vault and audit trail are all there and
that a new breach still alerts somebody.

And the trap found doing it by hand: a restored snapshot is the record of
the world as it was, but the texts, calls and emails sent since then have
still been sent. Rolling back that record made the sweep and the hourly
pass do them again -- an acknowledged incident rang the on-call phone, the
day's digest went out twice, and every sensor whose last reading in the
snapshot was hours old was texted about as having gone quiet.
"""

from datetime import timedelta
from pathlib import Path

import pytest

import automation
import backup
import digest
import notifications
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, utc_now

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


@pytest.fixture()
def on_disk(tmp_path, monkeypatch):
    from db import Database

    original = STORE._db
    STORE._db = Database(str(tmp_path / "live.sqlite3"))
    STORE.forget()
    STORE.load()
    (tmp_path / "snapshots").mkdir()
    monkeypatch.setattr(backup, "_directory", lambda: Path(tmp_path / "snapshots"))
    try:
        yield tmp_path
    finally:
        STORE._db.close()
        STORE._db = original
        STORE.forget()
        STORE.load()


@pytest.fixture()
def twilio(monkeypatch):
    sent = {"sms": [], "calls": []}

    class _Messages:
        def create(self, **kwargs):
            sent["sms"].append((kwargs["to"], kwargs["body"]))
            return type("M", (), {"sid": f"SM-{len(sent['sms'])}", "status": "queued"})()

    class _Calls:
        def create(self, **kwargs):
            sent["calls"].append(kwargs["to"])
            return type("C", (), {"sid": f"CA-{len(sent['calls'])}", "status": "queued"})()

    client = type("Client", (), {"messages": _Messages(), "calls": _Calls()})()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", client)
    monkeypatch.setattr(notifications, "_client_error", None)
    return sent


def _estate(api, operator_factory, sensor_factory, sensors=12, readings=40):
    headers, tenant, user = operator_factory(plan="enterprise")
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Night Engineer", "phone": "+15550901", "escalation_order": 1})
    for n in range(sensors):
        sensor_factory(headers, sensor_id=f"UNIT-{n:02d}")
    for r in range(readings):
        for n in range(sensors):
            resp = api.post("/api/sensor-pulse", headers=headers, json={
                "sensor_id": f"UNIT-{n:02d}", "temperature_fahrenheit": 70.0 + (r % 5)})
            assert resp.status_code == 200, resp.text
    return headers, tenant


def _restore(api, name):
    resp = api.post("/api/admin/backups/restore", headers=ADMIN,
                    json={"snapshot": name, "confirm": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_backup_restore_and_the_app_still_works(
        api, on_disk, operator_factory, sensor_factory, twilio, mailbox):
    headers, tenant = _estate(api, operator_factory, sensor_factory)
    breach = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "UNIT-00", "temperature_fahrenheit": 99.0}).json()
    attested = api.get("/api/vault/attestation/UNIT-01", headers=headers)
    assert attested.status_code == 200, attested.text
    audit_before = api.get("/api/accounts/audit?limit=100", headers=headers).json()
    counts = {k: STORE._db.count(k) for k in ("sensor", "reading", "incident", "audit", "anchor")}

    taken = api.post("/api/admin/backups", headers=ADMIN)
    assert taken.status_code == 201, taken.text
    name = Path(taken.json()["path"]).name
    assert api.get(f"/api/admin/backups/{name}/verify", headers=ADMIN).json()["ok"]

    # Something goes wrong after the snapshot: a sensor is deleted.
    assert api.delete("/api/licenses/me/sensors/UNIT-11", headers=headers).status_code == 200

    result = _restore(api, name)
    assert result["restored"] is True

    for kind, count in counts.items():
        assert STORE._db.count(kind) >= count, kind
    assert STORE.get_sensor("UNIT-11") is not None
    assert len(STORE.readings_for("UNIT-05")) == 40
    assert STORE.get_incident(breach["incident_id"]) is not None
    verified = api.get("/api/vault/verify/UNIT-01", headers=headers).json()
    assert verified["verifiable"] and verified["intact"], verified
    audit_after = api.get("/api/accounts/audit?limit=100", headers=headers).json()
    ids = lambda body: {e["entry_id"] for e in body["entries"]}  # noqa: E731
    assert ids(audit_before) <= ids(audit_after)

    # The same session still works, and a new breach still alerts.
    texts = len(twilio["sms"])
    fresh = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "UNIT-02", "temperature_fahrenheit": 99.0})
    assert fresh.json()["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(twilio["sms"]) == texts + 1
    assert fresh.json()["incident_id"] != breach["incident_id"]


def test_a_restore_does_not_call_or_mail_again_about_what_was_already_sent(
        api, on_disk, operator_factory, sensor_factory, twilio, mailbox, monkeypatch):
    headers, tenant = _estate(api, operator_factory, sensor_factory, sensors=3, readings=3)
    tenant_row = STORE.get_tenant(tenant["tenant_id"])
    incident_id = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "UNIT-00", "temperature_fahrenheit": 99.0}).json()["incident_id"]
    # The snapshot is taken with the incident just past its grace period,
    # unanswered and not yet escalated.
    incident = STORE.get_incident(incident_id)
    incident.opened_at = utc_now() - timedelta(minutes=VOICE_ESCALATION_GRACE_MINUTES + 1)
    STORE._db.put("incident", incident_id, incident.to_row())
    name = Path(backup.take(label="manual")["path"]).name

    # After the snapshot: the call goes out and the day's digest goes out.
    automation.sweep_tenant(tenant_row)
    assert len(twilio["calls"]) == 1
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "ops@cyberlogix.example")
    evening = utc_now().replace(hour=23)
    assert digest.run_digests(evening)["operator"]["sent"] is True
    digests = len(mailbox)

    _restore(api, name)

    # The sweep and the hourly pass run again on the restored database.
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    digest.run_digests(evening)
    assert len(twilio["calls"]) == 1, "the restored incident was escalated again"
    assert len(mailbox) == digests, "the day's digest was sent again"
    assert STORE.get_incident(incident_id).voice_escalated_at is not None


def test_a_restore_is_not_read_as_every_sensor_going_quiet(
        api, on_disk, operator_factory, sensor_factory, twilio):
    headers, tenant = _estate(api, operator_factory, sensor_factory, sensors=4, readings=1)
    name = Path(backup.take(label="manual")["path"]).name
    # The snapshot is two hours old by the time it is restored: in it, every
    # sensor was last heard from two hours ago. Since then they have all
    # kept reporting.
    for sensor in STORE.sensors_for(tenant["tenant_id"]):
        row = STORE._db.get("sensor", sensor.sensor_id)
        assert row is not None
    import sqlite3

    snap = sqlite3.connect(str(on_disk / "snapshots" / name))
    old = (utc_now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap.execute("UPDATE records SET data = json_set(data, '$.last_seen', ?) "
                 "WHERE kind = 'sensor'", (old,))
    snap.commit()
    snap.close()
    for n in range(4):
        api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": f"UNIT-{n:02d}", "temperature_fahrenheit": 70.0})
    texts = len(twilio["sms"])

    _restore(api, name)
    result = automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))

    assert result["sensors_offline"] == 0
    assert len(twilio["sms"]) == texts


def test_what_a_restore_carries_forward_is_only_what_was_sent(
        api, on_disk, operator_factory, sensor_factory, twilio):
    """A business that signed up after the snapshot is not resurrected
    through its incidents or its mail."""
    headers, tenant = _estate(api, operator_factory, sensor_factory, sensors=1, readings=1)
    name = Path(backup.take(label="manual")["path"]).name
    late_headers, late, _ = operator_factory(email="late@example.com",
                                             company_name="Late Signup")
    sensor_factory(late_headers, sensor_id="LATE-01")
    api.post("/api/sensor-pulse", headers=late_headers, json={
        "sensor_id": "LATE-01", "temperature_fahrenheit": 99.0})

    result = _restore(api, name)

    assert STORE.get_tenant(late["tenant_id"]) is None
    assert not [i for i in STORE.open_incidents() if i.tenant_id == late["tenant_id"]]
    assert result["kept_from_the_replaced_database"]["incidents"] == 0


def test_a_snapshot_can_be_downloaded_to_keep_off_the_machine(
        api, on_disk, operator_factory, sensor_factory, tmp_path):
    _estate(api, operator_factory, sensor_factory, sensors=2, readings=2)
    name = Path(api.post("/api/admin/backups", headers=ADMIN).json()["path"]).name

    assert api.get(f"/api/admin/backups/{name}/download").status_code in (401, 403)
    got = api.get(f"/api/admin/backups/{name}/download", headers=ADMIN)
    assert got.status_code == 200
    copy = tmp_path / "offsite.sqlite3"
    copy.write_bytes(got.content)
    assert backup.verify(str(copy))["ok"]
    # Only snapshots: not the live database beside the folder.
    assert api.get("/api/admin/backups/..%2Flive.sqlite3/download",
                   headers=ADMIN).status_code == 404
    assert api.get("/api/admin/backups/nothing.sqlite3/download",
                   headers=ADMIN).status_code == 404


def test_a_snapshot_that_would_fill_the_disk_is_refused_and_reported(
        api, on_disk, monkeypatch):
    """Fourteen daily snapshots on render.yaml's 1 GB disk fill it at a few
    hundred sensors (docs/LOAD-TEST.md), and a full disk refuses readings."""
    import faults

    monkeypatch.setattr(backup, "BACKUP_MIN_FREE_BYTES", 10**18)
    result = backup.take(label="manual")
    assert result["taken"] is False and result["status"] == "no_space"
    assert "readings" in result["detail"]
    assert backup.existing() == []
    assert any(f["source"] == "backup.take" for f in faults.recent())
    assert backup.status()["space_for_another"] is False
    assert api.post("/api/admin/backups", headers=ADMIN).status_code == 503

    monkeypatch.setattr(backup, "BACKUP_MIN_FREE_BYTES", 0)
    assert backup.take(label="manual")["taken"] is True
    assert backup.status()["space_for_another"] is True
