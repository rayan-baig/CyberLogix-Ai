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

# Where Twilio should send the keypress when the callee presses 1. Without
# a reachable base URL the call still goes out, it just cannot be
# acknowledged from the handset, so this is optional rather than required.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

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


def send_sms(
    to: str, body: str, tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """Send an SMS, returning a delivery record instead of raising."""
    from costs import allow_message, record

    allowed, reason = allow_message(tenant_id, "sms")
    if not allowed:
        record(tenant_id, "sms_suppressed")
        logger.warning("SMS to %s suppressed: %s", to, reason)
        return _undelivered("sms", to, "budget_exceeded", reason)

    blocked = _precheck("sms", to)
    if blocked is not None:
        return blocked

    text = (body or "").strip()[:MAX_SMS_CHARACTERS]

    try:
        message = _get_client().messages.create(
            to=to, from_=TWILIO_FROM_NUMBER, body=text
        )
        record(tenant_id, "sms_sent")
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


def build_twiml(spoken_text: str, action_url: Optional[str] = None) -> str:
    """Wrap spoken words in TwiML, escaping them so any text is safe.

    The script is model-authored, so an ampersand or angle bracket in it
    would otherwise produce malformed XML and a failed call.

    With an `action_url` the words are wrapped in a `<Gather>`, so the
    script's closing "press 1 to acknowledge" actually reaches something.
    Without one the call is read out twice and hangs up, which is the old
    behaviour and all that is possible when the service has no public URL.
    """
    safe = xml_escape((spoken_text or "").strip()[:MAX_SPOKEN_CHARACTERS])
    body = (
        f'<Say voice="alice">{safe}</Say>'
        '<Pause length="1"/>'
        f'<Say voice="alice">{safe}</Say>'
    )
    if action_url:
        inner = (
            f'<Gather numDigits="1" timeout="8" method="POST" '
            f'action="{xml_escape(action_url, {chr(34): "&quot;"})}">'
            f"{body}"
            "</Gather>"
            '<Say voice="alice">No acknowledgement received. '
            "The escalation stays open.</Say>"
        )
    else:
        inner = body
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{inner}"
        "</Response>"
    )


def build_gather_reply(spoken_text: str) -> str:
    """A one-line spoken reply to a keypress."""
    safe = xml_escape((spoken_text or "").strip()[:MAX_SPOKEN_CHARACTERS])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="alice">{safe}</Say><Hangup/></Response>'
    )


def acknowledgement_url(incident_id: str, token: str) -> Optional[str]:
    """Where Twilio should post a keypress for this call, if reachable."""
    if not PUBLIC_BASE_URL:
        return None
    return f"{PUBLIC_BASE_URL}/api/voice/keypress/{incident_id}/{token}"


def verify_twilio_signature(url: str, form: Dict[str, Any], signature: str) -> bool:
    """Check that a callback really came from Twilio.

    The keypress endpoint cannot carry a bearer token — Twilio is the
    caller — so the signature is the only thing standing between a stranger
    and silencing somebody else's escalation. No auth token configured
    means no way to verify, so nothing is trusted.
    """
    if not TWILIO_AUTH_TOKEN or not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator

        return RequestValidator(TWILIO_AUTH_TOKEN).validate(url, form, signature)
    except Exception as exc:  # noqa: BLE001 - an unverifiable callback is refused
        logger.error("Twilio signature could not be validated (%s).", exc)
        return False


def place_voice_call(
    to: str,
    spoken_text: str,
    tenant_id: Optional[str] = None,
    action_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Place an outbound call that speaks `spoken_text`, twice."""
    from costs import allow_message, record

    allowed, reason = allow_message(tenant_id, "voice")
    if not allowed:
        record(tenant_id, "voice_suppressed")
        logger.warning("Voice call to %s suppressed: %s", to, reason)
        return _undelivered("voice", to, "budget_exceeded", reason)

    blocked = _precheck("voice", to)
    if blocked is not None:
        return blocked

    try:
        call = _get_client().calls.create(
            to=to,
            from_=TWILIO_FROM_NUMBER,
            twiml=build_twiml(spoken_text, action_url),
        )
        record(tenant_id, "voice_calls")
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
