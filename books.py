"""The books, so somebody qualified can do the part I cannot.

Asked to cut the tax. Tax is not a cost this codebase can engineer away
— it is a share of profit, and the levers on it are entity structure,
timing and deductions, all of which depend on facts about the company
that live outside this repository and belong to an accountant.

What is squarely in scope is making that person cheap and making sure
nothing is left on the table:

* **An accountant bills by the hour.** Handing them a reconciled ledger
  instead of a database costs less than handing them a database.
* **You cannot claim a deduction you cannot evidence.** The metered
  spend and the fixed costs are known to this application and were
  exported nowhere, so at filing time they were somebody's memory.
* **Cash basis and accrual basis give different answers, in different
  tax years.** An invoice issued on 28 December and paid on 4 January is
  revenue in one year or the next depending on which basis applies. This
  module reports both and refuses to pick, because picking is a question
  about the entity, not about the data.

What it does not do is compute anybody's tax. It produces the record.
Nothing here is a tax return, an opinion about one, or a substitute for
the person who signs it.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from models import Finite

from auth import require_platform_admin
from store import EXPENSE_CATEGORIES, STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.books")

router = APIRouter(prefix="/api/books", tags=["Books"])


def _window(year: Optional[int], since: Optional[str], until: Optional[str]):
    """The period being reported, as (start, end, label)."""
    if year is not None:
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        return start, end, str(year)
    start = _parse_or(since, datetime(1970, 1, 1, tzinfo=timezone.utc))
    end = _parse_or(until, utc_now())
    return start, end, f"{iso(start)} to {iso(end)}"


def _parse_or(value: Optional[str], fallback: datetime) -> datetime:
    if not value:
        return fallback
    from store import _parse

    try:
        parsed = _parse(value if "T" in value else f"{value}T00:00:00Z")
    except ValueError:
        return fallback
    return parsed or fallback


def _company(tenant_id: str) -> str:
    tenant = STORE.get_tenant(tenant_id)
    return tenant.company_name if tenant else "(deleted account)"


def sales_ledger(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Every invoice issued in the period. The accrual-basis record."""
    rows = []
    for invoice in STORE.all_invoices():
        if invoice.issued_at is None or not (start <= invoice.issued_at < end):
            continue
        rows.append({
            "date": iso(invoice.issued_at),
            "number": invoice.number,
            "customer": _company(invoice.tenant_id),
            "tenant_id": invoice.tenant_id,
            "total_usd": invoice.total_usd,
            "paid_usd": invoice.amount_paid_usd,
            "balance_usd": invoice.balance_usd,
            "state": invoice.state,
            "purchase_order": invoice.purchase_order,
        })
    return sorted(rows, key=lambda r: (r["date"], r["number"]))


def cash_book(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Every payment actually received in the period.

    Reads the per-payment history rather than the invoice's total, which
    is the only reason this can be accurate: until each payment was kept
    with its own date and reference, an invoice paid in three
    instalments across a year-end was one number with one date, and no
    cash-basis figure could be derived from it at all.
    """
    rows = []
    for invoice in STORE.all_invoices():
        for payment in invoice.payments:
            from store import _parse

            at = _parse(payment.get("at"))
            if at is None or not (start <= at < end):
                continue
            rows.append({
                "date": iso(at),
                "reference": payment.get("reference"),
                "amount_usd": payment.get("amount_usd"),
                "invoice": invoice.number,
                "customer": _company(invoice.tenant_id),
                "tenant_id": invoice.tenant_id,
            })
    return sorted(rows, key=lambda r: (r["date"], r["reference"] or ""))


def write_offs(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Invoices voided in the period.

    A voided invoice is revenue that was recognised and then was not.
    Whether it is deductible, and in which year, is exactly the sort of
    question this module hands to somebody qualified rather than
    answering.
    """
    rows = []
    for invoice in STORE.all_invoices():
        if invoice.voided_at is None or not (start <= invoice.voided_at < end):
            continue
        rows.append({
            "date": iso(invoice.voided_at),
            "number": invoice.number,
            "customer": _company(invoice.tenant_id),
            "total_usd": invoice.total_usd,
            "recovered_usd": invoice.amount_paid_usd,
        })
    return sorted(rows, key=lambda r: r["date"])


def operating_window(
    start: datetime, end: datetime, now: Optional[datetime] = None
) -> "tuple[datetime, datetime]":
    """The part of a requested period the business was actually running.

    Infrastructure is billed by the month, so it is tempting to price it
    as rate x months and be done. That is how this file came to report
    $9,968 of hosting against an empty database: the default period
    starts at the Unix epoch, and 680 months of a server nobody rented
    is a deduction nobody may claim. Asking for a future year was worse
    — a full year of cost, already "spent", in 2031.

    So the rate is applied only where the requested period overlaps the
    period the company can evidence: from the first thing it ever
    recorded, to now. Before the first record there was no company to
    bill, and after now the money has not left yet. Where there is no
    history at all the overlap is empty and the cost is zero, which is
    the honest answer rather than a convenient one.
    """
    now = now or utc_now()
    began = first_activity()
    if began is None:
        return end, end  # no history: an empty window, and so no cost
    lower = max(start, began)
    upper = min(end, now)
    if upper <= lower:
        return upper, upper
    return lower, upper


def first_activity() -> Optional[datetime]:
    """The earliest moment this deployment has any record of itself.

    Normally the database's install stamp, because that is when the
    server it lives on started costing money. But a record older than
    the stamp is proof the stamp is young — a database restored from
    backup, or migrated onto new hardware, carries customers it cannot
    have served before it existed. The earlier of the two is the only
    one that can be true, so that is the one used.
    """
    from store import _parse

    stamps: List[datetime] = []
    stamped = STORE._db.installed_at()
    if stamped:
        try:
            parsed = _parse(stamped)
        except ValueError:
            parsed = None
        if parsed is not None:
            stamps.append(parsed)
    with STORE._lock:
        stamps.extend(
            t.activated_at for t in STORE._tenants.values() if t.activated_at
        )
    return min(stamps) if stamps else None


def deductible_costs(start: datetime, end: datetime) -> Dict[str, Any]:
    """What the business spent: metered, fixed, and recorded by hand.

    Tax is charged on profit, and profit is revenue minus what you can
    evidence. That makes recorded expenses the largest legitimate lever
    on a tax bill that anybody actually controls — and until there was
    somewhere to put them, everything bought on a card this application
    never sees existed only in somebody's memory at filing time, which
    is the same as not existing.

    Still says what it cannot see. An expense nobody enters is still
    invisible, so the warning stays until the entered total stops
    looking implausibly small.
    """
    from costs import RATE_AI_CALL, RATE_SMS, RATE_VOICE_CALL
    from margin import FIXED_MONTHLY_USD

    # Fixed cost accrues only where the requested period overlaps the
    # time the business was actually running — see operating_window.
    ran_from, ran_to = operating_window(start, end)
    overlap = (ran_to - ran_from).total_seconds() / (30.44 * 86400)
    # A month that has started is a month that has been billed: the server
    # is a monthly subscription, not a meter. But a period the business did
    # not trade in at all overlaps by nothing and costs nothing, which is
    # what stops a floor of one month from becoming a floor of 680.
    months = max(1.0, overlap) if overlap > 0 else 0.0

    # Counters, priced at the same rates the spend report uses, for the
    # days that fall inside the period. `day` is a plain YYYY-MM-DD, so
    # the comparison is on the date rather than a parsed instant.
    first, last = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    metered = 0.0
    for usage in STORE._usage.values():
        if not (first <= usage.day < last):
            continue
        metered += (
            usage.ai_calls * RATE_AI_CALL
            + usage.sms_segments * RATE_SMS
            + usage.voice_calls * RATE_VOICE_CALL
        )

    fixed = round(FIXED_MONTHLY_USD * months, 2)

    recorded = STORE.expenses_between(start, end)
    by_category: Dict[str, float] = {}
    for expense in recorded:
        by_category[expense.category] = round(
            by_category.get(expense.category, 0.0) + expense.amount_usd, 2
        )
    entered = round(sum(e.amount_usd for e in recorded), 2)
    missing_receipts = [e for e in recorded if not e.receipt.strip()]

    return {
        "metered_usage_usd": round(metered, 2),
        "fixed_infrastructure_usd": fixed,
        "recorded_expenses_usd": entered,
        "recorded_expense_count": len(recorded),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "known_total_usd": round(metered + fixed + entered, 2),
        "months_covered": round(months, 2),
        "infrastructure_charged_from": iso(ran_from) if months else None,
        "infrastructure_charged_to": iso(ran_to) if months else None,
        "without_a_receipt": len(missing_receipts),
        "warning": (
            "Metered spend and infrastructure are counted automatically; "
            "everything else is counted only if somebody entered it at "
            "POST /api/books/expenses. An expense nobody enters is a "
            "deduction nobody claims, and it is the largest lever on a "
            "tax bill that is actually in your hands."
            + (
                " Infrastructure is charged only for the part of this "
                "period the business was actually running, which is "
                f"{round(months, 2)} month(s) of it; a server nobody "
                "rented yet is not a deduction."
                if (ran_from > start or ran_to < end) else ""
            )
            + (
                f" {len(missing_receipts)} recorded expense(s) have no "
                "receipt reference, and a deduction you cannot evidence "
                "is one you may not get to keep."
                if missing_receipts else ""
            )
        ),
    }


def summary(
    year: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """The period on both bases, side by side, with neither preferred."""
    start, end, label = _window(year, since, until)
    ledger = sales_ledger(start, end)
    cash = cash_book(start, end)
    voided = write_offs(start, end)
    costs = deductible_costs(start, end)

    invoiced = round(sum(r["total_usd"] for r in ledger), 2)
    received = round(sum(r["amount_usd"] or 0 for r in cash), 2)
    written_off = round(sum(r["total_usd"] for r in voided), 2)

    return {
        "period": label,
        "from": iso(start),
        "to": iso(end),
        "accrual_basis": {
            "revenue_usd": invoiced,
            "invoices": len(ledger),
            "what_it_means": (
                "Revenue counted when the invoice was issued, whether or "
                "not the money has arrived."
            ),
        },
        "cash_basis": {
            "revenue_usd": received,
            "payments": len(cash),
            "what_it_means": (
                "Revenue counted when the money actually arrived. An "
                "invoice issued in December and paid in January belongs "
                "to a different tax year under this basis than under the "
                "other, which is the entire reason both are here."
            ),
        },
        "written_off_usd": written_off,
        "outstanding_at_period_end_usd": round(
            sum(r["balance_usd"] for r in ledger), 2
        ),
        "known_costs": costs,
        "note": (
            "A record, not a return. Which basis applies, what else is "
            "deductible, and what is owed are questions about the entity "
            "and its jurisdiction — they are for whoever signs the "
            "filing, and this file has no opinion on any of them."
        ),
    }


def _csv(rows: List[Dict[str, Any]], columns: List[str]) -> str:
    from automation import csv_safe

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: csv_safe(row.get(key)) for key in columns})
    return buffer.getvalue()


class ExpenseEntry(BaseModel):
    """Something the business paid for.

    Extras are refused rather than dropped, as on every money-bearing
    model here: a misspelt amount field that is silently ignored is a
    deduction quietly worth nothing.
    """

    model_config = ConfigDict(extra="forbid")

    amount_usd: Finite = Field(..., gt=0, le=10_000_000)
    category: str = Field(..., description="One of the known categories.")
    description: str = Field(..., min_length=1, max_length=300)
    spent_at: Optional[str] = Field(
        None, description="YYYY-MM-DD. Defaults to today."
    )
    supplier: str = Field("", max_length=200)
    receipt: str = Field(
        "", max_length=300,
        description="Where the evidence is: a filename, a link, a folder.",
    )


# --- routes ---------------------------------------------------------------


@router.get("")
def read_summary(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """The period on both bases, plus what the application knows it spent."""
    return summary(year, since, until)


@router.get("/ledger")
def read_ledger(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """Every invoice issued in the period."""
    start, end, label = _window(year, since, until)
    rows = sales_ledger(start, end)
    return {"period": label, "count": len(rows), "invoices": rows}


@router.get("/cash")
def read_cash(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """Every payment received in the period."""
    start, end, label = _window(year, since, until)
    rows = cash_book(start, end)
    return {"period": label, "count": len(rows), "payments": rows}


@router.get("/expense-categories")
def read_categories(_: None = Depends(require_platform_admin)):
    """The drawers of the filing cabinet.

    Not a tax schedule: whoever prepares the return maps these onto
    whatever the local form calls them.
    """
    return {"categories": list(EXPENSE_CATEGORIES)}


@router.post("/expenses", status_code=201)
def add_expense(
    payload: ExpenseEntry, _: None = Depends(require_platform_admin)
):
    """Record something the business paid for."""
    category = payload.category.strip().lower()
    if category not in EXPENSE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{payload.category}' is not a category. Use one of "
                f"{list(EXPENSE_CATEGORIES)} — 'other' is a real answer."
            ),
        )
    spent_at = _parse_or(payload.spent_at, utc_now())
    if spent_at > utc_now():
        raise HTTPException(
            status_code=422,
            detail="That expense is dated in the future.",
        )

    expense = STORE.record_expense(
        spent_at=spent_at,
        amount_usd=payload.amount_usd,
        category=category,
        description=payload.description.strip(),
        supplier=payload.supplier.strip(),
        receipt=payload.receipt.strip(),
    )
    return {
        "expense": expense.public(),
        "note": (
            "Keep the receipt where the reference points. A deduction you "
            "cannot evidence is one you may not get to keep."
            if expense.receipt else
            "No receipt reference on this one. Worth adding before the "
            "year ends, while you still remember where it is."
        ),
    }


@router.get("/expenses")
def read_expenses(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """Everything recorded in the period."""
    start, end, label = _window(year, since, until)
    rows = STORE.expenses_between(start, end)
    return {
        "period": label,
        "count": len(rows),
        "total_usd": round(sum(e.amount_usd for e in rows), 2),
        "expenses": [e.public() for e in rows],
    }


@router.delete("/expenses/{expense_id}")
def remove_expense(
    expense_id: str, _: None = Depends(require_platform_admin)
):
    """Remove one entered by mistake."""
    if not STORE.delete_expense(expense_id):
        raise HTTPException(status_code=404, detail="No such expense.")
    return {"expense_id": expense_id, "deleted": True}


@router.get("/expenses.csv")
def expenses_csv(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """The expense ledger as a file an accountant can open."""
    start, end, label = _window(year, since, until)
    rows = [e.public() for e in STORE.expenses_between(start, end)]
    body = _csv(rows, [
        "spent_at", "category", "description", "supplier",
        "amount_usd", "receipt",
    ])
    return Response(
        content=body, media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="expenses-{label}.csv"'
        },
    )


@router.get("/ledger.csv")
def ledger_csv(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """The sales ledger as a file an accountant can open.

    CSV because it opens in everything. The fields are escaped through
    the same guard the compliance export uses, so a company name
    beginning with `=` is text in a spreadsheet rather than a formula.
    """
    start, end, label = _window(year, since, until)
    body = _csv(sales_ledger(start, end), [
        "date", "number", "customer", "total_usd", "paid_usd",
        "balance_usd", "state", "purchase_order",
    ])
    return Response(
        content=body, media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="sales-ledger-{label}.csv"'
        },
    )


@router.get("/cash.csv")
def cash_csv(
    year: Optional[int] = Query(None, ge=2000, le=2200),
    since: Optional[str] = None,
    until: Optional[str] = None,
    _: None = Depends(require_platform_admin),
):
    """The cash book as a file an accountant can open."""
    start, end, label = _window(year, since, until)
    body = _csv(cash_book(start, end), [
        "date", "reference", "amount_usd", "invoice", "customer",
    ])
    return Response(
        content=body, media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="cash-book-{label}.csv"'
        },
    )
