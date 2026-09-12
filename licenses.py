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
from pydantic import BaseModel, EmailStr, Field

from accounts import require_role
from auth import (
    require_entitlement,
    require_role_or_machine,
    require_tenant,
    require_tenant_any_state,
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
    plan: str = Field("trial", description="One of: trial, growth, enterprise")


class UnitPreference(BaseModel):
    temperature_unit: str = Field(
        ..., description="F or C. Readings are stored in F and converted for display."
    )


class PlanChange(BaseModel):
    plan: str = Field(..., description="One of: trial, growth, enterprise")


class ThresholdOverride(BaseModel):
    """Null clears an override and restores the industry default."""

    danger_above: Optional[float] = Field(
        None, description="Upper bound in °F. Null restores the sector default."
    )
    danger_below: Optional[float] = Field(
        None, description="Lower bound in °F. Null restores the sector default."
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
    """Public price-list style catalogue of the commercial tiers."""
    return {
        "plans": [
            {"plan": key, **tier} for key, tier in PLAN_TIERS.items()
        ]
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


def _require_provisioning(supplied: Optional[str], plan: str) -> None:
    """Refuse to provision a paid plan without the provisioning key."""
    if plan == "trial":
        return
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
    """Entitlements and seat usage for the calling tenant."""
    return tenant.public(sensor_count=STORE.seat_count(tenant.tenant_id))


# Which tiers are above which. Self-service moves up this list and never
# down it, and never back onto the trial at all.
PLAN_RANK = {"trial": 0, "growth": 1, "enterprise": 2}


def next_tier_above(plan: str) -> Optional[str]:
    """The cheapest plan with more room than this one."""
    here = PLAN_RANK.get(plan, 0)
    above = sorted(
        (rank, key) for key, rank in PLAN_RANK.items() if rank > here
    )
    return above[0][1] if above else None


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
    from pricing import PRICE_BOOK

    here = tenant.entitlements()["name"]
    nxt = next_tier_above(tenant.plan)
    if nxt is None:
        return (
            f"Seat limit reached: the {here} plan allows {cap} sensors, and "
            "it is the largest one. Get in touch and we will size a "
            "contract for the estate you actually have."
        )

    tier = PLAN_TIERS[nxt]
    rate = PRICE_BOOK.get(vertical, {}).get("monthly_usd")
    price = (
        f" A {PRICE_BOOK[vertical]['unit']} is ${rate:,.0f} a month at the "
        "rate card."
        if rate
        else ""
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

    if seats_used > new_cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot downgrade to {PLAN_TIERS[plan]['name']}: {seats_used} "
                f"sensors are registered but the plan allows {new_cap}. "
                "Decommission sensors first."
            ),
        )

    STORE.change_plan(tenant, plan)
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

    sensor_id = payload.sensor_id.strip()
    serial = (payload.external_device_sn or "").strip() or None
    cap = tenant.entitlements()["max_sensors"]

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

    from pricing import PRICE_BOOK, build_subscription

    entry = PRICE_BOOK[vertical]
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
            "adds_monthly_usd": entry["monthly_usd"],
            "new_monthly_total_usd": build_subscription(tenant)["monthly_total_usd"],
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


@router.delete("/me/sensors/{sensor_id}")
def decommission_sensor(
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Release a seat by decommissioning a sensor."""
    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    STORE.remove_sensor(sensor_id)
    from pricing import PRICE_BOOK, build_subscription

    entry = PRICE_BOOK[sensor.industry_vertical]
    return {
        "message": f"Sensor '{sensor_id}' decommissioned.",
        "seats_used": STORE.seat_count(tenant.tenant_id),
        "seats_total": tenant.entitlements()["max_sensors"],
        "billing": {
            "removes_monthly_usd": entry["monthly_usd"],
            "new_monthly_total_usd": build_subscription(tenant)["monthly_total_usd"],
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
