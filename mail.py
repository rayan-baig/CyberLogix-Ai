"""Outbound email: the transport the money engine was missing.

Collections had a full escalation ladder — five stages, a late fee, a
delinquency cliff — and every notice it produced was appended to a list
and handed back to whoever called the endpoint. The docstring said so out
loud: *"There is no mail transport in this system yet."* A company can
run an entire dunning process against itself and never once tell the
customer they owe money.

This is the transport, and it is deliberately boring: SMTP over STARTTLS
with credentials from the environment, one message at a time. What is not
boring is everything around the send, because unattended mail from an
automated billing pass has exactly three ways to destroy a business:

* **Sending twice.** Two overlapping billing passes chasing the same
  invoice reads as either incompetence or dishonesty. Every send claims a
  `dedupe_key` under the store's lock first, and a claim that loses gets
  None and sends nothing.
* **Sending nothing and saying it did.** A message is written down before
  it is attempted, so a crash between the two leaves a queued row the
  next pass picks up — not a chase that silently evaporated.
* **Sending to people who said stop.** An unsubscribe suppresses
  commercial mail; a bounce suppresses everything, because hammering a
  dead address is how the whole domain stops being delivered anywhere.

The unsubscribe link is signed, so nobody can unsubscribe anyone else,
and it deliberately does not stop invoices: a footer link is not a way to
opt out of being billed, and pretending it is would be worse for the
customer than the honest answer.

Nothing here raises. The billing pass calls it, and a mail host having a
bad afternoon must not stop the company invoicing.
"""

from __future__ import annotations

import functools
import hmac
import hashlib
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from auth import require_platform_admin
from store import (
    MAIL_MAX_AGE_HOURS,
    MAIL_MAX_ATTEMPTS,
    STORE,
    MailMessage,
    iso,
    mask_email,
    utc_now,
)

logger = logging.getLogger("cyberlogix.mail")

router = APIRouter(prefix="/api/mail", tags=["Mail"])

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1").strip() not in ("0", "false", "")
SMTP_TIMEOUT_SECONDS = float(os.environ.get("SMTP_TIMEOUT_SECONDS", "20"))

MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "CyberLogix AI").strip()
MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO", "").strip()

# Where a customer is told to send the money. Without this an invoice is a
# statement of opinion: the ledger knows what is owed and the notice that
# reaches the customer has no way to pay it.
#
# The remittance details come from the invoicing module's issuer block
# rather than a second read of the same variable, so the address on the
# document and the address in the chase cannot drift apart — which is the
# kind of discrepancy that turns a late payment into a dispute.
PAY_URL = os.environ.get("CYBERLOGIX_PAY_URL", "").strip()

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Long enough that the whole body is not one paragraph in a phone client,
# short enough that no mail host wraps it for us and mangles a URL.
WRAP_COLUMNS = 72

# Deliberately permissive: this is a sanity check to catch an empty field
# or a stray comma, not an attempt to out-guess RFC 5322. The real
# verdict on an address comes from the mail host.
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def configured() -> bool:
    """True when there is somewhere to hand a message to."""
    return bool(SMTP_HOST and MAIL_FROM)


def missing_settings() -> List[str]:
    return [
        name
        for name, value in (("SMTP_HOST", SMTP_HOST), ("MAIL_FROM", MAIL_FROM))
        if not value
    ]


# --- the signing secret ---------------------------------------------------

_SECRET_KIND = "meta"
_SECRET_ID = "mail_secret"


def _secret() -> bytes:
    """The key that signs unsubscribe links.

    Persisted rather than generated per process, because a link in an
    email sent last Tuesday has to still verify after a restart — and an
    unsubscribe link that silently stops working is a customer who clicks
    it, sees an error, and marks the next message as spam instead.
    """
    configured_secret = os.environ.get("CYBERLOGIX_MAIL_SECRET", "").strip()
    if configured_secret:
        return configured_secret.encode("utf-8")
    row = STORE._db.get(_SECRET_KIND, _SECRET_ID)
    if row and row.get("value"):
        return str(row["value"]).encode("utf-8")
    import secrets as _secrets

    value = _secrets.token_urlsafe(32)
    STORE._db.put(_SECRET_KIND, _SECRET_ID, {"value": value})
    return value.encode("utf-8")


def unsubscribe_token(address: str) -> str:
    """A signature over one address, so a link cannot be pointed at another."""
    normalised = (address or "").strip().lower().encode("utf-8")
    return hmac.new(_secret(), normalised, hashlib.sha256).hexdigest()[:32]


def unsubscribe_link(address: str) -> Optional[str]:
    if not PUBLIC_BASE_URL:
        return None
    from urllib.parse import quote

    return (
        f"{PUBLIC_BASE_URL}/api/mail/unsubscribe"
        f"?address={quote((address or '').strip().lower())}"
        f"&token={unsubscribe_token(address)}"
    )


# --- composing ------------------------------------------------------------


def payment_instructions() -> str:
    """How to pay, or an honest admission that nobody has said.

    This block goes on every invoice notice. An unconfigured deployment
    used to send a demand with no way to satisfy it; now it says which
    setting is missing, which at least means the person who can fix it
    finds out from the first notice rather than the first unpaid quarter.
    """
    from invoicing import ISSUER

    remit_to = (ISSUER.get("remit_to") or "").strip()
    lines = []
    if PAY_URL:
        lines.append(f"Pay online: {PAY_URL}")
    if remit_to:
        lines.append("Remit to:")
        lines.extend(f"  {line}" for line in remit_to.splitlines() if line.strip())
    if not lines:
        return (
            "Payment details are not configured on this deployment "
            "(CYBERLOGIX_REMIT_TO or CYBERLOGIX_PAY_URL). Reply to this "
            "message and we will send them."
        )
    return "\n".join(lines)


def _footer(address: str, klass: str) -> str:
    parts = []
    if klass == "commercial":
        link = unsubscribe_link(address)
        if link:
            parts.append(f"Stop these messages: {link}")
        else:
            parts.append(
                "To stop messages like this one, reply with the word STOP."
            )
        parts.append(
            "This is not an invoice notice; billing mail is sent separately."
        )
    else:
        parts.append(
            "This message concerns your account and is sent regardless of "
            "marketing preferences."
        )
    return "\n".join(parts)


def compose(
    *,
    to_address: str,
    subject: str,
    body: str,
    klass: str,
) -> EmailMessage:
    """Build the MIME message, refusing anything that could inject a header.

    A subject line is assembled from customer-controlled text — a company
    name typed into the sign-up form. A newline in one of those, dropped
    straight into a header, lets whoever typed it add headers of their
    own: a Bcc onto every notice we send, or a Reply-To pointing at them.
    `EmailMessage` will refuse the header, but it raises to do it, and a
    raise inside the billing pass is its own problem. Cleaning the value
    here means the send simply happens with a tidy subject.
    """
    message = EmailMessage()
    message["Subject"] = _one_line(subject)
    message["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    message["To"] = _one_line(to_address)
    if MAIL_REPLY_TO:
        message["Reply-To"] = _one_line(MAIL_REPLY_TO)
    if klass == "commercial":
        link = unsubscribe_link(to_address)
        if link:
            # The header form is what a mail client's own unsubscribe
            # button reads, and honouring it is a large part of why a
            # sending domain stays out of the spam folder.
            message["List-Unsubscribe"] = f"<{link}>"
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(f"{body.rstrip()}\n\n--\n{_footer(to_address, klass)}\n")
    return message


def _one_line(value: str) -> str:
    """Collapse anything that could start a new header into a space."""
    return " ".join(str(value or "").split())


def valid_address(address: str) -> bool:
    _, parsed = parseaddr(address or "")
    return bool(parsed) and bool(_ADDRESS.match(parsed))


# --- sending --------------------------------------------------------------


def _transport(message: EmailMessage) -> None:
    """Hand one message to the mail host. The only place smtplib is used.

    Kept as its own function so a test can replace it, and so that
    everything above it — claiming, suppression, retry accounting — is
    exercised for real rather than mocked away.
    """
    context = ssl.create_default_context()
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS, context=context
        ) as client:
            if SMTP_USERNAME:
                client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as client:
        client.ehlo()
        if SMTP_STARTTLS:
            client.starttls(context=context)
            client.ehlo()
        if SMTP_USERNAME:
            client.login(SMTP_USERNAME, SMTP_PASSWORD)
        client.send_message(message)


def never_raises(fn):
    """A mail failure must never be able to stop the billing pass."""

    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the contract is total
            logger.exception("Mail failed unexpectedly (%s).", exc)
            return {
                "sent": False,
                "status": "error",
                "detail": f"Mail failed unexpectedly: {exc}",
                "message_id": None,
            }

    return guarded


def _result(message: Optional[MailMessage], sent: bool, status: str, detail: str):
    return {
        "sent": sent,
        "status": status,
        "detail": detail,
        "message_id": message.message_id if message else None,
    }


@never_raises
def send(
    *,
    to_address: str,
    subject: str,
    body: str,
    dedupe_key: str,
    klass: str = "transactional",
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send one message, at most once, ever.

    `dedupe_key` is the identity of the message, not of the attempt: the
    same key twice is the same notice, and the second call sends nothing
    and says so. Callers build it from what makes the message unique —
    invoice number and dunning stage, tenant and trial milestone — so
    that a pass replayed after a crash is a no-op rather than a second
    demand landing in the customer's inbox.
    """
    to_address = (to_address or "").strip()
    if not valid_address(to_address):
        logger.warning("Refusing to send to an unusable address (%r).", to_address)
        return _result(None, False, "bad_address",
                       "No usable email address for this recipient.")

    block = STORE.is_suppressed(to_address, klass)
    if block is not None:
        logger.info(
            "Suppressed %s mail to %s (%s).", klass, mask_email(to_address),
            block.reason,
        )
        return _result(None, False, "suppressed",
                       f"That address is suppressed ({block.reason}).")

    message = STORE.claim_message(
        dedupe_key=dedupe_key,
        to_address=to_address,
        subject=_one_line(subject),
        body=body,
        klass=klass,
        tenant_id=tenant_id,
    )
    if message is None:
        return _result(None, False, "duplicate",
                       "That exact message has already been sent.")

    return deliver(message)


@never_raises
def deliver(message: MailMessage) -> Dict[str, Any]:
    """Attempt one already-claimed message.

    Split from `send` so the retry pass can reuse it without re-claiming,
    and so a message that was queued while SMTP was unconfigured goes out
    the moment somebody configures it.
    """
    if not configured():
        detail = (
            "No mail transport configured. Missing: "
            + ", ".join(missing_settings())
            + ". The message is queued and will be sent when SMTP is set up, "
            f"if that happens within {MAIL_MAX_AGE_HOURS:g} hours."
        )
        STORE.record_attempt(message, "queued", detail)
        logger.warning(
            "Mail to %s queued unsent: SMTP not configured.",
            mask_email(message.to_address),
        )
        return _result(message, False, "not_configured", detail)

    try:
        _transport(compose(
            to_address=message.to_address,
            subject=message.subject,
            body=message.body,
            klass=message.klass,
        ))
    except smtplib.SMTPRecipientsRefused as exc:
        # The host says this mailbox does not exist. Retrying is not going
        # to change its mind, and doing it anyway is how a sending domain
        # earns a reputation that stops everyone else's mail too.
        STORE.suppress_address(
            message.to_address, "bounced", f"Refused by the mail host: {exc}"
        )
        STORE.record_attempt(message, "failed", f"Recipient refused: {exc}")
        logger.error(
            "Mail to %s refused; address suppressed.",
            mask_email(message.to_address),
        )
        return _result(message, False, "refused", f"Recipient refused: {exc}")
    except Exception as exc:  # noqa: BLE001 - every other failure may be transient
        attempts = message.attempts + 1
        final = attempts >= MAIL_MAX_ATTEMPTS
        STORE.record_attempt(
            message, "failed" if final else "queued", f"{type(exc).__name__}: {exc}"
        )
        logger.warning(
            "Mail to %s failed on attempt %d (%s).",
            mask_email(message.to_address), attempts, exc,
        )
        return _result(
            message, False, "failed" if final else "retry",
            f"Delivery failed: {exc}"
            + ("" if final else " — queued for another attempt."),
        )

    STORE.record_attempt(message, "sent", "Accepted by the mail host.")
    logger.info(
        "Mail sent to %s: %s", mask_email(message.to_address), message.subject
    )
    return _result(message, True, "sent", "Accepted by the mail host.")


def flush_queue(limit: int = 200) -> Dict[str, Any]:
    """Re-attempt everything still waiting. Called from the money pass.

    This is what makes the transport survive an outage instead of losing
    to one. A message too old or too often tried is dropped by
    `pending_mail` rather than retried forever.
    """
    attempted = 0
    sent = 0
    for message in STORE.pending_mail()[:limit]:
        attempted += 1
        if deliver(message).get("sent"):
            sent += 1
    if attempted:
        logger.info("Mail queue flush: %d attempted, %d sent.", attempted, sent)
    return {"attempted": attempted, "sent": sent}


def _can_be_paid() -> bool:
    from invoicing import ISSUER

    return bool(PAY_URL or (ISSUER.get("remit_to") or "").strip())


def status() -> Dict[str, Any]:
    """What the transport is, for the health endpoint and the console."""
    counts = STORE.mail_counts()
    return {
        "configured": configured(),
        "missing": missing_settings(),
        "host": SMTP_HOST or None,
        "from": MAIL_FROM or None,
        "starttls": SMTP_STARTTLS,
        "payment_details_configured": _can_be_paid(),
        "queued": counts.get("queued", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "suppressed_addresses": len(STORE.list_suppressions()),
        "max_attempts": MAIL_MAX_ATTEMPTS,
        "max_age_hours": MAIL_MAX_AGE_HOURS,
        "note": (
            "Without SMTP_HOST and MAIL_FROM the collections ladder still "
            "runs and still claims each stage, but nothing reaches the "
            "customer: notices queue here instead. Without "
            "CYBERLOGIX_REMIT_TO or CYBERLOGIX_PAY_URL the notices that do "
            "go out cannot tell anybody how to pay."
        ),
    }


# --- routes ---------------------------------------------------------------


@router.get("/status")
def read_status(_: None = Depends(require_platform_admin)):
    """Whether the company can actually reach its customers."""
    return status()


@router.get("/log")
def read_log(
    limit: int = Query(100, ge=1, le=500),
    _: None = Depends(require_platform_admin),
):
    """What has been sent, to whom, and whether it landed."""
    return {
        "generated_at": iso(utc_now()),
        "transport": status(),
        "messages": [m.public() for m in STORE.mail_log(limit)],
    }


@router.get("/suppressions")
def read_suppressions(_: None = Depends(require_platform_admin)):
    """Addresses we have stopped writing to, and why."""
    return {
        "suppressions": [b.public() for b in STORE.list_suppressions()],
        "note": (
            "An unsubscribe stops commercial mail only. A bounce stops "
            "everything, invoices included: an address the host has "
            "refused cannot receive one, and continuing to try is how the "
            "whole sending domain stops being delivered."
        ),
    }


class SuppressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(..., min_length=3, max_length=254)
    reason: str = Field("unsubscribed", pattern="^(unsubscribed|bounced)$")
    detail: str = Field("", max_length=500)


@router.post("/suppressions", status_code=201)
def add_suppression(
    payload: SuppressionRequest, _: None = Depends(require_platform_admin)
):
    """Stop writing to an address, by hand.

    For the call where somebody says "take me off this" rather than
    clicking the link.
    """
    if not valid_address(payload.address):
        raise HTTPException(status_code=422, detail="That is not an email address.")
    block = STORE.suppress_address(payload.address, payload.reason, payload.detail)
    # Logged rather than written to a tenant's audit trail: an address is
    # not owned by one account — the same person can be the contact on
    # several — and quietly attributing it to whichever tenant happens to
    # carry it would put a record in a customer's compliance log about a
    # decision they did not make.
    logger.warning(
        "Operator suppressed %s (%s).", mask_email(block.address), block.reason
    )
    return block.public()


@router.delete("/suppressions")
def remove_suppression(
    address: str = Query(..., min_length=3, max_length=254),
    _: None = Depends(require_platform_admin),
):
    """Let an address back in, after the customer asks."""
    if not STORE.unsuppress_address(address):
        raise HTTPException(status_code=404, detail="That address is not suppressed.")
    logger.warning("Operator released %s.", mask_email(address))
    return {"address": mask_email(address), "suppressed": False}


@router.get("/unsubscribe", include_in_schema=False)
def unsubscribe(
    address: str = Query(..., min_length=3, max_length=254),
    token: str = Query(..., min_length=8, max_length=64),
):
    """The link in the footer. Signed, so it only ever unsubscribes you.

    Unauthenticated by necessity — the person clicking it is reading an
    email, not holding an API key — which is exactly why the signature
    matters: without it the endpoint is a button anyone can press to stop
    us writing to any customer they can name.
    """
    if not hmac.compare_digest(unsubscribe_token(address), (token or "").strip()):
        raise HTTPException(
            status_code=403,
            detail="That unsubscribe link is not valid for this address.",
        )
    STORE.suppress_address(address, "unsubscribed", "Clicked the footer link.")
    logger.info("Unsubscribed %s at their own request.", mask_email(address))
    return {
        "address": mask_email(address),
        "unsubscribed": True,
        "detail": (
            "Done. You will not get any more mail like that one. Notices "
            "about your account — invoices, receipts, anything about a "
            "sensor — still come through, because an unsubscribe link is "
            "not a way to stop being billed."
        ),
    }


@router.post("/flush")
def flush(_: None = Depends(require_platform_admin)):
    """Try the queue again now, rather than waiting for the hourly pass."""
    return flush_queue()
