"""Invoices: the step between pricing an estate and being paid for it.

Everything up to here can quote. Nothing until here can bill. An invoice
is a numbered, dated, immutable document with line items that add up, a
due date somebody can be chased against, and a record of what was
actually charged rather than what the rate card says today.

Two things matter more than they look:

Immutability. Once issued, an invoice's figures never change, even if the
estate grows the next morning. A line item that moves after it was sent
is a dispute, and the customer is right to have one. So the lines are
snapshotted at issue and stored, not recomputed on read.

Numbering. Sequential, gapless, per year — because that is what an
accountant, and in most jurisdictions a tax authority, expects to see.

Payment collection itself is left to a processor. What is here is the
document and its lifecycle; `mark_paid` is where a Stripe webhook would
land, and the module works end to end without one so nothing depends on
a key that has not been bought yet.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from auth import (
    require_platform_admin,
    require_tenant_any_state,
    write_audit,
)
from store import STORE, Tenant, iso

logger = logging.getLogger("cyberlogix.invoicing")

router = APIRouter(prefix="/api/invoices", tags=["Invoicing"])

# Net 30 unless a contract says otherwise. Long enough to pass through a
# finance department, short enough to notice when it does not.
PAYMENT_TERMS_DAYS = 30

# Who is issuing the invoice. An invoice without the issuer's legal name,
# address and remittance details is not a document a finance department
# can pay against, and in most jurisdictions is not a valid tax invoice
# either. Configured rather than hard-coded, because the entity behind the
# product is a business decision and will change once it is incorporated.
ISSUER = {
    "legal_name": os.environ.get("CYBERLOGIX_LEGAL_NAME", "CyberLogix AI"),
    "address": os.environ.get("CYBERLOGIX_ADDRESS", ""),
    "tax_id": os.environ.get("CYBERLOGIX_TAX_ID", ""),
    "remit_to": os.environ.get("CYBERLOGIX_REMIT_TO", ""),
    "email": os.environ.get("CYBERLOGIX_BILLING_EMAIL", ""),
}


def issuer_block() -> Dict[str, Any]:
    """The issuer's details, with anything unset omitted rather than blank.

    A line reading "Tax ID:" with nothing after it looks like a mistake on
    a document whose whole job is to look correct.
    """
    block = {k: v for k, v in ISSUER.items() if v}
    block.setdefault("legal_name", "CyberLogix AI")
    if len(block) == 1:
        block["note"] = (
            "Issuer details are not configured. Set CYBERLOGIX_LEGAL_NAME, "
            "CYBERLOGIX_ADDRESS, CYBERLOGIX_TAX_ID and CYBERLOGIX_REMIT_TO "
            "before sending an invoice to a customer."
        )
    return block

INVOICE_STATES = ("issued", "paid", "void")

# How wide the amount column sits in the plain-text rendering. Wide enough
# that a five-figure line does not shove the description around, so a
# customer scanning the numbers reads a column rather than a zigzag.
_AMOUNT_COLUMN = 62


def invoice_date(moment) -> str:
    """A date a person reads, not a timestamp a machine emits.

    `2026-10-12T19:55:50Z` on a document whose only job is to be paid
    tells a finance department the second it was generated and makes
    them work out the day it is due. The seconds were never the point.
    """
    if moment is None:
        return "—"
    return f"{moment.day} {moment.strftime('%B %Y')}"


def render_invoice(invoice, tenant) -> str:
    """The invoice as a customer reads it, in plain text.

    An email is where most of these will actually be looked at, and a
    finance department that has to log in to see what it owes pays later
    than one that can forward the message to accounts payable. The
    figures are the frozen ones on the document, never recomputed: an
    invoice whose total moves between the API and the email is a dispute
    waiting to be raised, and the customer would be right to raise it.
    """
    issuer = issuer_block()
    out = [
        f"INVOICE {invoice.number}",
        "",
        f"From:   {issuer.get('legal_name', 'CyberLogix AI')}",
    ]
    for line in (issuer.get("address") or "").splitlines():
        if line.strip():
            out.append(f"        {line.strip()}")
    if issuer.get("tax_id"):
        out.append(f"        Tax ID {issuer['tax_id']}")
    out += [
        f"To:     {tenant.company_name}",
        f"        {tenant.contact_name}",
        "",
        f"Issued: {invoice_date(invoice.issued_at)}",
        f"Due:    {invoice_date(invoice.due_at)}  (net {invoice.terms_days})",
    ]
    if invoice.purchase_order:
        out.append(f"PO:     {invoice.purchase_order}")
    out += ["", "-" * _AMOUNT_COLUMN]

    for line in invoice.lines:
        description = str(line.get("description", ""))[:48]
        amount = f"${line.get('amount_usd', 0.0):,.2f}"
        out.append(f"{description:<48}{amount:>14}")

    out += [
        "-" * _AMOUNT_COLUMN,
        f"{'Total ' + invoice.currency:<48}{'$' + format(invoice.total_usd, ',.2f'):>14}",
    ]
    if invoice.amount_paid_usd:
        out.append(
            f"{'Received':<48}"
            f"{'-$' + format(invoice.amount_paid_usd, ',.2f'):>14}"
        )
        out.append(
            f"{'Balance':<48}"
            f"{'$' + format(invoice.balance_usd, ',.2f'):>14}"
        )
    return "\n".join(out)


class InvoiceRequest(BaseModel):
    # Unknown fields are refused, not dropped.
    #
    # Pydantic ignores extras by default, which on a model that carries a
    # quantity or an amount is a silent, one-directional loss. Measured:
    # posting {"reference": "STRIPE-1", "amount": 500.0} to /paid — the
    # field a Stripe adapter would naturally use — left `amount_usd` unset,
    # which means "paid in full", so $3,997 of a $4,497 invoice was written
    # off and the invoice closed. Same shape on the cluster endpoint:
    # `enrolled_branches: 12` alongside `total_branch_locations: 40` billed
    # forty.
    #
    # On a money-bearing model a misspelt field has to be a 422.
    model_config = ConfigDict(extra="forbid")
    include_add_ons: str = Field(
        "", description="Comma-separated add-on keys billed this period."
    )
    include_setup: bool = Field(
        False, description="Add the one-time per-site commissioning fee."
    )
    period_days: int = Field(30, ge=1, le=366)
    purchase_order: Optional[str] = Field(None, max_length=64)


class PaymentRecord(BaseModel):
    # Unknown fields are refused, not dropped.
    #
    # Pydantic ignores extras by default, which on a model that carries a
    # quantity or an amount is a silent, one-directional loss. Measured:
    # posting {"reference": "STRIPE-1", "amount": 500.0} to /paid — the
    # field a Stripe adapter would naturally use — left `amount_usd` unset,
    # which means "paid in full", so $3,997 of a $4,497 invoice was written
    # off and the invoice closed. Same shape on the cluster endpoint:
    # `enrolled_branches: 12` alongside `total_branch_locations: 40` billed
    # forty.
    #
    # On a money-bearing model a misspelt field has to be a 422.
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(..., min_length=1, max_length=120)
    amount_usd: Optional[float] = Field(None, ge=0)


def _tenant_or_404(tenant_id: str) -> Tenant:
    """Resolve the estate an operator route names.

    The operator routes take the tenant as a parameter rather than from a
    credential, because the credential is the platform's and speaks for
    every estate at once.
    """
    tenant = STORE.get_tenant((tenant_id or "").strip())
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such tenant '{tenant_id}'.",
        )
    return tenant


def _load(invoice_id: str, tenant: Tenant):
    invoice = STORE.get_invoice((invoice_id or "").strip())
    if invoice is None or invoice.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found for this tenant.",
        )
    return invoice


def build_lines(
    tenant: Tenant,
    add_on_keys: List[str],
    include_setup: bool,
    rate_multiplier: float = 1.0,
    months: int = 1,
) -> List[Dict[str, Any]]:
    """The line items, priced at this moment and then frozen.

    `rate_multiplier` carries the contract escalator. The rate card is what
    a new customer pays today; a customer three years into a term with a
    five percent escalator pays 1.1025 times it, and billing them the card
    rate is a discount nobody agreed to give. It applies to recurring
    lines only — commissioning is a one-time fee at the price it was
    quoted at, and escalating it would be charging interest on a job
    already done.

    `months` bills several months on one document, which is what an annual
    prepay is: twelve months at the same year's rate, on one invoice.
    """
    from pricing import ADD_ONS, SETUP_FEE_PER_SITE_USD, add_on_price, build_subscription

    subscription = build_subscription(tenant)
    lines: List[Dict[str, Any]] = []
    months = max(1, int(months))
    period_note = "" if months == 1 else f" × {months} months"

    def recurring(base: float) -> float:
        return round(base * rate_multiplier * months, 2)

    for row in subscription["line_items"]:
        lines.append(
            {
                "kind": "subscription",
                "description": f"{row['industry']} — {row['description']}{period_note}",
                "quantity": row["units"],
                "unit_price_usd": round(row["unit_price_usd"] * rate_multiplier, 2),
                "amount_usd": recurring(row["line_total_usd"]),
            }
        )

    units = subscription["units_total"]
    for key in add_on_keys:
        entry = ADD_ONS[key]
        amount = recurring(add_on_price(key, units))
        quantity = units if entry["basis"] == "per covered unit" else 1
        lines.append(
            {
                "kind": "add_on",
                "description": f"{entry['name']} ({entry['basis']}){period_note}",
                "quantity": quantity,
                "unit_price_usd": round(entry["monthly_usd"] * rate_multiplier, 2),
                "amount_usd": amount,
            }
        )

    if include_setup:
        sites = max(len(STORE.sites_for(tenant.tenant_id)), 1) if units else 0
        if sites:
            lines.append(
                {
                    "kind": "setup",
                    "description": (
                        f"Commissioning — {sites} site"
                        f"{'' if sites == 1 else 's'}, one time"
                    ),
                    "quantity": sites,
                    "unit_price_usd": SETUP_FEE_PER_SITE_USD,
                    "amount_usd": round(SETUP_FEE_PER_SITE_USD * sites, 2),
                }
            )

    return lines


@router.get("")
def list_invoices(tenant: Tenant = Depends(require_tenant_any_state)):
    """Every invoice ever issued to this tenant, newest first."""
    invoices = STORE.invoices_for(tenant.tenant_id)
    # `open` rather than `state == "issued"`, so a part-paid invoice stays
    # in the ledger and keeps being chased for its balance. And the figure
    # is the balance, not the face value — otherwise a $1 payment against
    # $48,000 would either vanish from collections or overstate it.
    outstanding = [i for i in invoices if i.open]
    return {
        "count": len(invoices),
        "outstanding_count": len(outstanding),
        "outstanding_usd": round(sum(i.balance_usd for i in outstanding), 2),
        "overdue_count": sum(1 for i in outstanding if i.overdue()),
        "part_paid_count": sum(1 for i in invoices if i.state == "part_paid"),
        "invoices": [i.public() for i in invoices],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def issue_invoice(
    payload: InvoiceRequest,
    tenant_id: str = Query(..., description="Which estate to bill."),
    _: None = Depends(require_platform_admin),
):
    """Issue an invoice for the current period.

    Platform operator only. This was `require_role("owner")` — which is
    the *customer's* owner, on the other side of the transaction. Issuing
    an invoice and recording that it was paid are things the vendor does;
    a customer being able to do either is not a permissions nicety, it is
    the ledger being writable by the party it bills.

    Figures are snapshotted now and never recomputed: an invoice whose
    total moves after it was sent is a dispute, and the customer would be
    right to raise it.
    """
    from pricing import ADD_ONS

    tenant = _tenant_or_404(tenant_id)
    wanted = [k.strip() for k in (payload.include_add_ons or "").split(",") if k.strip()]
    unknown = [k for k in wanted if k not in ADD_ONS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on(s) {unknown}. Allowed: {list(ADD_ONS)}",
        )

    lines = build_lines(tenant, wanted, payload.include_setup)
    if not lines:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "There is nothing to bill: no units are registered and no "
                "add-ons were selected."
            ),
        )

    invoice = STORE.create_invoice(
        tenant=tenant,
        lines=lines,
        period_days=payload.period_days,
        terms_days=PAYMENT_TERMS_DAYS,
        purchase_order=payload.purchase_order,
    )
    write_audit(
        tenant, None, "invoice.issued",
        f"{invoice.number} for ${invoice.total_usd:,.2f}, due {iso(invoice.due_at)}.",
        fallback_actor="Platform operator",
    )
    logger.info(
        "Invoice issued: %s tenant=%s total=%.2f",
        invoice.number, tenant.tenant_id, invoice.total_usd,
    )
    return invoice.public()


@router.get("/{invoice_id}")
def read_invoice(invoice_id: str, tenant: Tenant = Depends(require_tenant_any_state)):
    """One invoice, as issued."""
    invoice = _load(invoice_id, tenant)
    return {
        **invoice.public(),
        "issued_by": issuer_block(),
        "billed_to": {
            "company_name": tenant.company_name,
            "contact_name": tenant.contact_name,
            "contact_email": tenant.contact_email,
        },
        "terms": (
            f"Net {invoice.terms_days}. Amounts in USD. This invoice was "
            "priced at issue and does not change with the estate."
        ),
    }


@router.post("/{invoice_id}/paid")
def mark_paid(
    invoice_id: str,
    payload: PaymentRecord,
    tenant_id: str = Query(..., description="Which estate the invoice belongs to."),
    _: None = Depends(require_platform_admin),
):
    """Record settlement.

    Platform operator only, and this is the one that mattered most.
    Measured before the fix: a customer's own owner could POST here with a
    made-up reference and write off a $2,499 invoice in one call — the
    balance went to zero, it dropped out of collections, and nothing
    anywhere recorded that no money had arrived.

    Where a payment processor's webhook lands. Kept as a plain endpoint so
    the lifecycle is complete without one, and so a bank transfer — how
    most contracts at these sizes are actually settled — can be recorded
    the same way.
    """
    tenant = _tenant_or_404(tenant_id)
    invoice = _load(invoice_id, tenant)
    if invoice.state == "void":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} was voided and cannot be paid.",
        )
    if invoice.state == "paid":
        return {
            "message": f"{invoice.number} was already settled.",
            "invoice": invoice.public(),
        }

    STORE.settle_invoice(invoice, payload.reference, payload.amount_usd)
    settled = invoice.state == "paid"
    send_receipt(tenant, invoice, payload.reference, settled)
    write_audit(
        tenant, None,
        "invoice.paid" if settled else "invoice.part_paid",
        f"{invoice.number}: ${invoice.amount_paid_usd:,.2f} of "
        f"${invoice.total_usd:,.2f} received ({payload.reference})"
        + ("." if settled else f"; ${invoice.balance_usd:,.2f} still owed."),
        fallback_actor="Platform operator",
    )
    return {
        "message": (
            f"{invoice.number} settled."
            if settled
            else f"{invoice.number} part paid. "
                 f"${invoice.balance_usd:,.2f} is still outstanding."
        ),
        "invoice": invoice.public(),
    }


def send_receipt(tenant, invoice, reference: str, settled: bool) -> None:
    """Confirm that the money arrived.

    Cheap, and it removes an entire category of email: the customer who
    paid three weeks ago and wants to know whether we noticed. It also
    stops the most embarrassing thing a collections robot can do, which
    is chase somebody who has already paid — the dunning ladder skips a
    settled invoice, and this is how the customer finds out that it has.

    Keyed on the amount received, so a part payment followed by the rest
    produces two receipts rather than one, and a retried webhook produces
    neither twice.
    """
    from mail import send as send_mail

    if settled:
        subject = f"{invoice.number} paid. Thank you."
        closing = "Nothing further is owed on this invoice."
    else:
        subject = (
            f"{invoice.number}: ${invoice.amount_paid_usd:,.2f} received, "
            f"${invoice.balance_usd:,.2f} outstanding"
        )
        closing = (
            f"${invoice.balance_usd:,.2f} is still outstanding on this "
            "invoice, so it stays open."
        )

    send_mail(
        to_address=tenant.contact_email,
        subject=subject,
        body=(
            f"{tenant.contact_name},\n\n"
            f"We have recorded ${invoice.amount_paid_usd:,.2f} against "
            f"{invoice.number} (reference {reference}).\n\n"
            f"{closing}\n\n"
            f"{render_invoice(invoice, tenant)}"
        ),
        # The amount is in the key: a part payment and the balance that
        # follows are two different events and each deserves its own
        # confirmation, while the same webhook delivered twice is one.
        dedupe_key=(
            f"receipt:{invoice.invoice_id}:{invoice.amount_paid_usd:.2f}"
        ),
        klass="transactional",
        tenant_id=tenant.tenant_id,
    )


@router.post("/{invoice_id}/void")
def void_invoice(
    invoice_id: str,
    tenant_id: str = Query(..., description="Which estate the invoice belongs to."),
    _: None = Depends(require_platform_admin),
):
    """Void an invoice.

    Voided rather than deleted, and the number is never reissued: a gap in
    a sequence is the first thing an auditor asks about.
    """
    tenant = _tenant_or_404(tenant_id)
    invoice = _load(invoice_id, tenant)
    if invoice.state == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{invoice.number} is settled. Issue a credit note rather "
                "than voiding a paid invoice."
            ),
        )
    STORE.void_invoice(invoice)
    write_audit(tenant, None, "invoice.voided", f"{invoice.number} voided.",
                fallback_actor="Platform operator")
    return {"message": f"{invoice.number} voided.", "invoice": invoice.public()}
