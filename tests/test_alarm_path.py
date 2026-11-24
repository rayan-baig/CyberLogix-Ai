"""If alarms stop, food spoils: the alarm path is watched end to end.

The heartbeat (test_watchdog.py) says the sweep ran. These tests are for
the failures it cannot see, each of which leaves every console saying
the fridges are fine while no alarm can go out:

  * readings stop arriving from everybody at once;
  * the provider refuses or ignores every text, call or alert email;
  * the model that words the alert hangs, and the alert waits on it.

And for the two public checks an uptime monitor reads (/healthz and
/readyz), which must answer from states alone: no names, numbers,
versions or model names.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import timedelta

import pytest

import gemini
import mail
import notifications
import scheduler
import watchdog
from store import STORE, iso, utc_now


class _Sent:
    sid = "SM-test"
    status = "queued"


class _Twilio:
    """A Twilio client that answers, or fails the way it is told to."""

    def __init__(self):
        self.fail_with = None
        self.sent = []

    @property
    def messages(self):
        return self

    @property
    def calls(self):
        return self

    def create(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(kwargs)
        return _Sent()


class _TwilioError(Exception):
    """Shaped like twilio's TwilioRestException: a status and a code."""

    def __init__(self, status, code):
        super().__init__(f"HTTP {status} error: {code}")
        self.status, self.code = status, code


@pytest.fixture()
def twilio(monkeypatch):
    fake = _Twilio()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", fake)
    monkeypatch.setattr(notifications, "_client_error", None)
    return fake


@pytest.fixture()
def swept(monkeypatch):
    """A deployment whose sweep runs every minute and ran just now."""
    monkeypatch.setattr(scheduler, "_interval", lambda: 60)
    STORE._db.put("meta", "heartbeat", {"last_sweep_at": iso(utc_now()), "sweeps": 1})


@pytest.fixture()
def listening(monkeypatch):
    """A process that has been up long enough to have heard a reading."""
    monkeypatch.setattr(watchdog, "_STARTED", utc_now() - timedelta(hours=2))


def _ready(api):
    watchdog.forget_readiness()
    return api.get("/readyz")


# ---- /healthz -------------------------------------------------------------


def test_healthz_says_the_process_is_up_and_nothing_else(api):
    resp = api.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---- /readyz ------------------------------------------------------------------


def test_ready_when_the_sweep_ran_and_texts_are_on(api, twilio, swept):
    resp = _ready(api)
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["ready"] is True
    assert body["problems"] == []
    assert body["checks"]["alert_job"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["texts"] == "no_sends_yet"
    assert resp.headers["cache-control"] == "no-store"


def test_not_ready_when_the_alert_job_has_stopped(api, twilio, monkeypatch):
    monkeypatch.setattr(scheduler, "_interval", lambda: 60)
    STORE._db.put("meta", "heartbeat", {
        "last_sweep_at": iso(utc_now() - timedelta(minutes=30)), "sweeps": 9})
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["alert_job"] == "stopped"
    assert any("alert job has stopped" in p for p in resp.json()["problems"])


def test_not_ready_when_twilio_is_not_set_up(api, swept):
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["texts"] == "off"
    assert any("Twilio is not set up" in p for p in resp.json()["problems"])


def test_a_staging_copy_without_twilio_can_still_be_ready(api, swept, monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_NOINDEX", "1")
    resp = _ready(api)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["checks"]["texts"] == "off"


def test_not_ready_when_readings_stop_arriving_from_everybody(
        api, twilio, swept, listening, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, "WALKIN-1", "restaurant", "Kitchen")
    sensor = STORE.get_sensor("WALKIN-1")
    STORE.record_reading(sensor, 36.0, None, False,
                         at=utc_now() - timedelta(minutes=watchdog.INGEST_STALE_MINUTES + 5))
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["sensor_readings"] == "stopped"

    STORE.record_reading(sensor, 36.0, None, False)
    resp = _ready(api)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["checks"]["sensor_readings"] == "ok"


def test_a_quiet_fleet_right_after_a_restart_is_not_a_problem(
        api, twilio, swept, tenant_factory, sensor_factory, monkeypatch):
    """After a deploy every last reading is as old as the deploy took."""
    monkeypatch.setattr(watchdog, "_STARTED", utc_now() - timedelta(minutes=2))
    headers, _ = tenant_factory()
    sensor_factory(headers, "WALKIN-1", "restaurant", "Kitchen")
    STORE.record_reading(STORE.get_sensor("WALKIN-1"), 36.0, None, False,
                         at=utc_now() - timedelta(hours=1))
    assert _ready(api).status_code == 200


def test_no_sensors_yet_is_ready(api, twilio, swept, listening):
    resp = _ready(api)
    assert resp.status_code == 200
    assert resp.json()["checks"]["sensor_readings"] == "no_sensors_yet"


def test_not_ready_when_texts_keep_failing_at_the_provider(api, twilio, swept):
    twilio.fail_with = ConnectionError("Twilio did not answer")
    for _ in range(watchdog.FAILING_AFTER):
        assert notifications.send_sms("+15550123", "ALERT")["delivered"] is False
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["texts"] == "failing"

    twilio.fail_with = None
    assert notifications.send_sms("+15550123", "ALERT")["delivered"] is True
    resp = _ready(api)
    assert resp.status_code == 200
    assert resp.json()["checks"]["texts"] == "ok"


def test_failing_calls_are_counted_too(api, twilio, swept):
    twilio.fail_with = _TwilioError(503, 20500)
    for _ in range(watchdog.FAILING_AFTER):
        notifications.place_voice_call("+15550123", "Check the freezer.")
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["calls"] == "failing"


def test_one_businesss_bad_numbers_are_not_an_outage(api, twilio, swept):
    """Twilio saying a number is invalid is the roster, not the channel."""
    twilio.fail_with = _TwilioError(400, 21211)
    for _ in range(watchdog.FAILING_AFTER + 2):
        notifications.send_sms("+15550199", "ALERT")
    assert _ready(api).status_code == 200


def test_alert_email_that_the_mail_host_refuses_is_counted(api, twilio, swept, mailbox):
    mailbox.fail_times = watchdog.FAILING_AFTER
    for n in range(watchdog.FAILING_AFTER):
        mail.send(to_address="owner@example.com", subject="ALERT", body="x",
                  dedupe_key=f"incident-alert:INC-{n}")
    resp = _ready(api)
    assert resp.status_code == 503
    assert resp.json()["checks"]["email"] == "failing"


def test_a_broken_chat_hook_is_shown_but_never_makes_it_503(api, twilio, swept):
    for _ in range(watchdog.FAILING_AFTER):
        watchdog.note_delivery("webhook", False)
    resp = _ready(api)
    assert resp.status_code == 200
    assert resp.json()["checks"]["webhooks"] == "failing"


def test_the_answer_leaks_nothing(api, twilio, swept, tenant_factory, sensor_factory,
                                  monkeypatch):
    from version import __version__

    monkeypatch.setattr(gemini, "GEMINI_MODEL", "sentinel-model-42")
    headers, tenant = tenant_factory(company_name="Secret Sauce Bistro")
    sensor_factory(headers, "WALKIN-9", "restaurant", "Basement walk-in")
    twilio.fail_with = ConnectionError("refused for +15550123 at secret-host")
    for _ in range(watchdog.FAILING_AFTER):
        notifications.send_sms("+15550123", "ALERT")
    text = _ready(api).text
    for secret in ("Secret Sauce", "WALKIN-9", "Basement", "+1555", "secret-host",
                   "sentinel-model-42", tenant["tenant_id"], __version__,
                   "AC-test", "token"):
        assert secret not in text, secret


def test_hammering_it_is_cheap(api, twilio, swept, monkeypatch):
    calls = []
    real = watchdog.readiness
    monkeypatch.setattr(watchdog, "readiness", lambda: calls.append(1) or real())
    watchdog.forget_readiness()
    for _ in range(50):
        assert api.get("/readyz").status_code == 200
    assert len(calls) == 1


# ---- telling the owner ---------------------------------------------------------


@pytest.fixture()
def owner_contact(monkeypatch):
    import digest

    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "owner@kryozyn.example")
    monkeypatch.setattr(watchdog, "OPERATOR_PHONE", "+15550777")


def test_the_owner_is_told_once_when_texts_stop_and_once_when_they_are_back(
        twilio, swept, mailbox, owner_contact):
    twilio.fail_with = ConnectionError("down")
    for _ in range(watchdog.FAILING_AFTER):
        notifications.send_sms("+15550123", "ALERT")

    first = watchdog.check_alarm_path()
    assert first["started"] == ["texts_failing"]
    assert watchdog.check_alarm_path()["started"] == []   # not again
    assert watchdog.check_alarm_path()["started"] == []
    told = mailbox.to("owner@kryozyn.example")
    assert len(told) == 1
    assert "texts are failing" in told[0]["Subject"].lower()
    assert "If the app is down" in mailbox.bodies()[0]

    twilio.fail_with = None
    notifications.send_sms("+15550123", "ALERT")
    assert watchdog.check_alarm_path()["ended"] == ["texts_failing"]
    assert watchdog.check_alarm_path()["ended"] == []
    told = mailbox.to("owner@kryozyn.example")
    assert len(told) == 2
    assert told[1]["Subject"].startswith("Fixed:")
    # And a text to the owner's own phone: tried when texts were down (the
    # email is what reached them then), and sent once they were back.
    to_owner = [m for m in twilio.sent if m.get("to") == "+15550777"]
    assert [m["body"] for m in to_owner] == [
        "Kryozyn: Fixed: alert texts are going out again"]


def test_what_was_said_survives_a_restart(twilio, swept, mailbox, owner_contact):
    """Kept in the database, so a restart does not send it all again."""
    for _ in range(watchdog.FAILING_AFTER):
        watchdog.note_delivery("email", False)
    assert watchdog.check_alarm_path()["started"] == ["email_failing"]
    kept = STORE._db.get("meta", "alarm_watch")
    assert "email_failing" in kept["open"]
    # A new process: the counts are gone from memory, so the problem reads
    # as cleared -- which is told once, not as a new start.
    watchdog.forget_deliveries()
    for _ in range(watchdog.FAILING_AFTER):
        watchdog.note_delivery("email", False)
    assert watchdog.check_alarm_path()["started"] == []


def test_a_hung_sweep_is_noticed_by_the_watcher(twilio, mailbox, owner_contact,
                                                monkeypatch):
    monkeypatch.setattr(scheduler, "_interval", lambda: 60)
    STORE._db.put("meta", "heartbeat", {
        "last_sweep_at": iso(utc_now() - timedelta(minutes=20)), "sweeps": 3})
    assert "sweep_stopped" in watchdog.check_alarm_path()["started"]
    assert any("alert job has stopped" in s.lower()
               for s in mailbox.subjects())


def test_the_watcher_runs_on_its_own_thread(twilio, swept, monkeypatch):
    seen = threading.Event()
    monkeypatch.setattr(watchdog, "check_alarm_path", lambda: seen.set())
    monkeypatch.setenv("CYBERLOGIX_ALARM_WATCH_SECONDS", "1")
    try:
        assert watchdog.start_watching() is True
        assert seen.wait(5), "the watcher never looked"
    finally:
        watchdog.stop_watching()


def test_the_watcher_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_ALARM_WATCH_SECONDS", "0")
    assert watchdog.start_watching() is False


def test_the_daily_digest_lists_the_problem(twilio, swept):
    for _ in range(watchdog.FAILING_AFTER):
        watchdog.note_delivery("voice", False)
    assert any("phone calls are failing" in w for w in watchdog.warnings())


# ---- the model never holds an alert back ----------------------------------------


class _HangingModels:
    def __init__(self, seconds):
        self.seconds = seconds

    def generate_content(self, model, contents):
        time.sleep(self.seconds)
        return type("Resp", (), {"text": "Check the door seal."})()


def test_a_model_that_hangs_gets_the_template_on_time(monkeypatch):
    monkeypatch.setattr(gemini, "client", type("C", (), {"models": _HangingModels(3)})())
    monkeypatch.setattr(gemini, "AI_TIMEOUT_SECONDS", 0.3)
    started = time.monotonic()
    text, source = gemini.safe_generate("p-hang", "THE TEMPLATE", "emergency SMS")
    took = time.monotonic() - started
    assert (text, source) == ("THE TEMPLATE", "fallback_template")
    assert took < 2.0, took


def test_the_breach_text_goes_out_while_the_model_hangs(
        api, twilio, tenant_factory, sensor_factory, monkeypatch):
    monkeypatch.setattr(gemini, "client", type("C", (), {"models": _HangingModels(3)})())
    monkeypatch.setattr(gemini, "AI_TIMEOUT_SECONDS", 0.3)
    headers, _ = tenant_factory()
    sensor_factory(headers, "FRZ-1", "restaurant", "Kitchen freezer")
    started = time.monotonic()
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 60.0})
    took = time.monotonic() - started
    assert resp.status_code in (200, 201), resp.text
    assert twilio.sent, "the alert text went out"
    assert took < 2.5, took


def test_the_sdk_client_is_built_with_a_timeout():
    assert gemini.http_options() == {"timeout": int(gemini.AI_TIMEOUT_SECONDS * 1000)}
    assert 0 < gemini.AI_TIMEOUT_SECONDS <= 30


def test_no_model_named_means_templates_not_a_crash(monkeypatch):
    monkeypatch.setattr(gemini, "GEMINI_MODEL", "")
    assert gemini.safe_generate("p-none", "TEMPLATE", "x") == ("TEMPLATE",
                                                               "fallback_template")
    assert gemini.dispatch_ready() is False


# ---- the console loads fast ----------------------------------------------------


def test_text_is_compressed_for_a_browser_that_asks(api):
    with api.stream("GET", "/static/console.js",
                    headers={"Accept-Encoding": "gzip"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-encoding"] == "gzip"
        wire = b"".join(resp.iter_raw())
    raw = api.get("/static/console.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers
    assert len(wire) < len(raw.content) / 3


def test_the_console_names_its_scripts_and_styles_by_version(api):
    page = api.get("/console").text
    refs = re.findall(r'(?:src|href)="(/static/[^"]+?\.(?:js|css)(?:\?[^"]*)?)"', page)
    assert refs
    from main import STATIC_DIR

    for ref in refs:
        path, _, query = ref.partition("?v=")
        assert query, f"{ref} has no version"
        digest = hashlib.sha256((STATIC_DIR / path[len("/static/"):]).read_bytes())
        assert query == digest.hexdigest()[:12]


def test_a_versioned_file_is_kept_and_a_plain_one_is_checked(api):
    page = api.get("/console").text
    script = re.search(r'src="(/static/console\.js\?v=[0-9a-f]+)"', page).group(1)
    kept = api.get(script)
    assert kept.headers["cache-control"] == "public, max-age=31536000, immutable"
    plain = api.get("/static/console.js")
    assert "immutable" not in plain.headers.get("cache-control", "")
    again = api.get("/static/console.js", headers={"If-None-Match": plain.headers["etag"]})
    assert again.status_code == 304


def test_opening_the_console_again_costs_a_304(api):
    first = api.get("/console")
    etag = first.headers["etag"]
    again = api.get("/console", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers.get("etag") == etag
    # Still checked with the server every time it is opened.
    assert first.headers["cache-control"] == "no-cache"


def test_pages_rendered_with_values_get_versions_too(api):
    page = api.get("/").text
    assert re.search(r'href="/static/theme\.css\?v=[0-9a-f]{12}"', page)


def test_json_is_compressed_too(api):
    with api.stream("GET", "/openapi.json",
                    headers={"Accept-Encoding": "gzip"}) as resp:
        assert resp.headers.get("content-encoding") == "gzip"
        wire = b"".join(resp.iter_raw())
    assert len(wire) < len(api.get("/openapi.json").content) / 3


def test_icons_keep_their_plain_address(api):
    """The manifest and link previews name them too."""
    page = api.get("/console").text
    assert 'href="/static/icons/icon-180.png"' in page


def test_an_open_console_fetches_the_slow_cards_less_often():
    """28 requests every 20 seconds per open console, most of them for
    cards that only change when somebody changes them."""
    from main import STATIC_DIR

    js = (STATIC_DIR / "console.js").read_text(encoding="utf-8")
    body = js[js.index("async function refresh("):js.index("/* ---------------- render")]
    wanted = set(re.findall(r"\n    (\w+): \(\) =>", body))
    slow = set(re.findall(r'"(\w+)"', js[js.index("const SLOW_PANELS"):
                                         js.index("const SLOW_EVERY")]))
    assert slow and slow <= wanted, slow - wanted
    # What can change on its own between two looks is fetched every time.
    for live in ("escalation", "tasks", "knocking", "byodSeen", "pollers"):
        assert live in wanted and live not in slow, live
    # Only the timer asks for the light refresh; a person's action reloads all.
    assert "refresh({ poll: true })" in js
    assert js.count("refresh({ poll: true })") == 1
    assert int(re.search(r"const SLOW_EVERY = (\d+);", js).group(1)) <= 5
