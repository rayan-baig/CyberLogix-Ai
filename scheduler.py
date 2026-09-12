"""The watchdog that runs when nobody is watching.

Escalation is the product's whole promise: a text nobody reads becomes a
phone call. That only works if something runs the sweep, and until now the
only things that did were a button in the console and the test suite — so
an unacknowledged 3am breach sat open until somebody opened a browser.

This is a plain asyncio task inside the API process: one loop, no broker,
no extra service to pay for. It sweeps every tenant on an interval and
logs what it did. If the deployment already has an external scheduler
(Cloud Scheduler hitting POST /api/autopilot/sweep), set
CYBERLOGIX_SWEEP_SECONDS=0 to switch this off and avoid double-calling.

One process is assumed. Running several replicas with the loop enabled in
each would escalate the same incident more than once, so a multi-replica
deployment should disable it here and drive the endpoint externally.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from automation import sweep_tenant
from contracts import run_billing, run_dunning
from conversion import run_trial_conversion
from mail import flush_queue
from store import STORE

logger = logging.getLogger("cyberlogix.scheduler")

# Billing does not need a minute-by-minute sweep, and running it on the
# alert cadence would mean a thousand no-op passes a day over the whole
# invoice ledger. Once an hour is far more often than a monthly contract
# needs, and still catches up within the hour after a restart.
BILLING_EVERY_SECONDS = 3600


def _interval() -> int:
    """Seconds between sweeps; 0 or less disables the loop entirely."""
    try:
        return int(os.environ.get("CYBERLOGIX_SWEEP_SECONDS", "60"))
    except ValueError:
        logger.error("CYBERLOGIX_SWEEP_SECONDS is not a number; scheduler off.")
        return 0


def run_money_pass() -> dict:
    """Issue what is due and chase what is late.

    Separate from the alert sweep and far less frequent, but under the
    same rule: it runs unattended, so a failure in one account must not
    stop the others. This is the function that decides whether the company
    gets paid, and until it existed the answer was "if somebody remembers".

    Four steps, in the order that matters: issue what is due, chase what
    is late, ask the trials that are ending for the order, then push
    anything that queued because the mail host was briefly down. Each is
    guarded separately — a company that stops selling because collections
    threw is worse off than one that does neither well.
    """
    billed = {"invoices_issued": 0, "billed_usd": 0.0}
    chased = {"notices_count": 0, "late_fees_issued": []}
    asked = {"sent_count": 0}
    flushed = {"attempted": 0, "sent": 0}
    try:
        billed = run_billing()
    except Exception as exc:  # noqa: BLE001 - collections must still run
        logger.exception("Billing pass failed (%s).", exc)
    try:
        chased = run_dunning()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Collections pass failed (%s).", exc)
    try:
        asked = run_trial_conversion()
    except Exception as exc:  # noqa: BLE001 - selling must not stop collecting
        logger.exception("Trial conversion pass failed (%s).", exc)
    try:
        # Last, so that anything the three passes above queued against a
        # mail host that was briefly down gets one more attempt in the
        # same hour rather than waiting for the next.
        flushed = flush_queue()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Mail queue flush failed (%s).", exc)

    if (
        billed.get("invoices_issued")
        or chased.get("notices_count")
        or asked.get("sent_count")
    ):
        logger.info(
            "Money pass: %d invoice(s) for $%.2f issued, %d chase(s) due, "
            "%d late fee(s), %d trial(s) asked for the order.",
            billed.get("invoices_issued", 0),
            billed.get("billed_usd", 0.0),
            chased.get("notices_count", 0),
            len(chased.get("late_fees_issued", [])),
            asked.get("sent_count", 0),
        )
    return {
        "billing": billed,
        "collections": chased,
        "conversion": asked,
        "mail_queue": flushed,
    }


def run_one_pass() -> dict:
    """Sweep every tenant once, returning a summary of what was done.

    A failure on one tenant must not stop the others: the whole point is
    that nobody is watching, so a single bad estate cannot be allowed to
    silence the rest of the fleet.
    """
    swept = 0
    calls = 0
    failures = []
    for tenant in STORE.list_tenants():
        try:
            result = sweep_tenant(tenant, auto_escalate=True)
        except Exception as exc:  # noqa: BLE001 - one estate must not stop the rest
            logger.exception(
                "Sweep failed for tenant %s (%s).", tenant.tenant_id, exc
            )
            failures.append(tenant.tenant_id)
            continue
        # Inside the guard: reading the result is part of "sweeping this
        # tenant", and a malformed one must not take out every tenant
        # after it — which is the exact failure this function promises
        # not to have.
        try:
            swept += 1
            calls += result["voice_calls_placed"]
        except (KeyError, TypeError) as exc:
            logger.exception(
                "Sweep of tenant %s returned something unusable (%s).",
                tenant.tenant_id, exc,
            )
            failures.append(tenant.tenant_id)

    if calls:
        logger.critical(
            "Unattended sweep placed %d escalation call(s) across %d tenant(s).",
            calls,
            swept,
        )
    return {
        "tenants_swept": swept,
        "voice_calls_placed": calls,
        "failed_tenants": failures,
    }


async def _loop(interval: int) -> None:
    logger.info(
        "Autopilot scheduler started; sweeping every %ds, billing every %ds.",
        interval,
        BILLING_EVERY_SECONDS,
    )
    # Counted in seconds slept rather than in passes, so changing the sweep
    # interval does not silently change how often the company invoices.
    since_billing = 0.0
    while True:
        try:
            await asyncio.sleep(interval)
            # Sweeps touch SQLite and the telephony client, both blocking,
            # so they run on a worker thread rather than stalling the API.
            await asyncio.to_thread(run_one_pass)

            since_billing += interval
            if since_billing >= BILLING_EVERY_SECONDS:
                since_billing = 0.0
                await asyncio.to_thread(run_money_pass)
        except asyncio.CancelledError:
            logger.info("Autopilot scheduler stopping.")
            raise
        except Exception as exc:  # noqa: BLE001 - the watchdog must not die
            logger.exception("Autopilot sweep pass failed (%s).", exc)


_task: Optional[asyncio.Task] = None


def start() -> Optional[asyncio.Task]:
    """Start the loop, unless it is switched off or already running."""
    global _task
    interval = _interval()
    if interval <= 0:
        logger.info(
            "Autopilot scheduler disabled (CYBERLOGIX_SWEEP_SECONDS=%s). "
            "Escalation will only run when something calls "
            "POST /api/autopilot/sweep.",
            os.environ.get("CYBERLOGIX_SWEEP_SECONDS"),
        )
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_loop(interval))
    return _task


async def stop() -> None:
    """Cancel the loop and wait for it to unwind."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None


def status() -> dict:
    """What the scheduler is doing, for the health endpoint."""
    interval = _interval()
    return {
        "enabled": interval > 0,
        "interval_seconds": interval,
        "running": _task is not None and not _task.done(),
        "billing_interval_seconds": BILLING_EVERY_SECONDS,
        "note": (
            "Sweeps run in-process, and so does billing. Turning this off "
            "stops both: drive POST /api/autopilot/sweep for escalation "
            "*and* POST /api/contracts/run for billing and collections, or "
            "the company quietly stops invoicing. Set "
            "CYBERLOGIX_SWEEP_SECONDS=0 when an external scheduler does "
            "that, or when running more than one replica."
        ),
    }
