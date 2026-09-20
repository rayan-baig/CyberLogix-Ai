"""A defrost is not a failure, and a door is neither.

The forecaster fits a line and projects when it crosses the limit, and
is blind to the fact that decides whether an alert is worth sending: a
commercial freezer warms up on purpose, every six to twelve hours, to
melt frost off the coil. To a straight-line fit that is a steep slope
with a beautiful r-squared and forty minutes to a breach -- confident,
wrong, and repeated two to four times a day for ever.

The failure that matters is not a wrong number. It is the third false
alarm, after which nobody reads the fourth.
"""

from datetime import datetime, timedelta, timezone

import patterns
from store import Reading


def at(minutes: float) -> datetime:
    return datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def run(points) -> list:
    """(minutes from start, °F) -> readings."""
    return [
        Reading(sensor_id="FRZ-1", temperature_fahrenheit=float(t),
                humidity_percent=None, breached=False, recorded_at=at(m))
        for m, t in points
    ]


def steady(start_min, end_min, temp, step=10):
    return [(m, temp) for m in range(int(start_min), int(end_min), step)]


def cycle(start_min, warm_minutes, cold=0.0, hot=20.0, step=10):
    """One warm spell: cold, up, back to cold."""
    out = steady(start_min, start_min + 30, cold, step)
    out += [(start_min + 30 + m, hot)
            for m in range(0, int(warm_minutes), step)]
    return out


# --- the thing that gets the product switched off -------------------------


def test_a_regular_recovering_cycle_is_read_as_a_defrost():
    """Every eight hours, twenty minutes, back down. Four times."""
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    points += steady(4 * 480 - 100, 4 * 480 - 40, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "defrost"
    assert verdict["rhythm"]["interval_hours"] == 8.0
    assert verdict["rhythm"]["observed"] >= 3
    assert "every 8.0 hours" in verdict["because"]


def test_the_verdict_always_says_why():
    """A classifier that only says "defrost" is asking to be trusted.

    One that says which interval, how many times, and that it recovered
    can be argued with, and being argued with is how anybody comes to
    rely on it.
    """
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    points += steady(4 * 480 - 100, 4 * 480 - 40, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["because"]
    assert "8.0" in verdict["because"]
    assert str(verdict["rhythm"]["observed"]) in verdict["because"]


def test_a_short_spike_that_recovers_is_a_door():
    points = steady(0, 120, 0.0) + [(120, 20.0), (130, 22.0)]
    points += steady(140, 260, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "door"
    assert verdict["confident"] is True
    assert "door" in verdict["because"]


def test_a_climb_that_does_not_come_back_is_a_failure():
    points = steady(0, 240, 0.0)
    points += [(240 + m, 12.0 + m / 10.0) for m in range(0, 300, 10)]

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "failure"
    assert "not back down" in verdict["because"]


def test_a_unit_with_a_rhythm_that_breaks_it_is_still_a_failure():
    """The whole point of learning the schedule is noticing when
    something is off it."""
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    # ...and then one that starts early and never recovers.
    points += steady(4 * 480, 4 * 480 + 30, 0.0)
    points += [(4 * 480 + 30 + m, 20.0) for m in range(0, 400, 10)]

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "failure"
    assert "routine cycle and this is not it" in verdict["because"]


# --- refusing to guess ----------------------------------------------------


def test_with_no_history_it_says_so_rather_than_guessing():
    """Everything is a fault until the unit has earned the benefit of
    the doubt. A classifier that assumed "probably a defrost" on day one
    would swallow a real failure in week one."""
    points = steady(0, 60, 0.0) + [(60 + m, 20.0) for m in range(0, 60, 10)]

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "failure"
    assert verdict["rhythm"] is None


def test_one_gap_is_not_a_rhythm():
    """Two past events make one gap, and one gap is a coincidence with a
    ruler held up to it.

    Three events in total, because the rhythm is learned from everything
    *before* the current one -- so it takes three on screen to put two
    in the history and one gap between them.
    """
    points = (cycle(0, warm_minutes=20) + cycle(480, warm_minutes=20)
              + cycle(960, warm_minutes=20))
    points += steady(1040, 1100, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["rhythm"] is None
    assert verdict["pattern"] != "defrost"


def test_an_interval_no_freezer_runs_is_not_a_schedule():
    """Three warm spells forty minutes apart are three door openings in
    a busy hour, not a defrost timer."""
    points = []
    for n in range(4):
        points += cycle(n * 40, warm_minutes=10, step=5)
    points += steady(200, 260, 0.0, step=5)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] != "defrost"


def test_an_irregular_interval_is_not_a_schedule():
    """A mechanical timer drifts by minutes, not by hours."""
    points = []
    for start in (0, 480, 1100, 1300):
        points += cycle(start, warm_minutes=20)
    points += steady(1400, 1460, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["rhythm"] is None


def test_nothing_warm_at_all_is_reported_as_nothing():
    verdict = patterns.classify(run(steady(0, 300, 0.0)), above=10.0)

    assert verdict["pattern"] == "none"


def test_no_readings_is_unknown_not_fine():
    verdict = patterns.classify([], above=10.0)

    assert verdict["pattern"] == "unknown"
    assert verdict["confident"] is False


def test_a_spell_still_running_is_never_credited_with_recovering():
    """The window ended while it was warm. Not knowing how it ends is
    not the same as knowing it ended well."""
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    # Cold in between, or the last cycle and this one merge into one
    # enormous warm run -- which the classifier correctly refuses to
    # call a defrost, for the wrong reason.
    points += steady(4 * 480 - 420, 4 * 480, 0.0, step=30)
    points += [(4 * 480 + m, 20.0) for m in range(0, 30, 10)]

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["event"]["minutes"] == 20.0
    assert verdict["event"]["ongoing"] is True
    assert verdict["event"]["recovered"] is False
    assert verdict["confident"] is False, (
        "an unfinished event cannot be called confidently either way")


def test_a_defrost_that_never_ends_stops_being_a_defrost():
    """The failure a schedule-aware classifier would otherwise excuse.

    A unit that fails to come out of defrost started exactly on time, so
    every timing check passes -- and there is a warm freezer full of food
    behind it. Defrosts end; one running at twice its usual length is a
    fault whatever the clock says.
    """
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    points += steady(4 * 480 - 420, 4 * 480, 0.0, step=30)
    # Starts dead on schedule, and then just keeps going.
    points += [(4 * 480 + m, 20.0) for m in range(0, 300, 10)]

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "failure"
    assert verdict["rhythm"] is not None, (
        "the rhythm is still known; it is just not an excuse any more")


def test_a_cycle_at_the_wrong_time_is_not_excused_by_the_schedule():
    """It recovered, and it looks exactly like the others. It just
    happened at the wrong hour, which is the one thing a schedule is
    for noticing."""
    points = []
    for n in range(4):
        points += cycle(n * 480, warm_minutes=20)
    points += steady(1490, 1700, 0.0, step=30)
    # Three hours early, and otherwise a perfect copy of a defrost.
    points += cycle(1700, warm_minutes=20)
    points += steady(1780, 1850, 0.0, step=30)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] != "defrost"
    assert verdict["rhythm"] is not None


def test_a_spell_that_came_down_but_not_all_the_way_is_not_a_door():
    """A door shutting puts the temperature back where it was. Settling
    at a new, higher plateau is a unit losing the fight."""
    points = steady(0, 120, 0.0)
    points += [(120, 20.0), (130, 22.0)]
    # Back under the line, but nowhere near the 0 degrees it started at.
    points += steady(140, 300, 8.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["event"]["recovered"] is False
    assert verdict["pattern"] == "failure"


def test_a_long_recovering_spell_is_not_a_door_either():
    """Two hours is not somebody holding a door."""
    points = steady(0, 120, 0.0)
    points += [(120 + m, 20.0) for m in range(0, 120, 10)]
    points += steady(250, 400, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "failure"
    assert verdict["event"]["recovered"] is True, (
        "it did come back -- it is still not a door, because of how long")


def test_a_single_warm_sample_is_noise_rather_than_an_event():
    """One reading over the line is a sensor twitching. Naming it an
    event puts it in the history and lets three twitches invent a
    schedule."""
    points = steady(0, 120, 0.0) + [(120, 20.0)] + steady(130, 300, 0.0)

    verdict = patterns.classify(run(points), above=10.0)

    assert verdict["pattern"] == "none"


# --- what the forecast does with it ---------------------------------------


def test_the_forecast_carries_the_shape_alongside_the_slope(
        api, tenant_factory, sensor_factory):
    """The slope says when the line is crossed. The shape says whether
    crossing it means anything."""
    headers, _ = tenant_factory(plan="enterprise")
    sensor_factory(headers, "FRZ-1", "restaurant")
    for temp in (33.0, 34.0, 35.0):
        api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": "FRZ-1", "temperature_fahrenheit": temp})

    body = api.get("/api/forecast/sensor/FRZ-1", headers=headers).json()

    assert "pattern" in body
    assert body["pattern"]["pattern"] in {
        "defrost", "door", "failure", "none", "unknown"}
    assert body["pattern"]["because"]
