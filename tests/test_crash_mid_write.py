"""The database survives the process being killed in the middle of a write.

Render stops a container with SIGKILL when it will not stop politely, and
a machine can lose power at any moment. Whatever was committed before
that must be there afterwards, whatever was half-written must not be,
and the file must open cleanly: no "database disk image is malformed" on
the morning after a freezer failed.

Each test runs a real writer in a child process, kills it with SIGKILL
(no clean-up, no atexit, no rollback), and opens the file again here.
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"), reason="needs SIGKILL"
)


def _spawn(script: str, path: Path) -> subprocess.Popen:
    env = {**os.environ, "CYBERLOGIX_DB_PATH": str(path), "PYTHONPATH": str(ROOT)}
    return subprocess.Popen(
        [sys.executable, "-c", script], cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _wait_for_line(proc: subprocess.Popen, wanted: str, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if wanted in line:
            return
        if not line and proc.poll() is not None:
            raise AssertionError(f"writer exited early: {proc.stderr.read()}")
    raise AssertionError(f"writer never said {wanted!r}")


def _kill(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert proc.returncode == -signal.SIGKILL


def test_a_writer_killed_mid_transaction_loses_only_the_uncommitted_part(tmp_path):
    path = tmp_path / "crash.db"
    writer = _spawn(
        """
import os
from db import Database
db = Database(os.environ["CYBERLOGIX_DB_PATH"])
for i in range(200):
    db.put("reading", f"R{i:04d}", {"n": i, "pad": "x" * 500})
print("committed", flush=True)
# A big transaction, left open: inserted, never committed.
with db._lock:
    db._conn.execute("BEGIN")
    for i in range(200, 5000):
        db._conn.execute(
            "INSERT INTO records (kind, rec_id, data) VALUES (?, ?, ?)",
            ("reading", f"R{i:04d}", '{"n": %d, "pad": "%s"}' % (i, "y" * 500)))
    db._conn.execute("UPDATE records SET data = '{\\"n\\": -1}' WHERE rec_id = 'R0000'")
    print("mid-transaction", flush=True)
    import time
    time.sleep(60)
""",
        path,
    )
    try:
        _wait_for_line(writer, "committed")
        _wait_for_line(writer, "mid-transaction")
    finally:
        _kill(writer)

    # WAL is what the database runs in; it is what makes the above safe.
    assert (tmp_path / "crash.db-wal").exists()

    from db import Database

    reopened = Database(str(path))
    assert reopened._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert reopened._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    rows = dict(reopened.rows("reading"))
    assert len(rows) == 200, "the committed rows are all there, and nothing more"
    assert rows["R0000"]["n"] == 0, "the uncommitted update was rolled back"
    assert rows["R0199"]["n"] == 199

    # And it takes writes again straight away.
    reopened.put("reading", "after", {"n": 1})
    assert reopened.get("reading", "after") == {"n": 1}


def test_the_store_comes_back_from_a_killed_process_with_what_it_had_saved(tmp_path):
    """The whole store, not just the table: a business, its sensor and an
    open incident written by a process that is then killed while it is
    hammering readings in."""
    path = tmp_path / "store.db"
    writer = _spawn(
        """
from store import STORE
tenant = STORE.create_tenant("Crash Test Kitchen", "Dana", "+15550100",
                             "ops@example.com", "growth")
sensor = STORE.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "Walk-In")
incident = STORE.open_incident(
    tenant_id=tenant.tenant_id, sensor=sensor, temperature_fahrenheit=55.0,
    breach_details="too warm", sms_text="EMERGENCY",
    sms_dispatch_source="fallback_template")
print("saved", tenant.tenant_id, incident.incident_id, flush=True)
i = 0
while True:
    STORE.record_reading(sensor=sensor, temperature_fahrenheit=40.0 + (i % 10),
                         humidity_percent=50.0, breached=False)
    i += 1
    if i == 50:
        print("writing", flush=True)
""",
        path,
    )
    try:
        first = ""
        deadline = time.monotonic() + 30
        while "saved" not in first and time.monotonic() < deadline:
            first = writer.stdout.readline()
            if not first and writer.poll() is not None:
                raise AssertionError(writer.stderr.read())
        _, tenant_id, incident_id = first.split()
        _wait_for_line(writer, "writing")
        time.sleep(0.2)  # well into the stream of writes
    finally:
        _kill(writer)

    conn = sqlite3.connect(str(path))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()

    from db import Database
    from store import HubStore

    store = HubStore(db=Database(str(path)))
    assert store.get_tenant(tenant_id).company_name == "Crash Test Kitchen"
    assert store.get_incident(incident_id).open
    readings = store.readings_for("FRZ-1")
    assert len(readings) >= 50
    assert all(r.temperature_fahrenheit is not None for r in readings)
