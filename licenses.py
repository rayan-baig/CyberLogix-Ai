"""Corporate license management.

Owns tenant onboarding, API keys, plan entitlements and seat enforcement.
Every other router in the suite authenticates through the dependencies
exported here, so a suspended or expired license stops the whole platform
for that customer at the door.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from models import Finite

from accounts import require_role
from auth import (
    optional_operator,
    require_entitlement,
    require_role_or_machine,
    require_tenant,
    require_tenant_any_state,
    write_audit,
)

# Re-exported: several routers import these from here rather than reaching
# past this module into auth, so the dependency reads in one direction.
__all__ = ["router", "require_entitlement", "require_tenant"]
from store import (
    PLAN_TIERS,
    STORE,
    SeatClaimRefused,
    Tenant,
    User,
    resolve_vertical,
)

router = APIRouter(prefix="/api/licenses", tags=["Corporate License Management"])


class TenantCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=200)
    contact_name: str = Field(..., min_length=1, max_length=120)
    contact_phone: str = Field(..., min_length=5, max_length=40)
    contact_email: EmailStr
    plan: str = Field("trial", description="One of: trial, starter, growth, enterprise")


class UnitPreference(BaseModel):
    temperature_unit: str = Field(
        ..., description="F or C. Readings are stored in F and converted for display."
    )


class PlanChange(BaseModel):
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
    plan: str = Field(..., description="One of: trial, starter, growth, enterprise")
    # A paid plan is a twelve-month commitment, and one tap used to make
    # it. The console shows the terms (GET /me/plan/terms) and sends this
    # only from the button that says "Yes, move to ...".
    confirm: bool = Field(
        False,
        description=("Must be true for a self-service change: the owner has "
                     "seen the terms from GET /api/licenses/me/plan/terms."),
    )


class ThresholdOverride(BaseModel):
    """Null clears an override and restores the industry default."""

    danger_above: Optional[Finite] = Field(
        None, description="Upper bound in °F. Null restores the sector default."
    )
    danger_below: Optional[Finite] = Field(
        None, description="Lower bound in °F. Null restores the sector default."
    )


class AlertRule(BaseModel):
    """How long a unit must stay out of range before anybody is alerted."""

    alert_after_minutes: Optional[Finite] = Field(
        None,
        description=(
            "Minutes the unit must stay over (or under) its limit before "
            "anybody is texted. 0 texts on the first reading out of range. "
            "Null restores the default for its kind: 20 for a food fridge, "
            "30 for a food freezer, 10 for a vaccine or lab fridge."
        ),
    )
    alert_after_readings: Optional[int] = Field(
        None,
        description=(
            "Optional: alert sooner once this many readings in a row are out "
            "of range, for a sensor that reports rarely. Null: time only."
        ),
    )


class SensorRegister(BaseModel):
    sensor_id: str = Field(..., min_length=1, max_length=80)
    industry_vertical: str = Field(..., min_length=1)
    location_name: str = Field(..., min_length=1, max_length=200)
    external_device_sn: Optional[str] = Field(
        None,
        max_length=120,
        description=(
            "Serial or MAC of a third-party sensor that will report via the "
            "BYOD webhook bridge."
        ),
    )
    unit_kind: Optional[str] = Field(
        None,
        max_length=20,
        description=(
            "\"fridge\" or \"freezer\" for the sectors that ask "
            "(GET /api/industries lists each sector's unit_kinds). It sets "
            "the unit's alarm limit, saved as its own. Left out, the "
            "sector's own limit applies (a fridge's, where the sector asks)."
        ),
    )


def apply_unit_kind(sensor, vertical: str, kind: Optional[str]) -> None:
    """Save the limit "fridge or freezer?" gives, as the unit's own.

    Saved on the sensor rather than left to the sector default, so the
    line a unit alarms at is the one its owner chose when adding it, and
    a later change to a default can never move it.
    """
    from domain import unit_kind_limits, unit_kinds

    # Only when asked. Left out (an API caller that has never heard of the
    # question), the sector's own limit applies, which for the sectors that
    # ask is the first choice's anyway.
    if not kind or not unit_kinds(vertical):
        return
    limits = unit_kind_limits(vertical, kind)
    if limits is None:
        return
    STORE.set_sensor_overrides(sensor, limits[0], limits[1])
    # Kept, so the unit gets its kind's wait before alerting (a freezer
    # waits longer than a fridge: domain.default_alert_after_minutes).
    STORE.set_alert_rule(sensor, sensor.alert_after_minutes,
                         sensor.alert_after_readings,
                         unit_kind=(kind or "").strip().lower())


def check_unit_kind(vertical: str, kind: Optional[str]) -> None:
    """Refuse an answer to "fridge or freezer?" the sector does not offer."""
    from domain import unit_kind_limits, unit_kinds

    if not kind:
        return
    choices = unit_kinds(vertical)
    if choices and unit_kind_limits(vertical, kind) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("unit_kind must be one of "
                    f"{[c['kind'] for c in choices]}."),
        )


def _validate_plan(plan: str) -> str:
    key = (plan or "").strip().lower()
    if key not in PLAN_TIERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown plan. Allowed plans: {list(PLAN_TIERS)}",
        )
    return key


@router.get("/plans")
def list_plans():
    """Public price-list style catalogue of the commercial tiers.

    Starter carries its flat price; the other plans are priced by the
    rate card, so theirs is null.
    """
    from pricing import STARTER_MONTHLY_USD, STARTER_PLAN, add_ons_words, plan_features

    return {
        "plans": [
            {"plan": key, **tier,
             "monthly_usd": STARTER_MONTHLY_USD if key == STARTER_PLAN else None,
             # A few plain lines, distinct per plan (pricing.plan_features).
             "features": plan_features(key)}
            for key, tier in PLAN_TIERS.items()
        ],
        "add_ons": add_ons_words(),
    }


# Provisioning a *paid* account is an internal act, and this endpoint used
# to be an anonymous one. Unauthenticated, unthrottled, and it took the
# plan as a parameter — so anyone who read the API docs could mint
# themselves an unlimited Enterprise licence, for free, in a loop, and
# there was nothing in the system that would have noticed.
#
# A trial still needs no credential: that is what the public sign-up door
# at POST /api/signup creates, and it is rate limited there. Anything paid
# now needs this key.
#
# Unset means no paid plan can be provisioned at all. That default is
# deliberate: a deployment nobody has configured should refuse to hand out
# Enterprise accounts rather than hand them to everybody.
PROVISIONING_KEY = os.environ.get("CYBERLOGIX_PROVISIONING_KEY", "").strip()

# Days of monitoring a lapsed licence gets on moving up a plan, to pay in.
UPGRADE_GRACE_DAYS = 14


def _require_provisioning(supplied: Optional[str], plan: str) -> None:
    """Refuse to provision any account here without the provisioning key.

    Trials included. A trial needed no key on this route, and unlike
    POST /api/signup it had no rate limit, so anyone could make trial
    accounts by the hundred; each one could then be bootstrapped and spend
    the operator's SMS, voice and AI budget. The public way to a trial is
    /api/signup, which is limited.
    """
    if not PROVISIONING_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Provisioning a {PLAN_TIERS[plan]['name']} account requires "
                "CYBERLOGIX_PROVISIONING_KEY to be configured. Trials are "
                "created at POST /api/signup and need no key."
            ),
        )
    if not secrets.compare_digest((supplied or "").strip(), PROVISIONING_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A valid X-CyberLogix-Provisioning header is required to "
                "create a paid account."
            ),
        )


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
def onboard_tenant(
    payload: TenantCreate,
    provisioning_key: Optional[str] = Header(
        None,
        alias="X-CyberLogix-Provisioning",
        description="Required to provision anything above a trial.",
    ),
):
    """Onboard a customer and issue its API key.

    The key is returned exactly once, at creation, and is never echoed by
    any later endpoint.

    A paid plan needs the provisioning key. A trial does not, but the
    public route to one is POST /api/signup, which is rate limited and
    also creates the owner — this endpoint leaves a tenant nobody can sign
    into until somebody bootstraps it.
    """
    plan = _validate_plan(payload.plan)
    _require_provisioning(provisioning_key, plan)
    tenant = STORE.create_tenant(
        company_name=payload.company_name,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_email=str(payload.contact_email),
        plan=plan,
    )
    return {
        "message": "Tenant onboarded. Store this API key securely — it is shown once.",
        "api_key": tenant.api_key,
        "tenant": tenant.public(sensor_count=0),
    }


@router.get("/me")
def current_license(tenant: Tenant = Depends(require_tenant)):
    """Entitlements and seat usage for the calling tenant.

    `partner_name` names the reseller that manages the account, if one
    does: that firm's portal shows this business's units, alerts and
    batteries (never its roster or its bills), and the business should be
    able to see who that is, not only an ID.
    """
    body = tenant.public(sensor_count=STORE.seat_count(tenant.tenant_id))
    partner = STORE.get_partner(tenant.partner_id) if tenant.partner_id else None
    body["partner_name"] = partner.company_name if partner else None
    return body


# Which tiers are above which. Self-service moves up this list and never
# down it, and never back onto the trial at all. Starter sits between the
# trial and Growth: a trial may choose it, and Starter moves up to Growth.
PLAN_RANK = {"trial": 0, "starter": 1, "growth": 2, "enterprise": 3}


def next_tier_above(plan: str) -> Optional[str]:
    """The cheapest plan with more room than this one.

    More room means more sensors: a five-sensor trial is not offered the
    three-unit Starter as the way past its limit.
    """
    here = PLAN_RANK.get(plan, 0)
    room = PLAN_TIERS.get(plan, {}).get("max_sensors", 0)
    above = sorted(
        (rank, key) for key, rank in PLAN_RANK.items()
        if rank > here and PLAN_TIERS[key]["max_sensors"] > room
    )
    return above[0][1] if above else None


def starter_refusal(tenant: Tenant, what: str) -> str:
    """Why Starter cannot take this, and the way on, with no charge made.

    The fourth unit, a second location or a trade Starter is not for. All
    three are the same moment: the business has outgrown the flat plan.
    Nothing is charged by being refused, and nothing is charged by the
    move until the next invoice, at the rate card.
    """
    from pricing import PRICE_BOOK, STARTER_MONTHLY_USD, UNITS_PER_LOCATION

    growth = PLAN_TIERS["growth"]
    rate = PRICE_BOOK["restaurant"]
    return (
        f"{what} Starter covers one location and up to "
        f"{PLAN_TIERS['starter']['max_sensors']} fridges or freezers for "
        f"${STARTER_MONTHLY_USD:,.0f} a month. Move to {growth['name']} "
        f"(up to {growth['max_sensors']:,} units, phone calls included; "
        f"${rate['monthly_usd']:,.0f} a month per {rate['unit']} for a "
        f"restaurant, for up to {UNITS_PER_LOCATION} units there) from What "
        "you pay in the console, or POST "
        '/api/licenses/me/plan with {"plan": "growth"}, then try again. '
        "Nothing has been charged, and nothing is until your next invoice."
    )


def seat_limit_message(tenant: Tenant, cap: int, vertical: str) -> str:
    """Refuse the seat, and answer the question the refusal creates.

    Somebody hitting this is standing in a walk-in with a sensor in their
    hand. It is the highest-intent moment the product ever gets, and the
    message was "Upgrade to add more" — no tier named, no price, no route.
    A refusal that does not say what to do next is a lost sale dressed up
    as an error.

    Every figure here comes from the price book and the plan tiers, so it
    cannot promise a number the invoice will not honour.
    """
    from pricing import PRICE_BOOK, public_price

    if tenant.plan == "starter":
        return starter_refusal(
            tenant, f"This would be unit {cap + 1}, and Starter stops at {cap}.")
    here = tenant.entitlements()["name"]
    nxt = next_tier_above(tenant.plan)
    if nxt is None:
        return (
            f"Seat limit reached: the {here} plan allows {cap} sensors, and "
            "it is the largest one. Get in touch and we will size a "
            "contract for the estate you actually have."
        )

    tier = PLAN_TIERS[nxt]
    # Only a published rate is quoted; a trade priced by quote says so.
    rate = public_price(vertical) if vertical in PRICE_BOOK else None
    price = (
        f" A {PRICE_BOOK[vertical]['unit']} is ${rate:,.0f} a month at the "
        "rate card."
        if rate
        else (" Pricing for this trade is by quote: get in touch."
              if vertical in PRICE_BOOK else "")
    )
    return (
        f"Seat limit reached: the {here} plan allows {cap} sensors and all "
        f"{cap} are in use. {tier['name']} allows "
        f"{tier['max_sensors']:,}.{price} "
        f"POST /api/licenses/me/plan with {{\"plan\": \"{nxt}\"}} to move "
        "up, then register this sensor again — nothing is lost."
    )


def _refuse_self_service_downgrade(tenant: Tenant, plan: str) -> None:
    """Stop a customer re-selecting a trial, or quietly dropping a tier.

    `change_plan` sets `expires_at` to now plus the tier's term. So a
    customer whose trial had run out could POST `{"plan": "trial"}` and
    get a fresh fourteen days — and again, and again. Measured: a licence
    forced to expire yesterday came back with a full new term and a state
    of "active". The entire paid model was one API call wide, and making
    this route reachable while lapsed (which it has to be, so somebody can
    upgrade) is what put the last plank in.

    Downgrading is refused too. Not because a customer may never move
    down, but because doing it silently mid-term resets the licence clock
    and changes what was agreed; it is a conversation, and the operator
    can still do it with the provisioning key.
    """
    current = PLAN_RANK.get(tenant.plan, 0)
    target = PLAN_RANK.get(plan, 0)

    if plan == "trial":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A trial cannot be started again from here. Trials run once; "
                "move onto a paid plan, or get in touch if you need longer."
            ),
        )
    if plan == tenant.plan:
        # Choosing the plan you are on used to start a fresh term: a lapsed
        # licence renewed itself, for nothing, with one call.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You are already on {PLAN_TIERS[plan]['name']}. Your licence runs "
                "on from paid invoices, not from choosing the plan again."
            ),
        )
    if target < current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Moving from {PLAN_TIERS[tenant.plan]['name']} down to "
                f"{PLAN_TIERS[plan]['name']} is not a self-service change, "
                "because it resets the licence term and alters what was "
                "agreed. Get in touch and we will do it."
            ),
        )


def refuse_what_starter_cannot_hold(tenant: Tenant) -> None:
    """Moving onto Starter: refuse an estate bigger than it covers.

    Said in plain words, the same way the fourth unit is, rather than
    letting the plan change and the bill quietly charge the card.
    """
    from pricing import STARTER_VERTICALS

    sensors = STORE.sensors_for(tenant.tenant_id)
    cap = PLAN_TIERS["starter"]["max_sensors"]
    places = PLAN_TIERS["starter"]["max_locations"]
    problems = []
    if len(sensors) > cap:
        problems.append(f"{len(sensors)} units are registered.")
    if places is not None and len(STORE.sites_for(tenant.tenant_id)) > places:
        problems.append(f"{len(STORE.sites_for(tenant.tenant_id))} locations are set up.")
    other = sorted({s.industry_vertical for s in sensors} - set(STARTER_VERTICALS))
    if other:
        problems.append("Some units are on limits Starter is not for "
                        f"({', '.join(other)}).")
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=starter_refusal(tenant, " ".join(problems)).replace(
                "then try again. ", "instead. "),
        )


def refuse_off_starter_vertical(tenant: Tenant, vertical: str) -> None:
    """A unit on limits Starter is not for asks for Growth, like a 4th."""
    from pricing import STARTER_VERTICALS

    if tenant.plan == "starter" and vertical not in STARTER_VERTICALS:
        from store import INDUSTRY_PROFILES

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=starter_refusal(
                tenant,
                f"A unit on the {INDUSTRY_PROFILES[vertical]['name']} limits "
                "is not a cafe or shop fridge."),
        )


def plan_change_terms(tenant: Tenant, plan: str) -> dict:
    """What moving to `plan` means, in the words the confirmation shows.

    The plan, what it would cost for what is registered now, how long the
    term is, what cancelling means, and what happens to the free trial.
    Nothing here changes anything.
    """
    from dataclasses import replace
    from math import ceil

    from pricing import (CANCELLATION_RULE, STARTER_MONTHLY_USD,
                         build_subscription, public_price)
    from store import utc_now

    tier = PLAN_TIERS[plan]
    units = STORE.seat_count(tenant.tenant_id)
    monthly: Optional[float]
    if plan == "starter":
        monthly = float(STARTER_MONTHLY_USD)
        price_words = f"${STARTER_MONTHLY_USD:,.0f} a month, flat."
    else:
        try:
            monthly = float(build_subscription(replace(tenant, plan=plan))["monthly_total_usd"])
        except Exception:  # noqa: BLE001 - the words below still stand
            monthly = None
        rate = public_price(tenant.industry_vertical or "restaurant")
        if monthly:
            price_words = (f"About ${monthly:,.2f} a month for the {units} "
                           f"unit{'s' if units != 1 else ''} you have now, from "
                           "the rate card. It changes as you add or remove units.")
        elif rate is not None:
            price_words = (f"${rate:,.0f} a month per location from the rate card, "
                           "for the units you add.")
        else:
            price_words = "Priced from the rate card for the units you add."
    months = round(tier["term_days"] / 30.4)
    # Both halves said together, so "a 12-month plan" beside "cancel any
    # time" does not read as a contradiction: the plan's term renews each
    # year and is billed month by month, and cancelling stops the billing
    # whenever it happens (CANCELLATION_RULE). The terms are unchanged.
    term_words = (f"A {months}-month plan that renews each year, billed each "
                  "month (or yearly, if you choose). It is not a lock-in: you "
                  "can cancel any time, as below. Billing starts from your "
                  "next invoice, for the days after the move only; nothing is "
                  "charged on this screen.")
    trial_days_left: Optional[int] = None
    trial_words = ""
    if tenant.plan == "trial":
        trial_days_left = max(0, ceil((tenant.expires_at - utc_now()).total_seconds() / 86400))
        trial_words = (f"Your free trial has {trial_days_left} day"
                       f"{'s' if trial_days_left != 1 else ''} left. Moving now "
                       "ends the trial; staying on it costs nothing until then.")
    return {
        "plan": plan,
        "name": tier["name"],
        "monthly_usd": monthly,
        "price_words": price_words,
        "term_days": tier["term_days"],
        "term_words": term_words,
        "cancel_words": CANCELLATION_RULE,
        "trial_days_left": trial_days_left,
        "trial_words": trial_words,
        "calls": bool(tier["voice_escalation"]),
        "max_units": tier["max_sensors"],
    }


@router.get("/me/plan/terms")
def plan_terms(
    plan: str,
    tenant: Tenant = Depends(require_tenant_any_state),
    owner: User = Depends(require_role("owner")),
):
    """The confirmation screen for a change of plan. Changes nothing."""
    return plan_change_terms(tenant, _validate_plan(plan))


@router.post("/me/plan")
def change_plan(
    payload: PlanChange,
    tenant: Tenant = Depends(require_tenant_any_state),
    owner: User = Depends(require_role("owner")),
    provisioning_key: Optional[str] = Header(
        None,
        alias="X-CyberLogix-Provisioning",
        description=(
            "Operator override: lets the platform move a tenant down a tier "
            "or back onto a trial, which self-service cannot."
        ),
    ),
):
    """Move a tenant between tiers, refusing a downgrade that strands seats.

    Reachable even when the licence has lapsed. Refusing it would be a
    deadlock — the customer cannot pay because they have not paid — and it
    was a real one: an expired trial could not upgrade itself, so the only
    route from a finished trial to a paying customer ran through somebody
    answering an email.
    """
    plan = _validate_plan(payload.plan)
    operator_led = bool(
        PROVISIONING_KEY
        and provisioning_key
        and secrets.compare_digest(provisioning_key.strip(), PROVISIONING_KEY)
    )

    if not operator_led:
        _refuse_self_service_downgrade(tenant, plan)

    seats_used = STORE.seat_count(tenant.tenant_id)
    new_cap = PLAN_TIERS[plan]["max_sensors"]

    if plan == "starter":
        refuse_what_starter_cannot_hold(tenant)

    if seats_used > new_cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot downgrade to {PLAN_TIERS[plan]['name']}: {seats_used} "
                f"sensors are registered but the plan allows {new_cap}. "
                "Decommission sensors first."
            ),
        )

    # Nothing changes until the owner has seen the terms and said yes.
    # Checked after every other refusal, so a change that could never
    # happen says why rather than asking to be confirmed first.
    if not operator_led and not payload.confirm and plan != tenant.plan:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "message": (f"Moving to {PLAN_TIERS[plan]['name']} needs a yes: "
                            "send confirm: true once the terms have been shown."),
                "terms": plan_change_terms(tenant, plan),
            },
        )

    # A self-service change moves the plan and nothing else. It used to start
    # the new plan's full term as well, so a trial could become a year of
    # Enterprise with no money involved. The term now runs on from paid
    # invoices (store.settle_invoice); only the operator grants one directly.
    STORE.change_plan(tenant, plan, extend_term=operator_led)
    if not operator_led:
        # Except this: somebody whose licence has run out and who moves up a
        # plan gets monitoring back at once, for long enough to settle the
        # first invoice. Without it they could not pay because they had not
        # paid. It only happens moving up, and there are only so many steps
        # up, so it cannot be repeated into free time.
        STORE.grant_grace(tenant, UPGRADE_GRACE_DAYS)
    return tenant.public(sensor_count=seats_used)


@router.post("/me/temperature-unit")
def set_temperature_unit(
    payload: UnitPreference,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Choose Fahrenheit or Celsius for everything this tenant is shown.

    Readings are stored in Fahrenheit regardless, so switching units never
    rewrites history and never loses precision on data already collected.
    """
    from store import TEMPERATURE_UNITS

    unit = (payload.temperature_unit or "").strip().upper()
    if unit not in TEMPERATURE_UNITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"temperature_unit must be one of {list(TEMPERATURE_UNITS)}.",
        )

    STORE.set_temperature_unit(tenant, unit)
    return {
        "temperature_unit": unit,
        "message": (
            "Readings will be shown in "
            + ("Celsius." if unit == "C" else "Fahrenheit.")
        ),
    }


@router.post("/me/sensors", status_code=status.HTTP_201_CREATED)
def register_sensor(
    payload: SensorRegister,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Claim a license seat for a physical sensor node."""
    vertical = resolve_vertical(payload.industry_vertical)
    if vertical is None:
        from store import INDUSTRY_PROFILES

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid industry_vertical provided. Allowed keys: "
                f"{list(INDUSTRY_PROFILES)}"
            ),
        )

    refuse_off_starter_vertical(tenant, vertical)
    check_unit_kind(vertical, payload.unit_kind)
    sensor_id = payload.sensor_id.strip()
    serial = (payload.external_device_sn or "").strip() or None
    cap = tenant.entitlements()["max_sensors"]

    from pricing import build_subscription as _priced

    # Measured, not assumed. What one more sensor costs used to be read off
    # the rate card, which is right for a rack and wrong for a restaurant:
    # a thermometer added to a shop already billed costs nothing, and was
    # announced as a whole location's price.
    monthly_before = _priced(tenant)["monthly_total_usd"]

    # One call, one lock. Asking "is there a seat?" and then taking it as
    # two separate store calls let twenty concurrent registrations be
    # granted nine seats on a five-seat licence.
    try:
        sensor = STORE.claim_sensor_seat(
            sensor_id=sensor_id,
            tenant_id=tenant.tenant_id,
            industry_vertical=vertical,
            location_name=payload.location_name,
            max_sensors=cap,
            external_device_sn=serial,
        )
    except SeatClaimRefused as refused:
        detail = refused.detail
        if refused.reason == "no_seats":
            detail = seat_limit_message(tenant, cap, payload.industry_vertical)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=detail
        ) from None

    apply_unit_kind(sensor, vertical, payload.unit_kind)

    import usage

    usage.count("sensors_added")

    from pricing import PRICE_BOOK, build_subscription

    entry = PRICE_BOOK[vertical]
    monthly_after = build_subscription(tenant)["monthly_total_usd"]
    return {
        "sensor": sensor.public(),
        # Shown exactly once, like the tenant key. This is what belongs on
        # the hardware: it reports readings for this one asset and can do
        # nothing else. The tenant key can register assets, retune alarm
        # limits and suspend the licence, and has no business inside a box
        # bolted to the wall of a walk-in freezer.
        "ingest_key": sensor.ingest_key,
        "ingest_key_notice": (
            "Store this on the device. It is shown once and never echoed "
            "again. Send it as the X-CyberLogix-Sensor-Key header, or as "
            "api_key_token for third-party hardware."
        ),
        "seats_used": STORE.seat_count(tenant.tenant_id),
        "seats_total": cap,
        "billing": {
            "unit": entry["unit"],
            "adds_monthly_usd": round(monthly_after - monthly_before, 2),
            "new_monthly_total_usd": monthly_after,
        },
    }


@router.get("/me/sensors")
def list_sensors(tenant: Tenant = Depends(require_tenant)):
    """Full sensor fleet for the calling tenant."""
    sensors = STORE.sensors_for(tenant.tenant_id)
    return {
        "count": len(sensors),
        "online": sum(1 for s in sensors if not s.offline()),
        "low_battery": sum(1 for s in sensors if s.battery_low),
        "temperature_unit": tenant.temperature_unit,
        "sensors": [s.public(tenant.temperature_unit) for s in sensors],
    }


@router.post("/me/sensors/{sensor_id}/thresholds")
def set_thresholds(
    sensor_id: str,
    payload: ThresholdOverride,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Tune one sensor's limits away from its industry defaults.

    A particular freezer may be held colder than its sector's rule of thumb,
    and a hangar in Phoenix is not a hangar in Anchorage.
    """
    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    above, below = payload.danger_above, payload.danger_below
    if above is not None and below is not None and below >= above:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"danger_below ({below}°F) must be under danger_above "
                f"({above}°F), or the sensor can never read in band."
            ),
        )

    STORE.set_sensor_overrides(sensor, above, below)
    effective_above, effective_below = sensor.bounds()
    return {
        "sensor": sensor.public(),
        "effective_above": effective_above,
        "effective_below": effective_below,
        "message": (
            "Overrides cleared; the industry defaults apply."
            if above is None and below is None
            else "Overrides applied."
        ),
    }


@router.post("/me/sensors/{sensor_id}/alert-rule")
def set_alert_rule(
    sensor_id: str,
    payload: AlertRule,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Set how long this unit must stay out of range before we alert.

    A walk-in that is opened all through a lunch delivery may want longer;
    a vaccine fridge, less. A reading far past the limit
    (domain.ALERT_AT_ONCE_DEGREES_F) and a sensor going quiet alert at once
    whatever this says.
    """
    from domain import (ALERT_AFTER_MINUTES_MAX, ALERT_AFTER_READINGS_MAX,
                        alert_after_minutes, default_alert_after_minutes)

    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )
    minutes, readings = payload.alert_after_minutes, payload.alert_after_readings
    if minutes is not None and not 0 <= minutes <= ALERT_AFTER_MINUTES_MAX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"alert_after_minutes must be between 0 and "
                    f"{ALERT_AFTER_MINUTES_MAX:g}."),
        )
    if readings is not None and not 1 <= readings <= ALERT_AFTER_READINGS_MAX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"alert_after_readings must be between 1 and {ALERT_AFTER_READINGS_MAX}.",
        )
    STORE.set_alert_rule(sensor, minutes, readings)
    wait = alert_after_minutes(sensor)
    return {
        "sensor": sensor.public(tenant.temperature_unit),
        "alert_after_minutes": wait,
        "alert_after_minutes_default": default_alert_after_minutes(sensor),
        "alert_after_readings": readings,
        "message": (
            "Alerts on the first reading out of range." if wait <= 0 else
            f"Alerts once it has stayed out of range for {wait:g} minutes"
            + (f" or {readings} readings in a row" if readings else "")
            + ", or at once if it is far past its limit."
        ),
    }


@router.post("/me/sensors/{sensor_id}/rotate-key")
def rotate_sensor_key(
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Issue a new ingest key for one sensor and retire the old one.

    Needed the day a device is replaced, sold on, or found with its case
    open. Rotating is the whole reason the key is per-sensor: a compromised
    tenant key means re-keying an entire estate, while a compromised sensor
    key means re-keying one freezer.
    """
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    issued = STORE.rotate_ingest_key(sensor)
    return {
        "sensor_id": sensor.sensor_id,
        "ingest_key": issued,
        "message": (
            "New key issued. The previous key stopped working immediately; "
            "update the device before its next reading is due."
        ),
    }


@router.post("/me/rotate-key")
def rotate_business_key(owner: User = Depends(require_role("owner"))):
    """Replace this business's master key. The old one stops working now.

    For the day the key turns up somewhere it should not be: pasted into a
    chat, left in a script, sent by a device in a payload. Until this existed
    a leaked master key could not be taken back at all.

    An owner's signed-in session only, never the key itself: whoever had
    leaked it could otherwise rotate it and lock the business out of its own
    estate. Sensors on their own per-sensor keys are not affected.
    """
    tenant = STORE.get_tenant(owner.tenant_id)
    new_key = STORE.rotate_api_key(tenant)
    STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=f"{owner.full_name} <{owner.email}>",
        actor_role=owner.role,
        action="license.key_rotated",
        detail="Replaced the business's master key. The old key no longer works.",
    )
    return {
        "api_key": new_key,
        "message": (
            "New master key issued and shown once. The old key stopped working "
            "immediately: update any script or device that still uses it. "
            "Sensors with their own keys carry on as before."
        ),
    }


@router.delete("/me/sensors/{sensor_id}")
def decommission_sensor(
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Release a seat by decommissioning a sensor."""
    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    from pricing import build_subscription

    # Measured either side of the removal, for the reason given on
    # registration: taking one thermometer out of a shop that keeps others
    # does not take the shop's price off the bill.
    monthly_before = build_subscription(tenant)["monthly_total_usd"]
    closed = STORE.remove_sensor(sensor_id)
    monthly_after = build_subscription(tenant)["monthly_total_usd"]
    if closed:
        write_audit(
            tenant, operator, "incident.closed",
            f"{', '.join(closed)} closed because {sensor_id} was retired.",
        )
    return {
        "message": f"Sensor '{sensor_id}' decommissioned.",
        "seats_used": STORE.seat_count(tenant.tenant_id),
        "seats_total": tenant.entitlements()["max_sensors"],
        "billing": {
            "removes_monthly_usd": round(monthly_before - monthly_after, 2),
            "new_monthly_total_usd": monthly_after,
        },
    }


@router.post("/me/suspend")
def suspend_license(
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    """Voluntarily suspend a license; telemetry is refused while suspended.

    A named owner, never a machine. These two routes are the only ones in
    the licence router that will not accept the tenant API key, and the
    reason is the audit trail: turning off a whole company's monitoring,
    or moving it onto a plan without voice escalation, is not an action
    whose record should read "API key". Provisioning scripts have no
    business doing either.
    """
    STORE.set_suspended(tenant, True)
    return {
        "message": f"License for {tenant.company_name} suspended.",
        "license_active": tenant.active,
    }
