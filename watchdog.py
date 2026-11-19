"""Who watches the thing that watches the freezers.

The product's entire promise is that something is running when nobody is
looking. Until now, nothing checked that it was. If the process died at
2am — OOM, a bad deploy, the host rebooting — no sweep ran, no breach
was escalated, no invoice was issued, and nobody found out. Every
customer's console still said ONLINE, because the console reads the
database and the database was fine. A monitoring product that fails
silently is worse than no monitoring product: the customer has stopped
checking the freezer themselves.

The hard part is that **a deadman switch inside the process cannot
report its own death**. Anything that lives here dies with it. So this
module does two different jobs, and only the second one actually saves
you:

1. **A heartbeat, published.** Each sweep writes down that it happened.
   `GET /api/watchdog` reports how long ago, and answers **503** when the
   sweep has gone stale. 503 rather than a JSON field, because the
   dumbest possible external monitor — the free tier of any uptime
   service, a one-line cron with `curl -f` — understands a status code
   and nothing else. A liveness endpoint that requires the checker to
   parse it will not be checked.

2. **A ping, outbound.** After each successful sweep the app calls
   `CYBERLOGIX_HEARTBEAT_URL`. The alarm is the *absence* of that call,
   and it is armed by a service that is not this one. This is the only
   half that survives the machine going away entirely, which is the
   failure the whole module is about.

There is a third failure between the two, and it is the sneakiest: the
web process serving requests perfectly while the sweep task inside it
has died. Everything looks alive, `/api/health` is 200, and no alarm has
been raised in six hours. The heartbeat is written by the sweep rather
than by the request handler precisely so that this state is visible —
and the money pass, which runs on its own timer, checks the alert
sweep's heartbeat and shouts when it has gone quiet.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Response

from store import STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.watchdog")

router = APIRouter(prefix="/api/watchdog", tags=["Watchdog"])

# Where the app pings after every successful sweep. The service on the
# other end alarms when the pings stop. Any of the dead-man's-switch
# services take a plain GET; so does a self-hosted one.
HEARTBEAT_URL = os.environ.get("CYBERLOGIX_HEARTBEAT_URL", "").strip()
HEARTBEAT_TIMEOUT_SECONDS = float(
    os.environ.get("CYBERLOGIX_HEARTBEAT_TIMEOUT_SECONDS", "5")
)

# How late a sweep has to be before it counts as stopped. Generous
# against the configured interval rather than absolute: a deployment
# sweeping every five minutes should not be called dead at six.
#
# Three missed sweeps, floored at four minutes so that the default
# sixty-second cadence does not alarm on one slow pass while SQLite is
# checkpointing. An alarm that cries wolf gets muted, and a muted alarm
# is the same as no alarm.
STALE_AFTER_MULTIPLE = 3
STALE_FLOOR_SECONDS = 240

# The money pass runs hourly, so it is allowed to be much later before
# anybody worries. Nothing about being an hour late on an invoice is an
# emergency; a day late is.
MONEY_STALE_AFTER_SECONDS = 6 * 3600

_KIND = "meta"
_ID = "heartbeat"


def _read() -> Dict[str, Any]:
    return STORE._db.get(_KIND, _ID) or {}


def record_sweep(result: Optional[Dict[str, Any]] = None) -> None:
    """Write down that the alert sweep ran, and ping the outside world.

    Called by the sweep itself, never by a request handler. That is the
    whole point: a process that answers HTTP while its sweep task is
    dead must look dead here, because for every freezer on the fleet it
    is.
    """
    now = utc_now()
    state = _read()
    state["last_sweep_at"] = iso(now)
    state["sweeps"] = int(state.get("sweeps", 0)) + 1
    if result is not None:
        state["last_sweep_tenants"] = result.get("tenants_swept", 0)
        state["last_sweep_failures"] = len(result.get("failed_tenants", []))
    STORE._db.put(_KIND, _ID, state)
    ping()


def record_money_pass() -> None:
    """Write down that the billing and collections pass ran."""
    state = _read()
    state["last_money_pass_at"] = iso(utc_now())
    state["money_passes"] = int(state.get("money_passes", 0)) + 1
    STORE._db.put(_KIND, _ID, state)


def _age_seconds(stamp: Optional[str], now: datetime) -> Optional[float]:
    if not stamp:
        return None
    from store import _parse

    moment = _parse(stamp)
    return (now - moment).total_seconds() if moment else None


def _interval() -> int:
    import scheduler

    return scheduler._interval()


def _external_interval() -> int:
    """How often an external scheduler is expected to call /sweep-all.

    Only used when the built-in loop is off. Five minutes unless told."""
    try:
        return max(1, int(os.environ.get("CYBERLOGIX_EXTERNAL_SWEEP_SECONDS", "300")))
    except ValueError:
        return 300


def stale_after_seconds() -> float:
    """How long a silence has to last before it means something."""
    interval = _interval()
    if interval <= 0:
        interval = _external_interval()
    return max(interval * STALE_AFTER_MULTIPLE, STALE_FLOOR_SECONDS)


def state(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Liveness, and deliberately nothing else.

    No tenant counts, no revenue, no addresses. This endpoint is meant to
    be readable by an uptime service that is not us and holds no
    credential of ours, so what it returns has to be safe in a stranger's
    logs. How long ago something ran is not a business secret; how many
    customers there are is.
    """
    import scheduler

    now = now or utc_now()
    stored = _read()
    sweep_age = _age_seconds(stored.get("last_sweep_at"), now)
    money_age = _age_seconds(stored.get("last_money_pass_at"), now)
    limit = stale_after_seconds()

    enabled = scheduler.status()["enabled"]
    # Never swept and the loop is off is not a fault: an external
    # scheduler drives it, and it may simply not have fired yet. But once
    # it has fired, it is held to the same limit: an external scheduler
    # that stops is the same silence as a loop that dies.
    sweep_stale = (sweep_age is None or sweep_age > limit) if enabled else (
        sweep_age is not None and sweep_age > limit
    )
    money_stale = money_age is not None and money_age > MONEY_STALE_AFTER_SECONDS

    reasons = []
    if sweep_stale:
        reasons.append(
            "the alert sweep has not run"
            + (f" for {int(sweep_age)}s" if sweep_age is not None else " at all")
            + f"; anything above {int(limit)}s means escalation has stopped"
        )
    if money_stale:
        reasons.append(
            f"the billing pass has not run for {int(money_age)}s; nothing is "
            "being invoiced or chased"
        )

    return {
        "healthy": not reasons,
        "reasons": reasons,
        "sweep_stale": sweep_stale,
        "sweep_enabled": enabled,
        "sweep_interval_seconds": _interval(),
        "seconds_since_sweep": None if sweep_age is None else int(sweep_age),
        "seconds_since_money_pass": None if money_age is None else int(money_age),
        "stale_after_seconds": int(limit),
        "heartbeat_url_configured": bool(HEARTBEAT_URL),
        "checked_at": iso(now),
        "note": (
            "This endpoint answers 503 when the sweep has stopped, so a "
            "checker that reads only the status code still works. It "
            "cannot tell you the process is gone — nothing inside a "
            "process can. Set CYBERLOGIX_HEARTBEAT_URL so the alarm is "
            "the absence of a ping, armed somewhere that is not here."
        ),
    }


def ping() -> Dict[str, Any]:
    """Tell the outside world we are still here. Never raises.

    A failure to ping must never affect the sweep that triggered it: the
    freezers matter more than the telemetry about the freezers. It is
    logged loudly, because a ping that has been quietly failing means the
    alarm is disarmed and nobody knows.
    """
    if not HEARTBEAT_URL:
        return {"pinged": False, "status": "not_configured"}

    parsed = urllib.parse.urlsplit(HEARTBEAT_URL)
    if parsed.scheme not in ("http", "https"):
        logger.error(
            "CYBERLOGIX_HEARTBEAT_URL is not an http(s) URL, so the dead "
            "man's switch is not armed."
        )
        return {"pinged": False, "status": "bad_url"}

    try:
        request = urllib.request.Request(
            HEARTBEAT_URL, method="GET", headers={"User-Agent": "CyberLogix/1"}
        )
        with urllib.request.urlopen(
            request, timeout=HEARTBEAT_TIMEOUT_SECONDS
        ) as response:
            code = response.status
    except Exception as exc:  # noqa: BLE001 - the sweep must not care
        logger.error(
            "Heartbeat ping failed (%s). If this keeps happening the dead "
            "man's switch will fire while nothing is actually wrong — or "
            "worse, has already been disarmed.", exc,
        )
        return {"pinged": False, "status": "failed", "detail": str(exc)}

    return {"pinged": True, "status": code}


def warnings(now: Optional[datetime] = None) -> list:
    """For the operator digest. Empty when everything is running."""
    current = state(now)
    if (current["healthy"] and current["heartbeat_url_configured"]
            and not problems(now)):
        return []

    found = list(current["reasons"])
    for key, sentence in problems(now):
        if key not in ("sweep_stopped",):  # already in the reasons above
            found.append(sentence)
    if not current["heartbeat_url_configured"]:
        found.append(
            "No CYBERLOGIX_HEARTBEAT_URL is set, so if this process dies "
            "nothing will tell you. This email is written by the process."
        )
    return found


@router.get("")
def read_state(response: Response):
    """Liveness for an external checker. 503 when the sweep has stopped.

    Unauthenticated on purpose. The thing that needs to read this is an
    uptime service that must not hold a credential of ours, and what it
    returns is timings rather than anything about the business. Handing
    a third party the platform admin key so it could check we were alive
    would be a far worse trade.
    """
    current = state()
    if not current["healthy"]:
        response.status_code = 503
    return current



# --- is the alarm path working ----------------------------------------------
#
# The heartbeat above says the sweep ran. It does not say that a reading
# has arrived from anybody lately, or that a text handed to Twilio was
# taken. Either can stop while the sweep runs every minute on time: the
# sensor gateway's address changes, a vendor cloud stops answering, the
# Twilio account is suspended for an unpaid bill. Every console says the
# fridges are fine, because the last readings were fine, and no alarm can
# go out. So three more things are watched, and the operator is told,
# by email and text, when one stops -- from a thread of its own, not the
# sweep's, so a sweep that hangs is noticed too.

# Fleet-wide silence that means readings have stopped arriving. One
# sensor going quiet is the business's own alert (SENSOR-OFFLINE.md);
# every sensor quiet at once is ours.
INGEST_STALE_MINUTES = max(5, int(
    os.environ.get("CYBERLOGIX_INGEST_STALE_MINUTES", "20").strip() or 20))
# A sensor that has reported in the last day counts as one that should
# still be reporting. Older than that, it was unplugged or retired.
ACTIVE_WITHIN_HOURS = 24

# Attempts in a row that the provider refused or did not answer before a
# channel counts as failing. A number the provider says is wrong is the
# business's roster, not an outage, and is not counted.
FAILING_AFTER = 3

CHANNELS = ("sms", "voice", "email", "webhook")

# When this process started. Readings are only "stopped" once we have been
# listening long enough to have heard one: after a deploy, every sensor's
# last reading is as old as the deploy took.
_STARTED = utc_now()

_flow_lock = threading.Lock()
_flow: Dict[str, Dict[str, Any]] = {}


def note_delivery(channel: str, ok: bool, counts: bool = True) -> None:
    """Count one real attempt on an outbound channel. Never raises.

    `counts=False` for a failure that says nothing about the channel (the
    provider refused one number as invalid); a success always counts.
    Kept in memory: after a restart every channel starts clean.
    """
    if not ok and not counts:
        return
    try:
        now = utc_now()
        with _flow_lock:
            row = _flow.setdefault(channel, {
                "last_ok": None, "last_fail": None, "fails_in_a_row": 0})
            if ok:
                row["last_ok"] = now
                row["fails_in_a_row"] = 0
            else:
                row["last_fail"] = now
                row["fails_in_a_row"] += 1
    except Exception:  # noqa: BLE001 - counting must never stop a send
        logger.exception("Could not count a delivery.")


def forget_deliveries() -> None:
    """Start every channel clean. For tests."""
    with _flow_lock:
        _flow.clear()


def channel_state(channel: str) -> str:
    """'ok', 'failing', or 'no_sends_yet' for one channel."""
    with _flow_lock:
        row = _flow.get(channel)
        if row is None:
            return "no_sends_yet"
        return "failing" if row["fails_in_a_row"] >= FAILING_AFTER else "ok"


def readings_state(now: Optional[datetime] = None) -> Tuple[str, Optional[int]]:
    """('ok' | 'stopped' | 'no_sensors_yet', seconds since the newest reading)."""
    now = now or utc_now()
    newest = None
    active = False
    for sensor in STORE.all_sensors():
        seen = sensor.last_seen
        if seen is None:
            continue
        if newest is None or seen > newest:
            newest = seen
        if now - seen <= timedelta(hours=ACTIVE_WITHIN_HOURS):
            active = True
    if newest is None or not active:
        return "no_sensors_yet", None
    age = int((now - newest).total_seconds())
    limit = INGEST_STALE_MINUTES * 60
    listening = (now - _STARTED).total_seconds()
    if age > limit and listening > limit:
        return "stopped", age
    return "ok", age


def _database_ok() -> bool:
    try:
        STORE._db.get(_KIND, _ID)
        return True
    except Exception as exc:  # noqa: BLE001 - this is the question being asked
        logger.error("Readiness: the database did not answer (%s).", exc)
        return False


def _texts_switched_on() -> bool:
    import notifications

    return notifications.delivery_ready()


def _mail_switched_on() -> bool:
    import mail

    return mail.configured()


def _a_copy_not_the_live_product() -> bool:
    """Staging or a demo (CYBERLOGIX_NOINDEX), where texts are off on purpose."""
    return os.environ.get("CYBERLOGIX_NOINDEX", "").strip().lower() in (
        "1", "true", "yes")


# Plain sentences for the owner, keyed so one problem is one message.
_SENTENCES = {
    "database": "The database is not answering, so readings cannot be saved.",
    "sweep_stopped": (
        "The alert job has stopped. Open problems are not being followed "
        "up with calls."),
    "readings_stopped": (
        "No sensor readings have arrived from any business for {minutes} "
        "minutes. A warm fridge would not be noticed."),
    "texts_failing": (
        "Alert texts are failing: Twilio refused or did not answer the "
        "last {n} tries."),
    "calls_failing": (
        "Alert phone calls are failing: Twilio refused or did not answer "
        "the last {n} tries."),
    "email_failing": (
        "Alert emails are failing: the mail server refused or did not "
        "answer the last {n} tries."),
    "texts_off": (
        "Texts and calls are switched off: Twilio is not set up, so no "
        "alert reaches a phone."),
}


def problems(now: Optional[datetime] = None) -> List[Tuple[str, str]]:
    """What is wrong with the alarm path right now, as (key, sentence)."""
    now = now or utc_now()
    found: List[Tuple[str, str]] = []
    if not _database_ok():
        found.append(("database", _SENTENCES["database"]))
    if state(now)["sweep_stale"]:
        found.append(("sweep_stopped", _SENTENCES["sweep_stopped"]))
    readings, age = readings_state(now)
    if readings == "stopped":
        found.append(("readings_stopped", _SENTENCES["readings_stopped"].format(
            minutes=(age or 0) // 60)))
    for channel, key in (("sms", "texts_failing"), ("voice", "calls_failing"),
                         ("email", "email_failing")):
        if channel_state(channel) == "failing":
            found.append((key, _SENTENCES[key].format(n=FAILING_AFTER)))
    if not _texts_switched_on() and not _a_copy_not_the_live_product():
        found.append(("texts_off", _SENTENCES["texts_off"]))
    return found


# --- the readiness answer -----------------------------------------------------
#
# For an uptime monitor, hit every minute. States only: no counts, names,
# numbers, versions, model names or error messages, so it is safe in a
# stranger's logs. Worked out at most every few seconds however often it
# is asked, so hammering it costs nothing.
READY_CACHE_SECONDS = 5.0
_ready_lock = threading.Lock()
_ready_cache: Dict[str, Any] = {"at": None, "body": None}


def readiness(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Whether alerts can flow right now, and if not, why, in plain words."""
    now = now or utc_now()
    live = state(now)
    readings, _ = readings_state(now)
    texts_on = _texts_switched_on()
    mail_on = _mail_switched_on()

    def flow(channel: str, switched_on: bool) -> str:
        if not switched_on:
            return "off"
        return channel_state(channel)

    found = problems(now)
    return {
        "ready": not found,
        "checks": {
            "database": "ok" if _database_ok() else "failing",
            "alert_job": "stopped" if live["sweep_stale"] else "ok",
            "sensor_readings": readings,
            "texts": flow("sms", texts_on),
            "calls": flow("voice", texts_on),
            "email": flow("email", mail_on),
            # A business's own chat hook going wrong is theirs to fix,
            # so it is shown and never makes this answer 503.
            "webhooks": channel_state("webhook"),
            "outside_alarm": "set" if HEARTBEAT_URL else "not_set",
        },
        "problems": [sentence for _, sentence in found],
        "seconds_since_alert_job": live["seconds_since_sweep"],
        "checked_at": iso(now),
    }


def cached_readiness() -> Dict[str, Any]:
    import time

    with _ready_lock:
        at, body = _ready_cache["at"], _ready_cache["body"]
        if body is not None and time.monotonic() - at < READY_CACHE_SECONDS:
            return body
    body = readiness()
    with _ready_lock:
        _ready_cache["at"], _ready_cache["body"] = time.monotonic(), body
    return body


def forget_readiness() -> None:
    with _ready_lock:
        _ready_cache["at"], _ready_cache["body"] = None, None


# --- telling the owner ----------------------------------------------------------

OPERATOR_PHONE = os.environ.get("CYBERLOGIX_OPERATOR_PHONE", "").strip()

_ALARMS_ID = "alarm_watch"


def _operator_email() -> str:
    import digest

    return digest.OPERATOR_EMAIL


def _tell(subject: str, body: str, dedupe_key: str) -> Dict[str, Any]:
    """Email and text the operator. Never raises; says what went out."""
    told: Dict[str, Any] = {"email": "not_set", "text": "not_set"}
    address = _operator_email()
    if address:
        try:
            import mail

            told["email"] = mail.send(
                to_address=address, subject=subject, body=body,
                dedupe_key=dedupe_key, klass="transactional",
            ).get("status")
        except Exception as exc:  # noqa: BLE001 - try the text anyway
            logger.error("Could not email the operator (%s).", exc)
            told["email"] = "error"
    if OPERATOR_PHONE:
        try:
            import notifications

            told["text"] = notifications.send_sms(
                OPERATOR_PHONE, f"CyberLogix: {subject}").get("status")
        except Exception as exc:  # noqa: BLE001 - never raises
            logger.error("Could not text the operator (%s).", exc)
            told["text"] = "error"
    return told


def check_alarm_path(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Look once; tell the operator about anything new, and anything fixed.

    One message when a problem starts and one when it ends, however many
    times this runs in between, and across restarts: what has been said
    is kept in the database.
    """
    now = now or utc_now()
    found = dict(problems(now))
    kept = STORE._db.get("meta", _ALARMS_ID) or {}
    open_now: Dict[str, Any] = dict(kept.get("open") or {})
    started, ended = [], []

    for key, sentence in found.items():
        if key in open_now:
            continue
        open_now[key] = {"since": iso(now), "sentence": sentence}
        started.append(key)
        logger.critical("ALARM PATH PROBLEM [%s]: %s", key, sentence)
        _tell(
            subject=f"Problem: {sentence.split('.')[0]}",
            body=(
                f"{sentence}\n\n"
                "What to do: open docs/OPERATIONS.md, section \"If the app "
                "is down\", and find this message there. You will get one "
                "more email when it is fixed.\n\n"
                f"Noticed at {iso(now)} (UTC)."
            ),
            dedupe_key=f"alarm:{key}:{open_now[key]['since']}",
        )

    for key in list(open_now):
        if key in found:
            continue
        since = open_now.pop(key).get("since", "")
        ended.append(key)
        logger.warning("Alarm path problem cleared [%s].", key)
        _tell(
            subject=f"Fixed: {_FIXED.get(key, 'the problem has cleared')}",
            body=(f"{_FIXED.get(key, 'The problem has cleared')}.\n\n"
                  f"It started at {since} and cleared at {iso(now)} (UTC)."),
            dedupe_key=f"alarm-cleared:{key}:{since}",
        )

    if started or ended or open_now != (kept.get("open") or {}):
        STORE._db.put("meta", _ALARMS_ID, {"open": open_now,
                                            "checked_at": iso(now)})
    return {"open": sorted(open_now), "started": started, "ended": ended}


_FIXED = {
    "database": "the database is answering again",
    "sweep_stopped": "the alert job is running again",
    "readings_stopped": "sensor readings are arriving again",
    "texts_failing": "alert texts are going out again",
    "calls_failing": "alert calls are going out again",
    "email_failing": "alert emails are going out again",
    "texts_off": "texts and calls are switched on",
}


def _watch_interval() -> int:
    try:
        return int(os.environ.get("CYBERLOGIX_ALARM_WATCH_SECONDS", "60"))
    except ValueError:
        return 60


_watch_thread: Optional[threading.Thread] = None
_watch_stop = threading.Event()


def _watch_loop(every: int) -> None:
    # A plain thread, not the event loop and not the sweep's worker: a
    # sweep that hangs, or an event loop that is blocked, must not stop
    # the thing that notices.
    while not _watch_stop.wait(every):
        try:
            check_alarm_path()
        except Exception as exc:  # noqa: BLE001 - the watcher must not die
            logger.exception("Alarm path check failed (%s).", exc)


def start_watching() -> bool:
    """Start the alarm-path watcher, unless it is off or running."""
    global _watch_thread
    every = _watch_interval()
    if every <= 0:
        logger.info("Alarm path watcher off (CYBERLOGIX_ALARM_WATCH_SECONDS=0).")
        return False
    if _watch_thread is not None and _watch_thread.is_alive():
        return False
    if not (_operator_email() or OPERATOR_PHONE):
        logger.error(
            "Nobody will be told if alerts stop: set CYBERLOGIX_OPERATOR_EMAIL "
            "and CYBERLOGIX_OPERATOR_PHONE. Problems are only written to the log.")
    _watch_stop.clear()
    _watch_thread = threading.Thread(
        target=_watch_loop, args=(every,), name="alarm-watch", daemon=True)
    _watch_thread.start()
    return True


def stop_watching() -> None:
    global _watch_thread
    _watch_stop.set()
    if _watch_thread is not None:
        _watch_thread.join(timeout=2)
    _watch_thread = None


# --- the two public checks ---------------------------------------------------------

public_router = APIRouter(tags=["Watchdog"])


@public_router.get("/healthz", include_in_schema=False)
def healthz():
    """Is the process up. Touches nothing, so it is always cheap."""
    return {"status": "ok"}


@public_router.get("/readyz", include_in_schema=False)
def readyz(response: Response):
    """Can alerts flow right now. 503, with plain reasons, when not.

    For an uptime monitor. Unauthenticated, and states only.
    """
    body = cached_readiness()
    if not body["ready"]:
        response.status_code = 503
    response.headers["Cache-Control"] = "no-store"
    return body
