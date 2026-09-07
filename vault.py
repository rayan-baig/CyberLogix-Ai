"""The compliance vault: readings nobody can quietly rewrite.

A temperature log is only worth what a sceptical third party thinks it is
worth. An insurer settling a six-figure spoilage claim, an FDA auditor, a
buyer checking a cellar's provenance — each of them is asking the same
question, and "our database says so" is not an answer to it. The obvious
attack is not an outsider: it is the operator who discovers a bad night
and edits the row before the inspector arrives.

So every reading is chained. Each entry's digest covers the reading *and*
the digest before it, which means altering one historical value changes
every digest after it and the chain stops verifying. The customer cannot
rewrite their own history without it being obvious, and — the part that
makes this sellable — neither can we.

What this is not: a blockchain, a notary, or a legal guarantee. It is a
hash chain plus an attestation the customer can hand to someone who does
not trust either party. That is exactly what the claim is, and overselling
it would be the one thing that makes it worthless.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Reading,
    Tenant,
    display_temperature,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.vault")

router = APIRouter(prefix="/api/vault", tags=["Compliance Vault"])

# The genesis link. A chain has to start somewhere, and starting from a
# constant means a chain of one entry is still verifiable.
GENESIS = "0" * 64

# Signing key for attestations. Absent, attestations are still produced and
# still verifiable as a chain — they simply carry no counter-signature, and
# say so rather than pretending otherwise.
ATTESTATION_KEY = os.environ.get("CYBERLOGIX_ATTESTATION_KEY", "").strip()


def digest_fields(
    previous: str,
    sensor_id: str,
    temperature_fahrenheit: float,
    humidity_percent: Optional[float],
    breached: bool,
    at: str,
) -> str:
    """The link for one reading, covering the reading and its predecessor.

    Field order is fixed and explicit. Hashing a dict whose key order could
    change would produce a chain that stops verifying after a refactor,
    which is worse than no chain at all.

    Everything that derives a digest goes through here — our own chain and
    the public verifier alike. Two implementations of the same hash is how
    a verifier ends up disagreeing with the thing it verifies.
    """
    payload = "|".join(
        [
            previous,
            sensor_id,
            f"{temperature_fahrenheit:.4f}",
            "null" if humidity_percent is None else f"{humidity_percent:.4f}",
            "1" if breached else "0",
            at,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest_reading(previous: str, reading: Reading) -> str:
    """The link for one stored reading."""
    return digest_fields(
        previous,
        reading.sensor_id,
        reading.temperature_fahrenheit,
        reading.humidity_percent,
        reading.breached,
        iso(reading.recorded_at) or "",
    )


def build_chain(readings: List[Reading]) -> List[Dict[str, Any]]:
    """Chain a run of readings, oldest first."""
    chain: List[Dict[str, Any]] = []
    previous = GENESIS
    for reading in readings:
        link = digest_reading(previous, reading)
        chain.append(
            {
                "at": iso(reading.recorded_at),
                "temperature_fahrenheit": reading.temperature_fahrenheit,
                "humidity_percent": reading.humidity_percent,
                "breached": reading.breached,
                "previous": previous,
                "digest": link,
            }
        )
        previous = link
    return chain


def chain_head(readings: List[Reading]) -> str:
    """The final digest — the one figure that fixes the whole run."""
    previous = GENESIS
    for reading in readings:
        previous = digest_reading(previous, reading)
    return previous


def sign(head: str) -> Optional[str]:
    """Counter-sign a chain head, when a key is configured."""
    if not ATTESTATION_KEY:
        return None
    return hmac.new(
        ATTESTATION_KEY.encode("utf-8"), head.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def signing_state() -> Dict[str, Any]:
    """Whether attestations are counter-signed, stated plainly."""
    configured = bool(ATTESTATION_KEY)
    return {
        "counter_signed": configured,
        "algorithm": "HMAC-SHA256" if configured else None,
        "note": (
            "The chain head is counter-signed, so a recipient can confirm "
            "this attestation was issued by CyberLogix AI."
            if configured
            else "No signing key is configured, so this attestation carries "
            "no counter-signature. The hash chain still verifies on its own; "
            "set CYBERLOGIX_ATTESTATION_KEY to add one."
        ),
    }


def _owned_sensor(tenant: Tenant, sensor_id: str):
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )
    return sensor


def attest_sensor(
    tenant: Tenant,
    sensor,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    anchor: bool = False,
) -> Dict[str, Any]:
    """An attestation for one sensor over a period.

    `until` matters more than it looks. The claim packet clips its reading
    table to a window; without an upper bound here the head would cover
    readings the packet does not show, and an adjuster re-deriving it
    would get a different figure and conclude the evidence is false.

    `anchor=True` records the head, which is what later makes tampering
    detectable — a chain checked only against itself always agrees.
    """
    readings = STORE.readings_for(sensor.sensor_id, since=since)
    if until is not None:
        readings = [r for r in readings if r.recorded_at <= until]
    unit = tenant.temperature_unit
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    above, below = sensor.bounds()

    excursions = [r for r in readings if r.breached]
    head = chain_head(readings)

    if anchor:
        STORE.anchor_chain(
            tenant_id=tenant.tenant_id,
            sensor_id=sensor.sensor_id,
            chain_head=head,
            readings=len(readings),
            period_start=iso(readings[0].recorded_at) if readings else None,
            period_end=iso(readings[-1].recorded_at) if readings else None,
        )

    return {
        "sensor_id": sensor.sensor_id,
        "location_name": sensor.location_name,
        "industry": profile["name"],
        "asset_noun": profile["asset_noun"],
        "safe_band": {
            "above": display_temperature(above, unit),
            "below": display_temperature(below, unit),
            "unit": unit,
        },
        "period_start": iso(readings[0].recorded_at) if readings else None,
        "period_end": iso(readings[-1].recorded_at) if readings else None,
        "readings": len(readings),
        "excursions": len(excursions),
        "within_band_percent": (
            round((len(readings) - len(excursions)) / len(readings) * 100, 2)
            if readings
            else None
        ),
        "coldest": (
            display_temperature(
                min(r.temperature_fahrenheit for r in readings), unit
            )
            if readings
            else None
        ),
        "warmest": (
            display_temperature(
                max(r.temperature_fahrenheit for r in readings), unit
            )
            if readings
            else None
        ),
        "chain_head": head,
        "signature": sign(head),
    }


@router.get("/attestation")
def estate_attestation(
    days: int = Query(30, ge=1, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """A signed statement of the whole estate's record over a period.

    This is the document handed to an insurer or an auditor. It carries a
    digest per sensor and one over all of them, so a recipient can check
    that what they were shown is what was recorded.
    """
    since = utc_now() - timedelta(days=days)
    sensors = sorted(
        STORE.sensors_for(tenant.tenant_id), key=lambda s: s.sensor_id
    )
    # Anchored, because an attestation that is not recorded cannot be
    # checked against later — which is the only thing that makes the
    # tamper claim true.
    entries = [
        attest_sensor(tenant, sensor, since, anchor=True)
        for sensor in sensors
    ]

    # One digest over the per-sensor heads, so the estate has a single
    # number a recipient can quote.
    estate_head = hashlib.sha256(
        "|".join(f"{e['sensor_id']}:{e['chain_head']}" for e in entries).encode()
    ).hexdigest()

    readings = sum(e["readings"] for e in entries)
    excursions = sum(e["excursions"] for e in entries)

    return {
        "document": "Estate Temperature Attestation",
        "company_name": tenant.company_name,
        "tenant_id": tenant.tenant_id,
        "period_days": days,
        "issued_at": iso(utc_now()),
        "sensors": len(entries),
        "readings": readings,
        "excursions": excursions,
        "within_band_percent": (
            round((readings - excursions) / readings * 100, 2) if readings else None
        ),
        "estate_digest": estate_head,
        "signature": sign(estate_head),
        "signing": signing_state(),
        "entries": entries,
        "how_to_verify": (
            "Each entry's digest covers its reading and the digest before "
            "it, so changing any historical value changes every digest after "
            "it. POST the readings and the claimed chain head to "
            "/api/vault/verify to re-derive the chain independently."
        ),
    }


@router.get("/attestation/{sensor_id}")
def sensor_attestation(
    sensor_id: str,
    days: int = Query(30, ge=1, le=730),
    include_chain: bool = Query(
        False,
        description=(
            "Return every link. Large, and only needed by someone "
            "re-deriving the chain themselves."
        ),
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """An attestation for one sensor, optionally with the full chain."""
    sensor = _owned_sensor(tenant, sensor_id)
    since = utc_now() - timedelta(days=days)
    attestation = attest_sensor(tenant, sensor, since, anchor=True)
    attestation["company_name"] = tenant.company_name
    attestation["issued_at"] = iso(utc_now())
    attestation["signing"] = signing_state()

    if include_chain:
        attestation["chain"] = build_chain(
            STORE.readings_for(sensor.sensor_id, since=since)
        )
    return attestation


@router.get("/verify/{sensor_id}")
def verify_sensor(
    sensor_id: str,
    days: int = Query(30, ge=1, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """Check the stored record against the head we previously attested.

    The obvious implementation is a tautology and was the one here: derive
    the chain from the current rows, then check it against a chain derived
    from the same rows. That agrees no matter what was edited, so an
    operator could rewrite a bad night and the tamper check would still
    say "intact" — the single worst possible failure for the product this
    module exists to sell.

    A hash chain only detects tampering against a value recorded *before*
    it. So this compares today's head against the most recent head issued
    in an attestation. With no attestation yet there is nothing to compare
    to, and it says so rather than implying a pass.
    """
    sensor = _owned_sensor(tenant, sensor_id)
    since = utc_now() - timedelta(days=days)
    readings = STORE.readings_for(sensor.sensor_id, since=since)
    current = chain_head(readings)

    anchors = [
        a
        for a in STORE.anchors_for_sensor(sensor.sensor_id)
        if a.tenant_id == tenant.tenant_id
    ]
    if not anchors:
        return {
            "sensor_id": sensor.sensor_id,
            "verifiable": False,
            "links_checked": len(readings),
            "chain_head": current,
            "checked_at": iso(utc_now()),
            "note": (
                "No attestation has been issued for this sensor yet, so "
                "there is no earlier head to compare against. Issue one "
                "and this becomes a real check; until then a chain can "
                "only be checked against itself, which proves nothing."
            ),
        }

    latest = anchors[0]
    # An attestation covers the readings that existed when it was issued.
    # More have arrived since, and the ring buffer may have evicted older
    # ones, so re-derive over exactly the run the anchor covered.
    window = readings
    if latest.period_start:
        window = [r for r in window if iso(r.recorded_at) >= latest.period_start]
    if latest.period_end:
        window = [r for r in window if iso(r.recorded_at) <= latest.period_end]
    rederived = chain_head(window)

    intact = hmac.compare_digest(rederived, latest.chain_head)
    evicted = len(window) < latest.readings

    return {
        "sensor_id": sensor.sensor_id,
        "verifiable": True,
        "intact": intact and not evicted,
        "links_checked": len(window),
        "links_when_attested": latest.readings,
        "attested_at": iso(latest.issued_at),
        "attested_head": latest.chain_head,
        "rederived_head": rederived,
        "chain_head": current,
        "checked_at": iso(utc_now()),
        "note": (
            "The record still produces the head attested on "
            f"{iso(latest.issued_at)}. Nothing in that period has changed."
            if intact and not evicted
            else "Readings from the attested period have since been evicted "
            "by the retention limit, so the head cannot be reproduced. This "
            "is expected on an old attestation, and is not evidence of "
            "tampering — but it is also no longer proof of anything."
            if evicted
            else "The stored record does NOT reproduce the head attested on "
            f"{iso(latest.issued_at)}. Something in that period has been "
            "altered since. Investigate before relying on this record."
        ),
    }


@router.post("/verify")
def verify_supplied_chain(payload: Dict[str, Any]):
    """Verify a chain a third party was handed, without an account.

    Deliberately unauthenticated: the whole point is that a recipient who
    trusts neither the operator nor us can check the arithmetic themselves.
    It reads nothing and stores nothing — it only re-derives digests from
    the readings supplied in the request.
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send a JSON object with 'readings' and 'chain_head'.",
        )
    readings = payload.get("readings")
    # Coerced defensively: this endpoint is deliberately unauthenticated
    # and is the first thing an insurer or auditor touches, so anything
    # that is not a string must produce a 400, never a stack trace.
    raw_head = payload.get("chain_head")
    if raw_head is not None and not isinstance(raw_head, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'chain_head' must be a hex string.",
        )
    claimed = (raw_head or "").strip().lower()
    if claimed and not re.fullmatch(r"[0-9a-f]{64}", claimed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'chain_head' must be 64 hexadecimal characters.",
        )
    if not isinstance(readings, list) or not readings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send a non-empty 'readings' list, oldest first.",
        )
    if len(readings) > 20000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Verify at most 20,000 readings in one request.",
        )

    previous = GENESIS
    try:
        for row in readings:
            # The timestamp is part of the digest, so it is taken from the
            # supplied row verbatim rather than parsed and re-formatted.
            previous = digest_fields(
                previous,
                str(row["sensor_id"]),
                float(row["temperature_fahrenheit"]),
                None
                if row.get("humidity_percent") is None
                else float(row["humidity_percent"]),
                bool(row["breached"]),
                str(row["at"]),
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Each reading needs sensor_id, temperature_fahrenheit, "
                f"humidity_percent, breached and at. ({exc})"
            ),
        ) from exc

    matches = bool(claimed) and hmac.compare_digest(previous, claimed)
    return {
        "links_checked": len(readings),
        "derived_chain_head": previous,
        "claimed_chain_head": claimed or None,
        "matches": matches if claimed else None,
        "verdict": (
            "The readings supplied produce the chain head claimed. Nothing "
            "in this record has been altered since it was attested."
            if matches
            else "The readings supplied do NOT produce the chain head "
            "claimed. Either the record or the attestation has changed."
            if claimed
            else "No chain head was supplied to compare against; the derived "
            "head is returned for you to compare yourself."
        ),
        "checked_at": iso(utc_now()),
    }
