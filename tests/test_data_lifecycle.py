"""Data that has to outlive the things around it, and data that must not.

* One business deleting itself cannot touch another business's proof.
* The rolling window of readings must never eat the readings an incident
  saved, including the ones that arrive while it is still open.
* The download is what the database holds for that business, no more,
  and reads back.
* A backup restored after a deletion cannot bring the business back.
* Backups prune to their limit and every one kept still opens -- even
  when the one being restored is the oldest.
* Sent mail does not keep its full text forever.
* The audit record has no route that writes to it.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest

import backup
import store as store_module
from db import Database
from store import STORE, HubStore, utc_now

PASSWORD = "correct-horse-battery"


def _erase(api, headers, company):
    return api.post("/api/accounts/business/erase", headers=headers,
                    json={"company_name": company, "password": PASSWORD})


def _pulse(api, headers, sensor_id, temp):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- the vault, across businesses ----------------------------------------


def test_erasing_one_business_leaves_anothers_vault_verifying(
    api, operator_factory, sensor_factory
):
    keep_h, keep, _ = operator_factory(company_name="Keeps Ltd",
                                       email="keep@example.com")
    gone_h, gone, _ = operator_factory(company_name="Leaves Ltd",
                                       email="gone@example.com")
    sensor_factory(keep_h, sensor_id="KEEP-1", vertical="restaurant")
    sensor_factory(gone_h, sensor_id="GONE-1", vertical="restaurant")
    for temp in (20.0, 21.0, 40.0, 22.0):
        _pulse(api, keep_h, "KEEP-1", temp)
        _pulse(api, gone_h, "GONE-1", temp)

    before = api.get("/api/vault/attestation/KEEP-1", headers=keep_h)
    assert before.status_code == 200, before.text
    estate_before = api.get("/api/vault/attestation", headers=keep_h).json()
    assert api.get("/api/vault/attestation/GONE-1",
                   headers=gone_h).status_code == 200

    assert _erase(api, gone_h, "Leaves Ltd").status_code == 200
    assert STORE.get_tenant(gone["tenant_id"]) is None

    verify = api.get("/api/vault/verify/KEEP-1", headers=keep_h)
    assert verify.status_code == 200, verify.text
    assert verify.json()["intact"] is True, verify.json()
    assert verify.json()["attestations_broken"] == 0
    again = api.get("/api/vault/attestation/KEEP-1", headers=keep_h).json()
    assert again["chain_head"] == before.json()["chain_head"]
    assert again["readings"] == before.json()["readings"] == 4
    estate_after = api.get("/api/vault/attestation", headers=keep_h).json()
    assert estate_after["estate_digest"] == estate_before["estate_digest"]
    # And the anchors on record for the survivor are all still there.
    assert all(a.tenant_id == keep["tenant_id"]
               for a in STORE.anchors_for_sensor("KEEP-1"))
    assert STORE.anchors_for_sensor("KEEP-1")


# ---- the rolling window and the evidence ---------------------------------


@pytest.fixture()
def tiny_buffer(monkeypatch):
    """Five readings per sensor, so the window rolls within one test."""
    monkeypatch.setattr(store_module, "MAX_READINGS_PER_SENSOR", 5)
    return 5


def test_readings_saved_at_the_opening_survive_the_buffer_rolling(
    api, tenant_factory, sensor_factory, tiny_buffer
):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (20.0, 21.0, 22.0):
        _pulse(api, headers, "FRZ-1", temp)
    opened = _pulse(api, headers, "FRZ-1", 45.0)
    incident = STORE.get_incident(opened["incident_id"])
    saved_ids = {r["reading_id"] for r in incident.evidence_readings}
    assert len(saved_ids) == 4

    for _ in range(12):
        _pulse(api, headers, "FRZ-1", 20.0)

    assert len(STORE.readings_for("FRZ-1")) == tiny_buffer
    kept = STORE.get_incident(opened["incident_id"]).evidence_readings
    assert saved_ids <= {r["reading_id"] for r in kept}

    # And from disk, after a restart.
    STORE.forget()
    STORE.load()
    reloaded = STORE.get_incident(opened["incident_id"]).evidence_readings
    assert saved_ids <= {r["reading_id"] for r in reloaded}


def test_readings_during_a_long_breach_are_not_lost_to_the_buffer(
    api, tenant_factory, sensor_factory, tiny_buffer
):
    """The middle of the excursion is the peak, and the part an adjuster
    asks about first. Saved only when the incident opened and when it
    closed, a breach longer than the buffer lost everything in between."""
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    _pulse(api, headers, "FRZ-1", 20.0)
    opened = _pulse(api, headers, "FRZ-1", 40.0)
    peak = 70.0
    for temp in (45.0, 52.0, 60.0, peak, 61.0, 50.0, 44.0, 41.0, 39.0, 35.0):
        _pulse(api, headers, "FRZ-1", temp)

    incident = STORE.get_incident(opened["incident_id"])
    STORE.resolve_incident(incident, "Dana")

    temps = [r["temperature_fahrenheit"]
             for r in STORE.get_incident(opened["incident_id"]).evidence_readings]
    assert peak in temps, f"the peak of the excursion was lost: {temps}"
    assert len(temps) == 12, temps


def test_the_claim_packet_counts_every_saved_excursion(
    api, tenant_factory, sensor_factory, tiny_buffer
):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    opened = _pulse(api, headers, "FRZ-1", 40.0)
    for temp in (45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0):
        _pulse(api, headers, "FRZ-1", temp)
    for _ in range(6):
        _pulse(api, headers, "FRZ-1", 20.0)

    packet = api.post(f"/api/claims/{opened['incident_id']}/packet",
                      headers=headers)
    assert packet.status_code == 200, packet.text
    body = packet.json()
    excursions = [r for r in body["evidence"]["readings"] if r["breached"]]
    assert len(excursions) == 8, body["evidence"]


# ---- the download ---------------------------------------------------------


def test_the_download_is_exactly_this_business_and_reads_back(
    api, operator_factory, sensor_factory
):
    mine_h, mine, _ = operator_factory(company_name="Mine Ltd",
                                       email="mine@example.com")
    other_h, other, _ = operator_factory(company_name="Other Ltd",
                                         email="other@example.com")
    sensor_factory(mine_h, sensor_id="MINE-1", vertical="pharmacy")
    sensor_factory(other_h, sensor_id="OTHER-1", vertical="pharmacy")
    for temp in (40.0, 41.0, 50.0):
        _pulse(api, mine_h, "MINE-1", temp)
        _pulse(api, other_h, "OTHER-1", temp)
    api.post("/api/contacts", headers=mine_h, json={
        "full_name": "Night Manager", "phone": "+15550101", "role": "manager"})

    resp = api.get("/api/accounts/business/export", headers=mine_h)
    assert resp.status_code == 200
    body = json.loads(resp.content.decode("utf-8"))

    assert body["tenant_id"] == mine["tenant_id"]
    text = resp.content.decode("utf-8")
    assert other["tenant_id"] not in text
    assert "OTHER-1" not in text and "Other Ltd" not in text

    with STORE._lock:
        expected = STORE._rows_of_tenant_locked(mine["tenant_id"])
    by_kind = {}
    for kind, rec_id, data in expected:
        by_kind.setdefault(kind, {})[rec_id] = data
    records = body["records"]
    assert set(records) == set(by_kind)
    for kind, rows in records.items():
        assert len(rows) == len(by_kind[kind]), kind
        stored = list(by_kind[kind].values())
        for row in rows:
            # Every exported field is the stored value (or a mask of a
            # secret), and nothing stored is missing except the secrets.
            match = [d for d in stored
                     if all(d.get(k) == v for k, v in row.items()
                            if k not in ("headers", "target", "body"))]
            assert match, f"{kind} row {row} is not what the database holds"
            missing = set(match[0]) - set(row) - STORE._SECRET_FIELDS
            assert not missing, f"{kind} export dropped {missing}"
    assert len(records["reading"]) == 3
    assert {r["sensor_id"] for r in records["reading"]} == {"MINE-1"}


# ---- backups, restore and deletion ---------------------------------------


@pytest.fixture()
def durable(tmp_path, monkeypatch):
    disk = HubStore(Database(str(tmp_path / "live.sqlite3")))
    monkeypatch.setattr(backup, "STORE", disk)
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "snapshots"))
    return disk


def _company(disk, name):
    return disk.create_tenant(company_name=name, contact_name="Dana",
                              contact_phone="+15550100",
                              contact_email=f"{name.split()[0].lower()}@example.com",
                              plan="growth")


def test_restoring_an_older_backup_does_not_bring_back_a_deleted_business(
    durable
):
    """The privacy statement promises it: "We do not restore a backup to
    bring back something you deleted." Restoring the morning's snapshot
    after a business deleted itself at lunch did exactly that, silently."""
    disk = durable
    stays = _company(disk, "Stays Co")
    leaves = _company(disk, "Leaves Co")
    taken = backup.take(now=utc_now() - timedelta(hours=3))
    assert taken["taken"]

    disk.erase_tenant(leaves.tenant_id)
    assert disk.get_tenant(leaves.tenant_id) is None

    result = backup.restore(Path(taken["path"]).name)

    assert result["restored"] is True, result
    assert disk.get_tenant(stays.tenant_id) is not None
    assert disk.get_tenant(leaves.tenant_id) is None, (
        "the restore brought a deleted business back")
    assert leaves.tenant_id in result["deleted_again"]
    assert "deleted" in result["detail"].lower()
    # And on disk, not only in memory.
    reopened = HubStore(Database(disk._db.path))
    assert reopened.get_tenant(leaves.tenant_id) is None
    assert reopened.get_tenant(stays.tenant_id) is not None

    # The record of the deletion survives the restore too, so a second
    # restore from the same snapshot is just as safe.
    again = backup.restore(Path(taken["path"]).name)
    assert again["restored"] is True
    assert disk.get_tenant(leaves.tenant_id) is None


def test_a_restore_never_hands_out_an_id_already_used(durable):
    """Ids issued after the snapshot went into emails and pay links. A
    restore that winds the counter back gives the next invoice the id of
    one a customer is already holding."""
    disk = durable
    _company(disk, "First Co")
    taken = backup.take(now=utc_now() - timedelta(hours=1))
    later = _company(disk, "Second Co")

    backup.restore(Path(taken["path"]).name)
    fresh = _company(disk, "Third Co")

    assert fresh.tenant_id != later.tenant_id


def test_backups_prune_to_the_limit_and_every_one_kept_verifies(
    durable, monkeypatch
):
    disk = durable
    _company(disk, "Only Co")
    monkeypatch.setattr(backup, "BACKUP_KEEP", 4)
    base = utc_now().replace(minute=0, second=0, microsecond=0)
    for hours_ago in range(9, 0, -1):
        assert backup.take(now=base - timedelta(hours=hours_ago))["taken"]

    kept = backup.existing()
    assert len(kept) == 4
    for path in kept:
        checked = backup.verify(str(path))
        assert checked["ok"], checked
        assert checked["records"] > 0


def test_the_oldest_backup_can_still_be_restored_when_the_folder_is_full(
    durable, monkeypatch
):
    """The safety copy taken before a restore pruned the folder, and the
    snapshot it pruned was the oldest -- the one being restored. Two
    weeks is how long it takes to notice, so the oldest is exactly the
    one somebody reaches for."""
    disk = durable
    first = _company(disk, "Early Co")
    monkeypatch.setattr(backup, "BACKUP_KEEP", 3)
    base = utc_now().replace(minute=0, second=0, microsecond=0)
    oldest = backup.take(now=base - timedelta(hours=5))
    _company(disk, "Later Co")
    backup.take(now=base - timedelta(hours=4))
    backup.take(now=base - timedelta(hours=3))
    assert len(backup.existing()) == 3

    result = backup.restore(Path(oldest["path"]).name, now=base)

    assert result["restored"] is True, result
    assert [t.company_name for t in disk.list_tenants()] == [first.company_name]
    for path in backup.existing():
        assert backup.verify(str(path))["ok"]


# ---- mail retention -------------------------------------------------------


def test_sent_mail_does_not_keep_its_text_forever(mailbox):
    import mail
    from store import MAIL_BODY_KEEP_DAYS

    old = mail.send(to_address="ap@example.com", subject="Invoice INV-1",
                    body="You owe $100.", dedupe_key="test:old",
                    klass="transactional")
    recent = mail.send(to_address="ap@example.com", subject="Invoice INV-2",
                       body="You owe $200.", dedupe_key="test:recent",
                       klass="transactional")
    aged = STORE._mail[old["message_id"]]
    aged.created_at = utc_now() - timedelta(days=MAIL_BODY_KEEP_DAYS + 1)
    aged.sent_at = aged.created_at
    STORE._db.put("mail", aged.message_id, aged.to_row())

    mail.flush_queue()

    row = STORE._db.get("mail", old["message_id"])
    assert "You owe $100." not in row["body"]
    # Who, what and whether it went stay: "we never got that bill" still
    # has an answer.
    assert row["to_address"] == "ap@example.com"
    assert row["subject"] == "Invoice INV-1"
    assert row["status"] == "sent"
    assert STORE._db.get("mail", recent["message_id"])["body"] == "You owe $200."

    # The trimmed row still stops the same message going twice.
    again = mail.send(to_address="ap@example.com", subject="Invoice INV-1",
                      body="You owe $100.", dedupe_key="test:old",
                      klass="transactional")
    assert again["sent"] is False
    assert len(mailbox) == 2


def test_the_privacy_statement_states_how_long_mail_text_is_kept():
    import legal
    from store import MAIL_BODY_KEEP_DAYS

    text = legal.privacy_statement()
    assert f"{MAIL_BODY_KEEP_DAYS} days" in text


# ---- the audit record -----------------------------------------------------


def test_no_route_can_write_to_or_remove_an_audit_entry(
    api, operator_factory
):
    from main import app

    writers = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if "audit" in getattr(route, "path", "")
        and set(getattr(route, "methods", ()) or ()) - {"GET", "HEAD"}
    ]
    assert writers == [], f"routes that write to the audit record: {writers}"

    headers, tenant, _ = operator_factory()
    entries = api.get("/api/accounts/audit", headers=headers).json()
    entry_id = entries["entries"][0]["entry_id"]
    for method in ("put", "patch", "delete", "post"):
        for path in ("/api/accounts/audit", f"/api/accounts/audit/{entry_id}"):
            resp = getattr(api, method)(path, headers=headers)
            assert resp.status_code in (404, 405), (method, path, resp.status_code)
    after = api.get("/api/accounts/audit", headers=headers).json()["entries"]
    assert any(e["entry_id"] == entry_id for e in after)


def test_an_audit_entry_is_never_overwritten_by_a_new_one(api, operator_factory):
    headers, tenant, _ = operator_factory()
    first = list(STORE._audit.values())
    rows_before = {e.entry_id: e.to_row() for e in first}

    for _ in range(5):
        STORE.record_audit(tenant["tenant_id"], "x", "owner", "test.action", "d")
    STORE.forget()
    STORE.load()
    for _ in range(5):
        STORE.record_audit(tenant["tenant_id"], "y", "owner", "test.action", "d")

    for entry_id, row in rows_before.items():
        assert STORE._db.get("audit", entry_id) == row
    assert len({e.entry_id for e in STORE._audit.values()}) == len(first) + 10


def test_a_restore_never_reissues_an_invoice_number(durable):
    """Invoice numbers were worked out from the invoices on disk, so after
    restoring an older backup the next invoice took a number a customer was
    already holding. An audit fails on a duplicate invoice number."""
    disk = durable
    co = _company(disk, "Billed Co")
    line = [{"kind": "subscription", "description": "Plan", "quantity": 1,
             "unit_price_usd": 100.0, "amount_usd": 100.0}]
    disk.create_invoice(tenant=co, lines=line)
    taken = backup.take(now=utc_now() - timedelta(hours=1))
    sent = disk.create_invoice(tenant=co, lines=line)

    backup.restore(Path(taken["path"]).name)
    co = disk.get_tenant(co.tenant_id)
    fresh = disk.create_invoice(tenant=co, lines=line)

    assert fresh.number != sent.number
    assert fresh.number > sent.number
