"""Round 13 (3.36.0): the pack says what really happened, and reads well.

1. The summary told every adjuster "The alert was sent automatically at
   detection", on a server with texts switched off too. It now says how
   people were told, from the alert's own delivery record: "The alert was
   texted to 2 people at 2:18 AM, after the 20-minute wait; it was
   acknowledged 12 minutes later", "written but not delivered: texts are
   off", or that the texts failed. The saved site address is in it, alone
   where the site has no name. The sample pack says the same.
2. The pack page: "Closed at 4:41 AM", never "Closed: closed at 4:41 AM";
   readings with one decimal ("52.0°F"); the time zone on the issued
   line; on a phone the channel table stacks rather than scroll sideways
   out of sight; on paper the Recorded evidence box keeps together.
3. "Thrown away. What next?" fits a phone (theme.css; every dialog at
   320px and 375px in tests/test_round13_dialogs_fit.py), and /today waits
   after "It's fixed" instead of offering it again.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import claims

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
SIMPLE_JS = (STATIC / "simple.js").read_text()
THEME = (STATIC / "theme.css").read_text()
SAMPLE = (STATIC / "sample-pack.html").read_text()
SAMPLE_CSS = (STATIC / "sample-pack.css").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


def _sign_up(api, email="maya@bakery.example"):
    made = api.post("/api/signup", json={
        "company_name": "Maya's Bakery", "full_name": "Maya Okafor", "email": email,
        "password": "croissant-dough-31", "contact_phone": "+15550104477",
        "industry_vertical": "restaurant"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _alert(api, owner, warm=62.0):
    made = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": "WALK-IN-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in fridge", "unit_kind": "fridge"})
    assert made.status_code == 201, made.text
    key = made.json()["ingest_key"]
    assert api.post("/api/licenses/me/sensors/WALK-IN-1/alert-rule", headers=owner,
                    json={"alert_after_minutes": 0}).status_code == 200

    def pulse(temp):
        got = api.post("/api/sensor-pulse", headers={"X-Kryozyn-Sensor-Key": key},
                       json={"sensor_id": "WALK-IN-1", "temperature_fahrenheit": temp})
        assert got.status_code == 200, got.text
        return got.json()

    pulse(37.5)
    incident_id = pulse(warm)["incident_id"]
    assert incident_id
    return incident_id


def _packet(event=None, notified=None):
    """A pack as build_packet gives it, only the parts told_words reads."""
    return {"event": {"opened_at": "2026-03-15T06:18:00Z", "out_since": "2026-03-15T05:58:00Z",
                      "minutes_to_acknowledge": 12.0, **(event or {})},
            "response": {"notified": notified}}


NEW_YORK = claims.reader_zone("America/New_York")
NIGHT = datetime(2026, 3, 15, 5, 58, tzinfo=timezone.utc)  # 1:58 AM EDT
SENT = {"texts": "sent", "at": "2026-03-15T06:18:00Z", "texted": 2, "delivered": 2, "wait_minutes": 20.0,
        "out_minutes_before": 20.0, "retries": [], "call": None}


# --- 1. how people were told ----------------------------------------------------------

def test_the_summary_says_who_was_texted_when_and_after_the_wait():
    said = claims.told_words(_packet(notified=SENT), NEW_YORK, NIGHT)
    assert said == ("The alert was texted to 2 people at 2:18 AM, after the 20-minute wait; "
                    "it was acknowledged 12 minutes later.")
    # Another day: the date and the zone again.
    other = claims.told_words(_packet(notified=SENT), NEW_YORK, datetime(2026, 3, 13, tzinfo=timezone.utc))
    assert "at Mar 15, 2026, 2:18 AM EDT" in other


def test_texts_switched_off_are_written_but_not_delivered():
    off = dict(SENT, texts="off", delivered=0)
    said = claims.told_words(_packet(notified=off), NEW_YORK, NIGHT)
    assert said.startswith("The alert text was written at 2:18 AM, after the 20-minute wait, but not delivered: "
                           "texts are off on this server, so nobody was texted.")
    assert "texted to" not in said and "sent" not in said.split("texts are off")[0]
    assert "It was acknowledged 12 minutes after the alert opened." in said


def test_failed_texts_are_said_as_failed_and_so_is_each_try_again():
    failed = dict(SENT, delivered=0, retries=[{"at": "2026-03-15T06:23:00Z", "texted": 2, "delivered": 0},
                                              {"at": "2026-03-15T06:33:00Z", "texted": 2, "delivered": 1}])
    said = claims.told_words(_packet(notified=failed, event={"minutes_to_acknowledge": None}), NEW_YORK, NIGHT)
    assert said == ("The alert was texted to 2 people at 2:18 AM, after the 20-minute wait, and failed: none went through. "
                    "It was sent again at 2:23 AM: it failed again. It was sent again at 2:33 AM: 1 of 2 went through.")
    partly = claims.told_words(_packet(notified=dict(SENT, texted=3, delivered=2)), NEW_YORK, NIGHT)
    assert partly == ("The alert was texted to 3 people at 2:18 AM, after the 20-minute wait; 2 went through and 1 failed. "
                      "It was acknowledged 12 minutes after the alert opened.")


def test_nobody_to_text_calls_and_alerting_at_once():
    none = claims.told_words(_packet(notified=dict(SENT, texts="none", texted=0, delivered=0)), NEW_YORK, NIGHT)
    assert none.startswith("No alert text went out at 2:18 AM: nobody was on the list to text.")
    at_once = claims.told_words(_packet(notified=dict(SENT, wait_minutes=None, out_minutes_before=0.2)), NEW_YORK, NIGHT)
    assert at_once.startswith("The alert was texted to 2 people at 2:18 AM;"), at_once
    called = claims.told_words(_packet(notified=dict(SENT, call={"at": "2026-03-15T06:28:00Z", "reached": False,
                                                                 "off": True, "tried": True})), NEW_YORK, NIGHT)
    assert "The call step came at 2:28 AM, but calls are off on this server, so no call was placed." in called
    reached = claims.told_words(_packet(notified=dict(SENT, call={"at": "2026-03-15T06:28:00Z", "reached": True,
                                                                  "off": False, "tried": True})), NEW_YORK, NIGHT)
    assert "A phone call got through at 2:28 AM." in reached


def test_a_real_pack_on_a_server_with_texts_off_never_says_sent(api, break_gemini):
    break_gemini("missing")  # the plain letter, not an AI's
    owner = _sign_up(api)
    incident_id = _alert(api, owner)
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    told = pack["response"]["notified"]
    assert told["texts"] == "off" and told["texted"] >= 1 and told["delivered"] == 0
    letter = pack["cover_letter"]
    assert "sent automatically" not in letter
    assert "was written at" in letter and "but not delivered: texts are off on this server, so nobody was texted." in letter
    # One decimal on the pack: the breach and the worst.
    assert "62.0°F: too warm, over the 41°F limit." in pack["event"]["breach_details"]
    assert "the worst 62.0°F" in letter


def test_the_saved_address_is_in_the_summary(api, break_gemini):
    break_gemini("missing")
    owner = _sign_up(api)
    incident_id = _alert(api, owner)
    assert api.post("/api/claims/site-address", headers=owner,
                    json={"address": "12 Harbor Street, Springfield"}).status_code == 200
    pack = api.post(f"/api/claims/{incident_id}/packet", headers=owner).json()
    assert pack["location"]["site"] is None
    assert pack["cover_letter"].startswith(
        "Maya's Bakery reports that the Walk-in fridge at 12 Harbor Street, Springfield went out of its safe range at ")


def test_the_sample_pack_says_what_a_real_one_would():
    text = " ".join(SAMPLE.split())
    said = claims.told_words(_packet(notified=SENT), NEW_YORK, NIGHT)
    assert said in text, said
    assert "sent automatically" not in text
    assert "the Walk-in fridge at 12 Harbor Street, Springfield went out of its safe range at Mar 15, 2026, 1:58 AM EDT." in text
    # 3.37.0: closed by itself after Rosa said it was fixed, never "Closed by Rosa".
    assert ("Rosa Diaz said it was fixed at 4:36 AM; the alert closed by itself at 4:41 AM, back to 38.2°F. "
            "The food was recorded as “Thrown away” by Rosa Diaz at 4:44 AM.") in text
    assert "Closed by Rosa" not in text
    assert '<span class="k">Closed at</span><span class="v">Mar 15, 2026, 4:41 AM, back in range by itself</span>' in SAMPLE
    assert "closed at Mar" not in SAMPLE
    assert '<table class="doc-channels">' in SAMPLE and 'data-label="Outcome"' in SAMPLE
    assert '<div class="doc-keep">\n    <div class="doc-section">Recorded evidence</div>' in SAMPLE
    for temp in re.findall(r"(\d+(?:\.\d+)?)°F", text):
        assert "." in temp or temp in ("41",), f"{temp}°F: a reading has one decimal (a limit may not)"


# --- 2. the pack page ---------------------------------------------------------------

def test_the_pack_page_reads_well_on_a_phone_and_on_paper():
    packet = _function(CONSOLE_JS, "openPacket")
    assert 'const closedKey = ev.resolved_at ? "Closed at" : "Closed";' in packet
    assert "closed at ${stamp(ev.resolved_at)}" not in packet
    assert "issued ${esc(at(packet.issued_at))}" in packet
    assert "oneDecimal(r.temperature)" in packet and "oneDecimal(ev.detected_reading)" in packet
    assert "oneDecimal(evidence.peak_reading)" in packet
    assert '<table class="doc-channels">' in packet and 'data-label="Outcome"' in packet
    assert '<div class="doc-keep">' in packet
    # 3.40.0: and never for the demo's pretend texts.
    assert 'told.texts === "sent" && told.delivered && !told.demo ? "Alert texted" : "Alert opened"' in packet
    assert "Alert sent" not in packet
    zone = _function(CONSOLE_JS, "stampZone")
    assert 'timeZoneName: "short"' in zone
    assert "n.toFixed(1)" in _function(CONSOLE_JS, "oneDecimal")
    # Stacked on a phone, kept together on paper.
    phone = THEME[THEME.index(".doc-channels { width: 100%; }"):]
    assert "@media (max-width: 560px)" in phone[:300]
    assert ".doc-channels thead { display: none; }" in phone and "content: attr(data-label);" in phone
    assert ".doc-keep { break-inside: avoid; }" in THEME


# --- 3. the dialogs and /today -------------------------------------------------------

def test_dialog_buttons_wrap_and_thrown_away_stacks_on_a_phone():
    assert ".confirm-dialog .btn { white-space: normal; text-align: center; max-width: 100%; }" in THEME
    assert ".loss-dialog .food-acts .btn:not(.linkish) { flex: 1 1 220px; }" in THEME
    assert ".loss-dialog .food-acts .btn { flex: 1 1 auto; }" not in THEME
    assert ".connect-serial { display: grid; grid-template-columns: minmax(0, 1fr);" in THEME


def test_after_its_fixed_today_and_the_console_bar_wait():
    # 3.44.0: It's fixed stays on /today's card while the alert is open,
    # so Close anyway is never out of reach (round 21).
    assert 'data-fixed="${esc(i.incident_id)}">It\'s fixed</button>' in _function(SIMPLE_JS, "needButtons")
    assert "It closes by itself once it's cold." in _function(SIMPLE_JS, "fixWaiting")
    bar = _function(CONSOLE_JS, "renderNeedsBar")
    assert '${i.fix_reported_at && i.state === "acknowledged" ? ""' in bar
    assert "said it's fixed. It closes by itself once it's cold." in bar
