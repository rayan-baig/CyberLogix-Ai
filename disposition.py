"""How long it was warm, how warm, and what the guidance says about that.

When a walk-in goes out of range, the number on the screen is not the
question. The question is standing in the kitchen at 7am: do I throw
this away, or do I serve it?

The app already holds the only evidence that can answer it -- every
reading, timestamped -- and until now it made the manager do the
arithmetic from a chart of numbers while service was starting.

**What this does not do.** It does not say food is safe. It cannot: the
sensor measures the air, not the middle of a chicken, and a walk-in that
was opened repeatedly has warm product and cool air. It reports the
exposure it actually measured, states the rule of thumb that applies,
and leaves the call where the law leaves it -- with the operator and
their own written food safety plan.

The temptation is to print "SAFE" in green, and it is exactly the thing
that would make somebody sick and take the company with it. Every
sentence here is measurement or attributed guidance, never a verdict.

The guidance quoted is the widely-taught two-stage rule for food held
above refrigeration temperature: under two hours, cool it back down;
between two and four, use it but do not put it back; beyond four,
discard. Local codes differ, plans differ, and the plan wins -- which
is why the plan is asked for rather than assumed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from licenses import require_tenant
from store import STORE, Reading, Tenant, utc_now

logger = logging.getLogger("cyberlogix.disposition")

# The two-stage rule of thumb, in hours above the safe limit. Named as
# guidance, quoted as guidance, and never applied as law.
RECOVER_WITHIN_HOURS = 2.0
USE_IMMEDIATELY_WITHIN_HOURS = 4.0

# Above this, time stops being the only variable people care about: it
# is no longer "how long was it warm" but "it was hot".
DANGEROUSLY_WARM_F = 70.0


def exposure(
    readings: List[Reading],
    above: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The measured facts: how long over the line, and how far over.

    Time is counted between readings rather than by counting readings,
    so a sensor reporting every fifteen minutes and one reporting every
    minute give the same answer for the same freezer.
    """
    warm = [r for r in readings if r.temperature_fahrenheit > above]
    if not warm:
        return {
            "minutes_above": 0.0, "hours_above": 0.0, "peak_f": None,
            "started_at": None, "last_warm_at": None, "still_warm": False,
            "readings_above": 0,
        }

    minutes = 0.0
    previous: Optional[Reading] = None
    for reading in readings:
        if previous is not None and previous.temperature_fahrenheit > above:
            gap = (reading.recorded_at - previous.recorded_at).total_seconds()
            minutes += gap / 60.0
        previous = reading

    # Still above the line at the last sample: the clock is running, so
    # count up to now rather than stopping at the last reading. A gap in
    # reporting is not a gap in the food warming up.
    last = readings[-1]
    if last.temperature_fahrenheit > above:
        minutes += max(
            0.0,
            ((now or last.recorded_at) - last.recorded_at).total_seconds() / 60.0,
        )

    return {
        "minutes_above": round(minutes, 1),
        "hours_above": round(minutes / 60.0, 2),
        "peak_f": round(max(r.temperature_fahrenheit for r in warm), 2),
        "started_at": warm[0].recorded_at,
        "last_warm_at": warm[-1].recorded_at,
        "still_warm": last.temperature_fahrenheit > above,
        "readings_above": len(warm),
    }


def guidance(measured: Dict[str, Any], above: float) -> Dict[str, Any]:
    """What the common rule of thumb says about that exposure.

    Returns a band and the sentence that goes with it. The sentence says
    who is being quoted and whose decision it remains, every time,
    because this is the screen somebody reads at speed and the caveat
    has to travel with the answer rather than sit in a footer.
    """
    hours = measured["hours_above"]
    peak = measured["peak_f"]

    if measured["readings_above"] == 0:
        return {
            "band": "in_range",
            "headline": "Stayed in range.",
            "detail": (
                f"Nothing recorded above {above}° in the window looked "
                "at."
            ),
            "decision_is_yours": False,
        }

    if measured["still_warm"]:
        return {
            "band": "still_warm",
            "headline": "Still warm — the clock is running.",
            "detail": (
                f"{_span(hours)} above {above}° so far, peaking at "
                f"{peak}°. Nothing can be judged until it is back in "
                "range, and every minute counts against whatever is "
                "decided afterwards. Get it cold."
            ),
            "decision_is_yours": True,
        }

    if peak is not None and peak >= DANGEROUSLY_WARM_F:
        return {
            "band": "discard",
            "headline": "Commonly treated as discard.",
            "detail": (
                f"It reached {peak}°, which is room temperature or "
                f"above, for {_span(hours)}. Guidance for food held that "
                "warm is normally to discard rather than to count hours. "
                "Your own food safety plan decides."
            ),
            "decision_is_yours": True,
        }

    if hours < RECOVER_WITHIN_HOURS:
        return {
            "band": "cool_it_down",
            "headline": "Under two hours.",
            "detail": (
                f"{_span(hours)} above {above}°, peaking at {peak}°. "
                "The widely-taught rule is that food above its safe holding "
                "temperature for under two hours can be cooled back down "
                "and kept. That is guidance, not a ruling: the sensor "
                "measured the air, not the middle of the product, and your "
                "own plan decides."
            ),
            "decision_is_yours": True,
        }

    if hours < USE_IMMEDIATELY_WITHIN_HOURS:
        return {
            "band": "use_now",
            "headline": "Between two and four hours.",
            "detail": (
                f"{_span(hours)} above {above}°, peaking at {peak}°. "
                "The widely-taught rule is that food in this window is used "
                "immediately rather than returned to storage. That is "
                "guidance, not a ruling, and your own plan decides."
            ),
            "decision_is_yours": True,
        }

    return {
        "band": "discard",
        "headline": "Over four hours.",
        "detail": (
            f"{_span(hours)} above {above}°, peaking at {peak}°. "
            "The widely-taught rule is to discard food held above its safe "
            "temperature for more than four hours. That is guidance, not a "
            "ruling, and your own plan decides — but this is the band "
            "an inspector will ask about."
        ),
        "decision_is_yours": True,
    }


def _span(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} minutes"
    return f"{hours:.1f} hours"


def assess(
    readings: List[Reading],
    above: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Measurement and guidance together, with the caveats attached."""
    measured = exposure(readings, above, now)
    said = guidance(measured, above)
    return {
        "measured": {
            **measured,
            "started_at": (measured["started_at"].isoformat()
                           if measured["started_at"] else None),
            "last_warm_at": (measured["last_warm_at"].isoformat()
                             if measured["last_warm_at"] else None),
        },
        **said,
        "limits": {
            "safe_above_f": above,
            "cool_down_within_hours": RECOVER_WITHIN_HOURS,
            "use_immediately_within_hours": USE_IMMEDIATELY_WITHIN_HOURS,
        },
        "not_a_ruling": (
            "This is a measurement and a widely-taught rule of thumb, not "
            "a food safety decision. The sensor reads the air, not the "
            "centre of the product. Your written plan and your local code "
            "decide, and if they disagree with this, they are right."
        ),
    }


# --- route -----------------------------------------------------------------

router = APIRouter(prefix="/api/disposition", tags=["Product disposition"])


@router.get("/{sensor_id}")
def what_about_the_food(
    sensor_id: str,
    hours: float = Query(24.0, gt=0, le=168),
    tenant: Tenant = Depends(require_tenant),
):
    """How long this unit was warm, and what the guidance says about it.

    Asked after something went wrong, which is why the window defaults
    wide: the manager arriving in the morning wants last night, not the
    last twenty minutes.
    """
    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )
    danger_above, _ = sensor.bounds()
    if danger_above is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This unit has no upper limit set, so there is no line to "
                "have been above. Set one and ask again."
            ),
        )
    readings = STORE.readings_for(
        sensor_id, since=utc_now() - timedelta(hours=hours))
    return {
        "sensor_id": sensor_id,
        "location_name": sensor.location_name,
        "window_hours": hours,
        **assess(readings, danger_above, utc_now()),
    }
