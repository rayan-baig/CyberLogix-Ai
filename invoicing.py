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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Tenant, User, iso

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


class InvoiceRequest(BaseModel):
    include_add_ons: str = Field(
        "", description="Comma-separated add-on keys billed this period."
    )
    include_setup: bool = Field(
        False, description="Add the one-time per-site commissioning fee."
    )
    period_days: int = Field(30, ge=1, le=366)
    purchase_order: Optional[str] = Field(None, max_length=64)


class PaymentRecord(BaseModel):
    reference: str = Field(..., min_length=1, max_length=120)
    amount_usd: Optional[float] = Field(None, ge=0)


def _load(invoice_id: str, tenant: Tenant):
    invoice = STORE.get_invoice((invoice_id or "").strip())
    if invoice is None or invoice.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found for this tenant.",
        )
    return invoice


def build_lines(
    tenant: Tenant, add_on_keys: List[str], include_setup: bool
) -> List[Dict[str, Any]]:
    """The line items, priced at this moment and then frozen."""
    from pricing import ADD_ONS, SETUP_FEE_PER_SITE_USD, add_on_price, build_subscription

    subscription = build_subscription(tenant)
    lines: List[Dict[str, Any]] = []

    for row in subscription["line_items"]:
        lines.append(
            {
                "kind": "subscription",
                "description": f"{row['industry']} — {row['description']}",
                "quantity": row["units"],
                "unit_price_usd": row["unit_price_usd"],
                "amount_usd": row["line_total_usd"],
            }
        )

    units = subscription["units_total"]
    for key in add_on_keys:
        entry = ADD_ONS[key]
        amount = add_on_price(key, units)
        quantity = units if entry["basis"] == "per covered unit" else 1
        lines.append(
            {
                "kind": "add_on",
                "description": f"{entry['name']} ({entry['basis']})",
                "quantity": quantity,
                "unit_price_usd": entry["monthly_usd"],
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
def list_invoices(tenant: Tenant = Depends(require_tenant)):
    """Every invoice ever issued to this tenant, newest first."""
    invoices = STORE.invoices_for(tenant.tenant_id)
    outstanding = [i for i in invoices if i.state == "issued"]
    return {
        "count": len(invoices),
        "outstanding_count": len(outstanding),
        "outstanding_usd": round(sum(i.total_usd for i in outstanding), 2),
        "overdue_count": sum(1 for i in outstanding if i.overdue()),
        "invoices": [i.public() for i in invoices],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def issue_invoice(
    payload: InvoiceRequest,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Issue an invoice for the current period.

    Figures are snapshotted now and never recomputed: an invoice whose
    total moves after it was sent is a dispute, and the customer would be
    right to raise it.
    """
    from pricing import ADD_ONS

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
        tenant, operator, "invoice.issued",
        f"{invoice.number} for ${invoice.total_usd:,.2f}, due {iso(invoice.due_at)}.",
    )
    logger.info(
        "Invoice issued: %s tenant=%s total=%.2f",
        invoice.number, tenant.tenant_id, invoice.total_usd,
    )
    return invoice.public()


@router.get("/{invoice_id}")
def read_invoice(invoice_id: str, tenant: Tenant = Depends(require_tenant)):
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
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Record settlement.

    Where a payment processor's webhook lands. Kept as a plain endpoint so
    the lifecycle is complete without one, and so a bank transfer — how
    most contracts at these sizes are actually settled — can be recorded
    the same way.
    """
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
    write_audit(
        tenant, operator, "invoice.paid",
        f"{invoice.number} settled ({payload.reference}).",
    )
    return {"message": f"{invoice.number} settled.", "invoice": invoice.public()}


@router.post("/{invoice_id}/void")
def void_invoice(
    invoice_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Void an invoice.

    Voided rather than deleted, and the number is never reissued: a gap in
    a sequence is the first thing an auditor asks about.
    """
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
    write_audit(tenant, operator, "invoice.voided", f"{invoice.number} voided.")
    return {"message": f"{invoice.number} voided.", "invoice": invoice.public()}
