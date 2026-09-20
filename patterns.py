"""Telling a defrost from a dying compressor, and a door from both.

The forecaster fits a line and projects when it crosses the limit. That
is right as far as it goes, and it is blind to the one fact that decides
whether an alert is worth sending: a freezer warms up on purpose.

Commercial freezers run a defrost cycle every six to twelve hours to melt
frost off the coil. The temperature climbs hard, holds, and drops back.
To a straight-line fit that is a steep slope with a beautiful r-squared
and forty minutes to a breach -- confident, and wrong, two to four times
a day, for ever. Two or three of those and the product is switched off,
which is the failure that matters: not a wrong number, an ignored alarm.

A door being opened looks similar and is not the same thing: sharp,
short, irregular, and it recovers as soon as the door shuts.

So this classifies the *shape* rather than the slope:

    defrost   regular, recurring at a learned interval, recovers
    door      sharp, brief, irregular, recovers quickly
    failure   sustained, does not recover, no rhythm to it
    unknown   not enough history to say -- which is said, not guessed

Everything is learned per sensor from its own history. A defrost interval
is a property of one machine's timer, not of a model of freezers, and a
product that assumes eight hours will be wrong in the first kitchen that
runs six.

Nothing here suppresses an alert on its own. It annotates, and the
routing decides -- because the cost of being wrong is asymmetric: a
needless alert is an annoyance, and a swallowed one is the whole
product failing silently.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from store import Reading

logger = logging.getLogger("cyberlogix.patterns")

# A warm spell shorter than this is a sample or two of noise, not an
# event worth naming.
MIN_EVENT_MINUTES = 4.0

# A door is opened and shut. Beyond this it is not the door any more.
DOOR_MAX_MINUTES = 25.0

# Defrost cycles run on a timer. Anything outside this range is not one,
# whatever else it is -- four hours is aggressive for a commercial unit
# and eighteen is beyond any schedule in normal use.
DEFROST_MIN_INTERVAL_HOURS = 4.0
DEFROST_MAX_INTERVAL_HOURS = 18.0

# How close two gaps have to be to count as the same rhythm. A defrost
# timer drifts by minutes, and the detected start drifts by at most one
# sampling interval on top of that; forty-five minutes covers both. It
# was an hour and a half, which called eight hours and ten and a half
# "the same schedule" -- they are not, and a unit that warms up at
# unrelated times has no schedule to be excused by.
INTERVAL_TOLERANCE_HOURS = 0.75

# How many past events it takes before a rhythm is worth believing. Two
# gaps, so three events. Two events make one gap and one gap is not a
# pattern, it is a coincidence with a ruler held up to it.
MIN_EVENTS_FOR_RHYTHM = 3

# A cycle that recovers is one that came back to where it started. Within
# this much of the pre-event temperature counts as back.
RECOVERY_TOLERANCE_F = 2.0

# How far past its usual length a cycle still in progress is given before
# it stops being called a defrost. Defrosts end. One that started on time
# and is still going at twice its usual length is a unit that failed to
# come out of defrost, which is a real failure with a real warm freezer
# in it -- and it is exactly the failure a schedule-aware classifier
# would otherwise excuse for ever, because it began on time.
OVERRUN_FACTOR = 2.0
OVERRUN_GRACE_MINUTES = 10.0


def _warm_events(
    readings: List[Reading], above: float
) -> List[Dict[str, Any]]:
    """Every run of consecutive readings over the line, with its shape.

    A run rather than a point, because "how long" and "did it come back"
    are the two questions that separate a defrost from a failure, and
    neither can be asked of a single sample.
    """
    events: List[Dict[str, Any]] = []
    run: List[Reading] = []
    # The last in-range reading seen. A run's "before" is whatever that
    # was when the run started, so it has to be captured at the moment
    # the run opens rather than read afterwards -- by then it has moved
    # on to the reading that ended the run.
    last_cold: Optional[float] = None
    before: Optional[float] = None

    for reading in readings:
        if reading.temperature_fahrenheit > above:
            if not run:
                before = last_cold
            run.append(reading)
            continue
        if run:
            events.append(_describe(run, before, reading))
            run = []
        last_cold = reading.temperature_fahrenheit

    if run:
        # Still warm at the end of the window: unfinished, so it has no
        # recovery and must not be credited with one.
        events.append(_describe(run, before, None))
    return events


def _describe(
    run: List[Reading], before: Optional[float], after: Optional[Reading]
) -> Dict[str, Any]:
    minutes = (run[-1].recorded_at - run[0].recorded_at).total_seconds() / 60.0
    peak = max(r.temperature_fahrenheit for r in run)
    recovered = (
        after is not None
        and before is not None
        and after.temperature_fahrenheit <= before + RECOVERY_TOLERANCE_F
    )
    return {
        "started_at": run[0].recorded_at,
        "ended_at": run[-1].recorded_at,
        "minutes": round(minutes, 1),
        "peak_f": round(peak, 2),
        "before_f": None if before is None else round(before, 2),
        "recovered": recovered,
        "samples": len(run),
        "ongoing": after is None,
    }


def _rhythm(events: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """The interval this unit repeats on, if it repeats on one.

    Median rather than mean, though with the spread check below the two
    agree on everything that gets this far: a gap far enough out to pull
    a mean is already far enough out to fail the spread. The median is
    kept because it is the right default if that tolerance is ever
    loosened, not because it is doing work today.
    """
    finished = [e for e in events if not e["ongoing"]]
    if len(finished) < MIN_EVENTS_FOR_RHYTHM:
        return None

    gaps = [
        (b["started_at"] - a["started_at"]).total_seconds() / 3600.0
        for a, b in zip(finished, finished[1:])
    ]
    plausible = [
        g for g in gaps
        if DEFROST_MIN_INTERVAL_HOURS <= g <= DEFROST_MAX_INTERVAL_HOURS
    ]
    if len(plausible) < MIN_EVENTS_FOR_RHYTHM - 1:
        return None

    interval = statistics.median(plausible)
    spread = max(abs(g - interval) for g in plausible)
    if spread > INTERVAL_TOLERANCE_HOURS:
        return None

    return {
        "interval_hours": round(interval, 2),
        "spread_hours": round(spread, 2),
        "observed": len(plausible) + 1,
        "typical_minutes": round(
            statistics.median([e["minutes"] for e in finished]), 1),
    }


def classify(
    readings: List[Reading],
    above: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """What the current warm spell most likely is, and why.

    The reasoning is returned with the verdict, always. A classifier that
    only says "defrost" is asking to be trusted; one that says "defrost,
    because this unit has done exactly this every 8.1 hours for the last
    four days and recovered each time" can be argued with, which is the
    only way anybody comes to rely on it.
    """
    unknown = {
        "pattern": "unknown",
        "confident": False,
        "because": "Not enough history yet to tell a routine cycle from a "
                   "fault. Until there is, everything is treated as a fault.",
        "rhythm": None,
        "event": None,
    }
    if not readings:
        return unknown

    events = _warm_events(readings, above)
    real = [e for e in events if e["minutes"] >= MIN_EVENT_MINUTES]
    if not real:
        return {**unknown, "pattern": "none",
                "because": "Nothing has been above the line long enough to "
                           "count as an event."}

    current = real[-1]
    rhythm = _rhythm(real[:-1]) if len(real) > 1 else None

    # Defrost before door, deliberately. A unit whose cycle is shorter
    # than DOOR_MAX_MINUTES would otherwise be called a door on every
    # cycle: true enough that it is not a fault, and it throws away the
    # schedule, which is the thing worth knowing.
    if rhythm is not None:
        allowed = rhythm["typical_minutes"] * OVERRUN_FACTOR + OVERRUN_GRACE_MINUTES
        overrunning = current["ongoing"] and current["minutes"] > allowed
        _rhythmic = _on_schedule(real, rhythm) and not overrunning
        if _rhythmic and (current["recovered"] or current["ongoing"]):
            elapsed = (
                (now or current["ended_at"]) - current["started_at"]
            ).total_seconds() / 3600.0
            return {
                "pattern": "defrost",
                "confident": not current["ongoing"],
                "because": (
                    f"This unit warms up every {rhythm['interval_hours']:.1f} "
                    f"hours for about {rhythm['typical_minutes']:.0f} minutes "
                    f"and comes back down. It has done it "
                    f"{rhythm['observed']} times. This looks like the same "
                    "thing, on time."
                ),
                "rhythm": {
                    **rhythm,
                    "next_due_in_hours": (
                        None if current["ongoing"]
                        else round(rhythm["interval_hours"] - elapsed, 1)),
                },
                "event": current,
            }

    # A door: short, and it came back on its own.
    if (not current["ongoing"] and current["recovered"]
            and current["minutes"] <= DOOR_MAX_MINUTES):
        return {
            "pattern": "door",
            "confident": True,
            "because": (
                f"Up for {current['minutes']:.0f} minutes and straight back "
                f"down to {current['before_f']}°. That is the shape of a "
                "door being opened, not a unit failing."
            ),
            "rhythm": rhythm,
            "event": current,
        }

    # Everything else: warm, and nothing says it is meant to be.
    return {
        "pattern": "failure",
        "confident": bool(rhythm) or current["minutes"] > DOOR_MAX_MINUTES,
        "because": (
            f"Above the line for {current['minutes']:.0f} minutes"
            + ("" if current["recovered"] else " and not back down yet")
            + (
                ". This unit has a routine cycle and this is not it."
                if rhythm else
                ". No routine cycle has been learned for this unit, so "
                "there is nothing to excuse it."
            )
        ),
        "rhythm": rhythm,
        "event": current,
    }


def _on_schedule(events: List[Dict[str, Any]], rhythm: Dict[str, float]) -> bool:
    """Whether the latest event arrived when the rhythm said it would.

    The point of learning a rhythm is to notice when something breaks it.
    A cycle three hours early is not the timer; it is worth a look.
    """
    if len(events) < 2:
        return False
    gap = (
        events[-1]["started_at"] - events[-2]["started_at"]
    ).total_seconds() / 3600.0
    return abs(gap - rhythm["interval_hours"]) <= INTERVAL_TOLERANCE_HOURS
