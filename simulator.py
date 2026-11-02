"""Simulated sensors, for showing the product to somebody with no hardware.

A prospect, a new manager, the owner's business partner: none of them
should have to wait for a sensor to arrive in the post to see what a
failing freezer looks like on this screen. So an owner can put up to
three pretend units on their console, watch them read, break one, and
see what the roster would have been sent.

The rule this module exists to keep: **nothing simulated is ever a
record.** A made-up reading in a vaccine fridge's log is a forged log,
whatever the button that wrote it was called. So none of this touches the
store. The units live in this process's memory and nowhere else -- not in
the readings table, so not in a compliance report, an export, a claim
packet or the vault's hash chain; not in the seat count, so not on an
invoice; and not in an incident, so nobody is texted and nothing
escalates. A restart clears them, and that is the right answer for
something that was never real.

Owner only. A pretend breach on a shared screen is exactly the kind of
thing that gets somebody out of bed, so the person who can start one is
the person who answers for the account. Every unit and every reading says
SIMULATED on it, and its id starts SIM- so nothing could mistake one for
a sensor even out of context.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant
from domain import display_temperature, evaluate_breach
from store import INDUSTRY_PROFILES, STORE, Tenant, User

router = APIRouter(prefix="/api/simulator", tags=["Simulator"])

MAX_UNITS = 3
# One simulated reading every few real seconds, so a breach happens while
# somebody is still watching rather than over a real night.
TICK_SECONDS = 4.0
HISTORY_POINTS = 12
# A demo nobody stopped is cleared after this long, so a forgotten tab
# does not hold units in memory for ever.
LIFETIME_SECONDS = 4 * 60 * 60

NOTICE = (
    "Simulated. These are not real sensors and not real readings: nothing "
    "here is recorded, billed or sent to anybody."
)

# Swappable in tests, so time can be walked forward without sleeping.
clock: Callable[[], float] = time.monotonic


@dataclass
class SimUnit:
    unit_id: str
    tenant_id: str
    name: str
    vertical: str
    started: float
    broken_at: Optional[float] = None
    seed: float = 0.0


@dataclass
class _Estate:
    units: Dict[str, SimUnit] = field(default_factory=dict)
    counter: int = 0


_lock = threading.Lock()
_estates: Dict[str, _Estate] = {}


def forget() -> None:
    """Clear every simulation. For tests; a restart does the same."""
    with _lock:
        _estates.clear()


def _limits(vertical: str):
    profile = INDUSTRY_PROFILES[vertical]
    return profile.get("danger_above"), profile.get("danger_below")


def _nominal(vertical: str) -> float:
    above, below = _limits(vertical)
    if above is not None and below is not None:
        return (above + below) / 2
    if above is not None:
        return above - 12
    return below + 12  # type: ignore[operator]


def reading_at(unit: SimUnit, tick: int) -> float:
    """Fahrenheit, as a function of time. The same tick, the same reading.

    Steady units wobble a little around the middle of their safe range.
    A broken one climbs past its limit and on toward the room it sits in,
    the way a dead compressor does: fast at first, then flattening.
    """
    base = _nominal(unit.vertical)
    above, below = _limits(unit.vertical)
    span = (above - below) if (above is not None and below is not None) else 12.0
    wobble = math.sin((tick + unit.seed) / 2.3) * min(1.2, span / 8)
    value = base + wobble
    if unit.broken_at is not None:
        broke = int((unit.broken_at - unit.started) // TICK_SECONDS)
        if tick > broke:
            limit = above if above is not None else below
            # Where it ends up: well past the limit, on the wrong side.
            target = (limit + 25) if above is not None else (limit - 25)  # type: ignore[operator]
            steps = tick - broke
            value = base + (target - base) * (1 - math.exp(-steps / 4)) + wobble / 3
    return round(value, 1)


def _tick_now(unit: SimUnit) -> int:
    return int((clock() - unit.started) // TICK_SECONDS)


def describe(unit: SimUnit, tenant: Tenant) -> Dict[str, Any]:
    profile = INDUSTRY_PROFILES[unit.vertical]
    above, below = _limits(unit.vertical)
    temp_unit = tenant.temperature_unit
    now = _tick_now(unit)
    ticks = list(range(max(0, now - HISTORY_POINTS + 1), now + 1))
    temps = [reading_at(unit, t) for t in ticks]
    breached = [evaluate_breach(unit.vertical, t) is not None for t in temps]
    current = temps[-1]
    reason = evaluate_breach(unit.vertical, current, unit=temp_unit)

    would: Optional[Dict[str, Any]] = None
    if reason:
        # Who a real breach would have reached. Read, never written: the
        # roster is the account's, and the demo only names it.
        names = [c.full_name for c in STORE.sms_recipients(tenant)]
        would = {
            "text": (f"SIMULATED. A real breach would text: {tenant.company_name}: "
                     f"{unit.name} is at {display_temperature(current, temp_unit)}"
                     f"°{temp_unit}. {reason}"),
            "recipients": names,
            "sent": False,
        }
    return {
        "sensor_id": unit.unit_id,
        "simulated": True,
        "location_name": unit.name,
        "industry_vertical": unit.vertical,
        "industry_name": profile["name"],
        "asset_noun": profile["asset_noun"],
        "temperature_unit": temp_unit,
        "last_temperature_display": display_temperature(current, temp_unit),
        "danger_above_display": display_temperature(above, temp_unit),
        "danger_below_display": display_temperature(below, temp_unit),
        "breaching": reason is not None,
        "broken": unit.broken_at is not None,
        "spark": [display_temperature(t, temp_unit) for t in temps],
        "spark_breached": breached,
        "would_alert": would,
        "notice": NOTICE,
    }


def _estate(tenant_id: str) -> _Estate:
    estate = _estates.setdefault(tenant_id, _Estate())
    now = clock()
    for key in [k for k, u in estate.units.items()
                if now - u.started > LIFETIME_SECONDS]:
        del estate.units[key]
    return estate


def _unit(tenant: Tenant, unit_id: str) -> SimUnit:
    with _lock:
        unit = _estate(tenant.tenant_id).units.get(unit_id)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No simulated unit {unit_id}.")
    return unit


def snapshot(tenant: Tenant) -> Dict[str, Any]:
    with _lock:
        units = sorted(_estate(tenant.tenant_id).units.values(),
                       key=lambda u: u.unit_id)
    return {
        "simulated": True,
        "notice": NOTICE,
        "max_units": MAX_UNITS,
        "tick_seconds": TICK_SECONDS,
        "units": [describe(u, tenant) for u in units],
    }


class SimCreate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    industry_vertical: Optional[str] = None


@router.get("")
def list_simulated(
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    """The owner's pretend units, with their readings as of now."""
    return snapshot(tenant)


@router.post("/units", status_code=status.HTTP_201_CREATED)
def add_simulated(
    payload: Optional[SimCreate] = None,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    """Put a pretend unit on the console. It starts healthy."""
    payload = payload or SimCreate()
    vertical = (payload.industry_vertical or tenant.industry_vertical
                or "restaurant").strip().lower()
    if vertical not in INDUSTRY_PROFILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"'{vertical}' is not a sector we cover.")
    with _lock:
        estate = _estate(tenant.tenant_id)
        if len(estate.units) >= MAX_UNITS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"{MAX_UNITS} simulated units is the most at once. "
                        "Remove one first."),
            )
        estate.counter += 1
        noun = INDUSTRY_PROFILES[vertical]["asset_noun"]
        name = (payload.name or "").strip() or f"Demo {noun} {estate.counter}"
        unit = SimUnit(unit_id=f"SIM-{estate.counter}", tenant_id=tenant.tenant_id,
                       name=name, vertical=vertical, started=clock(),
                       seed=float(estate.counter * 3))
        estate.units[unit.unit_id] = unit
    return describe(unit, tenant)


@router.post("/units/{unit_id}/break")
def break_simulated(
    unit_id: str,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    """Make a pretend unit fail. It warms past its limit over the next few
    readings; nothing is sent to anybody."""
    unit = _unit(tenant, unit_id)
    with _lock:
        if unit.broken_at is None:
            unit.broken_at = clock()
    return describe(unit, tenant)


@router.post("/units/{unit_id}/fix")
def fix_simulated(
    unit_id: str,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    unit = _unit(tenant, unit_id)
    with _lock:
        unit.broken_at = None
    return describe(unit, tenant)


@router.delete("/units/{unit_id}")
def remove_simulated(
    unit_id: str,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    _unit(tenant, unit_id)
    with _lock:
        _estate(tenant.tenant_id).units.pop(unit_id, None)
    return {"removed": unit_id, "simulated": True}


@router.delete("")
def stop_simulation(
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    with _lock:
        count = len(_estates.pop(tenant.tenant_id, _Estate()).units)
    return {"removed": count, "simulated": True}
