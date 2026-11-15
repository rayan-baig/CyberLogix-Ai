"""Repair partners: refrigeration technicians who pay us for leads.

The forecaster and the pattern classifier already notice a unit that is
failing (warm, not coming back down, not a defrost or a door) or drifting
warm towards its limit. When they do, the console offers the business
"Get a repair quote" on that unit. Nothing is sent until the business
taps it, reads exactly what will be sent, picks who the technician should
contact, and confirms.

* **Apply.** A technician or repair company fills in a short form at
  /repair-partners (rate limited). The application is mailed to the
  operator (CYBERLOGIX_OPERATOR_EMAIL) with the ordinary mail sender.
* **Approve.** The operator approves it in the operator console (/book,
  Partners) and sets the areas it covers: postcode or ZIP prefixes, and
  city names. That gives the partner a dashboard key, mailed once.
* **Leads.** A confirmed request goes to one approved, switched-on
  partner who covers the location, picked fairly: whoever has gone
  longest without a lead goes first. At most one lead per unit every
  three days.
* **Fees.** Each delivered lead is recorded as owed at the partner's fee
  (CYBERLOGIX_REPAIR_LEAD_FEE_USD, $35 by default, kept with the partner
  when approved). A partner can mark a lead bad within seven days; the
  operator decides, and a refused lead is not charged. The operator
  invoices partners and records what they pay by hand. Nothing here moves
  money.

What a lead holds, and all a partner ever sees: the business name, the
contact person and the phone number and/or email the business chose, the
address of that location, and a plain summary of the problem (the kind of
unit, what we saw, since when). Never a reading, a sensor, a key or
anything else from inside the account. The partner key opens the
partner's own dashboard and nothing else.

We do not do repairs and do not vouch for any technician. The business
chooses whether to call them.

Storage, so a business deleting itself takes its details with it while
the money stays straight: the lead (with the contact details) is a row
that names the business and is erased with it; the fee for it is a
separate row that names nobody.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from accounts import require_role, tenant_of
from auth import require_platform_admin
from domain import mask_email
from ratelimit import Limit
from store import STORE, Sensor, Tenant, User, _parse, hash_secret, iso, utc_now

logger = logging.getLogger("cyberlogix.repairs")

router = APIRouter(prefix="/api/repair-partners", tags=["Repair partners"])
business_router = APIRouter(prefix="/api/repairs", tags=["Repair quotes"])

PARTNER_KIND = "repair_partner"
LEAD_KIND = "repair_lead"
FEE_KIND = "repair_fee"
PAYMENT_KIND = "repair_payment"


def _number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return min(high, max(low, value))


# What a partner pays us for each lead delivered to them. Kept with each
# partner when approved: changing this later changes the fee for partners
# approved after that, never for one already approved.
DEFAULT_LEAD_FEE_USD = _number("CYBERLOGIX_REPAIR_LEAD_FEE_USD", 35.0, 0.0, 1000.0)

# A partner can say a lead was bad (wrong number, not a real job) for this
# long after it was delivered.
BAD_LEAD_DAYS = 7

# One lead per unit in this long, so a unit that stays broken does not
# send the same business to a new technician every time somebody looks.
LEAD_GAP_DAYS = 3

KEY_PREFIX = "clx_rep_"

# The public form. A real repair company applies once; a script gets nowhere.
APPLY_LIMIT = Limit("repair-apply", per_caller=3, total=30,
                    window_seconds=3600, words="applications")

STATUS_WORDS = {
    "applied": "Waiting for a decision.",
    "approved": "Approved.",
    "declined": "Declined.",
}

# A fee's state, in the words the partner and the operator both read.
FEE_WORDS = {
    "owed": "Charged",
    "disputed": "You marked it bad. Waiting for our decision.",
    "kept": "Charged. We looked at it and kept the charge.",
    "refused": "Not charged. We agreed it was a bad lead.",
}
CHARGED = ("owed", "kept")

NOT_A_REPAIRER = (
    "We do not do repairs ourselves and we do not vouch for any technician. "
    "It is up to you whether to call them."
)


# ---- the money ---------------------------------------------------------------


def _cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fee_rows(partner_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in STORE._db.all(FEE_KIND) if r.get("partner_id") == partner_id),
                  key=lambda r: (str(r.get("delivered_at") or ""), str(r.get("fee_id"))))


def payments_for(partner_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in STORE._db.all(PAYMENT_KIND) if r.get("partner_id") == partner_id),
                  key=lambda r: (str(r.get("paid_on") or ""), str(r.get("payment_id"))))


def ledger(partner_id: str) -> Dict[str, Any]:
    """What a partner has been charged, what is in question, paid, still owed.

    Summed from the rows as stored, in Decimal, to the cent. A lead marked
    bad counts as "in question" until the operator decides: kept, it is
    charged; refused, it is not.
    """
    sums = {state: Decimal("0") for state in FEE_WORDS}
    counts = {state: 0 for state in FEE_WORDS}
    for row in fee_rows(partner_id):
        state = str(row.get("status") or "owed")
        if state not in sums:
            continue
        sums[state] += Decimal(str(row.get("amount_usd") or 0))
        counts[state] += 1
    charged = sums["owed"] + sums["kept"]
    paid = sum((Decimal(str(r.get("amount_usd") or 0)) for r in payments_for(partner_id)),
               Decimal("0"))
    return {
        "lead_count": sum(counts.values()),
        "charged_usd": _cents(charged),
        "in_question_usd": _cents(sums["disputed"]),
        "refused_usd": _cents(sums["refused"]),
        "paid_usd": _cents(paid),
        "owed_usd": _cents(charged - paid),
        "disputed_count": counts["disputed"],
    }


def bad_window_ends(delivered_at: Any) -> Optional[datetime]:
    when = _parse(delivered_at)
    return when + timedelta(days=BAD_LEAD_DAYS) if when is not None else None


def can_mark_bad(fee: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    ends = bad_window_ends(fee.get("delivered_at"))
    return (fee.get("status") == "owed" and ends is not None
            and (now or utc_now()) <= ends)


# ---- partners ----------------------------------------------------------------


def _partner(partner_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(PARTNER_KIND, (partner_id or "").strip())


def _partner_or_404(partner_id: str) -> Dict[str, Any]:
    row = _partner(partner_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such repair partner.")
    return row


def _save(row: Dict[str, Any]) -> None:
    STORE._db.put(PARTNER_KIND, str(row["partner_id"]), row)


def all_partners() -> List[Dict[str, Any]]:
    return sorted(STORE._db.all(PARTNER_KIND), key=lambda r: str(r.get("partner_id")))


def partner_by_key(key: Optional[str]) -> Optional[Dict[str, Any]]:
    key = (key or "").strip()
    if not key.startswith(KEY_PREFIX):
        return None
    digest = hash_secret(key)
    found = None
    for row in all_partners():
        if secrets.compare_digest(str(row.get("key_hash") or ""), digest):
            found = row
    return found


def _dashboard_url() -> str:
    from mail import PUBLIC_BASE_URL

    return (f"{PUBLIC_BASE_URL}/repair-partners/dashboard" if PUBLIC_BASE_URL
            else "/repair-partners/dashboard")


# ---- areas -------------------------------------------------------------------


def normalise_postcode(text: Optional[str]) -> str:
    """"sw1a 1aa" and "SW1A1AA" are one postcode; "02139-4307" starts 02139."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def normalise_city(text: Optional[str]) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def clean_areas(postcodes: List[str], cities: List[str]) -> Dict[str, List[str]]:
    """Areas as stored: each prefix and city normalised, empties and repeats out."""
    pcs = sorted({normalise_postcode(p) for p in postcodes} - {""})
    names = sorted({normalise_city(c) for c in cities} - {""})
    return {"postcodes": pcs[:200], "cities": names[:200]}


def covers(partner: Dict[str, Any], postcode: str, city: str) -> bool:
    """Whether a partner covers a location, by postcode prefix or by city."""
    code = normalise_postcode(postcode)
    if code and any(code.startswith(str(p)) for p in partner.get("postcodes") or [] if p):
        return True
    town = normalise_city(city)
    return bool(town) and town in (partner.get("cities") or [])


def _rotation_key(partner: Dict[str, Any]) -> tuple:
    # Never sent a lead sorts first (""), then whoever waited longest, then
    # whoever has had fewest, then the oldest partner: no ties left to luck.
    return (str(partner.get("last_lead_at") or ""), int(partner.get("leads_sent") or 0),
            str(partner.get("partner_id")))


def candidates(postcode: str, city: str) -> List[Dict[str, Any]]:
    """Approved, switched-on partners covering a location, next in line first."""
    found = [p for p in all_partners()
             if p.get("status") == "approved" and p.get("active") and covers(p, postcode, city)]
    return sorted(found, key=_rotation_key)


def take_next(postcode: str, city: str, now: datetime) -> Optional[Dict[str, Any]]:
    """Pick the partner next in line for this location, and mark them served.

    Picked and marked under one lock, so two requests at the same moment
    go to two partners rather than both to the same one.
    """
    with STORE._lock:
        line = candidates(postcode, city)
        if not line:
            return None
        chosen = line[0]
        chosen["last_lead_at"] = iso(now)
        chosen["leads_sent"] = int(chosen.get("leads_sent") or 0) + 1
        _save(chosen)
        return chosen


# ---- what the forecaster saw -------------------------------------------------


def _when(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    return _parse(value)


def _unit_kind(sensor: Sensor) -> str:
    above, _below = sensor.bounds()
    return "freezer" if above is not None and above <= 32.0 else "fridge"


def repair_signal(sensor: Sensor, forecast: Optional[Dict[str, Any]] = None,
                  unit: str = "F") -> Optional[Dict[str, Any]]:
    """What, if anything, says this unit needs a repair.

    Two things count, both already worked out by forecaster.py and
    patterns.py: a warm spell that is a failure (not a defrost, not a
    door, and it has not come back down), or a steady drift warmer that
    is heading for the upper limit. Anything else is None, and the console
    offers nothing.
    """
    from forecaster import forecast_sensor
    from store import format_temperature

    f = forecast if forecast is not None else forecast_sensor(sensor.sensor_id, 12.0)
    above, _below = sensor.bounds()
    if above is None:
        return None
    shape = f.get("pattern") or {}
    now_reading = f.get("current_temperature")
    limit = format_temperature(above, unit)
    if shape.get("pattern") == "failure" and shape.get("confident"):
        event = shape.get("event") or {}
        since = _when(event.get("started_at"))
        minutes = float(event.get("minutes") or 0.0)
        recovered = bool(event.get("recovered"))
        seen = (f"It went above its limit of {limit}"
                + (f" for about {round(minutes)} minutes" if minutes >= 1 else "")
                + ("" if recovered else " and has not come back down")
                + ". That is not how a defrost or an opened door looks, and is "
                  "how a failing compressor or fan often looks.")
        if now_reading is not None:
            seen += f" It read {format_temperature(now_reading, unit)} at the last check."
        return {"kind": "failing", "seen": seen, "since": iso(since) if since else None}
    trend = f.get("trend_f_per_hour")
    if (shape.get("pattern") not in ("defrost", "door")
            and f.get("forecast") == "breach_projected"
            and trend is not None and float(trend) > 0
            and f.get("risk_level") in ("critical", "high", "elevated")
            and float(f.get("confidence") or 0.0) >= 0.5):
        per_hour = float(trend) * (5.0 / 9.0 if unit == "C" else 1.0)
        window = float(f.get("trend_span_minutes") or 0.0)
        since = utc_now() - timedelta(minutes=window) if window else None
        hours = f.get("hours_until_breach")
        seen = (f"It has been getting warmer, about {per_hour:.1f}°{unit} an hour"
                + (f" over the last {max(1, round(window / 60))} hours" if window >= 60 else "")
                + f", and is heading for its limit of {limit}")
        if hours:
            seen += f" in about {max(1, round(float(hours)))} hours"
        seen += ". A unit that keeps creeping up like this usually needs a look."
        if now_reading is not None:
            seen += f" It read {format_temperature(now_reading, unit)} at the last check."
        return {"kind": "drifting", "seen": seen, "since": iso(since) if since else None}
    return None


def problem_summary(tenant: Tenant, sensor: Sensor, signal: Dict[str, Any]) -> str:
    """The plain words a lead carries about the problem, and nothing more."""
    since = _when(signal.get("since"))
    when = since.strftime("%d %b %Y, about %H:%M UTC") if since else "recently"
    return (
        f"Unit: a {_unit_kind(sensor)}, which the business calls "
        f"\"{sensor.location_name}\".\n"
        f"What we saw: {signal['seen']}\n"
        f"Since: {when}."
    )


# ---- leads -------------------------------------------------------------------


def leads_for_sensor(sensor_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in STORE._db.all(LEAD_KIND) if r.get("sensor_id") == sensor_id),
                  key=lambda r: str(r.get("delivered_at") or ""))


def next_allowed(sensor_id: str) -> Optional[datetime]:
    """When this unit may be sent to a repair company again, if not now."""
    rows = leads_for_sensor(sensor_id)
    if not rows:
        return None
    last = _parse(rows[-1].get("delivered_at"))
    if last is None:
        return None
    ready = last + timedelta(days=LEAD_GAP_DAYS)
    return ready if ready > utc_now() else None


def unit_state(tenant: Tenant, sensor: Sensor,
               forecast: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """What the console shows on a unit card: an offer, a recent request, or None."""
    rows = leads_for_sensor(sensor.sensor_id)
    last = rows[-1] if rows else None
    wait = next_allowed(sensor.sensor_id)
    if last is not None and wait is not None:
        return {"offer": False, "sent_at": last.get("delivered_at"),
                "sent_to": last.get("partner_name"), "again_after": iso(wait)}
    try:
        signal = repair_signal(sensor, forecast, tenant.temperature_unit)
    except Exception:  # noqa: BLE001 - a card must still render
        logger.exception("Repair check failed for %s.", sensor.sensor_id)
        return None
    if signal is None:
        return None
    return {"offer": True, "kind": signal["kind"]}


def lead_for_partner(lead: Optional[Dict[str, Any]], fee: Dict[str, Any],
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """One lead as its partner sees it: the four things, the fee, its state."""
    state = str(fee.get("status") or "owed")
    ends = bad_window_ends(fee.get("delivered_at"))
    out: Dict[str, Any] = {
        "lead_id": fee.get("fee_id"),
        "delivered_at": fee.get("delivered_at"),
        "fee_usd": fee.get("amount_usd"),
        "status": state,
        "status_words": FEE_WORDS.get(state, ""),
        "can_mark_bad": can_mark_bad(fee, now),
        "mark_bad_until": iso(ends) if ends else None,
        "bad_reason": fee.get("bad_reason") or "",
    }
    if lead is None:
        out.update({"business_name": "", "contact": {}, "address": {}, "problem": "",
                    "removed": True})
        return out
    out.update({
        "business_name": lead.get("business_name"),
        "contact": {k: lead.get(k) or "" for k in ("contact_name", "contact_phone",
                                                   "contact_email")},
        "address": dict(lead.get("address") or {}),
        "problem": lead.get("problem") or "",
        "removed": False,
    })
    return out


def _lead_mail(partner: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    from mail import send as send_mail

    address = lead["address"]
    contact = [lead["contact_name"]]
    if lead.get("contact_phone"):
        contact.append(f"phone {lead['contact_phone']}")
    if lead.get("contact_email"):
        contact.append(f"email {lead['contact_email']}")
    return send_mail(
        to_address=str(partner.get("contact_email")),
        subject=f"Repair lead: {lead['business_name']}, {address['city']}",
        body=(
            f"{partner.get('contact_name')},\n\n"
            f"{lead['business_name']} asked for a repair quote and chose to send "
            "you these details. Please get in touch with them directly.\n\n"
            f"Business: {lead['business_name']}\n"
            f"Contact: {', '.join(contact)}\n"
            f"Address: {address['street']}, {address['city']} {address['postcode']}\n\n"
            f"The problem, as our monitoring saw it:\n{lead['problem']}\n\n"
            f"This lead is ${float(lead['fee_usd']):,.2f}, added to what you owe us. "
            f"If it is not a real job (a wrong number, or nobody asked), mark it "
            f"bad on your dashboard within {BAD_LEAD_DAYS} days and we will look "
            f"at it: {_dashboard_url()}\n\n"
            "That is everything we send: no readings and no access to the "
            "business's account."),
        dedupe_key=f"repair-lead:{lead['lead_id']}",
        klass="transactional",
        # Somebody's phone number and email: blanked from our mail log once
        # it is sent, like a password reset.
        sensitive=True,
    )


# ---- routes: the business ----------------------------------------------------


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sensor_id: str = Field(..., min_length=1, max_length=120)
    contact_name: str = Field(..., min_length=2, max_length=120)
    contact_phone: str = Field("", max_length=40)
    contact_email: str = Field("", max_length=200)
    street: str = Field(..., min_length=3, max_length=200)
    city: str = Field(..., min_length=2, max_length=100)
    postcode: str = Field(..., min_length=2, max_length=20)
    # The business ticked "send these details to one repair company".
    consent: bool = False


def _own_sensor(tenant: Tenant, sensor_id: str) -> Sensor:
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That unit is not one of this business's.")
    return sensor


def _site_address(sensor: Sensor) -> str:
    site = STORE.get_site(sensor.site_id) if sensor.site_id else None
    return site.address if site is not None else ""


@business_router.get("/quote/{sensor_id}")
def quote_preview(sensor_id: str, user: User = Depends(require_role("operator"))):
    """The confirm screen: exactly what would be sent, before anything is.

    Sends nothing. The contact and address are suggestions for the form,
    which the business can change or clear.
    """
    tenant = tenant_of(user)
    sensor = _own_sensor(tenant, sensor_id)
    wait = next_allowed(sensor.sensor_id)
    signal = repair_signal(sensor, None, tenant.temperature_unit)
    return {
        "sensor_id": sensor.sensor_id,
        "unit_name": sensor.location_name,
        "business_name": tenant.company_name,
        "needs_repair": signal is not None,
        "problem": problem_summary(tenant, sensor, signal) if signal else "",
        "can_send": signal is not None and wait is None,
        "again_after": iso(wait) if wait else None,
        "suggested": {
            "contact_name": user.full_name,
            "contact_phone": tenant.contact_phone or "",
            "contact_email": user.email,
            "street": _site_address(sensor),
        },
        "what_is_sent": ["Your business name", "The contact you choose below",
                         "The address of this location", "The summary of the problem above"],
        "never_sent": "Nothing else: no readings, no sensors and no access to your account.",
        "not_a_repairer": NOT_A_REPAIRER,
    }


@business_router.post("/quote", status_code=status.HTTP_201_CREATED)
def request_quote(payload: QuoteRequest, user: User = Depends(require_role("operator"))):
    """Send one unit's problem to one approved repair partner near it.

    Only with the business's say-so (`consent`), only for a unit the
    forecaster says needs a repair, and only once per unit every three
    days.
    """
    tenant = tenant_of(user)
    if not payload.consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Tick the box to say we may send these details to one "
                    "repair company. Nothing has been sent."))
    phone, email = payload.contact_phone, payload.contact_email.lower()
    if not phone and not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Give a phone number or an email, so the repair company can reach you.")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That email does not look right.")
    sensor = _own_sensor(tenant, payload.sensor_id)
    wait = next_allowed(sensor.sensor_id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(f"A repair company was already sent this unit. You can ask "
                    f"again after {wait.strftime('%d %b %Y, %H:%M UTC')}."))
    signal = repair_signal(sensor, None, tenant.temperature_unit)
    if signal is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This unit looks fine to us right now, so there is nothing to send.")
    now = utc_now()
    partner = take_next(payload.postcode, payload.city, now)
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("No approved repair company covers that address yet. "
                    "Nothing was sent."))
    lead_id = STORE._next_id("RLD")
    fee = _cents(Decimal(str(partner.get("lead_fee_usd") or 0.0)))
    lead: Dict[str, Any] = {
        "lead_id": lead_id,
        "tenant_id": tenant.tenant_id,
        # Ours, to keep to one lead per unit. Never shown to the partner.
        "sensor_id": sensor.sensor_id,
        "partner_id": partner["partner_id"],
        "partner_name": partner.get("company_name"),
        "business_name": tenant.company_name,
        "contact_name": payload.contact_name,
        "contact_phone": phone,
        "contact_email": email,
        "address": {"street": payload.street, "city": payload.city,
                    "postcode": payload.postcode},
        "problem": problem_summary(tenant, sensor, signal),
        "kind": signal["kind"],
        "fee_usd": fee,
        "requested_by": user.user_id,
        "delivered_at": iso(now),
    }
    STORE._db.put(LEAD_KIND, lead_id, lead)
    STORE._db.put(FEE_KIND, lead_id, {
        "fee_id": lead_id,
        "partner_id": partner["partner_id"],
        "amount_usd": fee,
        "delivered_at": iso(now),
        "status": "owed",
        "bad_reason": "",
        "bad_marked_at": None,
        "decided_at": None,
        "decision_note": "",
    })
    sent = _lead_mail(partner, lead)
    STORE.record_audit(
        tenant_id=tenant.tenant_id, actor=f"{user.full_name} <{user.email}>",
        actor_role=user.role, action="repair.quote_requested",
        detail=(f"Asked for a repair quote on {sensor.location_name}. Sent to "
                f"{partner.get('company_name')}: the business name, the contact "
                "chosen, the address and a summary of the problem."))
    logger.info("Repair lead %s for %s sent to partner %s (contact %s).", lead_id,
                tenant.tenant_id, partner["partner_id"],
                mask_email(email) if email else "by phone")
    return {
        "lead_id": lead_id,
        "partner": {"company_name": partner.get("company_name"),
                    "contact_phone": partner.get("contact_phone") or "",
                    "website": partner.get("website") or ""},
        "mailed": bool(sent.get("sent") or sent.get("status") == "queued"),
        "again_after": iso(now + timedelta(days=LEAD_GAP_DAYS)),
        "message": (f"Sent to {partner.get('company_name')}. They should get in "
                    f"touch with {payload.contact_name} directly. {NOT_A_REPAIRER}"),
    }


# ---- routes: the public form -------------------------------------------------


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(..., min_length=2, max_length=160)
    contact_name: str = Field(..., min_length=2, max_length=120)
    contact_email: EmailStr
    contact_phone: str = Field(..., min_length=5, max_length=40)
    website: str = Field("", max_length=200)
    areas: str = Field(..., min_length=2, max_length=500)
    message: str = Field("", max_length=1000)


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
def apply(payload: Application, request: Request):
    """Apply to be a repair partner. Public; rate limited.

    The same answer whether or not this address applied before, so the
    form says nothing about who else has.
    """
    from digest import OPERATOR_EMAIL
    from mail import send as send_mail

    APPLY_LIMIT.check(request)
    email = str(payload.contact_email).strip().lower()
    answer = {"message": "Thank you. We read every application and will reply by email."}
    for earlier in all_partners():
        if str(earlier.get("contact_email") or "").lower() == email and \
                earlier.get("status") in ("applied", "approved"):
            return answer
    row: Dict[str, Any] = {
        "partner_id": STORE._next_id("RPT"),
        "status": "applied",
        "active": False,
        "company_name": payload.company_name,
        "contact_name": payload.contact_name,
        "contact_email": email,
        "contact_phone": payload.contact_phone,
        "website": payload.website,
        "areas_said": payload.areas,
        "message": payload.message,
        "applied_at": iso(utc_now()),
        "decided_at": None,
        "key_hash": None,
        "lead_fee_usd": None,
        "postcodes": [],
        "cities": [],
        "last_lead_at": None,
        "leads_sent": 0,
    }
    _save(row)
    logger.info("Repair partner application %s from %s.", row["partner_id"], mask_email(email))
    if OPERATOR_EMAIL:
        send_mail(
            to_address=OPERATOR_EMAIL,
            subject=f"Repair partner application: {payload.company_name}",
            body=(
                f"{payload.company_name} has applied to be a repair partner.\n\n"
                f"Contact: {payload.contact_name}, {email}, {payload.contact_phone}\n"
                + (f"Website: {payload.website}\n" if payload.website else "")
                + f"Areas they say they cover: {payload.areas}\n"
                + (f"\nWhat they said:\n{payload.message}\n" if payload.message else "")
                + "\nApprove it and set its areas, or decline it, in the operator "
                  "console: /book, the Partners tab."),
            dedupe_key=f"repair-application:{row['partner_id']}",
            klass="transactional",
        )
    else:
        logger.warning("No CYBERLOGIX_OPERATOR_EMAIL, so repair application %s was "
                       "not mailed. It is waiting in the operator console.",
                       row["partner_id"])
    return answer


# ---- routes: the operator ----------------------------------------------------


class Areas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    postcodes: List[str] = Field(default_factory=list, max_length=200)
    cities: List[str] = Field(default_factory=list, max_length=200)


class Approval(Areas):
    lead_fee_usd: Optional[float] = Field(None, ge=0.0, le=1000.0, allow_inf_nan=False)


class PartnerState(BaseModel):
    active: bool


class Payment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount_usd: float = Field(..., gt=0, le=1_000_000, allow_inf_nan=False)
    paid_on: Optional[date] = None
    reference: str = Field(..., min_length=1, max_length=120)
    note: str = Field("", max_length=300)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # True: we agree the lead was bad, and it is not charged.
    refuse: bool
    note: str = Field("", max_length=300)


def admin_view(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: row.get(k) for k in (
        "partner_id", "status", "active", "company_name", "contact_name",
        "contact_email", "contact_phone", "website", "areas_said", "message",
        "applied_at", "decided_at", "lead_fee_usd", "postcodes", "cities",
        "last_lead_at", "leads_sent")}
    out["status_words"] = STATUS_WORDS.get(str(row.get("status")), "")
    if row.get("status") == "approved":
        out.update(ledger(str(row["partner_id"])))
    return out


def disputes() -> List[Dict[str, Any]]:
    """Leads a partner marked bad, waiting for the operator."""
    names = {str(p["partner_id"]): str(p.get("company_name") or "") for p in all_partners()}
    out = []
    for fee in STORE._db.all(FEE_KIND):
        if fee.get("status") != "disputed":
            continue
        lead = STORE._db.get(LEAD_KIND, str(fee.get("fee_id")))
        out.append({
            "lead_id": fee.get("fee_id"),
            "partner_id": fee.get("partner_id"),
            "partner_name": names.get(str(fee.get("partner_id")), ""),
            "business_name": (lead or {}).get("business_name", ""),
            "delivered_at": fee.get("delivered_at"),
            "marked_bad_at": fee.get("bad_marked_at"),
            "reason": fee.get("bad_reason") or "",
            "amount_usd": fee.get("amount_usd"),
        })
    return sorted(out, key=lambda r: str(r.get("marked_bad_at") or ""))


@router.get("", dependencies=[Depends(require_platform_admin)])
def list_partners():
    """Every application and partner, what each owes us, and the disputes."""
    rows = [admin_view(r) for r in all_partners()]
    approved = [r for r in rows if r["status"] == "approved"]
    return {
        "applications": [r for r in rows if r["status"] == "applied"],
        "partners": approved,
        "declined": [r for r in rows if r["status"] == "declined"],
        "disputes": disputes(),
        "settings": {"lead_fee_usd": DEFAULT_LEAD_FEE_USD, "bad_lead_days": BAD_LEAD_DAYS,
                     "lead_gap_days": LEAD_GAP_DAYS},
        "owed_usd": round(sum(float(r.get("owed_usd") or 0) for r in approved), 2),
        "note": ("Repair partners pay us by hand. Send each one a statement, and "
                 "record what they pay once it has arrived; nothing here moves money."),
    }


def _issue_key(row: Dict[str, Any]) -> str:
    key = f"{KEY_PREFIX}{secrets.token_urlsafe(30)}"
    row["key_hash"] = hash_secret(key)
    return key


def _areas_words(row: Dict[str, Any]) -> str:
    parts = []
    if row.get("postcodes"):
        parts.append("postcodes starting " + ", ".join(str(p) for p in row["postcodes"]))
    if row.get("cities"):
        parts.append("cities " + ", ".join(str(c).title() for c in row["cities"]))
    return "; ".join(parts) or "no areas yet"


def _mail_key(row: Dict[str, Any], key: str, first: bool) -> bool:
    from mail import send as send_mail

    result = send_mail(
        to_address=str(row.get("contact_email")),
        subject=("You are a CyberLogix repair partner" if first
                 else "Your new CyberLogix repair partner key"),
        body=(
            f"{row.get('contact_name')},\n\n"
            + ("Your application has been approved.\n\n" if first else
               "Here is a new key for your dashboard. The old one no longer works.\n\n")
            + f"Areas you cover: {_areas_words(row)}.\n"
            f"Each lead we send you is ${float(row.get('lead_fee_usd') or 0):,.2f}. "
            f"If a lead is not a real job, mark it bad within {BAD_LEAD_DAYS} days "
            "and we will look at it. We send you a statement and you pay us by "
            "transfer; nothing is taken automatically.\n\n"
            f"Your dashboard: {_dashboard_url()}\n"
            f"Your dashboard key (keep it private): {key}\n\n"
            "A lead holds only what the business chose to send: its name, a "
            "contact, the address and a short summary of the problem. You never "
            "see readings or anything inside a business's account. We do not "
            "vouch for you to businesses, and they choose whether to call you."),
        dedupe_key=f"repair-key:{row['partner_id']}:{str(row['key_hash'])[:12]}",
        klass="transactional",
        sensitive=True,
    )
    return bool(result.get("sent") or result.get("status") == "queued")


@router.post("/{partner_id}/approve", dependencies=[Depends(require_platform_admin)])
def approve(partner_id: str, payload: Approval):
    """Approve an application with its areas and fee. The key is shown once."""
    row = _partner_or_404(partner_id)
    if row.get("status") == "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That repair partner is already approved.")
    areas = clean_areas(payload.postcodes, payload.cities)
    if not areas["postcodes"] and not areas["cities"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Give at least one postcode or ZIP prefix, or one city, they cover.")
    row.update({
        "status": "approved",
        "active": True,
        "decided_at": iso(utc_now()),
        "lead_fee_usd": _cents(Decimal(str(payload.lead_fee_usd
                                           if payload.lead_fee_usd is not None
                                           else DEFAULT_LEAD_FEE_USD))),
        **areas,
    })
    key = _issue_key(row)
    _save(row)
    mailed = _mail_key(row, key, first=True)
    logger.info("Repair partner %s approved.", row["partner_id"])
    return {**admin_view(row), "key": key, "mailed": mailed,
            "warning": ("The key is shown once. It has been mailed to the partner "
                        "as well." if mailed else
                        "The key is shown once and could not be mailed: give it "
                        "to the partner yourself.")}


@router.post("/{partner_id}/decline", dependencies=[Depends(require_platform_admin)])
def decline(partner_id: str):
    """Decline an application, with a short, polite note to them."""
    from mail import send as send_mail

    row = _partner_or_404(partner_id)
    if row.get("status") != "applied":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only a waiting application can be declined.")
    row.update({"status": "declined", "active": False, "decided_at": iso(utc_now())})
    _save(row)
    send_mail(
        to_address=str(row.get("contact_email")),
        subject="Your CyberLogix repair partner application",
        body=(f"{row.get('contact_name')},\n\nThank you for applying to be a "
              "CyberLogix repair partner. We are not able to take it further at "
              "the moment. You are welcome to apply again later."),
        dedupe_key=f"repair-declined:{row['partner_id']}",
        klass="transactional",
    )
    return admin_view(row)


def _approved_or_409(partner_id: str) -> Dict[str, Any]:
    row = _partner_or_404(partner_id)
    if row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only an approved repair partner can have that.")
    return row


@router.post("/{partner_id}/areas", dependencies=[Depends(require_platform_admin)])
def set_areas(partner_id: str, payload: Areas):
    """Change the postcodes and cities a partner covers."""
    row = _approved_or_409(partner_id)
    areas = clean_areas(payload.postcodes, payload.cities)
    if not areas["postcodes"] and not areas["cities"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Give at least one postcode or ZIP prefix, or one city. To stop "
                   "their leads, switch them off instead.")
    row.update(areas)
    _save(row)
    return admin_view(row)


@router.post("/{partner_id}/status", dependencies=[Depends(require_platform_admin)])
def set_status(partner_id: str, payload: PartnerState):
    """Switch a partner off (no leads, no dashboard) or on again."""
    row = _approved_or_409(partner_id)
    row["active"] = payload.active
    _save(row)
    logger.warning("Repair partner %s %s.", row["partner_id"],
                   "switched on" if payload.active else "switched off")
    return admin_view(row)


@router.post("/{partner_id}/rotate-key", dependencies=[Depends(require_platform_admin)])
def rotate_key(partner_id: str):
    """A new dashboard key; the old one stops working at once."""
    row = _approved_or_409(partner_id)
    key = _issue_key(row)
    _save(row)
    mailed = _mail_key(row, key, first=False)
    return {**admin_view(row), "key": key, "mailed": mailed}


@router.post("/{partner_id}/payments", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_platform_admin)])
def record_payment(partner_id: str, payload: Payment):
    """Write down money a partner paid us, by hand, outside this app."""
    row = _approved_or_409(partner_id)
    payment = {
        "payment_id": STORE._next_id("RPP"),
        "partner_id": row["partner_id"],
        "amount_usd": _cents(Decimal(str(payload.amount_usd))),
        "paid_on": (payload.paid_on or utc_now().date()).isoformat(),
        "reference": payload.reference,
        "note": payload.note,
        "recorded_at": iso(utc_now()),
    }
    STORE._db.put(PAYMENT_KIND, str(payment["payment_id"]), payment)
    logger.info("Payment of $%.2f from repair partner %s recorded.",
                payment["amount_usd"], row["partner_id"])
    return {**admin_view(row), "payment": payment}


def how_to_pay() -> str:
    """Bank transfer only: a partner's payment is recorded by hand."""
    from invoicing import ISSUER

    remit_to = (ISSUER.get("remit_to") or "").strip()
    if not remit_to:
        return "Reply to this message and we will send our bank details."
    return "Pay by bank transfer:\n" + "\n".join(
        f"  {line}" for line in remit_to.splitlines() if line.strip())


def statement_text(row: Dict[str, Any]) -> str:
    """A plain statement: every charged lead, payments, and the balance."""
    pid = str(row["partner_id"])
    money = ledger(pid)
    lines = []
    for fee in fee_rows(pid):
        state = str(fee.get("status") or "owed")
        lead = STORE._db.get(LEAD_KIND, str(fee.get("fee_id")))
        who = (lead or {}).get("business_name") or "a business that has since closed its account"
        lines.append(f"  {str(fee.get('delivered_at') or '')[:10]}  {fee.get('fee_id')}  "
                     f"{who}  ${float(fee.get('amount_usd') or 0):,.2f}  "
                     f"{FEE_WORDS.get(state, state)}")
    paid = [f"  {p.get('paid_on')}  {p.get('reference')}  ${float(p.get('amount_usd') or 0):,.2f}"
            for p in payments_for(pid)]
    return (
        "Leads:\n" + ("\n".join(lines) or "  No leads yet.") + "\n\n"
        "Payments received:\n" + ("\n".join(paid) or "  No payments yet.") + "\n\n"
        f"Charged: ${money['charged_usd']:,.2f}\n"
        f"Waiting for our decision: ${money['in_question_usd']:,.2f}\n"
        f"Paid: ${money['paid_usd']:,.2f}\n"
        f"Balance to pay: ${money['owed_usd']:,.2f}"
    )


@router.post("/{partner_id}/statement", dependencies=[Depends(require_platform_admin)])
def send_statement(partner_id: str):
    """Email a partner what they owe us. Billing is by hand: this asks for
    the money, it does not take it."""
    from mail import send as send_mail

    row = _approved_or_409(partner_id)
    money = ledger(str(row["partner_id"]))
    stamp = utc_now().strftime("%Y-%m-%dT%H%M")
    result = send_mail(
        to_address=str(row.get("contact_email")),
        subject=f"Your CyberLogix repair leads statement: ${money['owed_usd']:,.2f} to pay",
        body=(f"{row.get('contact_name')},\n\nHere is your statement for repair "
              f"leads.\n\n{statement_text(row)}\n\n{how_to_pay()}\n\n"
              "Reply to this message if anything on it is wrong."),
        dedupe_key=f"repair-statement:{row['partner_id']}:{stamp}",
        klass="transactional",
    )
    return {**admin_view(row), "mailed": bool(result.get("sent") or result.get("status") == "queued"),
            "statement": statement_text(row)}


@router.post("/leads/{lead_id}/decision", dependencies=[Depends(require_platform_admin)])
def decide(lead_id: str, payload: Decision):
    """Decide a lead a partner marked bad: refuse it (no charge) or keep it."""
    with STORE._lock:
        fee = STORE._db.get(FEE_KIND, (lead_id or "").strip())
        if fee is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="No such lead.")
        if fee.get("status") != "disputed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Only a lead marked bad is waiting for a decision.")
        fee["status"] = "refused" if payload.refuse else "kept"
        fee["decided_at"] = iso(utc_now())
        fee["decision_note"] = payload.note
        STORE._db.put(FEE_KIND, str(fee["fee_id"]), fee)
    logger.info("Repair lead %s %s.", lead_id, "refused" if payload.refuse else "kept")
    row = _partner(str(fee.get("partner_id")))
    return {"lead_id": fee["fee_id"], "status": fee["status"],
            "status_words": FEE_WORDS[str(fee["status"])],
            "partner": admin_view(row) if row else None}


# ---- routes: the partner's own dashboard ------------------------------------


class BadLead(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(..., min_length=3, max_length=300)


def require_repair_partner(
    x_cyberlogix_repair_partner: Optional[str] = Header(
        None, alias="X-CyberLogix-Repair-Partner"),
) -> Dict[str, Any]:
    """The partner making the request, by their dashboard key."""
    row = partner_by_key(x_cyberlogix_repair_partner)
    if row is None or row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="That partner key was not accepted.")
    if not row.get("active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This partner account is switched off. Please get in touch.")
    return row


@router.get("/me")
def dashboard(partner: Dict[str, Any] = Depends(require_repair_partner)):
    """The partner's leads, and what they owe. Nothing else."""
    pid = str(partner["partner_id"])
    now = utc_now()
    leads = [lead_for_partner(STORE._db.get(LEAD_KIND, str(fee.get("fee_id"))), fee, now)
             for fee in fee_rows(pid)]
    return {
        "partner": {"company_name": partner.get("company_name")},
        "areas": _areas_words(partner),
        "lead_fee_usd": partner.get("lead_fee_usd"),
        "bad_lead_days": BAD_LEAD_DAYS,
        "leads": list(reversed(leads)),
        **ledger(pid),
        "payments": [{"paid_on": p.get("paid_on"), "amount_usd": p.get("amount_usd"),
                      "reference": p.get("reference")} for p in payments_for(pid)],
        "privacy": ("Each lead holds only what the business chose to send: its "
                    "name, a contact, the address and a short summary of the "
                    "problem. You never see readings or anything inside an account."),
        "how_billing_works": ("We send you a statement and you pay us by transfer. "
                              "Nothing is taken automatically."),
    }


@router.post("/me/leads/{lead_id}/bad")
def mark_bad(lead_id: str, payload: BadLead,
             partner: Dict[str, Any] = Depends(require_repair_partner)):
    """Say a lead was bad, within seven days. The operator decides."""
    from digest import OPERATOR_EMAIL
    from mail import send as send_mail

    now = utc_now()
    with STORE._lock:
        fee = STORE._db.get(FEE_KIND, (lead_id or "").strip())
        if fee is None or fee.get("partner_id") != partner["partner_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="That lead is not one of yours.")
        if fee.get("status") != "owed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="That lead has already been marked or decided.")
        if not can_mark_bad(fee, now):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A lead can be marked bad for {BAD_LEAD_DAYS} days after we send it.")
        fee.update({"status": "disputed", "bad_reason": payload.reason,
                    "bad_marked_at": iso(now)})
        STORE._db.put(FEE_KIND, str(fee["fee_id"]), fee)
    logger.info("Repair partner %s marked lead %s bad.", partner["partner_id"], lead_id)
    if OPERATOR_EMAIL:
        send_mail(
            to_address=OPERATOR_EMAIL,
            subject=f"Repair lead marked bad: {fee['fee_id']} by {partner.get('company_name')}",
            body=(f"{partner.get('company_name')} marked lead {fee['fee_id']} as bad.\n\n"
                  f"Their reason: {payload.reason}\n\n"
                  "Refuse it (no charge) or keep the charge in the operator "
                  "console: /book, the Partners tab."),
            dedupe_key=f"repair-dispute:{fee['fee_id']}",
            klass="transactional",
        )
    return lead_for_partner(STORE._db.get(LEAD_KIND, str(fee["fee_id"])), fee, now)
