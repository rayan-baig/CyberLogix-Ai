"""Account credit: money we owe a business, taken off its next invoice.

Until this there was no way to give a customer money back except by hand:
an invoice was a list of charges, a void, or a payment. The first thing
that needed one was "bring a friend" (referrals.py), whose reward is a
month free on one location, and that is a credit, not a discount code.

How it works, in four rules:

* **A credit is a row with an amount and what is left of it.** Granted
  once per reason: the row's id is the reason (``referral:TEN-000042``),
  so the same reward cannot be granted twice, by a retry or by two
  payments recorded at the same moment.
* **It comes off the next invoice, as a line of its own.** Never by
  editing a figure: the invoice shows ``Credit: ...`` with a minus amount,
  and the total is what is owed after it. Applied after any prepayment
  discount, so a credit is worth what it says.
* **It never takes an invoice below zero.** What the invoice cannot use
  stays on the account for the next one. An invoice the credit pays in
  full is recorded as paid by the credit, so nobody is chased for $0.
* **Voiding gives it back.** The invoice it was used on is voided, the
  amount returns to the credit, and the re-issued invoice takes it again.

Only invoices for service take credit: a late charge is a separate
document about lateness and is paid as itself.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from store import STORE, Invoice, Tenant, iso, utc_now

logger = logging.getLogger("cyberlogix.credits")

KIND = "account_credit"


def _rows(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(KIND) if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: (r.get("created_at") or "", r.get("credit_id") or ""))


def for_tenant(tenant_id: str) -> List[Dict[str, Any]]:
    """Every credit this business has had, oldest first."""
    return _rows(tenant_id)


def get(credit_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(KIND, credit_id)


def balance(tenant_id: str) -> float:
    """What is still to come off this business's invoices."""
    return round(sum(float(r.get("remaining_usd") or 0.0) for r in _rows(tenant_id)
                     if not r.get("withdrawn_at")), 2)


def grant(tenant_id: str, credit_id: str, amount_usd: float, reason: str,
          source: str, **detail: Any) -> Optional[Dict[str, Any]]:
    """Put a credit on a business's account, once per `credit_id`.

    Returns the new row, or None if that credit already exists. The check
    and the write are one step under the store lock.
    """
    amount = round(float(amount_usd), 2)
    if amount <= 0:
        return None
    with STORE._lock:
        if STORE._db.get(KIND, credit_id) is not None:
            return None
        row: Dict[str, Any] = {
            "credit_id": credit_id,
            "tenant_id": tenant_id,
            "amount_usd": amount,
            "remaining_usd": amount,
            "reason": reason,
            "source": source,
            "created_at": iso(utc_now()),
            "applications": [],
            "withdrawn_at": None,
            "withdrawn_usd": 0.0,
            "withdrawn_reason": "",
            **detail,
        }
        STORE._db.put(KIND, credit_id, row)
    logger.info("Credit %s of $%.2f granted to %s.", credit_id, amount, tenant_id)
    return row


def withdraw(credit_id: str, reason: str) -> Optional[Dict[str, Any]]:
    """Take back what is left of a credit. What was already used stays used.

    For a reward whose reason has gone away (the referred business was
    refunded). Returns the row, or None if there was no such credit.
    """
    with STORE._lock:
        row = STORE._db.get(KIND, credit_id)
        if row is None:
            return None
        if row.get("withdrawn_at"):
            return row
        row["withdrawn_usd"] = round(float(row.get("remaining_usd") or 0.0), 2)
        row["remaining_usd"] = 0.0
        row["withdrawn_at"] = iso(utc_now())
        row["withdrawn_reason"] = reason
        STORE._db.put(KIND, credit_id, row)
        return row


def apply_to_lines(tenant: Tenant, lines: List[Dict[str, Any]]
                   ) -> Tuple[List[Dict[str, Any]], List[Tuple[str, float]]]:
    """The lines with this business's credit taken off, and what was used.

    Call with the store lock held, and record the result against the
    invoice with `record_applied` under the same hold, so two invoices
    issued at once cannot both spend the same credit.
    """
    owed = round(sum(float(line["amount_usd"]) for line in lines), 2)
    if owed <= 0:
        return lines, []
    out = list(lines)
    used: List[Tuple[str, float]] = []
    for row in _rows(tenant.tenant_id):
        if owed <= 0:
            break
        left = round(float(row.get("remaining_usd") or 0.0), 2)
        if left <= 0 or row.get("withdrawn_at"):
            continue
        take = round(min(left, owed), 2)
        out.append({
            "kind": "credit",
            "description": f"Credit: {row.get('reason') or 'account credit'}",
            "quantity": 1,
            "unit_price_usd": -take,
            "amount_usd": -take,
            "credit_id": row["credit_id"],
        })
        used.append((row["credit_id"], take))
        owed = round(owed - take, 2)
    return out, used


def record_applied(used: List[Tuple[str, float]], invoice: Invoice) -> None:
    """Write down which invoice each credit was used on."""
    with STORE._lock:
        for credit_id, amount in used:
            row = STORE._db.get(KIND, credit_id)
            if row is None:
                continue
            row["remaining_usd"] = round(max(0.0, float(row.get("remaining_usd") or 0.0) - amount), 2)
            row.setdefault("applications", []).append({
                "invoice_id": invoice.invoice_id,
                "number": invoice.number,
                "amount_usd": round(amount, 2),
                "at": iso(utc_now()),
            })
            STORE._db.put(KIND, credit_id, row)


def release_for_invoice(invoice_id: str) -> float:
    """A voided invoice gives back the credit it used. Returns the amount."""
    returned = 0.0
    with STORE._lock:
        for row in STORE._db.all(KIND):
            kept, back = [], 0.0
            for use in row.get("applications") or []:
                if use.get("invoice_id") == invoice_id:
                    back += float(use.get("amount_usd") or 0.0)
                else:
                    kept.append(use)
            if not back:
                continue
            row["applications"] = kept
            if not row.get("withdrawn_at"):
                row["remaining_usd"] = round(float(row.get("remaining_usd") or 0.0) + back, 2)
            STORE._db.put(KIND, row["credit_id"], row)
            returned += back
    return round(returned, 2)


def create_invoice(tenant: Tenant, lines: List[Dict[str, Any]], **kwargs: Any) -> Invoice:
    """Issue an invoice for service with any credit on the account taken off.

    The one way the billing run and a hand-issued invoice create their
    invoices, so neither can forget the credit.
    """
    with STORE._lock:
        lines, used = apply_to_lines(tenant, lines)
        invoice = STORE.create_invoice(tenant=tenant, lines=lines, **kwargs)
        record_applied(used, invoice)
        if used and invoice.total_usd <= 0:
            # Paid by the credit. Left open, a $0 invoice would sit in
            # collections and could be chased.
            invoice, _ = STORE.settle_invoice(invoice, f"credit:{invoice.invoice_id}", 0.0)
    return invoice


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    """A credit as the console shows it."""
    uses = row.get("applications") or []
    return {
        "credit_id": row["credit_id"],
        "amount_usd": row.get("amount_usd", 0.0),
        "remaining_usd": row.get("remaining_usd", 0.0),
        "reason": row.get("reason", ""),
        "source": row.get("source", ""),
        "created_at": row.get("created_at"),
        "used_on": [{"number": u.get("number"), "amount_usd": u.get("amount_usd")}
                    for u in uses],
        "withdrawn": bool(row.get("withdrawn_at")),
        "withdrawn_reason": row.get("withdrawn_reason", ""),
        "state": ("withdrawn" if row.get("withdrawn_at")
                  else "used" if not row.get("remaining_usd") and uses
                  else "part used" if uses
                  else "waiting for your next invoice"),
    }
