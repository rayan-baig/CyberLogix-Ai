"""Where money arriving becomes an invoice being settled.

Everything up to here can bill. Nothing until here closes the loop: an
invoice went out, a customer paid it, and somebody had to notice and
type the payment in by hand. For the first few customers that is fine —
contracts at these sizes settle by bank transfer, not card — but a
payment nobody records is chased anyway, and being chased for money you
have already sent is the fastest way to lose an account you had.

This is the adapter. It is written against Stripe's webhook because that
is what a payment link produces, and it deliberately does **not** import
the Stripe SDK: the signature is thirty lines of HMAC, and a dependency
that has to be installed before the tests can run is a dependency that
makes the tests optional.

Three things carry the whole module.

**The signature.** This endpoint is unauthenticated by necessity —
Stripe holds no credential of ours — so the signature *is* the
authentication. Without it, anybody who learns the URL can mark every
invoice in the system paid, which is a strictly better attack than
breaking in. It is verified over the exact bytes received, with a
timestamp tolerance, because a valid signature replayed a week later is
still a valid signature.

**Idempotency.** Stripe delivers at least once and retries for days on
anything that is not a 2xx. The event id is used as the payment
reference, so the ledger's own duplicate check does the work, and a
storm of retries settles an invoice exactly once.

**Unmatched money.** A payment that cannot be tied to an invoice is not
dropped and is not guessed at. It is recorded, and it appears on the
operator's worklist, because money we have taken and cannot account for
is the one thing worse than money we have not taken.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from auth import require_platform_admin
from store import STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.payments")

router = APIRouter(prefix="/api/payments", tags=["Payments"])

# The webhook signing secret from the Stripe dashboard (whsec_...).
# Unset, the endpoint refuses everything: an unauthenticated payment
# webhook that trusts its body is a button for marking every invoice in
# the system paid.
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()

# How far out of date a signature may be. Stripe's own guidance is five
# minutes. The window is what stops a signature captured once from being
# replayed forever.
SIGNATURE_TOLERANCE_SECONDS = int(
    os.environ.get("STRIPE_SIGNATURE_TOLERANCE_SECONDS", "300")
)

# The events that mean money arrived. Everything else gets a polite 200
# and is ignored — Stripe sends dozens of event types and retries any
# non-2xx for days.
SETTLING_EVENTS = (
    "checkout.session.completed",
    "payment_intent.succeeded",
    "charge.succeeded",
)

_UNMATCHED_KIND = "unmatched_payment"


def configured() -> bool:
    return bool(STRIPE_WEBHOOK_SECRET)


# --- the signature --------------------------------------------------------


def verify_signature(payload: bytes, header: str, now: Optional[float] = None) -> None:
    """Raise unless `header` is a signature Stripe made over `payload`.

    The header looks like `t=1699999999,v1=abc...,v1=def...`. The signed
    string is the timestamp, a dot, and the raw body — raw being the
    important word: re-serialising the JSON changes the bytes and the
    signature stops matching, which is the classic way this check gets
    "fixed" into uselessness by whoever debugs it next.

    Several v1 signatures can appear during a secret rotation, so any one
    matching is enough.
    """
    if not configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "STRIPE_WEBHOOK_SECRET is not set, so this endpoint cannot "
                "tell Stripe from anybody else and refuses everything."
            ),
        )

    timestamp = None
    signatures: List[str] = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise HTTPException(status_code=400, detail="Malformed Stripe signature.")

    try:
        sent_at = int(timestamp)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Malformed Stripe signature timestamp."
        ) from None

    age = abs((now if now is not None else time.time()) - sent_at)
    if age > SIGNATURE_TOLERANCE_SECONDS:
        # A signature stays valid forever without this, so one captured
        # request could be replayed for as long as the secret lives.
        raise HTTPException(
            status_code=400,
            detail=(
                f"That signature is {int(age)}s old; anything over "
                f"{SIGNATURE_TOLERANCE_SECONDS}s is refused as a replay."
            ),
        )

    expected = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()

    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        logger.error("A payment webhook arrived with a signature we did not make.")
        raise HTTPException(status_code=400, detail="Bad Stripe signature.")


# --- reading the event ----------------------------------------------------


def _amount_usd(obj: Dict[str, Any]) -> Optional[float]:
    """Stripe counts in the currency's smallest unit. USD means cents."""
    for key in ("amount_received", "amount_total", "amount_paid", "amount"):
        value = obj.get(key)
        if isinstance(value, (int, float)):
            return round(value / 100.0, 2)
    return None


def _invoice_reference(obj: Dict[str, Any]) -> Optional[str]:
    """Which invoice this payment is for, if the payment says.

    Read from the places a payment link and an API-created session
    actually carry it, in the order of how deliberate each one is.
    """
    metadata = obj.get("metadata") or {}
    for key in ("invoice_id", "cyberlogix_invoice_id", "invoice"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    reference = obj.get("client_reference_id")
    if isinstance(reference, str) and reference.strip():
        return reference.strip()
    return None


def record_unmatched(event_id: str, detail: Dict[str, Any]) -> None:
    """Write down money we have taken and cannot account for.

    Never dropped, and never guessed at. Guessing which invoice an
    unlabelled payment belongs to is how a customer ends up chased for
    something they paid and credited for something they did not.
    """
    row = STORE._db.get(_UNMATCHED_KIND, event_id)
    if row is not None:
        return
    STORE._db.put(_UNMATCHED_KIND, event_id, {**detail, "seen_at": iso(utc_now())})
    logger.error(
        "Payment %s for $%s could not be matched to an invoice. It is on "
        "the unmatched list and needs a person.",
        event_id, detail.get("amount_usd"),
    )


def unmatched() -> List[Dict[str, Any]]:
    return sorted(
        STORE._db.all(_UNMATCHED_KIND),
        key=lambda r: r.get("seen_at") or "",
        reverse=True,
    )


def clear_unmatched(event_id: str) -> bool:
    if STORE._db.get(_UNMATCHED_KIND, event_id) is None:
        return False
    STORE._db.delete(_UNMATCHED_KIND, event_id)
    return True


def apply_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Settle whatever this event says was paid.

    Returns a summary. Never raises on a payment it cannot use: Stripe
    retries anything that is not a 2xx, and retrying will not make an
    unmatched payment matchable.
    """
    event_id = str(event.get("id") or "")
    kind = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object")) or {}

    if kind not in SETTLING_EVENTS:
        return {"handled": False, "reason": "not a settling event", "type": kind}

    # A session that is not paid is a customer who opened the page and
    # walked away. Stripe sends the event either way.
    status = obj.get("payment_status") or obj.get("status")
    if status in ("unpaid", "no_payment_required", "requires_payment_method"):
        return {"handled": False, "reason": f"status is {status}", "type": kind}

    amount = _amount_usd(obj)
    currency = (obj.get("currency") or "usd").lower()
    reference = _invoice_reference(obj)

    detail = {
        "event_id": event_id, "type": kind, "amount_usd": amount,
        "currency": currency, "invoice_reference": reference,
    }

    if amount is None or amount <= 0:
        return {"handled": False, "reason": "no usable amount", **detail}

    if currency != "usd":
        # Converting here would invent an exchange rate and put it on a
        # customer's ledger. The invoice is in USD; this is a person's
        # problem, not a rounding decision.
        record_unmatched(event_id, {**detail, "why": "not a USD payment"})
        return {"handled": False, "reason": "currency is not USD", **detail}

    invoice = STORE.get_invoice(reference) if reference else None
    if invoice is None:
        record_unmatched(event_id, {
            **detail,
            "why": (
                "no invoice id on the payment"
                if not reference else f"no invoice {reference} exists"
            ),
        })
        return {"handled": False, "reason": "unmatched", **detail}

    if invoice.state == "void":
        record_unmatched(event_id, {**detail, "why": f"{invoice.number} was voided"})
        return {"handled": False, "reason": "invoice is void", **detail}

    tenant = STORE.get_tenant(invoice.tenant_id)
    # The Stripe event id as the reference, so the ledger's own duplicate
    # check does the idempotency. A storm of retries settles it once.
    invoice, applied = STORE.settle_invoice(invoice, event_id, amount)

    if applied and tenant is not None:
        from invoicing import send_receipt

        send_receipt(tenant, invoice, event_id, invoice.state == "paid")
        logger.info(
            "Stripe %s settled %s with $%.2f; %s.",
            event_id, invoice.number, amount,
            "paid in full" if invoice.state == "paid"
            else f"${invoice.balance_usd:,.2f} still outstanding",
        )

    return {
        "handled": True, "applied": applied, "invoice": invoice.number,
        "state": invoice.state, "balance_usd": invoice.balance_usd, **detail,
    }


# --- routes ---------------------------------------------------------------


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header("", alias="Stripe-Signature"),
):
    """Stripe's webhook. Unauthenticated, and signed rather than trusted.

    The raw body is read before anything parses it, because the
    signature covers the exact bytes that arrived and re-serialising
    them breaks it.

    Answers 200 for anything it has understood, including a payment it
    could not match — Stripe retries a non-2xx for days, and a retry
    cannot make an unmatched payment matchable. A refusal here means the
    request was not from Stripe.
    """
    payload = await request.body()
    verify_signature(payload, stripe_signature)

    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="That is not JSON.") from None
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="That is not a Stripe event.")

    return apply_event(event)


@router.get("/unmatched")
def read_unmatched(_: None = Depends(require_platform_admin)):
    """Money taken that no invoice claims. Each row needs a person.

    Not a technical curiosity: every line here is a customer who has
    paid and is still being chased for it.
    """
    rows = unmatched()
    return {
        "count": len(rows),
        "total_usd": round(sum(r.get("amount_usd") or 0 for r in rows), 2),
        "payments": rows,
        "note": (
            "A payment reaches this list when it carries no invoice id, "
            "names one that does not exist, is in another currency, or "
            "belongs to a voided invoice. Settle it by hand against the "
            "right invoice, then clear it."
        ),
    }


@router.delete("/unmatched/{event_id}")
def resolve_unmatched(event_id: str, _: None = Depends(require_platform_admin)):
    """Take a payment off the list once it has been dealt with."""
    if not clear_unmatched(event_id):
        raise HTTPException(status_code=404, detail="No such unmatched payment.")
    return {"event_id": event_id, "cleared": True}


def status() -> Dict[str, Any]:
    rows = unmatched()
    return {
        "stripe_webhook_configured": configured(),
        "unmatched_count": len(rows),
        "unmatched_usd": round(sum(r.get("amount_usd") or 0 for r in rows), 2),
        "note": (
            "Set STRIPE_WEBHOOK_SECRET and point a Stripe webhook at "
            "/api/payments/stripe. Until then payments are recorded by "
            "hand through POST /api/invoices/{id}/paid, which is fine "
            "while there are few enough of them to notice."
        ),
    }
