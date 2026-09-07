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

import functools
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

# Twilio bills per *segment*, not per message, and a segment is 160
# characters only while every character is in the GSM-7 alphabet. One
# character outside it — a degree sign, a curly quote, an em dash —
# switches the whole message to UCS-2 and the segment drops to 70.
#
# Every alert this product sends quoted the reading as "71.0°F". That one
# degree sign was taking a 158-character message from one segment to
# three, on the line that is the large majority of delivery cost, and the
# spend report counted messages so nobody would ever have seen it.
#
# So outbound text is transliterated into GSM-7 and trimmed to a single
# segment. The trim matters as much as the encoding: the body is normally
# model-written, so its length is not under this codebase's control, and
# an alert that runs to ten segments is both expensive and useless on a
# lock screen at 3am.
GSM7_SEGMENT = 160          # a message that fits in one
GSM7_MULTIPART = 153        # ...and per part once it does not
UCS2_SEGMENT = 70
UCS2_MULTIPART = 67

MAX_SMS_CHARACTERS = GSM7_SEGMENT

_GSM7_BASE = set(
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r"
    "\u00c5\u00e5\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e"
    "\u00c6\u00e6\u00df\u00c9 !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
    "\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7"
    "\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
)
_GSM7_EXT = set("^{}\\[~]|\u20ac")   # these cost two characters each

# Characters worth keeping the meaning of rather than dropping.
_TRANSLITERATE = {
    "\u00b0": "",        # 71.0°F -> 71.0F. The unit letter carries it.
    "\u2014": "-", "\u2013": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u00a0": " ", "\u2022": "*", "\u00d7": "x",
    "\u2192": "->", "\u00b5": "u", "\u2264": "<=", "\u2265": ">=",
}


def to_gsm7(text: str) -> str:
    """Fold text into the GSM-7 alphabet so it bills as 160 per segment.

    Anything with a sensible ASCII equivalent is transliterated; anything
    left that the alphabet cannot carry is dropped rather than allowed to
    triple the bill. Whitespace is collapsed afterwards so a removed
    character does not leave a double space behind.
    """
    out = []
    for ch in text or "":
        if ch in _GSM7_BASE or ch in _GSM7_EXT:
            out.append(ch)
        elif ch in _TRANSLITERATE:
            out.append(_TRANSLITERATE[ch])
        # else: dropped
    return " ".join("".join(out).split())


def sms_segments(text: str) -> int:
    """How many segments Twilio will actually bill for this body."""
    if not text:
        return 0
    if all(c in _GSM7_BASE or c in _GSM7_EXT for c in text):
        n = sum(2 if c in _GSM7_EXT else 1 for c in text)
        return 1 if n <= GSM7_SEGMENT else -(-n // GSM7_MULTIPART)
    n = len(text)
    return 1 if n <= UCS2_SEGMENT else -(-n // UCS2_MULTIPART)

# Twilio rejects TwiML documents above 64 kB; spoken alerts are far shorter,
# but the guard keeps a pathological model response from failing the call.
MAX_SPOKEN_CHARACTERS = 3000

# The Twilio SDK's default HTTP client is built with `timeout=None`, which
# `requests` reads as "wait forever". That is not a slow path, it is a
# permanently lost thread: nothing raises, so `never_raises` never fires,
# the delivery record is never written, and the worker handling that
# breach never comes back. Starlette's threadpool is finite, so a Twilio
# incident lasting a few minutes retires one thread per alert until there
# are none left and the platform stops answering anything at all —
# during exactly the kind of event the platform exists for.
#
# Eight seconds is longer than Twilio's own p99 and short enough that a
# stuck alert releases its thread before the next reading arrives.
TWILIO_TIMEOUT_SECONDS = float(
    os.environ.get("TWILIO_TIMEOUT_SECONDS", "8").strip() or 8
)

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
        from twilio.http.http_client import TwilioHttpClient

        _client = Client(
            TWILIO_ACCOUNT_SID,
            TWILIO_AUTH_TOKEN,
            http_client=TwilioHttpClient(timeout=TWILIO_TIMEOUT_SECONDS),
        )
        logger.info(
            "Twilio client initialized (from=%s, timeout=%ss).",
            TWILIO_FROM_NUMBER,
            TWILIO_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - delivery must never hard-fail
        _client_error = str(exc)
        logger.error("Twilio client could not be created (%s).", exc)

    return _client


def delivery_ready() -> bool:
    """True when Twilio credentials are present."""
    return _configured()


def never_raises(channel: str):
    """Guarantee a delivery record comes back, whatever went wrong.

    Both send functions are documented as never raising, and both only
    wrapped the provider call. Everything before it — the spend check, the
    client precheck, the usage write — sat outside the guard. Any
    exception there escapes into the breach handler, which by then has
    already opened the incident and recorded it, so the sensor gateway
    gets a 500 and retries into a handler that will fail the same way.

    The alert is the product. A promise that delivery cannot take down
    the breach path has to hold for the whole function, not for the line
    somebody remembered to wrap.
    """
    def wrap(fn):
        @functools.wraps(fn)
        def guarded(to, *args, **kwargs):
            try:
                return fn(to, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the contract is total
                logger.exception(
                    "%s delivery to %s raised unexpectedly (%s).", channel, to, exc
                )
                return _undelivered(
                    channel, to, "delivery_error",
                    f"Delivery failed unexpectedly: {exc}. The incident is "
                    "recorded; nobody was reached on this channel.",
                )
        return guarded
    return wrap


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


@never_raises("sms")
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

    # Folded into GSM-7 and trimmed to one segment before it goes
    # anywhere: this is the only choke point every message passes
    # through, including the model-written ones whose length and
    # punctuation nothing upstream controls.
    text = to_gsm7((body or "").strip())[:MAX_SMS_CHARACTERS]
    parts = sms_segments(text)

    try:
        message = _get_client().messages.create(
            to=to, from_=TWILIO_FROM_NUMBER, body=text
        )
        record(tenant_id, "sms_sent")
        # Billed per segment, so counted per segment. Counting messages
        # is what hid a 3x overspend on the largest delivery line.
        record(tenant_id, "sms_segments", parts)
        logger.info("SMS delivered to %s (sid=%s).", to, message.sid)
        return {
            "channel": "sms",
            "to": to,
            "delivered": True,
            "status": getattr(message, "status", "queued"),
            "provider_sid": message.sid,
            # What actually went on the wire, and what it cost. The
            # compliance record should say what was sent, not what was
            # drafted.
            "body": text,
            "segments": parts,
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


@never_raises("voice")
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
