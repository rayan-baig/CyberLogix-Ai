"""The watchdog that runs when nobody is watching.

Escalation is the product's whole promise: a text nobody reads becomes a
phone call. That only works if something runs the sweep, and until now the
only things that did were a button in the console and the test suite — so
an unacknowledged 3am breach sat open until somebody opened a browser.

This is a plain asyncio task inside the API process: one loop, no broker,
no extra service to pay for. It sweeps every tenant on an interval and
logs what it did. If the deployment already has an external scheduler
(Cloud Scheduler hitting POST /api/autopilot/sweep-all), set
CYBERLOGIX_SWEEP_SECONDS=0 to switch this off and avoid double-calling;
that route then also starts the hourly money pass (see
start_money_pass_if_due).

One process is assumed, either way. The store keeps its working set in
memory, so a second process or replica would hold its own copy of every
record and escalate the same incident again. An external scheduler moves
who triggers the sweep; it does not make a second replica safe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
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


_money_thread: Optional[threading.Thread] = None
_money_lock = threading.Lock()


def _money_pass_in_background() -> None:
    try:
        run_money_pass()
    except Exception as exc:  # noqa: BLE001 - nothing awaits this thread
        logger.exception("Money pass failed (%s).", exc)


def start_money_pass_if_due() -> bool:
    """Start the hourly money pass for an external scheduler, if it is due.

    With the built-in loop off (CYBERLOGIX_SWEEP_SECONDS=0) the only thing
    that ran the money pass was that loop, so a deployment driven by an
    external scheduler swept every business and never invoiced, chased,
    backed up, sent a digest, or flushed the mail queue -- the queued
    password-reset links included. /api/autopilot/sweep-all now starts it
    here when an hour has passed since the last one. On a thread of its
    own, never in the sweep's way, and one at a time.
    """
    global _money_thread
    if _interval() > 0:
        return False  # the built-in loop runs it; two would race
    from store import utc_now

    last = watchdog._read().get("last_money_pass_at")
    age = watchdog._age_seconds(last, utc_now())
    if age is not None and age < BILLING_EVERY_SECONDS:
        return False
    with _money_lock:
        if _money_thread is not None and _money_thread.is_alive():
            return False
        _money_thread = threading.Thread(
            target=_money_pass_in_background, name="money-pass", daemon=True)
        _money_thread.start()
    return True


def run_vendor_polls() -> dict:
    """Fetch from the sensor clouds that will not push to us.

    Imported inside, like the licence alerts, so the start-up import
    graph stays shallow.
    """
    import pollers

    return pollers.run_poll_pass()


# One fleet sweep at a time. Two could run at once: the built-in loop and
# an outside scheduler both configured, or an outside scheduler firing
# every minute while a pass is held up by a telephony outage (every text
# and call waiting out its timeout). Each send was already claimed, so
# nobody was texted twice -- but the passes stacked, each holding a worker
# thread for minutes, and those are the threads that take readings in.
# A pass that finds one running steps aside and says so, and does not
# write the heartbeat: if the running one never finishes, the watchdog
# has to go stale, because nothing is being swept.
_sweep_lock = threading.Lock()


def run_one_pass() -> dict:
    """Sweep every tenant once, unless a sweep is already running."""
    if not _sweep_lock.acquire(blocking=False):
        logger.warning("A sweep is still running; this one stands aside.")
        return {
            "tenants_swept": 0,
            "voice_calls_placed": 0,
            "failed_tenants": [],
            "skipped": "another sweep is still running",
        }
    try:
        return _sweep_every_tenant()
    finally:
        _sweep_lock.release()


def _sweep_every_tenant() -> dict:
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

    # Alert emails the mail host would not take, on the sweep's minute
    # timetable rather than the hourly queue's.
    mailed = {"tried": 0, "sent": 0, "dropped": 0}
    try:
        from mail import retry_alert_mail

        mailed = retry_alert_mail()
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Alert mail retry failed (%s).", exc)

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
        "alert_mail": mailed,
    }
    # Written by the sweep, never by a request handler. A process that
    # answers HTTP while this task is dead has to look dead to the
    # watchdog, because for every freezer on the fleet it is.
    watchdog.record_sweep(result)
    return result


# The first sweep after a start, in seconds. Not a whole interval: a
# deploy or a restart in the middle of an incident would otherwise hold its
# phone call back by a minute, and the watchdog answers 503 until the first
# sweep, so an uptime check would page somebody on every deploy.
FIRST_SWEEP_AFTER_SECONDS = 5.0


async def _loop(interval: float) -> None:
    logger.info(
        "Autopilot scheduler started; sweeping every %ss, billing every %ds.",
        interval,
        BILLING_EVERY_SECONDS,
    )
    clock = asyncio.get_running_loop().time
    # Counted in elapsed time rather than in passes, so changing the sweep
    # interval does not silently change how often the company invoices.
    since_billing = 0.0
    money: Optional[asyncio.Task] = None
    # On a fixed cadence, not "sleep, then sweep". Sleeping a whole
    # interval after each pass made the period the interval plus however
    # long the pass took, so the slower the sweep got under load, the
    # later every escalation call went out.
    next_due = clock() + min(interval, FIRST_SWEEP_AFTER_SECONDS)
    last_tick = clock()
    while True:
        try:
            await asyncio.sleep(max(0.0, next_due - clock()))
            # Sweeps touch SQLite and the telephony client, both blocking,
            # so they run on a worker thread rather than stalling the API.
            try:
                await asyncio.to_thread(run_one_pass)
            finally:
                # A pass that overran its interval is followed straight
                # away, never by a burst of the passes it missed. And one
                # that raised waits its interval like any other: left due
                # at once, a pass failing every time (the heartbeat write
                # on a full disk) swept the fleet back to back, flat out.
                next_due = max(next_due + interval, clock())

            now_tick = clock()
            since_billing += now_tick - last_tick
            last_tick = now_tick
            if since_billing >= BILLING_EVERY_SECONDS:
                since_billing = 0.0
                # Alongside the sweep, never in its way. The money pass sends
                # mail, and this loop used to wait for it: with the mail host
                # not answering, two hundred queued messages at a 20-second
                # timeout each was over an hour with no breach escalated.
                # One at a time, so a slow one is not stacked on.
                if money is None or money.done():
                    money = asyncio.create_task(asyncio.to_thread(run_money_pass))
                    money.add_done_callback(_money_pass_ended)
                else:
                    logger.warning("The last money pass is still running; "
                                   "skipping this one.")
        except asyncio.CancelledError:
            logger.info("Autopilot scheduler stopping.")
            raise
        except Exception as exc:  # noqa: BLE001 - the watchdog must not die
            logger.exception("Autopilot sweep pass failed (%s).", exc)


def _money_pass_ended(task: "asyncio.Task") -> None:
    """Write down a money pass that died, since nothing awaits it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Money pass failed (%s: %s).", type(exc).__name__, exc)


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


# When this process started sweeping. A sensor is only "gone quiet" once
# it has been silent for SENSOR_OFFLINE_AFTER_MINUTES while something was
# listening: after a deploy or an outage of our own, every sensor's last
# reading is old because we were not here to take one, and the first sweep
# would otherwise text every business that each of its sensors had gone
# quiet. Set only when the built-in loop runs.
_listening_since = None


def listening_since():
    return _listening_since


def start() -> Optional[asyncio.Task]:
    """Start the loop, unless it is switched off or already running."""
    global _task, _listening_since
    interval = _interval()
    if interval > 0 and _listening_since is None:
        from store import utc_now

        _listening_since = utc_now()
    if interval <= 0:
        logger.info(
            "Autopilot scheduler disabled (CYBERLOGIX_SWEEP_SECONDS=%s). "
            "Escalation will only run when something calls "
            "POST /api/autopilot/sweep-all.",
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
    global _task, _listening_since
    _listening_since = None
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
            "stops both unless an external scheduler calls POST "
            "/api/autopilot/sweep-all every few minutes: that sweeps every "
            "business and, once an hour, starts the money pass (billing, "
            "collections, backups, digests, the mail queue). POST "
            "/api/contracts/run bills and chases on its own, if wanted. "
            "Still one process either way: a second one keeps its own copy "
            "of every record and escalates the same incident again."
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
