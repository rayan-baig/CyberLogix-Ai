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
from backup import run_daily_backup
from digest import DIGEST_HOUR_UTC, run_digests, send_weekly_texts
from mail import flush_queue
import watchdog
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

    Five steps, in the order that matters: issue what is due, chase what
    is late, ask the trials that are ending for the order, send the daily
    and weekly summaries, then push anything that queued because the mail
    host was briefly down. Each is guarded separately — a company that
    stops selling because collections threw is worse off than one that
    does neither well.
    """
    billed = {"invoices_issued": 0, "billed_usd": 0.0}
    chased = {"notices_count": 0, "late_fees_issued": []}
    asked = {"sent_count": 0}
    summarised = {"operator": {"sent": False}, "customer_reports": {"sent_count": 0}}
    backed_up = {"taken": False, "status": "not_run"}
    flushed = {"attempted": 0, "sent": 0}
    licences = {"sent_count": 0, "skipped": "not_run"}
    texted = {"sent_count": 0}
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
        # Before the summaries and before the flush, because a snapshot
        # is worth most taken while the day's billing is fresh and
        # nothing else has had a chance to go wrong.
        backed_up = run_daily_backup()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Daily backup failed (%s).", exc)
    try:
        summarised = run_digests()
    except Exception as exc:  # noqa: BLE001 - a summary must not stop the work
        logger.exception("Digest pass failed (%s).", exc)
    try:
        # Rides the same weekly cadence as the emailed report, and is
        # opted into separately because it costs money per message.
        texted = send_weekly_texts()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Weekly reassurance text pass failed (%s).", exc)
    try:
        # After the summaries and before the flush: an owner whose cook
        # cannot legally be on shift tomorrow hears about it today, on
        # the same schedule as everything else that goes out on its own.
        licences = run_licence_alerts()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Licence alert pass failed (%s).", exc)
    try:
        # Last, so that anything the passes above queued against a mail
        # host that was briefly down gets one more attempt in the same
        # hour rather than waiting for the next.
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
    watchdog.record_money_pass()
    return {
        "billing": billed,
        "collections": chased,
        "conversion": asked,
        "digests": summarised,
        "licence_alerts": licences,
        "weekly_texts": texted,
        "backup": backed_up,
        "mail_queue": flushed,
    }


def run_vendor_polls() -> dict:
    """Fetch from the sensor clouds that will not push to us.

    Imported inside, like the licence alerts, so the start-up import
    graph stays shallow.
    """
    import pollers

    return pollers.run_poll_pass()


def run_one_pass() -> dict:
    """Sweep every tenant once, returning a summary of what was done.

    A failure on one tenant must not stop the others: the whole point is
    that nobody is watching, so a single bad estate cannot be allowed to
    silence the rest of the fleet.
    """
    # Before the sweep, deliberately. A reading fetched from a vendor
    # cloud has to be scored in the same pass it arrives in, or a
    # freezer that went out of range is noticed a minute late for no
    # reason. Guarded separately: a vendor being down is a Tuesday, and
    # it must not stop anybody's estate being swept.
    fetched = {"polled": 0, "readings": 0, "failed": 0}
    try:
        fetched = run_vendor_polls()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Vendor poll pass failed (%s).", exc)

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
    result = {
        "tenants_swept": swept,
        "voice_calls_placed": calls,
        "failed_tenants": failures,
        "vendor_polls": fetched,
    }
    # Written by the sweep, never by a request handler. A process that
    # answers HTTP while this task is dead has to look dead to the
    # watchdog, because for every freezer on the fleet it is.
    watchdog.record_sweep(result)
    return result


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
_restarts = 0

# A supervised loop that dies and comes back every 200ms is worse than one
# that stays down: it buries the reason. Backing off caps the churn while
# still recovering from something transient within a minute.
RESTART_BACKOFF_SECONDS = (1, 5, 15, 60)


def restarts() -> int:
    """How many times the loop has had to be brought back."""
    return _restarts


def _supervise(task: "asyncio.Task") -> None:
    """Bring the loop back if it ever stops without being told to.

    `_loop` already guards each pass, so one failing sweep cannot end it.
    What was unguarded was the task itself: a BaseException, a bug in the
    guard, or anything that escaped `while True` left no loop running and
    nothing to start another. The watchdog would have reported it -- 503,
    correctly -- and then waited for a human to read it, on a deployment
    whose whole point is that nobody has to.
    """
    global _restarts

    try:
        exc = task.exception()
    except asyncio.CancelledError:
        # A deliberate stop: `.exception()` raises rather than returns for
        # a cancelled task. Catching it keeps shutdown quiet. It is not
        # what prevents a restart -- CancelledError is a BaseException, so
        # letting it escape this callback would also skip the restart, and
        # a mutation test confirmed the two are behaviourally the same.
        # What prevents the restart is `_loop` re-raising rather than
        # swallowing, so a cancelled task ends cancelled.
        return
    if exc is None:
        detail = "the sweep loop returned, which it is not supposed to do"
    else:
        detail = f"the sweep loop died: {type(exc).__name__}: {exc}"
        try:
            import faults

            faults.record("scheduler._loop", exc, context="supervised restart")
        except Exception:  # noqa: BLE001 - recovery must not need the recorder
            logger.exception("Could not record the scheduler fault.")

    delay = RESTART_BACKOFF_SECONDS[
        min(_restarts, len(RESTART_BACKOFF_SECONDS) - 1)
    ]
    _restarts += 1
    logger.error("%s. Restarting in %ds (restart #%d).",
                 detail, delay, _restarts)

    async def _revive() -> None:
        await asyncio.sleep(delay)
        interval = _interval()
        if interval <= 0:
            return
        global _task
        _task = asyncio.create_task(_loop(interval))
        _task.add_done_callback(_supervise)

    try:
        asyncio.get_running_loop().create_task(_revive())
    except RuntimeError:
        # No loop left to schedule on: the process is going down anyway.
        logger.warning("No event loop to restart the sweep on.")


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
    _task.add_done_callback(_supervise)
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


def run_licence_alerts() -> dict:
    """Email owners about credentials that are lapsing on their account.

    Imported here rather than at the top because `people` reaches the
    mail layer and the scheduler is imported during application start-up;
    keeping it local keeps the start-up import graph shallow.

    The send hour is the digest's, passed in rather than read again, so
    that moving the daily email moves this with it instead of leaving two
    schedules that drift apart and nobody remembers why.
    """
    import people

    return people.send_licence_alerts(hour_utc=DIGEST_HOUR_UTC)
