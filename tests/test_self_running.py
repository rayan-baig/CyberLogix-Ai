"""Three things that stood between this and running itself.

Asked to make the app self-running, so a bug nobody is watching does not
need somebody to notice it. No program repairs its own logic, and
nothing here pretends to. What it can do is stop failing silently, come
back when its own heart stops, and be recoverable without a shell:

* **A fault is written down.** An unhandled error went to stdout, and in
  a container stdout is gone at the next restart. Something breaks at
  2am, the process logs a traceback, the container restarts, and the
  only evidence is a customer asking why.
* **The sweep loop is supervised.** Each pass was guarded; the task was
  not. Anything that escaped `while True` left no loop and nothing to
  start another -- on a deployment whose entire promise is that
  escalation happens when nobody is looking.
* **A snapshot can be put back.** Backups were written daily, verified,
  pruned and counted, and nothing could restore one. The answer to a
  corrupted database was a person with a shell at 3am.
"""

import asyncio
from pathlib import Path

import pytest

import backup
import faults
import scheduler
from store import STORE

ROOT = Path(__file__).resolve().parent.parent


# --- a fault is written down ---------------------------------------------


def _boom(message="kaboom"):
    try:
        raise ValueError(message)
    except ValueError as exc:
        return exc


def test_an_unhandled_error_is_recorded_not_just_logged(api, admin_headers):
    faults.record("test", _boom())

    body = api.get("/api/admin/faults", headers=admin_headers).json()

    assert body["distinct"] == 1
    assert body["faults"][0]["exception"] == "ValueError"
    assert "kaboom" in body["faults"][0]["message"]
    assert "Traceback" in body["faults"][0]["traceback"]


def test_the_same_bug_a_thousand_times_is_one_row(api, admin_headers):
    """The failure mode of every recent-errors list ever written: the
    noisy bug evicts the rare one. Deduplicated by where it was raised,
    so the bound is on kinds of failure, not occurrences."""
    for i in range(50):
        faults.record("test", _boom(f"sensor FRIDGE-{i} not found"))

    body = api.get("/api/admin/faults", headers=admin_headers).json()

    assert body["distinct"] == 1
    assert body["occurrences"] == 50
    assert body["faults"][0]["seen"] == 50


def test_different_bugs_stay_separate():
    a = faults.record("ingest", _boom())
    try:
        {}["missing"]
    except KeyError as exc:
        b = faults.record("ingest", exc)

    assert a["fault_id"] != b["fault_id"]
    assert len(faults.recent()) == 2


def test_recording_a_fault_never_raises(monkeypatch):
    """If this raised it would replace the real error with its own, and
    the thing being diagnosed would disappear."""
    monkeypatch.setattr(faults, "_record", lambda *a, **k: 1 / 0)

    assert faults.record("test", _boom()) == {}


def test_a_500_is_recorded_and_answers_with_an_id(api, admin_headers):
    """The whole point: the customer quoting a number is quoting
    something that can be looked up."""
    from fastapi.testclient import TestClient

    from main import app

    @app.get("/api/_test_explode", include_in_schema=False)
    def _explode():
        raise RuntimeError("deliberate")

    # The default test client re-raises server exceptions instead of
    # letting the handler answer. Nothing in production does that.
    caller = TestClient(app, raise_server_exceptions=False)
    try:
        resp = caller.get("/api/_test_explode")
        assert resp.status_code == 500
        body = resp.json()
        assert body["fault_id"], body
        # And it says nothing about the internals.
        assert "RuntimeError" not in resp.text
        assert "Traceback" not in resp.text

        listed = api.get("/api/admin/faults", headers=admin_headers).json()
        assert body["fault_id"] in [f["fault_id"] for f in listed["faults"]]
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "path", "") != "/api/_test_explode"
        ]


def test_a_fault_can_be_dismissed_and_comes_back(api, admin_headers):
    row = faults.record("test", _boom())

    assert api.delete(f"/api/admin/faults/{row['fault_id']}",
                      headers=admin_headers).status_code == 200
    assert faults.recent() == []
    assert api.delete(f"/api/admin/faults/{row['fault_id']}",
                      headers=admin_headers).status_code == 404

    faults.record("test", _boom())
    assert len(faults.recent()) == 1


def test_the_fault_list_needs_the_platform_key(api):
    assert api.get("/api/admin/faults").status_code == 401


def test_a_noisy_bug_cannot_evict_every_other_one(monkeypatch):
    """The ring is bounded on distinct faults, and prunes least-recently
    seen -- so the rare fault that matters is not pushed out by the
    chatty one that does not."""
    monkeypatch.setattr(faults, "MAX_DISTINCT", 5)
    for i in range(12):
        # A distinct fingerprint each time: different source.
        faults.record(f"source-{i}", _boom())

    assert len(faults.recent(limit=100)) <= 5


# --- the sweep loop is supervised ----------------------------------------


def test_a_dead_sweep_loop_is_brought_back(monkeypatch):
    """Each pass was already guarded. The task was not."""
    monkeypatch.setattr(scheduler, "RESTART_BACKOFF_SECONDS", (0,))
    monkeypatch.setattr(scheduler, "_restarts", 0)
    started = []

    async def exercise():
        real_loop = scheduler._loop

        async def dies_once(interval):
            started.append(interval)
            if len(started) == 1:
                raise RuntimeError("the loop fell over")
            await asyncio.sleep(3600)

        monkeypatch.setattr(scheduler, "_loop", dies_once)
        monkeypatch.setattr(scheduler, "_interval", lambda: 60)
        scheduler.start()
        # let it die, be noticed, back off (0s) and be recreated
        for _ in range(20):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        for _ in range(20):
            await asyncio.sleep(0)
        await scheduler.stop()
        monkeypatch.setattr(scheduler, "_loop", real_loop)

    asyncio.run(exercise())

    assert len(started) >= 2, "the loop died and nothing restarted it"
    assert scheduler.restarts() >= 1


def test_the_restart_is_recorded_as_a_fault(monkeypatch):
    """So "it keeps restarting" is a thing somebody can read, rather
    than a thing they infer from the estate going quiet."""
    monkeypatch.setattr(scheduler, "RESTART_BACKOFF_SECONDS", (0,))
    monkeypatch.setattr(scheduler, "_restarts", 0)

    async def exercise():
        async def dies(interval):
            raise RuntimeError("the loop fell over")

        monkeypatch.setattr(scheduler, "_loop", dies)
        monkeypatch.setattr(scheduler, "_interval", lambda: 60)
        scheduler.start()
        for _ in range(20):
            await asyncio.sleep(0)
        await scheduler.stop()

    asyncio.run(exercise())

    recorded = [f for f in faults.recent() if f["source"] == "scheduler._loop"]
    assert recorded, "the loop died silently"
    assert "fell over" in recorded[0]["message"]


def test_stopping_on_purpose_does_not_restart(monkeypatch):
    """Counted by how many times the loop is entered, not by the restart
    counter. CancelledError is a BaseException, so a broken handler lets
    it escape the done-callback before the counter is ever touched --
    which made the first version of this test pass either way.
    """
    monkeypatch.setattr(scheduler, "_restarts", 0)
    monkeypatch.setattr(scheduler, "RESTART_BACKOFF_SECONDS", (0,))
    entered = []

    async def exercise():
        async def forever(interval):
            entered.append(interval)
            await asyncio.sleep(3600)

        monkeypatch.setattr(scheduler, "_loop", forever)
        monkeypatch.setattr(scheduler, "_interval", lambda: 60)
        scheduler.start()
        for _ in range(10):
            await asyncio.sleep(0)
        await scheduler.stop()
        # long enough for a zero-second backoff to have revived it
        await asyncio.sleep(0.05)
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(exercise())

    assert entered == [60], (
        f"a deliberate stop started the loop again: entered {len(entered)}x"
    )
    assert scheduler.restarts() == 0


# --- a snapshot can be put back -------------------------------------------


@pytest.fixture()
def on_disk(tmp_path, monkeypatch):
    """A real file-backed database, because restore replaces a file.

    The suite runs on :memory: so it never touches a real path. Pointing
    the backup directory somewhere while leaving the database in memory
    would test a deployment that cannot exist -- and the first version of
    this fixture did exactly that, which is how the missing in-memory
    guard in restore() was found.
    """
    from db import Database

    original = STORE._db
    live = tmp_path / "live.sqlite3"
    STORE._db = Database(str(live))
    STORE.forget()
    STORE.load()
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "snapshots"),
                        raising=False)
    (tmp_path / "snapshots").mkdir()
    monkeypatch.setattr(backup, "_directory",
                        lambda: Path(tmp_path / "snapshots"))
    try:
        yield tmp_path
    finally:
        STORE._db.close()
        STORE._db = original
        STORE.forget()
        STORE.load()


def test_an_in_memory_deployment_has_nothing_to_restore_over(monkeypatch,
                                                             tmp_path):
    """Configure a backup directory on an in-memory database and the
    guard in _directory stops firing -- but there is still no file to
    replace, and copying over the literal path ":memory:" would create a
    file of that name and reopen an empty database."""
    monkeypatch.setattr(backup, "_directory", lambda: Path(tmp_path))

    result = backup.restore("anything.sqlite3")

    assert result["restored"] is False
    assert result["status"] == "nothing_to_replace"


def test_a_snapshot_can_be_verified(on_disk, tenant_factory):
    tenant_factory(company_name="Harbor Cold Store")
    taken = backup.take()
    assert taken["taken"], taken

    checked = backup.verify(taken["path"])

    assert checked["ok"] is True
    assert checked["records"] > 0


def test_a_corrupt_snapshot_is_refused(on_disk):
    bad = on_disk / 'snapshots' / "cyberlogix-broken.sqlite3"
    bad.write_bytes(b"this is not a database")

    checked = backup.verify(str(bad))

    assert checked["ok"] is False


def test_restoring_something_unusable_changes_nothing(on_disk):
    bad = on_disk / 'snapshots' / "cyberlogix-broken.sqlite3"
    bad.write_bytes(b"this is not a database")

    result = backup.restore("cyberlogix-broken.sqlite3")

    assert result["restored"] is False
    assert result["status"] == "unusable"


def test_a_restore_snapshots_what_it_replaces(on_disk, tenant_factory):
    """The most likely mistake is restoring the wrong one; the second is
    being right about the corruption and wrong about which copy was
    good. Both are survivable only if the thing replaced was kept."""
    tenant_factory(company_name="Harbor Cold Store")
    taken = backup.take()
    name = Path(taken["path"]).name

    result = backup.restore(name)

    assert result["restored"] is True
    saved = result["previous_database_saved_to"]
    assert saved and Path(saved).exists()
    assert "pre-restore" in saved
    assert backup.verify(saved)["ok"] is True


def test_a_restore_leaves_the_service_serving(on_disk, tenant_factory,
                                              api, admin_headers):
    """The first version closed the database and stopped there, so every
    request afterwards answered 500 until somebody restarted the
    container. A recovery step that needs a human at the end is not
    one."""
    _, tenant = tenant_factory(company_name="Harbor Cold Store")
    taken = backup.take()

    result = backup.restore(Path(taken["path"]).name)

    assert result["status"] == "ok", result
    # The database is open and the working set is rebuilt from it.
    assert STORE.get_tenant(tenant["tenant_id"]) is not None
    assert api.get("/api/admin/backups",
                   headers=admin_headers).status_code == 200


def test_a_restore_brings_back_what_the_snapshot_held(
    on_disk, tenant_factory
):
    """The actual job: data that was there at snapshot time comes back,
    and a write made afterwards does not."""
    _, before = tenant_factory(company_name="Was Here")
    taken = backup.take()
    _, after = tenant_factory(company_name="Came Later")
    assert STORE.get_tenant(after["tenant_id"]) is not None

    backup.restore(Path(taken["path"]).name)

    assert STORE.get_tenant(before["tenant_id"]) is not None
    assert STORE.get_tenant(after["tenant_id"]) is None, (
        "a write made after the snapshot survived the restore"
    )


def test_a_restore_cannot_reach_outside_the_snapshot_directory(on_disk):
    """The name is joined to the backup directory, so a path from a
    caller cannot walk out of it."""
    result = backup.restore("../../../etc/passwd")

    assert result["restored"] is False
    assert result["status"] == "unusable"


def test_restore_needs_the_platform_key_and_an_explicit_confirm(
    api, admin_headers
):
    assert api.post("/api/admin/backups/restore",
                    json={"snapshot": "x", "confirm": True}).status_code == 401
    refused = api.post("/api/admin/backups/restore", headers=admin_headers,
                       json={"snapshot": "x", "confirm": False})
    assert refused.status_code == 400
    assert "discards" in refused.json()["detail"]


# --- it has to reach somebody ---------------------------------------------


def test_the_daily_digest_reports_what_broke():
    """A record nobody reads is the same as no record, and the operator
    console is a page somebody has to remember to open. The digest
    arrives without being asked."""
    from digest import _render_operator, operator_digest

    for _ in range(3):
        faults.record("ingest", _boom("sensor gone"))

    d = operator_digest()
    text = _render_operator(d)

    assert d["faults"]["distinct"] == 1
    assert d["faults"]["occurrences"] == 3
    assert "fault(s)" in text
    assert "ingest" in text


def test_a_clean_day_says_nothing_about_faults():
    """A line that is always there is a line nobody reads."""
    from digest import _render_operator, operator_digest

    assert "fault(s)" not in _render_operator(operator_digest())


def test_the_digest_survives_a_broken_fault_store(monkeypatch):
    """The summary must not be able to stop the email that carries it."""
    import faults as faults_module

    monkeypatch.setattr(faults_module, "status",
                        lambda: 1 / 0)
    from digest import operator_digest

    assert operator_digest()["faults"]["distinct"] == 0


def test_no_file_called_memory_is_ever_created(tmp_path, monkeypatch):
    """This actually happened.

    The first restore copied a snapshot over `Path(STORE._db.path)`, and
    on the suite's in-memory store that path is the literal string
    ":memory:" -- so it created a 16KB file of that name in the
    repository root and reopened an empty database. The guard is what
    stops it; this is what stops the guard being removed.
    """
    monkeypatch.setattr(backup, "_directory", lambda: Path(tmp_path))
    stray = Path(ROOT) / ":memory:"

    backup.restore("anything.sqlite3")

    assert not stray.exists(), "a file called ':memory:' was created"
