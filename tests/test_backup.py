"""The one file whose loss is the company.

Every tenant, every reading, every invoice and the whole hash-chained
vault live in one SQLite file. The vault is the part that hurts: a
customer's proof that their freezer held temperature for two years
cannot be reconstructed from anywhere, and an insurer will not accept
"we lost it".

The failures worth guarding are the quiet ones. A backup that was never
taken, a backup that was taken and is corrupt, and a backup directory
that fills the disk the live database is sitting on — which turns a
backup policy into an outage.
"""

from pathlib import Path

import pytest

import backup
from db import Database
from store import HubStore, utc_now


@pytest.fixture()
def durable(tmp_path, monkeypatch):
    """A real database on a real disk, with somewhere to snapshot it to.

    The suite's own store is in memory, and the point of this module is
    what happens to a file.
    """
    store = HubStore(Database(str(tmp_path / "live.sqlite3")))
    monkeypatch.setattr(backup, "STORE", store)
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "snapshots"))
    return store, tmp_path / "snapshots"


def _populate(store, count=3):
    for index in range(count):
        store.create_tenant(
            company_name=f"Company {index}",
            contact_name="Dana",
            contact_phone="+15550100",
            contact_email=f"d{index}@example.com",
            plan="growth",
        )
    return store


# ---- taking one ---------------------------------------------------------


def test_a_snapshot_contains_the_data_and_says_so(durable):
    store, directory = durable
    _populate(store)

    result = backup.take()

    assert result["taken"] is True
    assert result["kinds"]["tenant"] == 3
    assert Path(result["path"]).exists()


def test_a_snapshot_is_a_working_database_not_a_file_shaped_object(durable):
    """The whole point. A copy that cannot be opened is not a backup."""
    store, _ = durable
    _populate(store, count=2)

    path = backup.take()["path"]
    restored = HubStore(Database(path))

    assert len(restored.list_tenants()) == 2
    assert {t.company_name for t in restored.list_tenants()} == {
        "Company 0", "Company 1"
    }


def test_a_snapshot_taken_mid_write_is_still_consistent(durable):
    """SQLite's own backup, not `cp`.

    A file copy of a live database can catch a torn page or a WAL that
    has not been checkpointed, and produce something that looks like a
    backup right up until the afternoon it is needed.
    """
    store, _ = durable
    _populate(store, count=5)

    path = backup.take()["path"]
    # Writes after the snapshot must not appear in it, and must not
    # corrupt it either.
    _populate(store, count=2)

    restored = HubStore(Database(path))
    assert len(restored.list_tenants()) == 5


def test_a_corrupt_snapshot_is_refused_rather_than_counted(durable, monkeypatch):
    """An unread backup is a guess. This one is read before it counts."""
    store, _ = durable
    _populate(store)

    def _lie(destination):
        raise RuntimeError("the snapshot failed its own integrity check (bad)")

    monkeypatch.setattr(store._db, "snapshot", _lie)

    result = backup.take()

    assert result["taken"] is False
    assert result["status"] == "failed"


def test_a_backup_failure_cannot_stop_the_unattended_pass(durable, monkeypatch):
    """It is reported loudly instead. A silent failure is the worst kind:
    somebody is relying on it."""
    store, _ = durable

    def _explode(destination):
        raise OSError("read-only file system")

    monkeypatch.setattr(store._db, "snapshot", _explode)

    result = backup.take()  # does not raise
    assert result["taken"] is False
    assert "read-only file system" in result["detail"]


def test_an_in_memory_database_says_there_is_nothing_to_snapshot(monkeypatch):
    """Rather than writing the test suite's store into the repository."""
    monkeypatch.setattr(backup, "BACKUP_DIR", "")

    result = backup.take()

    assert result["taken"] is False
    assert result["status"] == "nowhere_to_write"
    assert "CYBERLOGIX_DB_PATH" in result["detail"]


# ---- retention ----------------------------------------------------------


def test_old_snapshots_are_pruned_oldest_first(durable, monkeypatch):
    """A backup directory that fills the live disk is an outage."""
    store, directory = durable
    _populate(store)
    monkeypatch.setattr(backup, "BACKUP_KEEP", 3)

    names = []
    for hour in range(6):
        moment = utc_now().replace(hour=hour, minute=0, second=0, microsecond=0)
        names.append(Path(backup.take(now=moment)["path"]).name)

    kept = [p.name for p in backup.existing()]
    assert len(kept) == 3
    assert kept == sorted(names)[-3:]


# ---- the daily one ------------------------------------------------------


def test_only_one_a_day(durable):
    """The money pass runs hourly. One snapshot lands."""
    store, _ = durable
    _populate(store)

    first = backup.run_daily_backup()
    rest = [backup.run_daily_backup() for _ in range(23)]

    assert first["taken"] is True
    assert all(r["status"] == "already_today" for r in rest)
    assert len(backup.existing()) == 1


def test_a_snapshot_deleted_by_hand_is_taken_again(durable):
    """Checked by what is on disk, not by a flag saying it was done.

    A flag would mean a deleted snapshot is assumed to be there, which is
    exactly the failure this module exists to prevent.
    """
    store, _ = durable
    _populate(store)

    backup.run_daily_backup()
    backup.existing()[0].unlink()

    assert backup.run_daily_backup()["taken"] is True


# ---- what the operator is told -----------------------------------------


def test_the_status_admits_when_the_backup_shares_the_live_disk(
    tmp_path, monkeypatch
):
    """Beside the database survives a bad migration and not a lost disk."""
    store = HubStore(Database(str(tmp_path / "live.sqlite3")))
    monkeypatch.setattr(backup, "STORE", store)
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "backups"))

    note = backup.status()
    assert note["on_the_same_disk_as_the_database"] is True
    assert "set CYBERLOGIX_BACKUP_DIR" in note["note"]


def test_the_status_still_says_to_copy_them_off_the_machine(
    tmp_path, monkeypatch
):
    (tmp_path / "db").mkdir()
    store = HubStore(Database(str(tmp_path / "db" / "live.sqlite3")))
    monkeypatch.setattr(backup, "STORE", store)
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "elsewhere" / "snaps"))

    assert "not one" in backup.status()["note"]


def test_backups_are_not_readable_or_triggerable_without_the_platform_key(api):
    """The snapshot is every customer's data in one file."""
    assert api.get("/api/admin/backups").status_code == 401
    assert api.post("/api/admin/backups").status_code == 401


def test_the_operator_can_list_them(api, admin_headers, durable):
    store, _ = durable
    _populate(store)
    backup.take()

    body = api.get("/api/admin/backups", headers=admin_headers).json()

    assert body["count"] == 1
    assert body["snapshots"][0]["size_bytes"] > 0
