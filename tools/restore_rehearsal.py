"""Backup and restore rehearsal on a big database, timed.

    python tools/restore_rehearsal.py                    # 1000 sensors x 500 readings
    python tools/restore_rehearsal.py --sensors 3000

Follows the restore checklist in docs/OPERATIONS.md against real servers
started the way the image starts them (tools/fake_providers.py: Twilio is
a recording sink, nothing leaves the machine):

1. seeds a big database and starts "production" on it; opens incidents,
   attests the vault, writes the audit trail;
2. takes a snapshot with POST /api/admin/backups, lists it, verifies it
   and downloads it, as an operator copying it off the machine would;
3. restores the download into a fresh copy -- a new folder, the file put
   in place of the database, the stale -wal/-shm removed -- starts a
   second server on it in rehearsal mode (sweep off), and checks sensors,
   readings, incidents, the vault and the audit trail are all there and
   that a new breach still sends its text;
4. restores the snapshot in place on "production" with
   POST /api/admin/backups/restore, and checks that the next sweeps send
   nothing that was already sent.

Prints each step's time. The numbers are rough on a shared machine.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from load_test import HeartbeatSink, MailSink, Server, count_rows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def step(timings, label, started):
    timings.append((label, time.time() - started))
    print(f"  {label:58} {timings[-1][1]:7.1f}s", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sensors", type=int, default=1000)
    parser.add_argument("--history", type=int, default=500)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="cyberlogix-restore-"))
    prod_dir, fresh_dir = work / "production", work / "fresh"
    prod_dir.mkdir()
    fresh_dir.mkdir()
    mail, heartbeat = MailSink(), HeartbeatSink()
    timings: list = []
    problems: list = []
    servers = []
    try:
        t = time.time()
        subprocess.run(
            [args.python, str(ROOT / "tools" / "load_test.py"), "_seed",
             "--db", str(prod_dir / "cyberlogix.db"), "--sensors", str(args.sensors),
             "--history", str(args.history), "--console-users", "3",
             "--out", str(work / "seeded.json")], check=True, cwd=str(ROOT))
        seeded = json.loads((work / "seeded.json").read_text())
        step(timings, f"seed {args.sensors} sensors x {args.history} readings", t)

        t = time.time()
        prod = Server(prod_dir, mail, heartbeat, sweep_seconds=60, grace_minutes=1.0,
                      python=args.python)
        servers.append(prod)
        prod.wait_ready()
        step(timings, "start production on it", t)
        admin = {"X-CyberLogix-Admin": prod.admin_key}
        owner = {"Authorization": f"Bearer {seeded['console'][0]}"}
        first = seeded["businesses"][0]
        by_id = {s["sensor_id"]: s for s in seeded["sensors"]}
        breach_sensor = by_id[first["first_sensor"]]

        with httpx.Client(base_url=prod.base, timeout=600) as api:
            hit = api.post("/api/sensor-pulse", headers={
                "X-CyberLogix-Sensor-Key": breach_sensor["key"]},
                json={"sensor_id": breach_sensor["sensor_id"],
                      "temperature_fahrenheit": breach_sensor["breach"]})
            incident_id = hit.json()["incident_id"]
            other = [s for s in seeded["sensors"] if s["business"] == 0][1]
            attest = api.get(f"/api/vault/attestation/{other['sensor_id']}", headers=owner)
            if attest.status_code != 200:
                problems.append(f"attestation: {attest.status_code} {attest.text[:200]}")
            api.post("/api/contacts", headers=owner, json={
                "full_name": "Rehearsal Contact", "phone": "+15559990001"})
            audit_before = api.get("/api/accounts/audit?limit=100", headers=owner).json()

            # 2. Snapshot, list, verify, download -- the checklist's steps.
            t = time.time()
            taken = api.post("/api/admin/backups", headers=admin)
            taken.raise_for_status()
            name = Path(taken.json()["path"]).name
            step(timings, f"POST /api/admin/backups ({taken.json()['size_bytes'] / 1e6:.0f} MB)", t)
            t = time.time()
            listed = api.get("/api/admin/backups", headers=admin).json()
            assert listed["snapshots"][0]["name"] == name
            verified = api.get(f"/api/admin/backups/{name}/verify", headers=admin).json()
            if not verified.get("ok"):
                problems.append(f"verify: {verified}")
            step(timings, "list and verify it", t)
            t = time.time()
            with api.stream("GET", f"/api/admin/backups/{name}/download", headers=admin) as r:
                r.raise_for_status()
                with open(work / name, "wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
            step(timings, "download it (the copy kept off the machine)", t)

            # The call goes out after the snapshot, so the restore must not
            # make it again.
            deadline = time.time() + 200
            while time.time() < deadline:
                if any(e["event"] == "call" for e in prod.events()):
                    break
                time.sleep(2)
            calls_before = sum(1 for e in prod.events() if e["event"] == "call")
            if not calls_before:
                problems.append("production never placed the escalation call")

        # 3. A fresh copy: the downloaded file as the database of a new server.
        t = time.time()
        shutil.copyfile(work / name, fresh_dir / "cyberlogix.db")
        for stale in ("cyberlogix.db-wal", "cyberlogix.db-shm"):
            (fresh_dir / stale).unlink(missing_ok=True)
        fresh = Server(fresh_dir, mail, heartbeat, sweep_seconds=0, grace_minutes=1.0,
                       python=args.python)
        servers.append(fresh)
        fresh.wait_ready()
        step(timings, "fresh copy: put the file in place and start", t)
        t = time.time()
        expected = count_rows(work / name)
        fresh_counts = count_rows(fresh_dir / "cyberlogix.db")
        for kind in ("tenant", "sensor", "reading", "incident", "audit", "anchor", "user"):
            if fresh_counts.get(kind, 0) != expected.get(kind, 0):
                problems.append(f"fresh copy {kind}: {fresh_counts.get(kind)} != {expected.get(kind)}")
        with httpx.Client(base_url=fresh.base, timeout=120) as api:
            overview = api.get("/api/console/overview", headers=owner)
            if overview.status_code != 200:
                problems.append(f"fresh copy console: {overview.status_code}")
            incidents = api.get("/api/voice/incidents", headers=owner).json()
            if incident_id not in {i["incident_id"] for i in incidents.get("incidents", [])}:
                problems.append("fresh copy is missing the open incident")
            vault = api.get(f"/api/vault/verify/{other['sensor_id']}", headers=owner).json()
            if not vault.get("intact"):
                problems.append(f"fresh copy vault: {vault.get('note')}")
            audit_after = api.get("/api/accounts/audit?limit=100", headers=owner).json()
            if {e["entry_id"] for e in audit_before["entries"]} - {
                    e["entry_id"] for e in audit_after["entries"]}:
                problems.append("fresh copy is missing audit entries")
            sent_before = len(fresh.events())
            spare = [s for s in seeded["sensors"] if s["business"] == 1][0]
            hit = api.post("/api/sensor-pulse", headers={
                "X-CyberLogix-Sensor-Key": spare["key"]},
                json={"sensor_id": spare["sensor_id"], "temperature_fahrenheit": spare["breach"]})
            if hit.json().get("status") != "CRITICAL_CATASTROPHE_TRIGGERED":
                problems.append(f"fresh copy breach: {hit.text[:200]}")
            texts = [e for e in fresh.events()[sent_before:] if e["event"] == "sms"]
            if not texts:
                problems.append("fresh copy: a new breach sent no text")
        step(timings, "fresh copy: check data, vault, audit, a new alert", t)

        # 4. In place, on production, as the checklist's API restore does.
        t = time.time()
        with httpx.Client(base_url=prod.base, timeout=900) as api:
            restored = api.post("/api/admin/backups/restore", headers=admin,
                                json={"snapshot": name, "confirm": True})
            if restored.status_code != 200:
                problems.append(f"in-place restore: {restored.status_code} {restored.text[:300]}")
            else:
                print(f"  kept from the replaced database: "
                      f"{restored.json().get('kept_from_the_replaced_database')}")
        step(timings, "in-place restore through the API", t)
        t = time.time()
        sweeps_then = sum(1 for e in prod.events() if e["event"] == "sweep")
        while sum(1 for e in prod.events() if e["event"] == "sweep") < sweeps_then + 2:
            time.sleep(2)
            if time.time() - t > 200:
                problems.append("production stopped sweeping after the restore")
                break
        calls_after = sum(1 for e in prod.events() if e["event"] == "call")
        if calls_after != calls_before:
            problems.append(f"the restore re-sent {calls_after - calls_before} call(s)")
        step(timings, "two sweeps after it: nothing sent twice", t)
    finally:
        for server in servers:
            server.stop()
        mail.stop()
        heartbeat.stop()
        db = prod_dir / "cyberlogix.db"
        if db.exists():
            print(f"  database {db.stat().st_size / 1e6:.0f} MB, "
                  f"{count_rows(db).get('reading', 0)} readings")
        if args.keep:
            print(f"  kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print("\n  " + ("PASS" if not problems else "FAIL"))
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
