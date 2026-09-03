"""Real-world message delivery via Twilio.

The rest of the platform decides *what* to say; this module is the only
place that actually puts a message on the wire. Delivery never raises: a
telephony outage or a missing credential is recorded on the incident as an
undelivered attempt, so the operator can see that an alert was written but
not sent, rather than the whole breach handler dying on a network error.

Unconfigured is a first-class state, not an error. With no Twilio
credentials the platform runs end to end in dry-run: alerts are composed,
incidents open and escalate, and every delivery is marked `not_configured`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger("cyberlogix.notifications")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "").strip()

# Twilio caps a single SMS body; longer text is segmented and billed per
# segment, so the alert is trimmed rather than silently fragmented.
MAX_SMS_CHARACTERS = 1500

# Twilio rejects TwiML documents above 64 kB; spoken alerts are far shorter,
# but the guard keeps a pathological model response from failing the call.
MAX_SPOKEN_CHARACTERS = 3000

_client = None
_client_error: Optional[str] = None


def _configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def _get_client():
    """Build the Twilio client on first use, caching success and failure."""
    global _client, _client_error

    if _client is not None or _client_error is not None:
        return _client

    try:
        from twilio.rest import Client  # imported lazily so the dep is optional

        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("Twilio client initialized (from=%s).", TWILIO_FROM_NUMBER)
    except Exception as exc:  # noqa: BLE001 - delivery must never hard-fail
        _client_error = str(exc)
        logger.error("Twilio client could not be created (%s).", exc)

    return _client


def delivery_ready() -> bool:
    """True when Twilio credentials are present."""
    return _configured()


def _undelivered(channel: str, to: str, status: str, detail: str) -> Dict[str, Any]:
    return {
        "channel": channel,
        "to": to,
        "delivered": False,
        "status": status,
        "provider_sid": None,
        "detail": detail,
    }


def _precheck(channel: str, to: str) -> Optional[Dict[str, Any]]:
    """Return an undelivered record when the message cannot be attempted."""
    if not to or not to.strip():
        return _undelivered(
            channel, to, "no_recipient", "No contact number on file for this tenant."
        )

    if not _configured():
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID),
                ("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN),
                ("TWILIO_FROM_NUMBER", TWILIO_FROM_NUMBER),
            )
            if not value
        ]
        logger.warning(
            "Twilio not configured (missing %s); %s to %s composed but not sent.",
            ", ".join(missing),
            channel,
            to,
        )
        return _undelivered(
            channel,
            to,
            "not_configured",
            f"Twilio is not configured. Missing: {', '.join(missing)}.",
        )

    if _get_client() is None:
        return _undelivered(
            channel,
            to,
            "client_unavailable",
            f"Twilio client could not be created: {_client_error}",
        )

    return None


def send_sms(to: str, body: str) -> Dict[str, Any]:
    """Send an SMS, returning a delivery record instead of raising."""
    blocked = _precheck("sms", to)
    if blocked is not None:
        return blocked

    text = (body or "").strip()[:MAX_SMS_CHARACTERS]

    try:
        message = _get_client().messages.create(
            to=to, from_=TWILIO_FROM_NUMBER, body=text
        )
        logger.info("SMS delivered to %s (sid=%s).", to, message.sid)
        return {
            "channel": "sms",
            "to": to,
            "delivered": True,
            "status": getattr(message, "status", "queued"),
            "provider_sid": message.sid,
            "detail": "Alert handed to Twilio for delivery.",
        }
    except Exception as exc:  # noqa: BLE001 - a send failure must not kill the breach path
        logger.exception("SMS to %s failed (%s).", to, exc)
        return _undelivered("sms", to, "send_failed", f"Twilio rejected the send: {exc}")


def build_twiml(spoken_text: str) -> str:
    """Wrap spoken words in TwiML, escaping them so any text is safe.

    The script is model-authored, so an ampersand or angle bracket in it
    would otherwise produce malformed XML and a failed call.
    """
    safe = xml_escape((spoken_text or "").strip()[:MAX_SPOKEN_CHARACTERS])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say voice="alice">{safe}</Say>'
        '<Pause length="1"/>'
        f'<Say voice="alice">{safe}</Say>'
        "</Response>"
    )


def place_voice_call(to: str, spoken_text: str) -> Dict[str, Any]:
    """Place an outbound call that speaks `spoken_text`, twice."""
    blocked = _precheck("voice", to)
    if blocked is not None:
        return blocked

    try:
        call = _get_client().calls.create(
            to=to, from_=TWILIO_FROM_NUMBER, twiml=build_twiml(spoken_text)
        )
        logger.info("Voice call placed to %s (sid=%s).", to, call.sid)
        return {
            "channel": "voice",
            "to": to,
            "delivered": True,
            "status": getattr(call, "status", "queued"),
            "provider_sid": call.sid,
            "detail": "Call handed to Twilio for dialling.",
        }
    except Exception as exc:  # noqa: BLE001 - a dial failure must not kill escalation
        logger.exception("Voice call to %s failed (%s).", to, exc)
        return _undelivered(
            "voice", to, "call_failed", f"Twilio rejected the call: {exc}"
        )
