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

from fastapi import APIRouter, Depends, Query, Response

from auth import require_platform_admin
from store import STORE, iso, utc_now

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


def deductible_costs(start: datetime, end: datetime) -> Dict[str, Any]:
    """What the business spent, as far as this application knows it.

    Deliberately incomplete, and it says so. The application meters what
    it spends on models and telephony and knows its own fixed costs; it
    has no idea what was paid for a laptop, an accountant, a domain
    bought on a card it never sees, or anything else. Presenting this as
    the whole expense side would cost more in unclaimed deductions than
    it saves in effort.
    """
    from costs import RATE_AI_CALL, RATE_SMS, RATE_VOICE_CALL
    from margin import FIXED_MONTHLY_USD

    months = max(1.0, (end - start).total_seconds() / (30.44 * 86400))

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
    return {
        "metered_usage_usd": round(metered, 2),
        "fixed_infrastructure_usd": fixed,
        "known_total_usd": round(metered + fixed, 2),
        "months_covered": round(months, 2),
        "warning": (
            "This is only what the application can see: model calls, "
            "telephony, and the infrastructure figure in "
            "CYBERLOGIX_FIXED_MONTHLY_USD. Hardware, professional fees, "
            "software, travel and everything else bought outside this "
            "system are missing and are almost certainly the larger half. "
            "Every one of them is a deduction that goes unclaimed if it "
            "is not given to whoever files the return."
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
