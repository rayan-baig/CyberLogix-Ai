"""After "Close anyway", the pages stay honest; and every live alert counts.

The round-5 review, from a bakery's walk-in fridge:

- Closed by hand ("Close anyway") at 47.6°F, then a reading of 60.5°F, and
  /today said "Nothing is spoiling. None of it is an emergency." Nobody
  was alerted: the hold after a close by hand held even a reading 19°F
  over the limit.
- The "Open alerts" tile said 0 while an alert was open, because Maya had
  pressed "I'm on it": it counted only alerts nobody had answered.

Now a reading 15°F or more past the limit ends the hold and alerts at
once; while it is held, every page says who closed it, when, how warm it
is and when we alert again; and the tile counts every alert not closed,
with who is handling it.
"""

from datetime import timedelta

from domain import _parse
from store import STORE

import tests.test_its_fixed_and_one_clock as base
from tests.test_its_fixed_and_one_clock import open_alert, overview, pulse, unit_row

# The same café and the same recording phone as the "It's fixed" tests.
cafe = base.cafe
phone = base.phone

ROOT_JS = __import__("pathlib").Path(__file__).resolve().parent.parent / "static"


def close_anyway(api, rosa, incident_id, note="Moved the food to another fridge"):
    resp = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={
        "close_anyway": True, "note": note})
    assert resp.status_code == 200, resp.text
    return STORE.get_incident(incident_id)


# --- the hold after a close by hand ------------------------------------------


def test_a_reading_far_past_the_limit_ends_the_hold_and_alerts_at_once(api, cafe, phone):
    """The review's case: closed at 47.6°F, then 60.5°F, and nobody alerted."""
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers, temp=47.6)
    close_anyway(api, rosa, incident_id)
    held = pulse(api, headers, 47.6)
    assert held["status"] == "WATCHING", held
    texts = len(phone["sms"])

    far = pulse(api, headers, 60.5)            # 19.5°F over a 41°F limit

    assert far["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", far
    again = STORE.latest_unresolved_incident("WALK-IN")
    assert again is not None and again.incident_id != incident_id
    assert len(phone["sms"]) > texts, "nobody was texted"


def test_fifteen_degrees_over_is_the_line(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers, temp=47.6)
    close_anyway(api, rosa, incident_id)
    assert pulse(api, headers, 55.9)["status"] == "WATCHING"     # 14.9 over
    assert pulse(api, headers, 56.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


def test_closed_while_already_far_out_keeps_its_wait(api, cafe, phone):
    """She saw 60°F and closed it anyway: the same reading again is not news."""
    headers, rosa, _ = cafe
    pulse(api, headers, 37.0)
    raised = pulse(api, headers, 60.0)         # far past: alerted at once
    assert raised["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", raised
    close_anyway(api, rosa, raised["incident_id"], note="Called for a repair")
    assert pulse(api, headers, 60.0)["status"] == "WATCHING"
    assert STORE.latest_unresolved_incident("WALK-IN") is None


def test_closed_far_out_then_back_under_then_far_again_alerts(api, cafe, phone):
    headers, rosa, _ = cafe
    pulse(api, headers, 37.0)
    raised = pulse(api, headers, 60.5)
    close_anyway(api, rosa, raised["incident_id"], note="Called for a repair")
    assert pulse(api, headers, 47.6)["status"] == "WATCHING"      # came down
    assert pulse(api, headers, 60.5)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


def test_while_held_every_page_gets_who_when_how_warm_and_when_again(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers, temp=47.6)
    closed = close_anyway(api, rosa, incident_id)
    pulse(api, headers, 47.6)

    cond = unit_row(overview(api, rosa))["condition"]

    assert cond["state"] == "watching"
    assert cond["held_after_close"] is True
    assert cond["closed_by"] == "Rosa"
    assert abs((_parse(cond["closed_at"]) - closed.resolved_at).total_seconds()) < 1
    assert cond["temperature_display"] == 47.6
    assert cond["far_display"] == 56.0
    assert abs((_parse(cond["alert_at"]) - (closed.resolved_at + timedelta(minutes=20)))
               .total_seconds()) < 1


def test_today_never_says_nothing_is_spoiling_while_a_unit_is_out_of_range():
    js = (ROOT_JS / "simple.js").read_text()
    spoil = js.index("<h1>Nothing is spoiling</h1>")
    out = js.index('(u.condition || {}).state === "watching"')
    # The out-of-range check returns before the calm headline is reached.
    assert out < spoil
    assert "is still ${c.word === \"Too cold\" ? \"too cold\" : \"too warm\"}" in js
    words = (ROOT_JS / "words.js").read_text()
    assert "Closed by hand by ${person(c.closed_by)} at ${clock(c.closed_at)}." in words
    assert "We'll alert again at ${clock(c.alert_at)}" in words


def test_the_watching_words_never_say_the_wait_is_already_up(api, cafe, phone):
    """'Out of range for 1 minute. We alert if it stays out for 1 minute.'"""
    headers, rosa, _ = cafe
    set_wait = api.post("/api/licenses/me/sensors/WALK-IN/alert-rule", headers=rosa,
                        json={"alert_after_minutes": 1})
    assert set_wait.status_code == 200, set_wait.text
    assert STORE.get_sensor("WALK-IN").alert_after_minutes == 1
    pulse(api, headers, 37.0)
    for r in STORE.readings_for("WALK-IN"):
        r.recorded_at -= timedelta(minutes=5)
    first = pulse(api, headers, 44.0)
    for r in STORE.readings_for("WALK-IN"):
        r.recorded_at -= timedelta(seconds=36)    # out 0.6 minutes: rounds to 1
    held = pulse(api, headers, 44.2)
    assert first["status"] == held["status"] == "WATCHING", held
    words = held["message"]
    assert "Out of range for 1 minute. We alert if it stays out for 1 minute" not in words
    assert "more seconds" in words, words


# --- the Open alerts tile ----------------------------------------------------


def test_open_alerts_counts_one_somebody_is_handling(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    assert overview(api, rosa)["summary"]["open_incidents"] == 1
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=rosa, json={})

    s = overview(api, rosa)["summary"]

    assert s["open_incidents"] == 1, "the tile said 0 while Rosa had it"
    assert s["unanswered_incidents"] == 0
    assert s["handled_incidents"] == 1
    assert s["handled_by"] == ["Rosa"]


def test_open_alerts_drops_to_zero_once_closed(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    close_anyway(api, rosa, incident_id)
    s = overview(api, rosa)["summary"]
    assert s["open_incidents"] == 0 and s["handled_incidents"] == 0


def test_a_units_own_page_counts_a_handled_alert_as_open(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=rosa, json={})
    detail = api.get("/api/console/sensor/WALK-IN", headers=rosa).json()
    assert detail["open_incidents"] == 1


def test_the_tile_says_who_is_handling_it():
    js = (ROOT_JS / "console.js").read_text()
    assert 'tile("Open alerts", `${s.open_incidents}`, openAlertsNote(s))' in js
    assert "being handled by" in js
