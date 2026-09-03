"""Corporate license management.

Owns tenant onboarding, API keys, plan entitlements and seat enforcement.
Every other router in the suite authenticates through the dependencies
exported here, so a suspended or expired license stops the whole platform
for that customer at the door.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from store import PLAN_TIERS, STORE, Tenant, resolve_vertical

router = APIRouter(prefix="/api/licenses", tags=["Corporate License Management"])


class TenantCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=200)
    contact_name: str = Field(..., min_length=1, max_length=120)
    contact_phone: str = Field(..., min_length=5, max_length=40)
    contact_email: EmailStr
    plan: str = Field("trial", description="One of: trial, growth, enterprise")


class PlanChange(BaseModel):
    plan: str = Field(..., description="One of: trial, growth, enterprise")


class SensorRegister(BaseModel):
    sensor_id: str = Field(..., min_length=1, max_length=80)
    industry_vertical: str = Field(..., min_length=1)
    location_name: str = Field(..., min_length=1, max_length=200)


def require_tenant(
    x_cyberlogix_key: Optional[str] = Header(
        None, description="Tenant API key issued at onboarding."
    ),
) -> Tenant:
    """Resolve the calling tenant from its API key.

    Rejects unknown keys with 401 and inactive licenses with 402, so a
    billing lapse is distinguishable from a bad credential.
    """
    if not x_cyberlogix_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-CyberLogix-Key header.",
        )

    tenant = STORE.tenant_by_key(x_cyberlogix_key)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unrecognised API key.",
        )

    if tenant.suspended:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"License for {tenant.company_name} is suspended.",
        )

    if tenant.expired:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"License for {tenant.company_name} expired on "
                f"{tenant.expires_at.date()}."
            ),
        )

    return tenant


def require_entitlement(feature: str):
    """Build a dependency asserting the tenant's plan includes `feature`."""

    def _dependency(tenant: Tenant = Depends(require_tenant)) -> Tenant:
        if not tenant.entitlements().get(feature, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The {tenant.entitlements()['name']} plan does not include "
                    f"'{feature}'. Upgrade to unlock it."
                ),
            )
        return tenant

    return _dependency


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
    )
    return {
        "sensor": sensor.public(),
        "seats_used": seats_used + 1,
        "seats_total": cap,
    }


@router.get("/me/sensors")
def list_sensors(tenant: Tenant = Depends(require_tenant)):
    """Full sensor fleet for the calling tenant."""
    sensors = STORE.sensors_for(tenant.tenant_id)
    return {
        "count": len(sensors),
        "online": sum(1 for s in sensors if not s.offline()),
        "sensors": [s.public() for s in sensors],
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
    return {
        "message": f"Sensor '{sensor_id}' decommissioned.",
        "seats_used": STORE.seat_count(tenant.tenant_id),
        "seats_total": tenant.entitlements()["max_sensors"],
    }


@router.post("/me/suspend")
def suspend_license(tenant: Tenant = Depends(require_tenant)):
    """Voluntarily suspend a license; telemetry is refused while suspended."""
    STORE.set_suspended(tenant, True)
    return {
        "message": f"License for {tenant.company_name} suspended.",
        "license_active": tenant.active,
    }
