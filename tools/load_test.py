"""Load test: many sensors reporting through the real ingest endpoint.

    python tools/load_test.py                         # 250, 1000, 3000, 5000
    python tools/load_test.py --sensors 1000 --minutes 3
    python tools/load_test.py --sensors 5000 --saturate 30

For each step of the ramp it

1. seeds businesses, sites, sensors, an on-call roster and a few console
   users into a throwaway SQLite file in a temporary folder;
2. starts the server the way the image does -- uvicorn, one worker, the
   sweep loop on its production 60-second interval -- with Twilio swapped
   for a recording sink (tools/fake_providers.py) and a local mail sink
   standing in for SMTP, so nothing leaves the machine;
3. has every sensor post one reading a minute to POST /api/sensor-pulse
   with its own sensor key, spread over the minute the way unsynchronised
   hardware is, while console users refresh every 20 seconds as the page
   does and an uptime check reads /api/watchdog;
4. puts some sensors out of range and times each bad reading to its alert
   text (and to its email, and to the sweep's phone call once the
   shortened grace has passed);
5. stops the server and counts the readings on disk against the ones the
   server said it took.

`--saturate N` adds N seconds of back-to-back posting at the end, to find
the ceiling rather than the comfortable rate.

The numbers depend on the machine. On a shared one they are rough; run it
twice before believing a difference of a few milliseconds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import secrets
import shutil
import signal
import socket
import socketserver
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent

# What the console asks for on every 20-second refresh (static/console.js).
CONSOLE_FIRST = ["/api/console/overview", "/api/health"]
CONSOLE_REST = [
    "/api/costs?days=30", "/api/accounts/audit?limit=8", "/api/accounts/me",
    "/api/contacts", "/api/accounts/users", "/api/accounts/businesses",
    "/api/webhooks", "/api/invoices", "/api/contracts",
    "/api/contracts/pipeline", "/api/forecast/fleet", "/api/claims/eligible",
    "/api/contacts/preview", "/api/v1/bridge/samples", "/api/doorstep",
    "/api/people/alerts", "/api/pollers", "/api/tasks", "/api/people",
    "/api/people/calendar?days=180", "/api/people/departures",
]
CONSOLE_POLL_SECONDS = 20


# --------------------------------------------------------------------------
# Seeding. Runs in its own process, because the store opens its database
# from CYBERLOGIX_DB_PATH when it is first imported.
# --------------------------------------------------------------------------

def seed(db_path: str, sensors: int, per_business: int, console_users: int,
         history: int, out: str) -> None:
    os.environ["CYBERLOGIX_DB_PATH"] = db_path
    sys.path.insert(0, str(ROOT))
    from datetime import timedelta

    from store import INDUSTRY_PROFILES, STORE, Reading, utc_now

    verticals = sorted(INDUSTRY_PROFILES)
    businesses = math.ceil(sensors / per_business)
    made: Dict[str, Any] = {"sensors": [], "businesses": [], "console": []}
    now = utc_now()
    history_rows: List[tuple] = []
    for b in range(businesses):
        vertical = verticals[b % len(verticals)]
        phone = f"+1555{b:07d}"
        tenant = STORE.create_tenant(
            company_name=f"Load Test Business {b}",
            contact_name=f"Owner {b}",
            contact_phone=phone,
            contact_email=f"owner{b}@load.example",
            plan="enterprise",
            industry_vertical=vertical,
        )
        sites = [STORE.create_site(tenant.tenant_id, f"Site {s}") for s in range(2)]
        phones = []
        for order in (1, 2):
            contact_phone = f"+1556{b:05d}{order:02d}"
            STORE.add_contact(tenant.tenant_id, f"On call {order}", contact_phone,
                              escalation_order=order)
            phones.append(contact_phone)
        first = None
        count = min(per_business, sensors - b * per_business)
        for s in range(count):
            sensor_id = f"LT{b:04d}-{s:02d}"
            sensor = STORE.register_sensor(
                sensor_id=sensor_id, tenant_id=tenant.tenant_id,
                industry_vertical=vertical,
                location_name=f"Unit {s}",
            )
            STORE.assign_sensor_to_site(sensor, sites[s % 2].site_id)
            above, below = sensor.bounds()
            if above is not None and below is not None:
                nominal = (above + below) / 2.0
            elif above is not None:
                nominal = above - 8.0
            else:
                nominal = below + 8.0
            breach = above + 25.0 if above is not None else below - 25.0
            made["sensors"].append({
                "sensor_id": sensor_id, "key": sensor.ingest_key,
                "business": b, "nominal": round(nominal, 1),
                "breach": round(breach, 1),
            })
            if first is None:
                first = sensor_id
            for h in range(history):
                history_rows.append((sensor_id, nominal, now - timedelta(minutes=history - h)))
        made["businesses"].append({
            "tenant_id": tenant.tenant_id, "phones": phones,
            "email": tenant.contact_email, "first_sensor": first,
        })
        if b < console_users:
            user = STORE.create_user(tenant.tenant_id, f"console{b}@load.example",
                                     f"Console {b}", "owner", secrets.token_urlsafe(12))
            session = STORE.start_session(user)
            made["console"].append(session.secret)

    if history_rows:
        # Written straight to the table, oldest first, with ids from the
        # store's own counter, so a restart loads them like any others.
        start = STORE.id_counter() + 1
        batch = []
        for n, (sensor_id, temp, at) in enumerate(history_rows):
            reading = Reading(sensor_id=sensor_id, temperature_fahrenheit=round(temp, 1),
                              humidity_percent=50.0, breached=False, recorded_at=at,
                              reading_id=f"RDG-{start + n:06d}")
            batch.append((reading.reading_id, reading.to_row()))
            if len(batch) >= 50_000:
                STORE._db.put_many("reading", batch)
                batch = []
        STORE._db.put_many("reading", batch)
        STORE.raise_id_counter(start + len(history_rows))

    Path(out).write_text(json.dumps(made), encoding="utf-8")


# --------------------------------------------------------------------------
# Local stand-ins for the outside world.
# --------------------------------------------------------------------------

class MailSink:
    """A minimal SMTP server: takes every message and notes when.

    `mode` is `ok`, `tempfail` (451 on MAIL FROM) or `down` (the port is
    closed, so connecting is refused), and can be changed while running.
    """

    def __init__(self) -> None:
        self.received: List[Dict[str, Any]] = []
        self.mode = "ok"
        self.port = _free_port()
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self.start()

    def start(self) -> None:
        sink = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.wfile.write(b"220 sink ESMTP\r\n")
                rcpt: List[str] = []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    cmd = line.decode("latin-1").strip()
                    upper = cmd.upper()
                    if upper.startswith(("EHLO", "HELO")):
                        self.wfile.write(b"250-sink\r\n250 OK\r\n")
                    elif upper.startswith("MAIL FROM"):
                        if sink.mode == "tempfail":
                            self.wfile.write(b"451 4.3.0 Try again later\r\n")
                        else:
                            self.wfile.write(b"250 OK\r\n")
                    elif upper.startswith("RCPT TO"):
                        rcpt.append(cmd.split(":", 1)[1].strip(" <>"))
                        self.wfile.write(b"250 OK\r\n")
                    elif upper == "DATA":
                        self.wfile.write(b"354 go on\r\n")
                        body = []
                        while True:
                            chunk = self.rfile.readline()
                            if not chunk or chunk in (b".\r\n", b".\n"):
                                break
                            body.append(chunk.decode("utf-8", "replace"))
                        text = "".join(body)
                        subject = next((ln[9:].strip() for ln in text.splitlines()
                                        if ln.lower().startswith("subject: ")), "")
                        sink.received.append({"t": time.time(), "to": list(rcpt),
                                              "subject": subject})
                        rcpt = []
                        self.wfile.write(b"250 OK queued\r\n")
                    elif upper == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"250 OK\r\n")

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def down(self) -> None:
        self.mode = "down"
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def up(self) -> None:
        self.mode = "ok"
        if self._server is None:
            self.start()

    def stop(self) -> None:
        self.down()


class HeartbeatSink:
    """Counts the dead man's switch pings the server sends after a sweep."""

    def __init__(self) -> None:
        self.pings: List[float] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the stdlib's name
                sink.pings.append(time.time())
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/ping"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------
# The server, started the way the image starts it.
# --------------------------------------------------------------------------

class Server:
    def __init__(self, workdir: Path, mail: MailSink, heartbeat: HeartbeatSink,
                 sweep_seconds: int, grace_minutes: Optional[float],
                 python: str = sys.executable) -> None:
        self.workdir = workdir
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.admin_key = secrets.token_urlsafe(24)
        self.sink = workdir / "provider-sink.jsonl"
        self.mode_file = workdir / "twilio-mode"
        self.mode_file.write_text("ok", encoding="utf-8")
        self.log = workdir / "server.log"
        # Only what a Python process needs from this shell: never a real
        # credential, so nothing the server does can reach a real provider.
        env = {k: v for k, v in os.environ.items()
               if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "VIRTUAL_ENV")}
        env.update({
            "PYTHONPATH": str(ROOT),
            "CYBERLOGIX_DB_PATH": str(workdir / "cyberlogix.db"),
            "CYBERLOGIX_BACKUP_DIR": str(workdir / "backups"),
            "CYBERLOGIX_SWEEP_SECONDS": str(sweep_seconds),
            "CYBERLOGIX_ADMIN_KEY": self.admin_key,
            "CYBERLOGIX_MAIL_SECRET": secrets.token_urlsafe(24),
            "CYBERLOGIX_HEARTBEAT_URL": heartbeat.url,
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(mail.port),
            "SMTP_STARTTLS": "0",
            "SMTP_TIMEOUT_SECONDS": "5",
            "MAIL_FROM": "alerts@load.example",
            "LOADTEST_SINK": str(self.sink),
            "LOADTEST_TWILIO_MODE": str(self.mode_file),
        })
        if grace_minutes is not None:
            env["LOADTEST_GRACE_MINUTES"] = str(grace_minutes)
        self._log_fh = open(self.log, "ab")
        self.proc = subprocess.Popen(
            [python, "-m", "uvicorn", "fake_providers:app", "--app-dir", str(TOOLS),
             "--host", "127.0.0.1", "--port", str(self.port), "--workers", "1"],
            cwd=str(ROOT), env=env, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        self.started_at = time.time()

    def wait_ready(self, timeout: float = 600.0) -> float:
        import httpx

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early; see {self.log}")
            try:
                if httpx.get(self.base + "/api/health", timeout=2).status_code == 200:
                    return time.time() - self.started_at
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError("server did not become ready")

    def cpu_seconds(self) -> float:
        try:
            fields = Path(f"/proc/{self.proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            return float("nan")

    def peak_rss_mb(self) -> float:
        try:
            for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
        except OSError:
            pass
        return float("nan")

    def rss_mb(self) -> float:
        try:
            for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        except OSError:
            pass
        return float("nan")

    def twilio(self, mode: str) -> None:
        self.mode_file.write_text(mode, encoding="utf-8")

    def events(self) -> List[Dict[str, Any]]:
        if not self.sink.exists():
            return []
        return [json.loads(line) for line in self.sink.read_text().splitlines() if line]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._log_fh.close()


# --------------------------------------------------------------------------
# The load.
# --------------------------------------------------------------------------

def pct(values: List[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(math.ceil(q / 100.0 * len(ordered))) - 1))
    return ordered[k]


async def run_stage(server: Server, mail: MailSink, heartbeat: HeartbeatSink,
                    seeded: Dict[str, Any], minutes: float, interval: float,
                    breaches: int, saturate: float, clients: int = 3) -> Dict[str, Any]:
    import httpx

    sensors = seeded["sensors"]
    duration = minutes * 60.0
    rng = random.Random(7)
    latencies: List[float] = []
    statuses: Dict[str, int] = {}
    ok_readings = [0]
    errors: List[str] = []
    console_times: List[float] = []
    console_status: Dict[str, int] = {}
    watchdog_codes: Dict[int, int] = {}
    watchdog_age: List[int] = []
    breach_sent: Dict[str, Dict[str, Any]] = {}
    lateness: List[float] = []

    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=server.base, limits=limits,
                                 timeout=httpx.Timeout(60.0)) as client:

        async def pulse(sensor: Dict[str, Any], temp: float, due: float) -> Optional[dict]:
            started = time.perf_counter()
            lateness.append(max(0.0, time.time() - due))
            try:
                response = await client.post(
                    "/api/sensor-pulse",
                    json={"sensor_id": sensor["sensor_id"], "temperature_fahrenheit": temp},
                    headers={"X-CyberLogix-Sensor-Key": sensor["key"]},
                )
            except httpx.HTTPError as exc:
                statuses["transport_error"] = statuses.get("transport_error", 0) + 1
                errors.append(f"{type(exc).__name__}: {exc}"[:200])
                return None
            latencies.append((time.perf_counter() - started) * 1000.0)
            key = str(response.status_code)
            statuses[key] = statuses.get(key, 0) + 1
            if response.status_code == 200:
                ok_readings[0] += 1
                return response.json()
            errors.append(f"{response.status_code}: {response.text[:200]}")
            return None

        async def reporters(stop_at: float) -> None:
            # Each sensor at its own offset within the minute, with a little
            # jitter on every report, as real unsynchronised hardware does.
            schedule = []
            t0 = time.time() + 1.0
            for sensor in sensors:
                offset = rng.uniform(0, interval)
                k = 0
                while True:
                    due = t0 + offset + k * interval + rng.uniform(-1.0, 1.0)
                    if due >= stop_at:
                        break
                    schedule.append((due, sensor))
                    k += 1
            schedule.sort(key=lambda item: item[0])
            pending = set()
            for due, sensor in schedule:
                delay = due - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if sensor["sensor_id"] in breach_sent:
                    temp = sensor["breach"]   # a failed unit stays failed
                else:
                    temp = sensor["nominal"] + rng.uniform(-1.0, 1.0)
                task = asyncio.create_task(pulse(sensor, round(temp, 1), due))
                pending.add(task)
                task.add_done_callback(pending.discard)
            if pending:
                await asyncio.gather(*pending)

        async def console_user(token: str, stop_at: float) -> None:
            headers = {"Authorization": f"Bearer {token}"}
            await asyncio.sleep(rng.uniform(0, CONSOLE_POLL_SECONDS))
            while time.time() < stop_at:
                started = time.perf_counter()
                for group in (CONSOLE_FIRST, CONSOLE_REST):
                    results = await asyncio.gather(
                        *(client.get(path, headers=headers) for path in group),
                        return_exceptions=True)
                    for path, result in zip(group, results):
                        code = (type(result).__name__ if isinstance(result, Exception)
                                else str(result.status_code))
                        if code != "200":
                            label = f"{path.split('?')[0]} {code}"
                            console_status[label] = console_status.get(label, 0) + 1
                console_times.append((time.perf_counter() - started) * 1000.0)
                await asyncio.sleep(CONSOLE_POLL_SECONDS)

        async def uptime_check(stop_at: float) -> None:
            while time.time() < stop_at:
                try:
                    response = await client.get("/api/watchdog")
                    watchdog_codes[response.status_code] = (
                        watchdog_codes.get(response.status_code, 0) + 1)
                    age = response.json().get("seconds_since_sweep")
                    if age is not None:
                        watchdog_age.append(age)
                except (httpx.HTTPError, ValueError):
                    watchdog_codes[0] = watchdog_codes.get(0, 0) + 1
                await asyncio.sleep(5)

        async def break_some(stop_at: float) -> None:
            # A third of the way in, so the load is steady when they fail.
            await asyncio.sleep(min(duration / 3.0, 60.0))
            chosen = rng.sample(seeded["businesses"], min(breaches, len(seeded["businesses"])))
            by_id = {s["sensor_id"]: s for s in sensors}
            for business in chosen:
                sensor = by_id[business["first_sensor"]]
                breach_sent[sensor["sensor_id"]] = {
                    "t": time.time(), "phones": business["phones"],
                    "email": business["email"],
                }
                body = await pulse(sensor, sensor["breach"], time.time())
                breach_sent[sensor["sensor_id"]]["status"] = (body or {}).get("status")
                breach_sent[sensor["sensor_id"]]["incident"] = (body or {}).get("incident_id")
                await asyncio.sleep(rng.uniform(1.0, 4.0))

        stop_at = time.time() + duration
        cpu0, wall0 = server.cpu_seconds(), time.time()
        pings0 = len(heartbeat.pings)
        await asyncio.gather(
            reporters(stop_at),
            uptime_check(stop_at),
            break_some(stop_at),
            *(console_user(t, stop_at) for t in seeded["console"]),
        )
        wall = time.time() - wall0
        cpu = server.cpu_seconds() - cpu0
        sent_readings = sum(statuses.values())

        saturation: Dict[str, Any] = {}
        if saturate > 0:
            calm = [s for s in sensors if s["sensor_id"] not in breach_sent]
            saturation = saturate_server(server, calm, saturate, clients)
            ok_readings[0] += saturation.pop("ok")

    # Give the alert emails a moment; they go from a worker.
    await asyncio.sleep(3)
    events = server.events()
    phone_owner = {}
    for sensor_id, info in breach_sent.items():
        for phone in info["phones"]:
            phone_owner[phone] = sensor_id
    first_sms: Dict[str, float] = {}
    first_call: Dict[str, float] = {}
    sweeps = [e for e in events if e["event"] == "sweep"]
    for event in events:
        owner = phone_owner.get(event.get("to"))
        if owner is None:
            continue
        if event["event"] == "sms":
            first_sms.setdefault(owner, event["t"])
        elif event["event"] == "call":
            first_call.setdefault(owner, event["t"])
    first_mail: Dict[str, float] = {}
    for message in mail.received:
        for sensor_id, info in breach_sent.items():
            if info["email"] in message["to"]:
                first_mail.setdefault(sensor_id, message["t"])
    to_sms = [first_sms[s] - i["t"] for s, i in breach_sent.items() if s in first_sms]
    to_mail = [first_mail[s] - i["t"] for s, i in breach_sent.items() if s in first_mail]
    grace = float(os.environ.get("LOADTEST_GRACE_MINUTES_USED", "0") or 0)
    call_late = [first_call[s] - (i["t"] + grace * 60.0)
                 for s, i in breach_sent.items() if s in first_call]

    return {
        "sensors": len(sensors),
        "businesses": len(seeded["businesses"]),
        "console_users": len(seeded["console"]),
        "seconds": round(wall, 1),
        "readings_sent": sent_readings,
        "readings_ok": ok_readings[0],
        "readings_per_second": round(sent_readings / wall, 1),
        "ingest_median_ms": round(statistics.median(latencies), 1) if latencies else None,
        "ingest_p95_ms": round(pct(latencies, 95), 1),
        "ingest_p99_ms": round(pct(latencies, 99), 1),
        "ingest_max_ms": round(max(latencies), 1) if latencies else None,
        "client_lateness_p95_s": round(pct(lateness, 95), 2),
        "statuses": statuses,
        "errors": errors[:5],
        "server_cpu_percent": round(100.0 * cpu / wall),
        "console_refresh_median_ms": (round(statistics.median(console_times))
                                      if console_times else None),
        "console_refresh_p95_ms": round(pct(console_times, 95)) if console_times else None,
        "console_non_200": console_status,
        "watchdog_codes": watchdog_codes,
        "watchdog_max_seconds_since_sweep": max(watchdog_age) if watchdog_age else None,
        "heartbeat_pings": len(heartbeat.pings) - pings0,
        "sweeps": len(sweeps),
        "sweep_seconds_max": (round(max(e["end"] - e["start"] for e in sweeps), 2)
                              if sweeps else None),
        "sweep_gap_max_s": (round(max(b["start"] - a["start"]
                                      for a, b in zip(sweeps, sweeps[1:])), 1)
                            if len(sweeps) > 1 else None),
        "breaches": len(breach_sent),
        "breach_statuses": sorted({str(i.get("status")) for i in breach_sent.values()}),
        "breach_to_sms_s": {"n": len(to_sms),
                            "median": round(statistics.median(to_sms), 3) if to_sms else None,
                            "max": round(max(to_sms), 3) if to_sms else None},
        "breach_to_email_s": {"n": len(to_mail),
                              "median": round(statistics.median(to_mail), 2) if to_mail else None,
                              "max": round(max(to_mail), 2) if to_mail else None},
        "call_after_grace_s": {"n": len(call_late),
                               "median": round(statistics.median(call_late), 1) if call_late else None,
                               "max": round(max(call_late), 1) if call_late else None},
        "saturation": saturation,
    }


def _hammer(base: str, sensors: List[Dict[str, Any]], seconds: float,
            concurrency: int, out) -> None:
    """One client process posting back-to-back. One Python process cannot
    keep a server busy on its own: measured, the client sat at 100% of a
    core while the server used half of one."""
    import httpx

    latencies: List[float] = []
    codes: Dict[str, int] = {}

    async def go() -> None:
        stop = time.time() + seconds
        limits = httpx.Limits(max_connections=concurrency)
        async with httpx.AsyncClient(base_url=base, limits=limits,
                                     timeout=httpx.Timeout(60.0)) as client:
            async def worker(i: int) -> None:
                while time.time() < stop:
                    sensor = sensors[i % len(sensors)]
                    i += concurrency
                    started = time.perf_counter()
                    try:
                        r = await client.post(
                            "/api/sensor-pulse",
                            json={"sensor_id": sensor["sensor_id"],
                                  "temperature_fahrenheit": sensor["nominal"]},
                            headers={"X-CyberLogix-Sensor-Key": sensor["key"]})
                        code = str(r.status_code)
                    except httpx.HTTPError as exc:
                        code = type(exc).__name__
                    latencies.append((time.perf_counter() - started) * 1000.0)
                    codes[code] = codes.get(code, 0) + 1

            await asyncio.gather(*(worker(i) for i in range(concurrency)))

    asyncio.run(go())
    out.put({"latencies": latencies, "codes": codes})


def saturate_server(server: "Server", sensors: List[Dict[str, Any]],
                    seconds: float, clients: int, concurrency: int = 16) -> Dict[str, Any]:
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    chunks = [sensors[i::clients] for i in range(clients)]
    procs = [ctx.Process(target=_hammer, args=(server.base, chunk, seconds, concurrency, out))
             for chunk in chunks if chunk]
    cpu0, wall0 = server.cpu_seconds(), time.time()
    for proc in procs:
        proc.start()
    results = [out.get(timeout=seconds + 120) for _ in procs]
    for proc in procs:
        proc.join(timeout=30)
    wall = time.time() - wall0
    latencies = [x for r in results for x in r["latencies"]]
    codes: Dict[str, int] = {}
    for r in results:
        for code, n in r["codes"].items():
            codes[code] = codes.get(code, 0) + n
    return {
        "seconds": round(wall, 1),
        "client_processes": len(procs),
        "concurrency": len(procs) * concurrency,
        "readings_per_second": round(sum(codes.values()) / wall, 1),
        "median_ms": round(statistics.median(latencies), 1) if latencies else None,
        "p95_ms": round(pct(latencies, 95), 1),
        "statuses": codes,
        "server_cpu_percent": round(100.0 * (server.cpu_seconds() - cpu0) / wall),
        "ok": codes.get("200", 0),
    }


def max_reading_number(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(CAST(substr(rec_id, 5) AS INTEGER)) FROM records "
                           "WHERE kind = 'reading'").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def count_new_readings(db_path: Path, above: int) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM records WHERE kind = 'reading' "
            "AND CAST(substr(rec_id, 5) AS INTEGER) > ?", (above,)).fetchone()[0])
    finally:
        conn.close()


def count_rows(db_path: Path) -> Dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind"))
    finally:
        conn.close()


def one_stage(args: argparse.Namespace, sensors: int, mail: MailSink,
              heartbeat: HeartbeatSink) -> Dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="cyberlogix-load-"))
    try:
        keys = workdir / "seeded.json"
        db_path = workdir / "cyberlogix.db"
        t = time.time()
        subprocess.run(
            [args.python, str(Path(__file__).resolve()), "_seed", "--db", str(db_path),
             "--sensors", str(sensors), "--per-business", str(args.per_business),
             "--console-users", str(args.console_users), "--history", str(args.history),
             "--out", str(keys)],
            check=True, cwd=str(ROOT),
        )
        seed_seconds = time.time() - t
        seeded = json.loads(keys.read_text())
        seeded_max = max_reading_number(db_path)
        os.environ["LOADTEST_GRACE_MINUTES_USED"] = str(args.grace_minutes)
        server = Server(workdir, mail, heartbeat, args.sweep_seconds, args.grace_minutes,
                        python=args.python)
        try:
            ready = server.wait_ready()
            result = asyncio.run(run_stage(
                server, mail, heartbeat, seeded, args.minutes, args.interval,
                args.breaches, args.saturate, args.clients))
            result["startup_seconds"] = round(ready, 1)
            result["peak_rss_mb"] = round(server.peak_rss_mb())
            result["rss_mb"] = round(server.rss_mb())
        finally:
            server.stop()
        # Counted by id rather than by rows: once a sensor's buffer is full
        # every new reading deletes its oldest, so the row count stands
        # still while readings are being stored.
        stored = count_new_readings(db_path, seeded_max)
        result["readings_stored"] = stored
        result["readings_lost"] = result["readings_ok"] - stored
        result["seed_seconds"] = round(seed_seconds, 1)
        result["db_mb"] = round(db_path.stat().st_size / 1e6, 1)
        wal = Path(str(db_path) + "-wal")
        result["wal_mb"] = round(wal.stat().st_size / 1e6, 1) if wal.exists() else 0.0
        log = (workdir / "server.log").read_text(errors="replace")
        result["log_mb"] = round(len(log) / 1e6, 1)
        # Less the one expected line per alert: no key for generated
        # wording is set, so it comes from the fixed template.
        result["log_errors"] = sum(
            1 for line in log.splitlines()
            if (" ERROR " in line or "Traceback" in line)
            and "deterministic template" not in line)
        result["database_locked"] = log.count("database is locked")
        return result
    finally:
        if args.keep:
            print(f"  kept {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def show(result: Dict[str, Any]) -> None:
    print(f"\n== {result['sensors']} sensors, {result['businesses']} businesses, "
          f"{result['console_users']} console users, {result['seconds']}s ==")
    for key in ("readings_per_second", "readings_sent", "readings_ok", "readings_stored",
                "readings_lost", "ingest_median_ms", "ingest_p95_ms", "ingest_p99_ms",
                "ingest_max_ms", "statuses", "server_cpu_percent", "peak_rss_mb", "rss_mb",
                "startup_seconds", "console_refresh_median_ms", "console_refresh_p95_ms",
                "console_non_200", "watchdog_codes", "watchdog_max_seconds_since_sweep",
                "heartbeat_pings", "sweeps", "sweep_seconds_max", "sweep_gap_max_s",
                "breaches", "breach_statuses", "breach_to_sms_s", "breach_to_email_s",
                "call_after_grace_s", "db_mb", "wal_mb", "log_mb", "log_errors",
                "database_locked", "errors", "saturation"):
        print(f"  {key:34} {result.get(key)}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    seeding = sub.add_parser("_seed")
    seeding.add_argument("--db", required=True)
    seeding.add_argument("--sensors", type=int, required=True)
    seeding.add_argument("--per-business", type=int, default=10)
    seeding.add_argument("--console-users", type=int, default=5)
    seeding.add_argument("--history", type=int, default=0)
    seeding.add_argument("--out", required=True)

    parser.add_argument("--sensors", default="250,1000,3000,5000",
                        help="comma-separated ramp (default 250,1000,3000,5000)")
    parser.add_argument("--minutes", type=float, default=3.0,
                        help="minutes of steady load per step (default 3)")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="seconds between one sensor's readings (default 60)")
    parser.add_argument("--per-business", type=int, default=10)
    parser.add_argument("--console-users", type=int, default=5)
    parser.add_argument("--breaches", type=int, default=10,
                        help="sensors put out of range during each step")
    parser.add_argument("--history", type=int, default=0,
                        help="backdated readings per sensor seeded before the run")
    parser.add_argument("--sweep-seconds", type=int, default=60)
    parser.add_argument("--grace-minutes", type=float, default=1.0,
                        help="voice grace for the run (production is 10)")
    parser.add_argument("--saturate", type=float, default=0.0,
                        help="seconds of back-to-back posting after the steady load")
    parser.add_argument("--clients", type=int, default=3,
                        help="client processes for --saturate (default 3)")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--keep", action="store_true", help="keep the temporary folders")
    parser.add_argument("--json", help="also write the results here")
    args = parser.parse_args(argv)

    if args.command == "_seed":
        seed(args.db, args.sensors, args.per_business, args.console_users,
             args.history, args.out)
        return 0

    mail = MailSink()
    heartbeat = HeartbeatSink()
    results = []
    try:
        for n in [int(x) for x in args.sensors.split(",") if x.strip()]:
            print(f"-- seeding and loading {n} sensors ...", file=sys.stderr)
            result = one_stage(args, n, mail, heartbeat)
            show(result)
            results.append(result)
    finally:
        mail.stop()
        heartbeat.stop()
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    failed = any(r["readings_lost"] or set(r["statuses"]) - {"200"} for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
