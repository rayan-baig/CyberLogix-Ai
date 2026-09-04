"""Spend controls and the cost report.

Three levers, in order of how much they save:

1. **Cache generated copy.** A walk-in that fails on Monday and again on
   Friday produces the same alert prompt, so the second one is served from
   cache for nothing. Alerts are highly repetitive by nature — same sensor,
   same catastrophe, same severity band — so the hit rate climbs with use.
2. **Cap daily spend per tenant.** A sensor stuck in a breach loop, or a
   compromised key, cannot run up an unbounded bill: past the cap, alerts
   fall back to the deterministic template (still sent, just not written by
   a model) and extra messages are suppressed with a recorded reason.
3. **Meter everything.** `/api/costs` reports what was spent, what the cache
   saved, and what the caps prevented, so the numbers are visible before
   they arrive on an invoice.

Rates are list prices at the time of writing and are configurable; treat
the output as an estimate for capacity planning, not a bill.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from licenses import require_tenant
from store import STORE, Tenant, utc_now

logger = logging.getLogger("cyberlogix.costs")

router = APIRouter(prefix="/api/costs", tags=["Spend Controls"])


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("%s is not an integer; using %s.", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("%s is not a number; using %s.", name, default)
        return default


# Daily per-tenant caps. 0 disables a cap.
MAX_AI_CALLS_PER_DAY = _env_int("CYBERLOGIX_MAX_AI_CALLS_PER_DAY", 200)
MAX_SMS_PER_DAY = _env_int("CYBERLOGIX_MAX_SMS_PER_DAY", 100)
MAX_VOICE_CALLS_PER_DAY = _env_int("CYBERLOGIX_MAX_VOICE_CALLS_PER_DAY", 30)

# Unit rates in USD, used only to turn counters into an estimate.
RATE_AI_CALL = _env_float("CYBERLOGIX_RATE_AI_CALL", 0.0012)
RATE_SMS = _env_float("CYBERLOGIX_RATE_SMS", 0.0079)
RATE_VOICE_CALL = _env_float("CYBERLOGIX_RATE_VOICE_CALL", 0.0140)

CAPS = {
    "ai_calls": MAX_AI_CALLS_PER_DAY,
    "sms": MAX_SMS_PER_DAY,
    "voice_calls": MAX_VOICE_CALLS_PER_DAY,
}


def cache_key(prompt: str, purpose: str) -> str:
    """Stable key for a generation request."""
    digest = hashlib.sha256(f"{purpose}\x00{prompt}".encode("utf-8")).hexdigest()
    return digest[:32]


def allow_ai_call(tenant_id: Optional[str]) -> Tuple[bool, str]:
    """Whether a fresh model call is within today's budget."""
    if not tenant_id or MAX_AI_CALLS_PER_DAY <= 0:
        return True, ""
    usage = STORE.usage_for(tenant_id)
    if usage.ai_calls >= MAX_AI_CALLS_PER_DAY:
        return False, (
            f"Daily AI generation cap reached ({MAX_AI_CALLS_PER_DAY}). "
            "Falling back to the deterministic template."
        )
    return True, ""


def allow_message(tenant_id: Optional[str], channel: str) -> Tuple[bool, str]:
    """Whether an SMS or voice call is within today's budget."""
    if not tenant_id:
        return True, ""
    usage = STORE.usage_for(tenant_id)

    if channel == "sms":
        cap, used = MAX_SMS_PER_DAY, usage.sms_sent
    elif channel == "voice":
        cap, used = MAX_VOICE_CALLS_PER_DAY, usage.voice_calls
    else:
        return True, ""

    if cap > 0 and used >= cap:
        return False, f"Daily {channel} cap reached ({cap})."
    return True, ""


def record(tenant_id: Optional[str], field: str, amount: int = 1) -> None:
    """Increment a usage counter, tolerating an unattributed call."""
    if tenant_id:
        STORE.bump_usage(tenant_id, field, amount)


def _estimate(usage) -> Dict[str, Any]:
    spend = (
        usage.ai_calls * RATE_AI_CALL
        + usage.sms_sent * RATE_SMS
        + usage.voice_calls * RATE_VOICE_CALL
    )
    # What the cache and caps kept off the bill.
    saved = (
        usage.ai_cache_hits * RATE_AI_CALL
        + usage.ai_suppressed * RATE_AI_CALL
        + usage.sms_suppressed * RATE_SMS
        + usage.voice_suppressed * RATE_VOICE_CALL
    )
    return {
        "day": usage.day,
        "ai_calls": usage.ai_calls,
        "ai_cache_hits": usage.ai_cache_hits,
        "ai_suppressed": usage.ai_suppressed,
        "sms_sent": usage.sms_sent,
        "sms_suppressed": usage.sms_suppressed,
        "voice_calls": usage.voice_calls,
        "voice_suppressed": usage.voice_suppressed,
        "estimated_spend_usd": round(spend, 4),
        "estimated_saved_usd": round(saved, 4),
    }


@router.get("")
def cost_report(
    days: int = Query(30, ge=1, le=90),
    tenant: Tenant = Depends(require_tenant),
):
    """Metered usage, estimated spend, and what the controls saved."""
    history = [_estimate(u) for u in STORE.usage_history(tenant.tenant_id, days)]
    today = _estimate(STORE.usage_for(tenant.tenant_id))

    totals = {
        field: sum(row[field] for row in history)
        for field in (
            "ai_calls",
            "ai_cache_hits",
            "ai_suppressed",
            "sms_sent",
            "sms_suppressed",
            "voice_calls",
            "voice_suppressed",
        )
    }
    spend = round(sum(row["estimated_spend_usd"] for row in history), 4)
    saved = round(sum(row["estimated_saved_usd"] for row in history), 4)

    attempted = totals["ai_calls"] + totals["ai_cache_hits"]
    hit_rate = (
        round(totals["ai_cache_hits"] / attempted * 100, 1) if attempted else None
    )

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "period_days": days,
        "generated_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": today,
        "totals": totals,
        "estimated_spend_usd": spend,
        "estimated_saved_usd": saved,
        "savings_percent": (
            round(saved / (spend + saved) * 100, 1) if (spend + saved) else None
        ),
        "ai_cache_hit_rate_percent": hit_rate,
        "ai_cache_entries": STORE.cache_size(),
        "daily_caps": CAPS,
        "unit_rates_usd": {
            "ai_call": RATE_AI_CALL,
            "sms": RATE_SMS,
            "voice_call": RATE_VOICE_CALL,
        },
        "daily": history,
        "note": (
            "Unit rates are configurable list-price estimates for capacity "
            "planning, not a bill."
        ),
    }
