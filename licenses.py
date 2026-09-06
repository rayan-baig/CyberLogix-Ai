"""Corporate license management.

Owns tenant onboarding, API keys, plan entitlements and seat enforcement.
Every other router in the suite authenticates through the dependencies
exported here, so a suspended or expired license stops the whole platform
for that customer at the door.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from auth import require_entitlement, require_tenant

# Re-exported: several routers import these from here rather than reaching
# past this module into auth, so the dependency reads in one direction.
__all__ = ["router", "require_entitlement", "require_tenant"]
from store import PLAN_TIERS, STORE, Tenant, resolve_vertical

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


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
def onboard_tenant(payload: TenantCreate):
    """Onboard a customer and issue its API key.

    The key is returned exactly once, at creation, and is never echoed by
    any later endpoint.
    """
    plan = _validate_plan(payload.plan)
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


@router.post("/me/plan")
def change_plan(payload: PlanChange, tenant: Tenant = Depends(require_tenant)):
    """Move a tenant between tiers, refusing a downgrade that strands seats."""
    plan = _validate_plan(payload.plan)
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
    payload: UnitPreference, tenant: Tenant = Depends(require_tenant)
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
    payload: SensorRegister, tenant: Tenant = Depends(require_tenant)
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
    existing = STORE.get_sensor(sensor_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sensor '{sensor_id}' is already registered.",
        )

    serial = (payload.external_device_sn or "").strip() or None
    if serial and STORE.device_sn_taken(serial):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device serial '{serial}' is already bound to a sensor.",
        )

    seats_used = STORE.seat_count(tenant.tenant_id)
    cap = tenant.entitlements()["max_sensors"]
    if seats_used >= cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Seat limit reached: the {tenant.entitlements()['name']} plan "
                f"allows {cap} sensors. Upgrade to add more."
            ),
        )

    sensor = STORE.register_sensor(
        sensor_id=sensor_id,
        tenant_id=tenant.tenant_id,
        industry_vertical=vertical,
        location_name=payload.location_name,
        external_device_sn=serial,
    )
    from pricing import PRICE_BOOK, build_subscription

    entry = PRICE_BOOK[vertical]
    return {
        "sensor": sensor.public(),
        "seats_used": seats_used + 1,
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


@router.delete("/me/sensors/{sensor_id}")
def decommission_sensor(sensor_id: str, tenant: Tenant = Depends(require_tenant)):
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
def suspend_license(tenant: Tenant = Depends(require_tenant)):
    """Voluntarily suspend a license; telemetry is refused while suspended."""
    STORE.set_suspended(tenant, True)
    return {
        "message": f"License for {tenant.company_name} suspended.",
        "license_active": tenant.active,
    }
