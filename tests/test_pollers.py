"""Fetching from sensor clouds that will not push to us.

The bridge assumes a sensor can be told where to send its readings. The
one this product most often recommends cannot: a SensorPush G1 gateway
posts to SensorPush, and the way to get the data is to ask their API.
Without this the recommendation was hollow -- buy the sensor, point it
at nothing, watch an empty console.

The dangerous part is not the fetching. It is that a customer supplies a
URL and our server goes and gets it, which is the shape of a server-side
request forgery.
"""

import json
import urllib.error

import pytest

import pollers
from store import STORE


@pytest.fixture()
def estate(api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    owner = owner_headers(headers)
    resp = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-1", "industry_vertical": "restaurant",
        "location_name": "Kitchen", "external_device_sn": "SP-0001"})
    assert resp.status_code == 201, resp.text
    return {**headers, **owner}, tenant


def source(api, auth, **over):
    """`auth` rather than `headers`: the payload has a "headers" key of
    its own, and naming both the same made every call that overrode the
    vendor's headers pass them as the request's instead."""
    body = {"name": "SensorPush", "url": "https://api.sensorpush.com/samples",
            "headers": {"Authorization": "secret-token"},
            "interval_minutes": 10}
    body.update(over)
    return api.post("/api/pollers", headers=auth, json=body)


# --- the part that could be turned against us -----------------------------


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data/",   # cloud metadata
    "https://127.0.0.1/admin",
    "https://localhost/admin",
    "https://10.0.0.5/internal",
    "https://192.168.1.1/router",
    "https://172.16.0.9/internal",
    "https://metadata.google.internal/computeMetadata/v1/",
])
def test_it_refuses_to_fetch_inside_our_own_network(api, estate, url):
    """Left open, somebody stores the cloud metadata address and reads
    our credentials back through us."""
    headers, _ = estate

    refused = source(api, headers, url=url)

    assert refused.status_code == 422, f"{url} was accepted"


@pytest.mark.parametrize("url", [
    # Every one of these reached the same private addresses as the list
    # above, and every one was accepted, because the check compared the
    # front of a string instead of reading an address.
    "https://[::1]:8080/admin",              # brackets: split(':')[0] is '['
    "https://[fd00::1]/internal",            # IPv6 unique-local
    "https://[fe80::1]/link-local",
    "https://[::ffff:127.0.0.1]/admin",      # IPv4 wearing an IPv6 coat
    "https://0.0.0.0/admin",
    "https://2130706433/admin",              # 127.0.0.1, in decimal
    "https://0x7f000001/admin",              # 127.0.0.1, in hex
    "https://user@127.0.0.1/admin",          # hidden behind userinfo
    "https://LOCALHOST/admin",               # the check was case-sensitive
    "https://localhost./admin",              # and stopped at a trailing dot
    # Six ways exist to write an IPv4 address as an IPv6 one, and
    # ipaddress only knows the first is private. These two it calls
    # global, and both still arrive at 127.0.0.1.
    "https://[::127.0.0.1]/admin",           # IPv4-compatible, deprecated
    "https://[64:ff9b::7f00:1]/admin",       # NAT64 well-known prefix
])
def test_the_ways_round_that_check(api, estate, url):
    """A prefix match on a string is not an address check.

    Each of these is a real bypass of the original: a private address
    spelled so that no listed prefix matches it.
    """
    headers, _ = estate

    refused = source(api, headers, url=url)

    assert refused.status_code == 422, f"{url} was accepted"


def test_a_public_name_that_points_somewhere_private_is_refused(estate,
                                                                monkeypatch):
    """The check that reads the URL cannot see where a name goes.

    Nothing about 'sensors.example' looks private. The customer owns the
    name and points it at the cloud metadata service, and every check
    that reads spelling passes it.
    """
    import socket

    real = socket.getaddrinfo

    def pointing_inward(host, *a, **k):
        if host == "sensors.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     ("169.254.169.254", 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", pointing_inward)

    with pytest.raises(ValueError, match="169.254.169.254"):
        pollers._fetch("https://sensors.example/samples", {})


def test_a_redirect_is_not_followed(estate, monkeypatch):
    """A redirect is a second URL, picked after every check has run.

    urllib follows one by default and carries the customer's vendor
    credential to wherever it lands, so one 302 undoes all of the above.
    """
    import http.server
    import threading

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header(
                "Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Isolate the redirect: the address checks would refuse 127.0.0.1
    # long before the request, and they are not what is under test here.
    monkeypatch.setattr(pollers, "_resolve_to_public", lambda host: None)
    try:
        with pytest.raises(ValueError, match="[Rr]edirect"):
            pollers._fetch(
                f"http://127.0.0.1:{server.server_address[1]}/samples",
                {"Authorization": "secret-token"})
    finally:
        server.shutdown()
        server.server_close()


def test_a_real_vendor_url_still_works(api, estate):
    """The point is to fetch from vendors, so this must not over-refuse."""
    headers, _ = estate

    for url in ("https://api.sensorpush.com/api/v1/samples",
                "https://example.com/readings?since=1"):
        assert source(api, headers, url=url, name=url).status_code == 201, url


def test_plain_http_is_refused(api, estate):
    """The headers are a credential, and http sends them in the clear."""
    headers, _ = estate

    refused = source(api, headers, url="http://api.sensorpush.com/samples")

    assert refused.status_code == 422


def test_a_newline_in_a_header_is_refused(api, estate):
    """It is how one request is turned into two."""
    headers, _ = estate

    refused = source(api, headers,
                     headers={"Authorization": "a\r\nX-Evil: yes"})

    assert refused.status_code == 422


def test_the_credential_never_comes_back_out(api, estate):
    """A console is logged into by an operator who should not be able to
    read the owner's vendor password off the screen."""
    headers, _ = estate
    source(api, headers)

    body = api.get("/api/pollers", headers=headers).json()
    blob = json.dumps(body)

    assert "secret-token" not in blob
    assert body["sources"][0]["header_names"] == ["Authorization"]


def test_only_an_owner_may_add_one(api, estate):
    headers, _ = estate
    key_only = {"X-CyberLogix-Key": headers["X-CyberLogix-Key"]}

    refused = source(api, key_only)

    assert refused.status_code in (401, 403)


# --- fetching -------------------------------------------------------------


def test_a_reading_from_a_vendor_cloud_becomes_a_reading(api, estate, monkeypatch):
    headers, tenant = estate
    made = source(api, headers).json()["source"]

    monkeypatch.setattr(pollers, "_fetch", lambda url, h: {
        "samples": [{"sensor": "SP-0001", "temperature": 41.0,
                     "observed": "2026-09-20T10:00:00Z"}]})
    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["status"] == "ok"
    assert out["result"]["ingested"] == 1
    assert out["source"]["readings_ingested"] == 1
    assert STORE.readings_for("FRZ-1")


def test_the_vendor_being_down_is_not_a_crash(api, estate, monkeypatch):
    """A vendor being unreachable is a Tuesday. The scheduler must not
    care, and the console must be able to say which source is broken."""
    headers, _ = estate
    made = source(api, headers).json()["source"]

    def dead(url, h):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(pollers, "_fetch", dead)
    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["status"] == "failed"
    assert "Could not reach the vendor" in out["result"]["detail"]
    assert out["source"]["consecutive_failures"] == 1


def test_a_login_page_instead_of_json_says_what_that_means(api, estate,
                                                           monkeypatch):
    headers, _ = estate
    made = source(api, headers).json()["source"]

    def html(url, h):
        raise json.JSONDecodeError("no", "<html>", 0)

    monkeypatch.setattr(pollers, "_fetch", html)
    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert "credential is wrong or expired" in out["result"]["detail"]


def test_repeated_failure_is_called_broken_rather_than_quiet(api, estate,
                                                             monkeypatch):
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch",
                        lambda u, h: (_ for _ in ()).throw(ValueError("nope")))

    for _ in range(pollers.FAILURES_BEFORE_BROKEN):
        api.post(f"/api/pollers/{made['source_id']}/test", headers=headers)

    body = api.get("/api/pollers", headers=headers).json()
    assert body["broken"] == 1
    assert body["sources"][0]["broken"] is True
    assert "Nothing is arriving" in body["note"]


def test_one_success_clears_the_failure_count(api, estate, monkeypatch):
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch",
                        lambda u, h: (_ for _ in ()).throw(ValueError("nope")))
    api.post(f"/api/pollers/{made['source_id']}/test", headers=headers)

    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {
        "samples": [{"sensor": "SP-0001", "temperature": 40.0}]})
    api.post(f"/api/pollers/{made['source_id']}/test", headers=headers)

    body = api.get("/api/pollers", headers=headers).json()
    assert body["sources"][0]["consecutive_failures"] == 0


@pytest.mark.parametrize("shape", [
    [{"sensor": "SP-0001", "temperature": 40.0}],
    {"samples": [{"sensor": "SP-0001", "temperature": 40.0}]},
    {"data": [{"sensor": "SP-0001", "temperature": 40.0}]},
    {"sensor": "SP-0001", "temperature": 40.0},
    {"sensors": {"SP-0001": {"temperature_f": 40.0}}},
])
def test_it_reads_the_shapes_vendors_actually_return(api, estate,
                                                     monkeypatch, shape):
    """A list, a wrapper, one reading, or a dict keyed by device. All
    four are common and none is wrong, so all four are handled rather
    than one being declared the format."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: shape)

    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["ingested"] == 1, out["result"]


def test_an_unregistered_serial_is_skipped_not_fatal(api, estate, monkeypatch):
    """One unknown device in a batch must not lose the rest of it."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {"samples": [
        {"sensor": "WHO-IS-THIS", "temperature": 40.0},
        {"sensor": "SP-0001", "temperature": 40.0},
    ]})

    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["ingested"] == 1
    assert "skipped" in out["result"]["detail"]


def test_a_reading_with_no_unit_is_never_guessed(api, estate, monkeypatch):
    """Four degrees is a fridge in Celsius and a disaster in Fahrenheit."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch",
                        lambda u, h: {"samples": [{"sensor": "SP-0001",
                                                   "reading": 4.0}]})

    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["ingested"] == 0


def test_a_keyed_shape_that_loses_its_unit_is_refused_rather_than_guessed(
        api, estate, monkeypatch):
    """A dict keyed by device carries the serial in the key and often
    nothing about units in the row. "temperature: 40" is a fridge in
    Fahrenheit and a hot room in Celsius, and the vendor preset that
    would have supplied the unit does not match a reshaped row -- so
    this is exactly where a guess would be most tempting and most
    wrong."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch",
                        lambda u, h: {"sensors": {"SP-0001":
                                                  {"temperature": 40.0}}})

    out = api.post(f"/api/pollers/{made['source_id']}/test",
                   headers=headers).json()

    assert out["result"]["ingested"] == 0
    assert out["result"]["status"] == "nothing_usable"


# --- the schedule ---------------------------------------------------------


def test_a_new_source_is_polled_at_once(api, estate):
    """Adding one and waiting ten minutes for nothing to happen is how
    somebody concludes it does not work."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])

    assert pollers.due(row) is True


def test_a_disabled_source_is_never_polled(api, estate):
    headers, _ = estate
    made = source(api, headers, enabled=False).json()["source"]
    row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])

    assert pollers.due(row) is False


def test_it_waits_its_interval_between_polls(api, estate, monkeypatch):
    from datetime import timedelta

    from store import utc_now

    headers, _ = estate
    made = source(api, headers, interval_minutes=10).json()["source"]
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {"samples": []})
    api.post(f"/api/pollers/{made['source_id']}/test", headers=headers)
    row = STORE._db.get(pollers.SOURCE_KIND, made["source_id"])

    assert pollers.due(row, utc_now() + timedelta(minutes=4)) is False
    assert pollers.due(row, utc_now() + timedelta(minutes=11)) is True


def test_one_estate_cannot_touch_anothers_source(api, estate, tenant_factory,
                                                 owner_headers):
    headers, _ = estate
    mine = source(api, headers).json()["source"]
    other, _ = tenant_factory(plan="enterprise", company_name="Other")
    other = {**other, **owner_headers(other, email="other@ex.com")}

    assert api.get("/api/pollers", headers=other).json()["count"] == 0
    assert api.post(f"/api/pollers/{mine['source_id']}/test",
                    headers=other).status_code == 404
    assert api.delete(f"/api/pollers/{mine['source_id']}",
                      headers=other).status_code == 404


# --- one slow vendor must not hold up the alarms ---------------------------


def test_slow_vendors_do_not_hold_up_the_sweep(api, estate, monkeypatch):
    """The pass used to fetch one source after another just ahead of the
    sweep that phones people, so a handful of stuck vendors delayed every
    escalation in the fleet by minutes."""
    import threading
    import time as _time

    headers, _ = estate
    for i in range(4):
        assert source(api, headers, name=f"Vendor {i}").status_code == 201
    release = threading.Event()

    def stuck(url, h):
        release.wait(10)
        return {"samples": []}

    monkeypatch.setattr(pollers, "_fetch", stuck)
    monkeypatch.setattr(pollers, "POLL_PASS_SECONDS", 0.5)
    started = _time.monotonic()
    first = pollers.run_poll_pass()
    assert _time.monotonic() - started < 3
    assert first["still_running"] == 4
    # Still being fetched: the next pass does not start them a second time.
    assert pollers.run_poll_pass()["still_running"] == 0
    release.set()
    for _ in range(50):
        if not pollers._in_flight:
            break
        _time.sleep(0.05)
    assert not pollers._in_flight


def test_sources_run_side_by_side(api, estate, monkeypatch):
    import threading

    headers, _ = estate
    for i in range(3):
        source(api, headers, name=f"Vendor {i}")
    seen, lock, gate = set(), threading.Lock(), threading.Barrier(3, timeout=5)

    def together(url, h):
        gate.wait()            # only returns if all three are in flight at once
        with lock:
            seen.add(threading.get_ident())
        return {"samples": []}

    monkeypatch.setattr(pollers, "_fetch", together)
    out = pollers.run_poll_pass()
    assert out["polled"] == 3 and len(seen) == 3


def test_an_account_has_a_ceiling_on_sources(api, estate):
    headers, _ = estate
    for i in range(pollers.MAX_SOURCES_PER_TENANT):
        assert source(api, headers, name=f"Vendor {i}").status_code == 201
    assert source(api, headers, name="One too many").status_code == 409


def test_a_trickling_vendor_hits_the_deadline(estate, monkeypatch):
    class Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b" "

    monkeypatch.setattr(pollers, "_resolve_to_public", lambda host: None)
    monkeypatch.setattr(pollers._OPENER, "open", lambda request, timeout: Trickle())
    monkeypatch.setattr(pollers, "FETCH_DEADLINE_SECONDS", 0.2)
    with pytest.raises(TimeoutError):
        pollers._fetch("https://sensors.example/samples", {})


def test_an_unexpected_fault_does_not_show_its_insides(api, estate, monkeypatch):
    headers, _ = estate
    made = source(api, headers).json()["source"]

    def broken(url, h):
        raise KeyError("/srv/app/internal_module.py secret_thing")

    monkeypatch.setattr(pollers, "_fetch", broken)
    out = api.post(f"/api/pollers/{made['source_id']}/test", headers=headers).json()
    assert out["result"]["status"] == "failed"
    assert "internal_module" not in out["result"]["detail"]


def test_a_vendor_trickling_its_headers_is_cut_off(monkeypatch):
    """The deadline used to be checked only between body chunks, so headers
    sent a byte at a time held a worker for as long as the vendor liked."""
    import socket
    import threading
    import time as _time

    monkeypatch.setattr(pollers, "_resolve_to_public", lambda host: None)
    # Each byte comes well inside the per-read timeout, so only the overall
    # deadline can end it.
    monkeypatch.setattr(pollers, "TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(pollers, "FETCH_DEADLINE_SECONDS", 0.6)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
        try:
            for _ in range(40):
                _time.sleep(0.15)
                c.sendall(b"a")
        except OSError:
            pass
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    started = _time.monotonic()
    with pytest.raises(TimeoutError):
        pollers._fetch(f"http://127.0.0.1:{port}/x", {})
    assert _time.monotonic() - started < 3
    srv.close()


def test_a_source_removed_during_a_fetch_stays_removed(api, estate, monkeypatch):
    import threading
    import time as _time

    headers, _ = estate
    made = source(api, headers).json()["source"]
    release = threading.Event()

    def slow(url, h):
        release.wait(5)
        return {"samples": [{"sensor": "SP-0001", "temperature": 41.0}]}

    monkeypatch.setattr(pollers, "_fetch", slow)
    monkeypatch.setattr(pollers, "POLL_PASS_SECONDS", 0.2)
    assert pollers.run_poll_pass()["still_running"] == 1
    assert api.delete(f"/api/pollers/{made['source_id']}", headers=headers).status_code == 204
    release.set()
    for _ in range(50):
        if not pollers._in_flight:
            break
        _time.sleep(0.05)
    assert STORE._db.get(pollers.SOURCE_KIND, made["source_id"]) is None
    assert not STORE.readings_for("FRZ-1")


# --- the same reading, fetched twice ----------------------------------------


def _poll(api, headers, source_id):
    return api.post(f"/api/pollers/{source_id}/test", headers=headers).json()


def test_a_reading_already_taken_is_not_taken_again(api, estate, monkeypatch):
    """A vendor's "latest" endpoint keeps answering with the last reading
    it has, however old. Taken again on every poll, a sensor whose battery
    died read as alive for ever (it never went offline, so nobody was told)
    and one old warm reading re-opened the incident each time somebody
    resolved it, texting the whole roster again for a fault already dealt
    with."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    stale = {"samples": [{"sensor": "SP-0001", "temperature": 55.0,
                          "observed": "2026-09-20T10:00:00Z"}]}
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: stale)

    first = _poll(api, headers, made["source_id"])
    assert first["result"]["ingested"] == 1
    incident = STORE.latest_unresolved_incident("FRZ-1")
    assert incident is not None
    STORE.resolve_incident(incident, "Dana")
    before = STORE.get_sensor("FRZ-1").last_seen

    again = _poll(api, headers, made["source_id"])

    assert again["result"]["ingested"] == 0
    assert len(STORE.readings_for("FRZ-1")) == 1
    assert STORE.get_sensor("FRZ-1").last_seen == before
    assert STORE.latest_unresolved_incident("FRZ-1") is None
    # Nothing new is not the vendor failing: the source is not called broken.
    assert again["source"]["consecutive_failures"] == 0
    assert "nothing new" in again["result"]["detail"].lower()


def test_a_newer_reading_is_still_taken(api, estate, monkeypatch):
    headers, _ = estate
    made = source(api, headers).json()["source"]
    reply = {"samples": [{"sensor": "SP-0001", "temperature": 30.0,
                          "observed": "2026-09-20T10:00:00Z"}]}
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: reply)
    _poll(api, headers, made["source_id"])

    reply = {"samples": [{"sensor": "SP-0001", "temperature": 31.0,
                          "observed": "2026-09-20T10:10:00Z"}]}
    out = _poll(api, headers, made["source_id"])

    assert out["result"]["ingested"] == 1
    assert STORE.get_sensor("FRZ-1").last_temperature == 31.0


def test_history_newest_first_leaves_the_newest_reading_current(api, estate,
                                                               monkeypatch):
    """Vendors often list samples newest first. Taken in the order given,
    the oldest one became the sensor's current temperature."""
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {"samples": [
        {"sensor": "SP-0001", "temperature": 31.0, "observed": "2026-09-20T10:20:00Z"},
        {"sensor": "SP-0001", "temperature": 30.0, "observed": "2026-09-20T10:10:00Z"},
        {"sensor": "SP-0001", "temperature": 29.0, "observed": "2026-09-20T10:00:00Z"},
    ]})

    out = _poll(api, headers, made["source_id"])

    assert out["result"]["ingested"] == 3
    assert STORE.get_sensor("FRZ-1").last_temperature == 31.0
    # And the next poll, with the same three, takes none of them.
    assert _poll(api, headers, made["source_id"])["result"]["ingested"] == 0


@pytest.mark.parametrize("stamp", [1790000000, 1790000000000, "/Date(1790000000000)/",
                                   "2026-09-20 10:00:00", "2026-09-20T10:00:00.123+00:00"])
def test_the_usual_ways_of_writing_a_time_are_understood(api, estate, monkeypatch, stamp):
    headers, _ = estate
    made = source(api, headers).json()["source"]
    monkeypatch.setattr(pollers, "_fetch", lambda u, h: {"samples": [
        {"sensor": "SP-0001", "temperature": 30.0, "observed": stamp}]})

    assert _poll(api, headers, made["source_id"])["result"]["ingested"] == 1
    assert _poll(api, headers, made["source_id"])["result"]["ingested"] == 0
