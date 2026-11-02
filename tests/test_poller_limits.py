"""A vendor poller only ever reaches the public internet, gives up loudly,
and once removed is gone for good.

Three ways the poller could go wrong without anyone noticing: a name that
answers "public" for the check and "private" for the connect (DNS
rebinding) taking our server, and the customer's vendor credential, into
our own network; a source failing every ten minutes all night, hammering
a vendor with a dead password while the console stays empty; and a
source removed by its owner that still writes one last batch afterwards.
"""

import http.server
import socket
import threading
import time

import pytest

import pollers
from store import STORE


@pytest.fixture()
def estate(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    owner = owner_headers(headers)
    resp = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-1", "industry_vertical": "restaurant",
        "location_name": "Kitchen", "external_device_sn": "SP-0001"})
    assert resp.status_code == 201, resp.text
    return {**headers, **owner}, tenant


def _add(api, auth, **over):
    body = {"name": "SensorPush", "url": "https://api.sensorpush.com/samples",
            "headers": {"Authorization": "secret-token"}, "interval_minutes": 10}
    body.update(over)
    resp = api.post("/api/pollers", headers=auth, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["source"]


class _Inside(http.server.BaseHTTPRequestHandler):
    """Something on our own network that must never be reached."""

    hits = []

    def do_GET(self):
        _Inside.hits.append((self.path, self.headers.get("Authorization")))
        body = b'{"samples": [{"sensor": "SP-0001", "temperature": 99.0}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # pragma: no cover - nothing should post here
        self.do_GET()

    def log_message(self, *a):
        pass


@pytest.fixture()
def inside():
    _Inside.hits = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Inside)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


# ---- private addresses, after a redirect and after a rebind ----------------


def _port(args):
    """The port a resolver puts in its answer, as the real one does."""
    port = args[0] if args else 0
    return int(port) if isinstance(port, (int, str)) and str(port).isdigit() else 0


def test_a_name_that_rebinds_to_a_private_address_is_never_connected_to(
        monkeypatch, inside):
    """Public for the check, 127.0.0.1 a moment later for the connect."""
    real = socket.getaddrinfo
    answers = {"n": 0}

    def rebinding(host, *a, **k):
        if host == "rebind.example":
            answers["n"] += 1
            ip = "93.184.216.34" if answers["n"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, _port(a)))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    with pytest.raises(ValueError, match="127.0.0.1"):
        pollers._fetch(f"http://rebind.example:{inside}/samples",
                       {"Authorization": "secret-token"})
    assert _Inside.hits == [], "the credential reached our own network"


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch, inside):
    """Name resolved once; the socket goes to that answer, not a new one."""
    real = socket.getaddrinfo
    asked = []

    def counting(host, *a, **k):
        if host == "vendor.example":
            asked.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", _port(a)))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    # Treat loopback as public for this one test, to see where the socket goes.
    monkeypatch.setattr(pollers, "_reaches_the_public_internet", lambda ip: True)
    got = pollers._fetch(f"http://vendor.example:{inside}/samples", {})
    assert got["samples"][0]["sensor"] == "SP-0001"
    # Once by the early check and once by the connect, which used its own
    # checked answer rather than handing the name to the socket layer.
    assert len(asked) == 2


def test_a_redirect_to_a_private_address_is_not_followed(monkeypatch, inside):
    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{inside}/latest/meta-data/")
            self.end_headers()

        def log_message(self, *a):
            pass

    outside = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=outside.serve_forever, daemon=True).start()
    real = socket.getaddrinfo

    def public_name(host, *a, **k):
        if host == "vendor.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", _port(a)))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", public_name)
    monkeypatch.setattr(
        pollers, "_reaches_the_public_internet",
        lambda ip: True)
    try:
        with pytest.raises(ValueError, match="[Rr]edirect"):
            pollers._fetch(
                f"http://vendor.example:{outside.server_address[1]}/samples",
                {"Authorization": "secret-token"})
    finally:
        outside.shutdown()
        outside.server_close()
    assert _Inside.hits == []


def test_no_proxy_stands_between_the_check_and_the_connection():
    """A proxy resolves the name itself, out of reach of every check, so
    the opener carries none, whatever the environment it started in."""
    assert not [h for h in pollers._OPENER.handlers
                if isinstance(h, pollers.urllib.request.ProxyHandler) and h.proxies]


# ---- repeated failure: stop, and tell the owner ----------------------------


def test_after_repeated_failures_it_stops_and_tells_the_owner(
        api, estate, monkeypatch, mailbox):
    headers, tenant = estate
    made = _add(api, headers)
    calls = []

    def refused(url, h):
        calls.append(url)
        raise pollers.urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(pollers, "_fetch", refused)
    for _ in range(pollers.FAILURES_BEFORE_BROKEN):
        row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])
        row["last_polled_at"] = None  # due now
        STORE._db.put(pollers.SOURCE_KIND, made["source_id"], row)
        pollers.run_poll_pass()
    assert len(calls) == pollers.FAILURES_BEFORE_BROKEN

    listed = api.get("/api/pollers", headers=headers).json()
    assert listed["sources"][0]["stopped"] is True
    assert "no longer being asked" in listed["note"]

    # Stopped: however long it is left, it is not asked again.
    row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])
    row["last_polled_at"] = "2020-01-01T00:00:00Z"
    STORE._db.put(pollers.SOURCE_KIND, made["source_id"], row)
    for _ in range(3):
        pollers.run_poll_pass()
    assert len(calls) == pollers.FAILURES_BEFORE_BROKEN

    # The owner was told, once, by email and in the audit trail.
    told = [m for m in mailbox if "stopped fetching" in m["Subject"]]
    assert len(told) == 1
    assert told[0]["To"] == "ops@example.com"
    assert "401" in told[0].get_content()
    assert "secret-token" not in told[0].get_content()
    audit = api.get("/api/accounts/audit", headers=headers).text
    assert "poller.stopped" in audit

    # Fixed at the vendor, one "Try now" starts it again.
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {
        "samples": [{"sensor": "SP-0001", "temperature": 40.0}]})
    api.post(f"/api/pollers/{made['source_id']}/test", headers=headers)
    listed = api.get("/api/pollers", headers=headers).json()["sources"][0]
    assert listed["stopped"] is False and listed["consecutive_failures"] == 0
    assert pollers.due(STORE._db.get(pollers.SOURCE_KIND, made["source_id"])) is False
    row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])
    row["last_polled_at"] = None
    STORE._db.put(pollers.SOURCE_KIND, made["source_id"], row)
    assert pollers.due(row) is True


# ---- removed means removed --------------------------------------------------


def test_once_removal_returns_nothing_that_source_fetched_is_written(
        api, estate, monkeypatch):
    """Removal mid-ingest waits for the write in progress, then nothing more."""
    headers, _ = estate
    made = _add(api, headers)
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {
        "samples": [{"sensor": "SP-0001", "temperature": 41.0}]})

    started, go = threading.Event(), threading.Event()
    order = []
    real_ingest = pollers._ingest

    def paused_ingest(tenant, row, payload):
        started.set()
        go.wait(5)
        result = real_ingest(tenant, row, payload)
        order.append("written")
        return result

    monkeypatch.setattr(pollers, "_ingest", paused_ingest)
    poll = threading.Thread(target=pollers.poll_once,
                            args=(STORE._db.get(pollers.SOURCE_KIND, made["source_id"]),))
    poll.start()
    assert started.wait(5)

    def remove():
        resp = api.delete(f"/api/pollers/{made['source_id']}", headers=headers)
        assert resp.status_code == 204
        order.append("removed")

    remover = threading.Thread(target=remove)
    remover.start()
    time.sleep(0.3)
    go.set()
    poll.join(5)
    remover.join(5)

    assert order == ["written", "removed"]
    count = len(STORE.readings_for("FRZ-1"))

    # After removal: no pass, no test, no restart brings it back.
    for _ in range(3):
        pollers.run_poll_pass()
    assert api.post(f"/api/pollers/{made['source_id']}/test",
                    headers=headers).status_code == 404
    STORE._forget_locked()
    STORE.load()
    pollers.run_poll_pass()
    assert STORE._db.get(pollers.SOURCE_KIND, made["source_id"]) is None
    assert len(STORE.readings_for("FRZ-1")) == count
