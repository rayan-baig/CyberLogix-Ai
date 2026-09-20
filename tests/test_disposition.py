"""Do I throw this away, or do I serve it?

The number on the screen is not the question a manager is asking at 7am
with service starting. This module answers the real one -- and the
single most important property of it is that it never answers "yes,
that is safe", because it cannot: the sensor reads the air, not the
middle of a chicken.

Every sentence it produces is either something measured or guidance with
an attribution and a caveat. A green SAFE badge is the version of this
feature that makes somebody ill and takes the company with it.
"""

from datetime import datetime, timedelta, timezone

import disposition
from store import Reading


def at(minutes: float) -> datetime:
    return datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def run(points) -> list:
    return [
        Reading(sensor_id="FRZ-1", temperature_fahrenheit=float(t),
                humidity_percent=None, breached=False, recorded_at=at(m))
        for m, t in points
    ]


def warm_for(minutes, temp=48.0, then_cold=True):
    points = [(0, 38.0)]
    points += [(10 + m, temp) for m in range(0, int(minutes), 10)]
    if then_cold:
        points.append((10 + minutes, 38.0))
    return points


# --- the line this must never cross ---------------------------------------


def test_it_never_says_food_is_safe():
    """The whole reason to be careful here.

    Across every band it can produce, no output may assert safety. It
    reports exposure and quotes guidance; the decision stays with the
    operator and their written plan.
    """
    cases = [
        warm_for(30), warm_for(150), warm_for(300),
        warm_for(60, temp=85.0), warm_for(60, then_cold=False),
        [(0, 38.0), (10, 38.0)],
    ]
    for points in cases:
        out = disposition.assess(run(points), above=41.0, now=at(10_000))
        blob = (out["headline"] + " " + out["detail"]).lower()
        for banned in ("is safe", "safe to serve", "safe to sell",
                       "food is fine", "ok to use"):
            assert banned not in blob, f"{banned!r} in {blob!r}"


def test_every_judgement_says_whose_decision_it_is():
    for points in (warm_for(30), warm_for(150), warm_for(300)):
        out = disposition.assess(run(points), above=41.0, now=at(10_000))
        assert out["decision_is_yours"] is True
        assert "your own plan decides" in out["detail"].lower()


def test_the_caveat_travels_with_the_answer():
    """Not in a footer. This is a screen somebody reads at speed."""
    out = disposition.assess(run(warm_for(150)), above=41.0, now=at(10_000))

    assert "not a food safety decision" in out["not_a_ruling"]
    assert "reads the air" in out["not_a_ruling"]
    assert "they are right" in out["not_a_ruling"]


# --- the measurement ------------------------------------------------------


def test_it_counts_time_not_readings():
    """A sensor reporting every minute and one reporting every fifteen
    must give the same answer for the same freezer."""
    slow = [(0, 38.0)] + [(10 + m, 48.0) for m in range(0, 180, 30)] + [(190, 38.0)]
    fast = [(0, 38.0)] + [(10 + m, 48.0) for m in range(0, 180, 2)] + [(190, 38.0)]

    a = disposition.exposure(run(slow), above=41.0)
    b = disposition.exposure(run(fast), above=41.0)

    assert abs(a["minutes_above"] - b["minutes_above"]) < 1.0
    assert a["readings_above"] != b["readings_above"]


def test_a_unit_still_warm_keeps_counting_up_to_now():
    """A gap in reporting is not a gap in the food warming up."""
    points = [(0, 38.0)] + [(10 + m, 48.0) for m in range(0, 30, 10)]

    out = disposition.exposure(run(points), above=41.0, now=at(300))

    assert out["still_warm"] is True
    # Warm from minute 10 to 300, not just to the last reading at 30.
    assert out["minutes_above"] > 250


def test_the_peak_is_the_worst_it_got_not_the_last_reading():
    points = [(0, 38.0), (10, 44.0), (20, 62.0), (30, 45.0), (40, 38.0)]

    out = disposition.exposure(run(points), above=41.0)

    assert out["peak_f"] == 62.0


# --- the bands ------------------------------------------------------------


def test_under_two_hours_is_the_cool_it_down_band():
    out = disposition.assess(run(warm_for(60)), above=41.0, now=at(10_000))

    assert out["band"] == "cool_it_down"
    assert "under two hours" in out["detail"].lower()


def test_between_two_and_four_hours_is_use_it_now():
    out = disposition.assess(run(warm_for(180)), above=41.0, now=at(10_000))

    assert out["band"] == "use_now"
    assert "immediately" in out["detail"]


def test_over_four_hours_is_the_discard_band():
    out = disposition.assess(run(warm_for(300)), above=41.0, now=at(10_000))

    assert out["band"] == "discard"
    assert "inspector" in out["detail"]


def test_room_temperature_is_discard_whatever_the_clock_says():
    """An hour at 85 degrees is not the same as an hour at 44, and
    counting hours is the wrong question at that point."""
    out = disposition.assess(run(warm_for(60, temp=85.0)),
                             above=41.0, now=at(10_000))

    assert out["band"] == "discard"
    assert "room temperature" in out["detail"]


def test_a_unit_still_warm_refuses_to_judge_yet():
    out = disposition.assess(run(warm_for(60, then_cold=False)),
                             above=41.0, now=at(80))

    assert out["band"] == "still_warm"
    assert "Get it cold" in out["detail"]


def test_nothing_out_of_range_says_so_and_asks_nothing_of_anybody():
    out = disposition.assess(run([(0, 38.0), (10, 39.0)]), above=41.0)

    assert out["band"] == "in_range"
    assert out["decision_is_yours"] is False
