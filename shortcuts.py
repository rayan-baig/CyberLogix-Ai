"""Sector shortcuts: the one document each industry actually has to produce.

Every vertical has a piece of paperwork its operators dread. A restaurant
manager transcribes freezer temperatures onto a health department sheet. A
lab technician assembles chain-of-custody logs for a vaccine vault. A
dispatcher writes a handover certificate so a receiving dock cannot reject
the load.

The platform already holds the readings those documents are made of, so it
writes them. Each shortcut is grounded in that tenant's real telemetry for
the period — never invented — and says so when there is no data to draw on.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant, write_audit
from auth import optional_operator
from gemini import safe_generate
from store import INDUSTRY_PROFILES, STORE, Tenant, User, iso, resolve_vertical, utc_now

logger = logging.getLogger("cyberlogix.shortcuts")

router = APIRouter(prefix="/api/shortcuts", tags=["Sector Shortcuts"])


def _evidence(tenant: Tenant, vertical: str, days: int) -> Dict[str, Any]:
    """The tenant's own readings for this vertical over the period."""
    since = utc_now() - timedelta(days=days)
    sensors = [
        s
        for s in STORE.sensors_for(tenant.tenant_id)
        if s.industry_vertical == vertical
    ]

    rows: List[Dict[str, Any]] = []
    total_readings = 0
    total_breached = 0

    for sensor in sensors:
        readings = STORE.readings_for(sensor.sensor_id, since=since)
        temps = [r.temperature_fahrenheit for r in readings]
        breached = sum(1 for r in readings if r.breached)
        total_readings += len(readings)
        total_breached += breached

        above, below = sensor.bounds()
        rows.append(
            {
                "sensor_id": sensor.sensor_id,
                "location": sensor.location_name,
                "readings": len(readings),
                "excursions": breached,
                "min_f": min(temps) if temps else None,
                "max_f": max(temps) if temps else None,
                "mean_f": round(sum(temps) / len(temps), 2) if temps else None,
                "limit_above_f": above,
                "limit_below_f": below,
                "last_seen": iso(sensor.last_seen),
            }
        )

    incidents = [
        i
        for i in STORE.incidents_for(tenant.tenant_id, since=since)
        if i.industry_vertical == vertical
    ]

    return {
        "sensors": rows,
        "sensor_count": len(sensors),
        "total_readings": total_readings,
        "total_excursions": total_breached,
        "compliance_percent": (
            round((total_readings - total_breached) / total_readings * 100, 2)
            if total_readings
            else None
        ),
        "incidents": [
            {
                "incident_id": i.incident_id,
                "sensor_id": i.sensor_id,
                "opened_at": iso(i.opened_at),
                "detail": i.breach_details,
                "answered_by": i.acknowledged_by,
                "resolved_at": iso(i.resolved_at),
            }
            for i in incidents
        ],
        "period_start": iso(since),
        "period_end": iso(utc_now()),
    }


def _fallback(profile: Dict[str, Any], evidence: Dict[str, Any], days: int) -> str:
    """A plain document when the model is unavailable, still from real data."""
    lines = [
        f"{profile['shortcut_name']} — {profile['name']}",
        f"Period: last {days} days "
        f"({evidence['period_start']} to {evidence['period_end']})",
        "",
        f"Sensors covered: {evidence['sensor_count']}",
        f"Readings logged: {evidence['total_readings']}",
        f"Excursions outside the safe band: {evidence['total_excursions']}",
    ]
    if evidence["compliance_percent"] is not None:
        lines.append(f"Time within band: {evidence['compliance_percent']}%")

    lines.append("")
    for row in evidence["sensors"]:
        span = (
            f"{row['min_f']}–{row['max_f']}°F, mean {row['mean_f']}°F"
            if row["readings"]
            else "no readings in period"
        )
        lines.append(
            f"  {row['sensor_id']} ({row['location']}): {span}; "
            f"{row['excursions']} excursion(s) in {row['readings']} reading(s)"
        )

    if evidence["incidents"]:
        lines.append("")
        lines.append("Incidents:")
        for incident in evidence["incidents"]:
            answered = incident["answered_by"] or "unanswered"
            lines.append(
                f"  {incident['incident_id']} on {incident['sensor_id']} "
                f"({incident['opened_at']}) — {incident['detail']} [{answered}]"
            )

    lines.append("")
    lines.append(
        "Compiled automatically by CyberLogix AI from continuous sensor "
        "telemetry. Figures cover only the period stated."
    )
    return "\n".join(lines)


@router.get("")
def list_shortcuts():
    """The document each vertical can generate."""
    return {
        "count": len(INDUSTRY_PROFILES),
        "shortcuts": [
            {
                "vertical": key,
                "industry": profile["name"],
                "shortcut_name": profile["shortcut_name"],
                "description": profile["shortcut_description"],
            }
            for key, profile in INDUSTRY_PROFILES.items()
        ],
    }


@router.post("/{vertical}")
def run_shortcut(
    vertical: str,
    days: int = Query(30, ge=1, le=365),
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
):
    """Produce this sector's document from the tenant's own readings."""
    key = resolve_vertical(vertical)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid industry_vertical provided. Allowed keys: "
                f"{list(INDUSTRY_PROFILES)}"
            ),
        )

    profile = INDUSTRY_PROFILES[key]
    evidence = _evidence(tenant, key, days)

    # A document with nothing behind it is worse than none: an inspector
    # would read it as an attestation.
    if evidence["sensor_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{tenant.company_name} has no {profile['name']} sensors "
                "registered, so there is nothing to attest to."
            ),
        )

    prompt = f"""
    You are the CyberLogix AI compliance clerk producing a document titled
    "{profile['shortcut_name']}" for a {profile['name']} operator.

    Purpose of this document: {profile['shortcut_description']}

    Company: {tenant.company_name}
    Reporting period: last {days} days
    Sensors covered: {evidence['sensor_count']}
    Readings logged: {evidence['total_readings']}
    Excursions outside the safe band: {evidence['total_excursions']}
    Time within band: {evidence['compliance_percent']}%

    Per-sensor detail (the only figures you may cite):
    {evidence['sensors']}

    Incidents in the period:
    {evidence['incidents']}

    Write the document as the reader in this sector expects it: a health
    inspector, an FAA auditor, a receiving dock, a board, whichever fits.
    Use plain prose and simple headed sections. Cite only the figures given
    above — inventing a reading would make this document worthless as
    evidence. If a figure is missing, say it is not available rather than
    estimating. No markdown syntax, no bullet characters, no tables.
    """

    document, source = safe_generate(
        prompt,
        fallback=_fallback(profile, evidence, days),
        purpose=f"shortcut:{key}",
        tenant_id=tenant.tenant_id,
    )

    write_audit(
        tenant,
        operator,
        "shortcut.generated",
        f"{profile['shortcut_name']} over {days} days "
        f"({evidence['sensor_count']} sensors).",
    )
    logger.info(
        "Shortcut generated: vertical=%s tenant=%s sensors=%d source=%s",
        key,
        tenant.tenant_id,
        evidence["sensor_count"],
        source,
    )

    return {
        "status": "SHORTCUT_GENERATED",
        "vertical": key,
        "industry": profile["name"],
        "shortcut_name": profile["shortcut_name"],
        "description": profile["shortcut_description"],
        "period_days": days,
        "document": document,
        "document_source": source,
        "evidence": evidence,
        "generated_at": iso(utc_now()),
    }
