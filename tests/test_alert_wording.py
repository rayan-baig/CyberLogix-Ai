"""What the alert says: the text, the email and the call, for every trade.

Settled on, and held here:

  * The text fits ONE SMS segment: 160 GSM-7 characters. A second segment
    doubles the cost of the largest delivery line, and a split message
    can arrive out of order or half of it not at all.
  * Facts first: the unit, where, the reading, and how far past its limit
    -- then what to check, then "Reply 1". If anything has to give it is
    the "what to check" line, then the names; never the reading, the
    limit or how to answer.
  * Plain words. No sector catalogue names, no "catastrophe".
  * The call says which unit, which site, how far out, and "press 1" --
    and only says "press 1" when pressing it reaches something.
"""

import re

import pytest

import notifications
import wording
from domain import INDUSTRY_PROFILES
from notifications import GSM7_SEGMENT, sms_segments
from store import STORE

SECTORS = sorted(INDUSTRY_PROFILES)
# What a real customer types: a unit name and a site that are not short.
LOCATION = "Main kitchen walk-in freezer"
SITE = "Harborside Street"


def _over(vertical, by=12.0):
    profile = INDUSTRY_PROFILES[vertical]
    if profile["danger_above"] is not None:
        return profile["danger_above"] + by
    return profile["danger_below"] - by


@pytest.fixture()
def estate(api, operator_factory, sensor_factory, break_gemini):
    break_gemini("missing")

    def _make(vertical, unit="F", location=LOCATION, sensor_id="UNIT-01",
              company="Harbor Grill and Oyster Bar"):
        headers, tenant, _ = operator_factory(company_name=company)
        api.post("/api/licenses/me/temperature-unit", headers=headers,
                 json={"temperature_unit": unit})
        site = api.post("/api/sites", headers=headers, json={"name": SITE}).json()
        made = api.post("/api/licenses/me/sensors", headers=headers, json={
            "sensor_id": sensor_id, "industry_vertical": vertical,
            "location_name": location})
        assert made.status_code == 201, made.text
        placed = api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
                          json={"sensor_id": sensor_id})
        assert placed.status_code == 200, placed.text
        body = api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": sensor_id, "temperature_fahrenheit": _over(vertical)}).json()
        assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", body
        return headers, STORE.get_tenant(tenant["tenant_id"]), STORE.get_incident(
            body["incident_id"])

    return _make


@pytest.mark.parametrize("unit", ["F", "C"])
@pytest.mark.parametrize("vertical", SECTORS)
def test_every_trades_text_fits_one_segment_with_the_facts_first(estate, vertical, unit):
    _, tenant, incident = estate(vertical, unit)
    text = incident.sms_text

    assert len(text) <= GSM7_SEGMENT, f"{len(text)} characters: {text}"
    assert sms_segments(text) == 1, text
    assert notifications.to_gsm7(text) == text, "a character outside GSM-7"
    # The order a person reads it in.
    assert text.startswith(f"ALERT {LOCATION} (UNIT-01) at {SITE}: ")
    facts = re.search(rf": (-?[\d.]+){unit}, ([\d.]+){unit} (above|below) "
                      rf"the (-?[\d.]+){unit} limit\. ", text)
    assert facts, text
    reading, delta, _, limit = facts.groups()
    assert float(delta) == pytest.approx(abs(float(reading) - float(limit)), abs=0.11)
    assert wording.ACTIONS[vertical] in text
    assert text.endswith("Reply 1 if you are on it.")
    # Plain words: not the catalogue name, not the cause, not shouting.
    assert INDUSTRY_PROFILES[vertical]["name"] not in text
    assert "catastrophe" not in text.lower() and "EMERGENCY" not in text


def test_every_trades_line_leaves_room_for_the_facts():
    for vertical, line in wording.ACTIONS.items():
        assert len(line) <= wording.ACTION_ROOM, vertical
        assert notifications.to_gsm7(line) == line, vertical


@pytest.mark.parametrize("vertical", ["restaurant", "cryostorage", "pharmacy"])
def test_a_very_long_name_gives_way_but_the_facts_never_do(estate, vertical):
    _, _, incident = estate(vertical, location="North building basement level two " * 3,
                            sensor_id="SENSOR-WITH-A-VERY-LONG-HARDWARE-ID-0001",
                            company="The Very Long Name Hospitality Group International")
    text = incident.sms_text

    assert len(text) <= GSM7_SEGMENT, text
    assert re.search(r"-?[\d.]+F, [\d.]+F (above|below) the -?[\d.]+F limit\.", text), text
    assert text.endswith("Reply 1 if you are on it.")


def test_a_rambling_model_line_is_not_used(api, operator_factory, sensor_factory,
                                            stub_gemini):
    stub_gemini._text = ("URGENT!!! The walk-in freezer is failing and every item "
                        "of stock is at risk, attend immediately\nand call everyone.")
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant", location="Walk-in")
    body = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "FRZ-1", "temperature_fahrenheit": 50.0}).json()

    assert body["sms_dispatch_source"] == "fallback_template"
    assert "URGENT" not in body["dispatched_sms_text"]
    assert wording.ACTIONS["restaurant"] in body["dispatched_sms_text"]


# --- the email ---------------------------------------------------------------


def test_the_business_gets_the_alert_by_email_once(estate, mailbox):
    import telemetry

    _, tenant, incident = estate("pharmacy")
    assert telemetry.wait_for_alert_emails()

    mails = mailbox.to(tenant.contact_email)
    assert len(mails) == 1
    subject, body = mails[0]["Subject"], mails[0].get_body(preferencelist=("plain",)).get_content()
    assert subject.startswith(f"ALERT: {LOCATION} at {SITE} is 58")
    assert "above the limit" in subject
    first = body.splitlines()[0]
    assert first.startswith(f"{LOCATION} (UNIT-01) at ") and "°F" in first
    assert "What to do: " + wording.ACTIONS["pharmacy"] in body
    assert "reply 1 to the text message" in body
    assert incident.incident_id in body
    assert "MTRX Horizyn" in body  # the footer every mail carries


# --- the call ----------------------------------------------------------------


@pytest.mark.parametrize("vertical", SECTORS)
def test_the_call_says_which_unit_where_how_far_and_press_1(estate, vertical):
    from voice_dispatch import build_voice_script

    _, tenant, incident = estate(vertical)
    script, source = build_voice_script(incident, tenant)

    assert source == "fallback_template"
    assert script.startswith("This is CyberLogix AI with a temperature alarm for "
                             f"{tenant.company_name}.")
    assert f"{LOCATION}, at {SITE}, is at " in script
    assert re.search(r"That is [\d.]+ degrees Fahrenheit (above|below) its limit of "
                     r"-?[\d.]+ degrees Fahrenheit\.", script), script
    assert "Press 1 now to say you are dealing with it" in script
    assert wording.ACTIONS[vertical] in script
    # Short: Twilio reads it twice, and it has to be understood half awake.
    assert len(script.split()) <= 75, len(script.split())
    assert "°" not in script and "catastrophe" not in script.lower()


def test_with_nowhere_to_send_a_keypress_the_call_does_not_promise_one(
        api, estate, configured_twilio, monkeypatch):
    configured_twilio()
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "")
    calls = []
    real = notifications.place_voice_call
    monkeypatch.setattr("voice_dispatch.place_voice_call",
                        lambda to, text, tenant_id=None, action_url=None:
                        calls.append((text, action_url)) or real(to, text, tenant_id, action_url))
    headers, _, incident = estate("restaurant")

    resp = api.post(f"/api/voice/escalate/{incident.incident_id}?force=true", headers=headers)

    assert resp.status_code == 200, resp.text
    text, action_url = calls[0]
    assert action_url is None
    assert "Press 1" not in text
    assert "reply 1 to the alert text" in text


def test_press_1_acknowledges_and_no_second_call_is_placed(
        api, estate, configured_twilio, monkeypatch, age_incident):
    """The whole point of saying "press 1": it ends the ladder."""
    from automation import sweep_tenant
    from twilio.request_validator import RequestValidator

    configured_twilio()
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://hub.example.com")
    twiml = []
    monkeypatch.setattr(notifications, "build_twiml",
                        lambda text, url=None, _real=notifications.build_twiml:
                        twiml.append(_real(text, url)) or twiml[-1])
    headers, tenant, incident = estate("restaurant")
    age_incident(incident.incident_id, minutes=30)

    first = sweep_tenant(tenant)
    assert STORE.get_incident(incident.incident_id).voice_escalated_at is not None, first
    assert "<Gather" in twiml[0] and "Press 1 now" in twiml[0]

    token = STORE.get_incident(incident.incident_id).ack_token
    url = notifications.acknowledgement_url(incident.incident_id, token)
    form = {"Digits": "1", "To": "+15550100"}
    pressed = api.post(f"/api/voice/keypress/{incident.incident_id}/{token}", data=form,
                       headers={"X-Twilio-Signature":
                                RequestValidator("token").compute_signature(url, form)})
    assert pressed.status_code == 200 and "Acknowledged" in pressed.text

    calls_before = len(twiml)
    age_incident(incident.incident_id, minutes=120)
    sweep_tenant(tenant)
    assert len(twiml) == calls_before, "a call was placed after 1 was pressed"
    assert not STORE.get_incident(incident.incident_id).open


@pytest.mark.parametrize("vertical", ["restaurant", "cryostorage", "pharmacy"])
def test_a_shortened_name_still_bills_as_one_segment(estate, vertical):
    """A name cut short to fit used to be marked with "~", which GSM-7
    sends as two characters: a text of 160 characters cost two segments,
    doubling the largest delivery line for every long-named unit."""
    _, _, incident = estate(vertical, location="North building basement level two " * 3,
                            sensor_id="SENSOR-WITH-A-VERY-LONG-HARDWARE-ID-0001",
                            company="The Very Long Name Hospitality Group International")
    assert sms_segments(incident.sms_text) == 1, incident.sms_text


def test_a_name_with_brackets_still_bills_as_one_segment(estate):
    """[ ] { } | ^ ~ \\ and the euro sign are two characters each in GSM-7."""
    _, _, incident = estate("restaurant", location="Walk-in [A] {back} | stores " * 2,
                            sensor_id="SENSOR-WITH-A-VERY-LONG-HARDWARE-ID-0001")
    assert sms_segments(incident.sms_text) == 1, incident.sms_text
    assert incident.sms_text.endswith("Reply 1 if you are on it.")
