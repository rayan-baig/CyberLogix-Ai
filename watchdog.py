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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

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


def stale_after_seconds() -> float:
    """How long a silence has to last before it means something."""
    return max(_interval() * STALE_AFTER_MULTIPLE, STALE_FLOOR_SECONDS)


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
    # scheduler drives it, and it may simply not have fired yet.
    sweep_stale = enabled and (sweep_age is None or sweep_age > limit)
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
    if current["healthy"] and current["heartbeat_url_configured"]:
        return []

    found = list(current["reasons"])
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
