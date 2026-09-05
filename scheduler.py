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
from store import STORE

logger = logging.getLogger("cyberlogix.scheduler")


def _interval() -> int:
    """Seconds between sweeps; 0 or less disables the loop entirely."""
    try:
        return int(os.environ.get("CYBERLOGIX_SWEEP_SECONDS", "60"))
    except ValueError:
        logger.error("CYBERLOGIX_SWEEP_SECONDS is not a number; scheduler off.")
        return 0


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
        swept += 1
        calls += result["voice_calls_placed"]

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
    logger.info("Autopilot scheduler started; sweeping every %ds.", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            # Sweeps touch SQLite and the telephony client, both blocking,
            # so they run on a worker thread rather than stalling the API.
            await asyncio.to_thread(run_one_pass)
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
        "note": (
            "Sweeps run in-process. Set CYBERLOGIX_SWEEP_SECONDS=0 when an "
            "external scheduler drives POST /api/autopilot/sweep, or when "
            "running more than one replica."
        ),
    }
