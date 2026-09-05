"""Physical sites: the layer between a customer and its sensors.

A chain does not experience its estate as a flat list of thermometers. It
has stores, and a health inspector visits one of them. Without sites a
compliance report cannot be produced per store, the Boca Raton manager is
woken for a Boynton Beach freezer, and an enterprise contract's branch
count is a number somebody typed rather than something the fleet can be
checked against.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Site, Tenant, User

router = APIRouter(prefix="/api/sites", tags=["Sites"])


class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    address: str = Field("", max_length=300)


class SiteUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    address: Optional[str] = Field(None, max_length=300)


class SensorAssignment(BaseModel):
    sensor_id: str = Field(..., min_length=1)


def _load(site_id: str, tenant: Tenant) -> Site:
    site = STORE.get_site(site_id)
    if site is None or site.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Site '{site_id}' not found for this tenant.",
        )
    return site


def _own_sensor(sensor_id: str, tenant: Tenant):
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )
    return sensor


@router.get("")
def list_sites(tenant: Tenant = Depends(require_tenant)):
    """Every site, with its sensor count, plus anything not yet placed."""
    unit = tenant.temperature_unit
    sites = STORE.sites_for(tenant.tenant_id)
    rows = []
    for site in sites:
        sensors = STORE.sensors_at_site(site.site_id)
        rows.append(
            {
                **site.public(
                    sensor_count=len(sensors),
                    online=sum(1 for s in sensors if not s.offline()),
                ),
                "sensors": [s.public(unit) for s in sensors],
            }
        )

    orphans = STORE.unassigned_sensors(tenant.tenant_id)
    return {
        "count": len(rows),
        "sites": rows,
        "unassigned_sensors": [s.public(unit) for s in orphans],
        "unassigned_count": len(orphans),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteCreate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Add a location."""
    site = STORE.create_site(
        tenant_id=tenant.tenant_id, name=payload.name, address=payload.address
    )
    write_audit(tenant, operator, "site.created", f"{site.name} ({site.site_id}).")
    return site.public()


@router.get("/{site_id}")
def get_site(site_id: str, tenant: Tenant = Depends(require_tenant)):
    """One site and everything at it."""
    site = _load(site_id, tenant)
    sensors = STORE.sensors_at_site(site_id)
    contacts = [
        c for c in STORE.contacts_for(tenant.tenant_id) if c.site_id == site_id
    ]
    return {
        **site.public(
            sensor_count=len(sensors),
            online=sum(1 for s in sensors if not s.offline()),
        ),
        "sensors": [s.public(tenant.temperature_unit) for s in sensors],
        "contacts": [c.public() for c in contacts],
        "breaching": sum(1 for s in sensors if s.last_temperature is not None
                         and s.bounds() and _breaching(s)),
    }


def _breaching(sensor) -> bool:
    from store import evaluate_sensor_breach

    if sensor.last_temperature is None:
        return False
    return evaluate_sensor_breach(sensor, sensor.last_temperature) is not None


@router.patch("/{site_id}")
def update_site(
    site_id: str,
    payload: SiteUpdate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Rename or re-address a site."""
    site = _load(site_id, tenant)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update."
        )
    for field, value in changes.items():
        setattr(site, field, value)
    STORE.save_site(site)
    write_audit(
        tenant, operator, "site.updated", f"{site.name}: {', '.join(sorted(changes))}."
    )
    return site.public(sensor_count=len(STORE.sensors_at_site(site_id)))


@router.delete("/{site_id}")
def delete_site(
    site_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Remove a site. Its sensors are released, never deleted."""
    site = _load(site_id, tenant)
    released = len(STORE.sensors_at_site(site_id))
    STORE.remove_site(site_id)
    write_audit(
        tenant, operator, "site.deleted",
        f"{site.name} removed; {released} sensor(s) released.",
    )
    return {
        "message": f"{site.name} removed.",
        "sensors_released": released,
        "note": "Released sensors keep their readings and remain billable.",
    }


@router.post("/{site_id}/sensors")
def assign_sensor(
    site_id: str,
    payload: SensorAssignment,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Place a sensor at this site."""
    site = _load(site_id, tenant)
    sensor = _own_sensor(payload.sensor_id, tenant)
    STORE.assign_sensor_to_site(sensor, site_id)
    write_audit(
        tenant, operator, "site.sensor_assigned",
        f"{sensor.sensor_id} placed at {site.name}.",
    )
    return {"site": site.public(), "sensor": sensor.public(tenant.temperature_unit)}


@router.delete("/{site_id}/sensors/{sensor_id}")
def unassign_sensor(
    site_id: str,
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Take a sensor off a site without decommissioning it."""
    site = _load(site_id, tenant)
    sensor = _own_sensor(sensor_id, tenant)
    if sensor.site_id != site_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{sensor_id} is not placed at {site.name}.",
        )
    STORE.assign_sensor_to_site(sensor, None)
    write_audit(
        tenant, operator, "site.sensor_removed",
        f"{sensor.sensor_id} removed from {site.name}.",
    )
    return {"message": f"{sensor_id} removed from {site.name}."}


@router.get("/{site_id}/reconciliation")
def reconcile(site_id: str, tenant: Tenant = Depends(require_tenant)):
    """Whether this site actually has the coverage it is billed for."""
    site = _load(site_id, tenant)
    sensors = STORE.sensors_at_site(site_id)
    silent = [s for s in sensors if s.offline()]
    flat = [s for s in sensors if s.battery_low]
    return {
        "site": site.public(sensor_count=len(sensors)),
        "sensors": len(sensors),
        "offline": [s.sensor_id for s in silent],
        "low_battery": [s.sensor_id for s in flat],
        "covered": bool(sensors) and not silent,
        "note": (
            "A site with no sensor, or only silent ones, is billed but not "
            "protected."
        ),
    }
