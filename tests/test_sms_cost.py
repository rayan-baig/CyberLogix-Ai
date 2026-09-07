"""What an alert actually costs to send.

Twilio bills per segment, and a segment is 160 characters only while
every character is in the GSM-7 alphabet. One character outside it — a
degree sign, a curly quote, an em dash — switches the whole message to
UCS-2 and the segment drops to 70.

Every alert this product sent quoted the reading as "71.0°F", which took
a 158-character message from one segment to three. On the line that is
the large majority of delivery cost. And the spend report counted
messages, so the overspend was invisible from inside the product.
"""

import pytest

from notifications import (
    GSM7_SEGMENT,
    sms_segments,
    to_gsm7,
)

ALERT = (
    "EMERGENCY ALERT: Retail & Hospital Pharmacies sensor FRIDGE-01 at "
    "Vaccine fridge reported critical temperature 71.0°F. "
    "Immediate physical inspection required."
)


def test_one_degree_sign_used_to_triple_the_bill():
    """The finding itself, kept as a test so it cannot come back."""
    assert sms_segments(ALERT) == 3, "the premise of this fix has changed"
    assert sms_segments(to_gsm7(ALERT)) == 1


def test_folding_keeps_the_reading_readable():
    """Cheaper is worthless if the person woken at 3am cannot read it."""
    folded = to_gsm7(ALERT)
    for must_survive in ("EMERGENCY", "FRIDGE-01", "Vaccine fridge", "71.0F"):
        assert must_survive in folded, f"{must_survive!r} was lost"
    assert "°" not in folded


@pytest.mark.parametrize("raw,expected", [
    ("71.0°F", "71.0F"),
    ("café", "café"),                    # already in GSM-7
    ("don’t — now", "don't - now"),      # curly quote, em dash
    ("a … b", "a ... b"),
    ("→ 5µm", "-> 5um"),
    ("你好 ok", "ok"),                     # unmappable, dropped
])
def test_transliteration(raw, expected):
    assert to_gsm7(raw) == expected


def test_every_folded_alert_fits_one_segment():
    """Across all twelve sectors, at the worst asset name they support."""
    from store import INDUSTRY_PROFILES, format_temperature

    for key, profile in INDUSTRY_PROFILES.items():
        text = (f"EMERGENCY ALERT: {profile['name']} sensor STORE118-WALKIN "
                f"at Store 118 / Walk-In reported critical temperature "
                f"{format_temperature(71.0, 'F')}. "
                "Immediate physical inspection required.")
        folded = to_gsm7(text)[:GSM7_SEGMENT]
        assert sms_segments(folded) == 1, (
            f"{key} still bills as {sms_segments(folded)} segments"
        )


def test_a_model_written_alert_cannot_run_away_with_the_bill(
    api, operator_factory, sensor_factory, monkeypatch, stub_gemini
):
    """The body is normally model-written, so its length is not ours.

    Nothing upstream stops a model returning four hundred characters of
    prose with an em dash in it — which is ten segments. The trim is at
    the send path for exactly that reason, not at the composer.
    """
    import gemini
    import notifications

    runaway = ("URGENT — " + "the situation is deteriorating rapidly. " * 12)
    monkeypatch.setattr(
        gemini, "client",
        type("C", (), {"models": type("M", (), {
            "generate_content": staticmethod(
                lambda model, contents: type("R", (), {"text": runaway})())
        })()})()
    )

    sent = []
    monkeypatch.setattr(notifications, "_get_client", lambda: type("C", (), {
        "messages": type("E", (), {
            "create": staticmethod(lambda **kw: (
                sent.append(kw["body"]) or type("R", (), {"sid": "SM", "status": "queued"})()))
        })()})())
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RUN-01", vertical="pharmacy")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RUN-01", "temperature_fahrenheit": 71.0})

    assert sent, "nothing reached the provider"
    for body in sent:
        assert sms_segments(body) == 1, (
            f"a {sms_segments(body)}-segment alert went out; the trim is "
            "not on the send path"
        )
        assert "—" not in body


def test_the_spend_report_counts_segments_not_messages(
    api, operator_factory, sensor_factory, configured_twilio, break_gemini
):
    """Counting messages is what hid the overspend.

    A report that says "20 texts" when the carrier invoiced 60 segments
    is not a cost control, it is a reason to believe there is nothing to
    fix.
    """
    # The deterministic template, not a stub: it is the one that carries
    # the degree sign, and the degree sign is the whole finding. A stub
    # returning "URGENT: attend the site" is one segment either way and
    # would let the regression through.
    break_gemini("outage")

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="SEG-01", vertical="pharmacy")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "SEG-01", "temperature_fahrenheit": 71.0})

    report = api.get("/api/costs", headers=headers).json()
    blob = str(report)
    assert "sms_segments" in blob, (
        "the spend report still has no idea what a segment is"
    )

    # And the number is used, not merely present. After the fix segments
    # and messages are equal by construction — everything is trimmed to
    # one — which is the whole point: the estimate is now the real
    # invoice rather than a third of it. The counter earns its keep the
    # day that stops being true, and this asserts it is still true.
    import re

    sent = int(re.search(r"'sms_sent': (\d+)", blob).group(1))
    segs = int(re.search(r"'sms_segments': (\d+)", blob).group(1))
    assert sent > 0, "the fixture sent nothing"
    assert segs == sent, (
        f"{sent} alerts billed as {segs} segments — something upstream is "
        "emitting text that no longer fits one"
    )
