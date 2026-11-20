"""The public demo: a pretend kitchen anyone can watch, with no account.

A restaurant owner deciding whether to try this should be able to see
it work before they hand over an email address. So `/demo` is a console
with four pretend units -- a walk-in cooler, a freezer, a prep fridge and
a bar fridge -- and a minute in, the walk-in starts to warm. They watch
the alert open, the texts go out, the calls start when nobody answers,
and what replying 1 does.

All of it runs in the visitor's browser (static/demo.js). The server's
only part is this one answer: the story, with the words a real alert
would use, put together by the same functions that word real alerts
(wording.py, and the reply and test texts in voice_dispatch.py and
firstday.py), so the demo cannot drift
away from what the product actually sends.

What this module promises, and tests/test_demo.py holds it to:

* **Nothing is sent.** No text, no call, no email: it never calls a
  sending function, and the story is words, not messages.
* **Nothing is kept.** It reads nothing from the store and writes
  nothing to it: no business, no person, no sensor, no reading, no audit
  line, no usage count. There is no sandbox to clean up because there is
  nothing on the server to clean.
* **It is rate limited**, per caller and in total, because it is a door
  anybody can knock on.
* **It names no AI model.** Nothing in it comes from the model layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request

import ratelimit

router = APIRouter(prefix="/api/demo", tags=["Demo"])

# A visitor loads the story once per run, and "Watch it again" once more.
# Thirty in ten minutes is far more than a person needs and far less than
# a script would like.
LIMIT = ratelimit.Limit("demo", per_caller=30, total=900, window_seconds=600,
                        words="demo requests")

LABEL = "Demo — pretend data"
NOTICE = ("Everything on this page is pretend. No texts or calls are sent, "
          "nothing is saved, and no account is made.")

BUSINESS = "Corner Bistro"

# Who the pretend alerts go to. First names only, and nobody in particular.
PEOPLE = [
    {"name": "Sam", "role": "Owner"},
    {"name": "Alex", "role": "Head chef"},
]

# The units, each on the limit the product itself gives it when it is
# added: the add form's "Fridge or freezer?" answer (domain.UNIT_KINDS).
# The demo used to hand-set its own limits while a real walk-in cooler was
# given a freezer's 32°F, so the demo and the product disagreed.
def _limit(kind: str) -> float:
    from domain import unit_kind_limits

    limits = unit_kind_limits("restaurant", kind)
    assert limits is not None and limits[0] is not None
    return limits[0]


UNITS: List[Dict[str, Any]] = [
    {"id": "WALK-IN-1", "name": "Walk-in cooler", "kind": "fridge",
     "limit_f": _limit("fridge"), "start_f": 37.2},
    {"id": "FREEZER-1", "name": "Freezer", "kind": "freezer",
     "limit_f": _limit("freezer"), "start_f": -1.5},
    {"id": "PREP-1", "name": "Prep fridge", "kind": "fridge",
     "limit_f": _limit("fridge"), "start_f": 36.4},
    {"id": "BAR-1", "name": "Bar fridge", "kind": "fridge",
     "limit_f": _limit("fridge"), "start_f": 37.0},
]
DRIFTING = "WALK-IN-1"

# One pretend reading every two seconds. A real sensor reads every few
# minutes; the page says so.
TICK_SECONDS = 2
# The walk-in starts to warm a minute in.
DRIFT_STARTS_SECONDS = 60
# Its readings from then on, one per tick: a door left open, then the
# compressor losing the fight. The alert opens on the first one over the
# limit; the call is placed when the real wait has passed.
DRIFT_F = [37.8, 38.7, 39.6, 40.5, 42.0, 43.1, 44.0, 44.6, 45.1, 45.5,
           45.8, 46.1, 46.3, 46.5, 46.6, 46.7, 46.8, 46.9, 47.0, 47.1]
# What it does once somebody has dealt with it: back down to normal.
RECOVER_F = [45.2, 43.4, 41.5, 39.8, 38.6, 37.9, 37.5, 37.3]

# How long the demo stands in for the real wait before a call. The real
# wait is wording's grace window (VOICE_ESCALATION_GRACE_MINUTES).
DEMO_WAIT_SECONDS = 20
RING_SECONDS = 8


class _Unit(SimpleNamespace):
    """Enough of a sensor for the wording functions, and nothing more."""

    def bounds(self) -> Tuple[Optional[float], Optional[float]]:
        return self.limit_f, None


class _Incident(SimpleNamespace):
    def minutes_open(self, now=None) -> float:
        return float(self.minutes)


def _unit(row: Dict[str, Any]) -> _Unit:
    return _Unit(sensor_id=row["id"], location_name=row["name"],
                 industry_vertical="restaurant", site_id=None,
                 limit_f=row["limit_f"])


def story() -> Dict[str, Any]:
    """The whole demo, as data. Built fresh each time; nothing is stored."""
    import wording
    from firstday import sms_text
    from domain import VOICE_ESCALATION_GRACE_MINUTES
    from voice_dispatch import ack_reply_text

    business = SimpleNamespace(company_name=BUSINESS)
    walk_in = _unit(next(u for u in UNITS if u["id"] == DRIFTING))
    alert_at = next(i for i, f in enumerate(DRIFT_F) if f > walk_in.limit_f)
    alert_f = DRIFT_F[alert_at]
    # Where it has got to by the time the call goes out.
    call_at = min(alert_at + DEMO_WAIT_SECONDS // TICK_SECONDS, len(DRIFT_F) - 1)
    incident = _Incident(temperature_fahrenheit=DRIFT_F[call_at],
                         minutes=VOICE_ESCALATION_GRACE_MINUTES)
    first = PEOPLE[0]["name"]

    return {
        "label": LABEL,
        "notice": NOTICE,
        "business": BUSINESS,
        "people": PEOPLE,
        "units": UNITS,
        "drifting": DRIFTING,
        "tick_seconds": TICK_SECONDS,
        "drift_starts_seconds": DRIFT_STARTS_SECONDS,
        "drift_f": DRIFT_F,
        "recover_f": RECOVER_F,
        "alert_index": alert_at,
        "demo_wait_seconds": DEMO_WAIT_SECONDS,
        "ring_seconds": RING_SECONDS,
        "real_wait_minutes": VOICE_ESCALATION_GRACE_MINUTES,
        "messages": {
            # Word for word what a real alert would say, from the same code.
            "alert_text": wording.alert_sms(business, walk_in, alert_f, "F"),
            "call_script": wording.voice_script(business, walk_in, incident, "F"),
            "ack_reply": ack_reply_text(first, walk_in.location_name),
            "test_text": sms_text(business),
        },
        "signup_href": "/signup?sector=restaurant",
    }


@router.get("/story")
def get_story(request: Request) -> Dict[str, Any]:
    """The pretend kitchen and the words its alert would use. Sends
    nothing, keeps nothing, and needs no sign-in."""
    LIMIT.check(request)
    return story()
