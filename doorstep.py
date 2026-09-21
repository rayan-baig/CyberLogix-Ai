"""Sensors that turned up uninvited, and how to let them in.

Connecting a sensor used to require getting a serial number exactly
right before anything worked. You registered the device, typed its
serial, and only then did readings count. Get one character wrong, or
not know the serial at all -- which is normal, because it is printed
inside a battery compartment bolted inside a freezer -- and every
reading was answered with a 404 and thrown away. The console stayed
empty and said nothing about why.

That is the whole hassle of setting this up, and it is backwards. A
device that is sending us readings has already done the hard part.

So an unrecognised device is not an error any more. It is left on the
doorstep: we hold its recent readings, show it in the console as
something knocking, and one tap adopts it -- choosing what kind of place
it is watching and where. The readings taken while it waited are
replayed on adoption, so the history starts from the moment the sensor
started talking rather than from the moment somebody got round to the
paperwork.

Two things this deliberately does not do:

**It does not guess the unit.** Four degrees is a fridge in Celsius and
a disaster in Fahrenheit. A device that never says which is held until
somebody says, and adopting it is where they say it.

**It does not adopt anything by itself.** A seat costs money and an
adopted sensor starts being billed, so the tap is a person's. What this
removes is the typing, not the decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounts import require_role
from auth import write_audit
from licenses import require_tenant
from store import INDUSTRY_PROFILES, STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.doorstep")

router = APIRouter(prefix="/api/doorstep", tags=["Devices knocking"])

KNOCK_KIND = "doorstep"

# Readings kept per waiting device. Enough that adopting one gives a
# graph with a shape rather than a single dot, and few enough that a
# device nobody adopts cannot fill the database on its own.
KEPT_READINGS = 24

# Devices held per account. A misconfigured gateway can invent a new
# serial on every request, and without a ceiling that is an unbounded
# write endpoint wearing a friendly name.
MAX_WAITING = 50


def waiting_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(KNOCK_KIND)
            if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: r.get("last_seen_at") or "", reverse=True)


def _key(tenant_id: str, serial: str) -> str:
    return f"{tenant_id}::{serial}"


def knock(tenant_id: str, serial: str, value: Optional[float],
          unit: Optional[str], payload: Any = None) -> None:
    """Record that an unrecognised device reported. Never raises.

    Called from the ingest path, which is answering a device rather than
    a person: it must not fail because the doorstep is full or the
    payload is strange.
    """
    try:
        serial = (serial or "").strip()
        if not serial:
            return
        key = _key(tenant_id, serial)
        row = STORE._db.get(KNOCK_KIND, key) or {
            "key": key,
            "tenant_id": tenant_id,
            "serial": serial,
            "first_seen_at": iso(utc_now()),
            "readings": [],
            "times_seen": 0,
        }
        if len(waiting_for(tenant_id)) >= MAX_WAITING and row["times_seen"] == 0:
            # Already at the ceiling and this is a device we have never
            # seen: drop it rather than let one broken gateway inventing
            # serials push out the real devices somebody is about to
            # adopt.
            return
        held = (row.get("readings") or [])[-(KEPT_READINGS - 1):]
        held.append({"value": value, "unit": unit, "at": iso(utc_now())})
        STORE._db.put(KNOCK_KIND, key, {
            **row,
            "readings": held,
            "times_seen": row["times_seen"] + 1,
            "last_seen_at": iso(utc_now()),
            "last_value": value,
            "last_unit": unit,
        })
    except Exception:  # noqa: BLE001 - a device must not get a 500 for this
        logger.exception("Could not record a knock from %r.", serial)


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    readings = row.get("readings") or []
    unit = row.get("last_unit")
    return {
        "serial": row["serial"],
        "times_seen": row.get("times_seen", 0),
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "last_value": row.get("last_value"),
        "unit": unit,
        "needs_unit": unit is None,
        "held_readings": len(readings),
        "recent": [r.get("value") for r in readings][-12:],
        "why": (
            "This device is sending readings and does not say whether "
            "they are Celsius or Fahrenheit. Say which when you add it: "
            "four degrees is a fridge in one and a disaster in the other."
            if unit is None else
            f"This device has sent {row.get('times_seen', 0)} reading(s) "
            "and is not set up yet. Adding it takes one tap."
        ),
    }


def forget(tenant_id: str, serial: str) -> None:
    STORE._db.delete(KNOCK_KIND, _key(tenant_id, serial))


# --- routes ----------------------------------------------------------------


class Adopt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial: str = Field(..., min_length=1, max_length=160)
    industry_vertical: str = Field(..., min_length=2, max_length=60)
    location_name: str = Field(..., min_length=1, max_length=160)
    sensor_id: str = Field("", max_length=60,
                           description="Left out, the serial is used.")
    unit: str = Field("", max_length=20,
                      description="Required only if the device never said.")

    @field_validator("unit")
    @classmethod
    def a_known_unit(cls, value: str) -> str:
        clean = value.strip().lower()
        # The bridge's own names, so a unit chosen here and a unit sent
        # by a device mean the same thing.
        from hardware_bridge import SUPPORTED_METRICS

        if clean and clean not in SUPPORTED_METRICS:
            raise ValueError(f"unit must be one of {list(SUPPORTED_METRICS)}")
        return clean


@router.get("")
def list_waiting(tenant: Tenant = Depends(require_tenant)):
    """Devices sending us readings that are not set up yet."""
    rows = [public(r) for r in waiting_for(tenant.tenant_id)]
    return {
        "count": len(rows),
        "devices": rows,
        "note": (
            f"{len(rows)} device(s) are sending readings and are not set "
            "up. Nothing from them is being watched until you add them."
            if rows else
            "Nothing is knocking. Point a sensor at us and it appears "
            "here on its own — you do not need its serial in advance."
        ),
    }


@router.post("/adopt", status_code=status.HTTP_201_CREATED)
def adopt(
    payload: Adopt,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Let one in, and replay what it sent while it waited.

    Never automatic. A seat costs money and an adopted sensor starts
    being billed, so the decision is a person's -- what this removes is
    the typing, not the choice.
    """
    from licenses import seat_limit_message
    from store import SeatClaimRefused, resolve_vertical

    row = STORE._db.get(KNOCK_KIND, _key(tenant.tenant_id, payload.serial))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No device called '{payload.serial}' is waiting.")

    vertical = resolve_vertical(payload.industry_vertical)
    if vertical is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Unknown industry. Allowed keys: "
                    f"{list(INDUSTRY_PROFILES)}"))

    unit = payload.unit or row.get("last_unit")
    if not unit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This device never said whether its readings are Celsius "
                "or Fahrenheit, so it cannot be added without being told. "
                "Four degrees is a fridge in one and a disaster in the "
                "other, and it is not guessed from the number."
            ))

    cap = tenant.entitlements()["max_sensors"]
    try:
        sensor = STORE.claim_sensor_seat(
            sensor_id=(payload.sensor_id.strip() or payload.serial.strip()),
            tenant_id=tenant.tenant_id,
            industry_vertical=vertical,
            location_name=payload.location_name.strip(),
            max_sensors=cap,
            external_device_sn=payload.serial.strip(),
        )
    except SeatClaimRefused as refused:
        detail = refused.detail
        if refused.reason == "no_seats":
            detail = seat_limit_message(tenant, cap, payload.industry_vertical)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=detail) from None

    replayed = _replay(sensor, tenant, row, unit)
    forget(tenant.tenant_id, payload.serial)
    write_audit(tenant, operator, "doorstep.adopted",
                f"{payload.serial} as {sensor.sensor_id} ({replayed} "
                "reading(s) replayed)")
    return {
        "sensor": sensor.public(),
        "ingest_key": sensor.ingest_key,
        "replayed_readings": replayed,
        "note": (
            f"{replayed} reading(s) taken while it was waiting have been "
            "kept, so its history starts when the sensor did rather than "
            "when you got round to this."
        ),
    }


def _replay(sensor, tenant: Tenant, row: Dict[str, Any], unit: str) -> int:
    """Record what it sent while it waited.

    Through `process_reading`, the same function a live pulse and a
    webhook both go through, so a replayed reading is scored against the
    industry profile and opens an incident exactly as it would have at
    the time. A second path that merely stored the numbers would give a
    graph with no alarms in it -- the shape of the history without its
    meaning.

    One unusable row loses one row. A device that sent a string where a
    number should be for six of its twenty-four held readings is a
    device worth adopting for the other eighteen.
    """
    from store import ImplausibleReading, require_plausible
    from telemetry import process_reading

    taken = 0
    for held in row.get("readings") or []:
        value = held.get("value")
        if value is None:
            continue
        try:
            temperature = float(require_plausible(value))
        except (ImplausibleReading, TypeError, ValueError):
            continue
        if (held.get("unit") or unit) == "temperature_c":
            temperature = round(temperature * 9.0 / 5.0 + 32.0, 2)
        try:
            process_reading(tenant=tenant, sensor=sensor,
                            temperature=temperature,
                            humidity=sensor.last_humidity)
            taken += 1
        except Exception:  # noqa: BLE001 - one held reading is not worth a 500
            logger.exception("Could not replay a held reading for %s.",
                             sensor.sensor_id)
    return taken


@router.delete("/{serial}", status_code=status.HTTP_204_NO_CONTENT)
def turn_away(
    serial: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Not ours. Stop holding it.

    It will come back if the device keeps reporting, which is correct:
    something is still sending us readings and pretending otherwise
    would be hiding it rather than answering it.
    """
    if STORE._db.get(KNOCK_KIND, _key(tenant.tenant_id, serial)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No device called '{serial}' is waiting.")
    forget(tenant.tenant_id, serial)
    write_audit(tenant, operator, "doorstep.dismissed", serial)
    return None
