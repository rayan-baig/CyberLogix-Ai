"""Autonomous compliance clerk and operations autopilot.

Two jobs a human would otherwise do by hand. The clerk assembles the
temperature-log paperwork that health, pharmaceutical and cold-chain
inspectors ask for. The autopilot sweep is the unattended watchdog: it
finds sensors that have gone quiet and incidents nobody has answered, and
escalates them without waiting to be asked.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from gemini import safe_generate
from licenses import require_tenant
from store import (
    INDUSTRY_PROFILES,
    SENSOR_OFFLINE_AFTER_MINUTES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Tenant,
    iso,
    utc_now,
)
from voice_dispatch import dispatch_voice_call

logger = logging.getLogger("cyberlogix.autopilot")

router = APIRouter(prefix="/api/autopilot", tags=["Autonomous Compliance Clerk"])


def _sensor_compliance(sensor, since) -> Dict[str, Any]:
    """Compliance statistics for one sensor over the reporting period."""
    readings = STORE.readings_for(sensor.sensor_id, since=since)
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    total = len(readings)
    breaches = sum(1 for r in readings if r.breached)
    in_band = total - breaches

    temps = [r.temperature_fahrenheit for r in readings]

    return {
        "sensor_id": sensor.sensor_id,
        "location_name": sensor.location_name,
        "industry_name": profile["name"],
        "readings_logged": total,
        "readings_in_band": in_band,
        "readings_breached": breaches,
        "compliance_percent": round(in_band / total * 100, 2) if total else None,
        "min_temperature": min(temps) if temps else None,
        "max_temperature": max(temps) if temps else None,
        "mean_temperature": round(sum(temps) / len(temps), 2) if temps else None,
        "last_seen": iso(sensor.last_seen),
        "currently_online": not sensor.offline(),
        "compliant": breaches == 0 and total > 0,
    }


@router.get("/compliance")
def compliance_report(
    days: int = Query(7, ge=1, le=90, description="Reporting period in days."),
    narrate: bool = Query(
        False, description="Have Gemini write the executive summary paragraph."
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """Assemble the inspector-ready temperature log for the period."""
    since = utc_now() - timedelta(days=days)
    sensors = STORE.sensors_for(tenant.tenant_id)
    per_sensor = [_sensor_compliance(s, since) for s in sensors]

    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    total_readings = sum(row["readings_logged"] for row in per_sensor)
    total_breached = sum(row["readings_breached"] for row in per_sensor)
    non_compliant = [row for row in per_sensor if row["readings_breached"] > 0]

    resolved = [i for i in incidents if i.resolved_at is not None]
    mean_response = (
        round(sum(i.minutes_open() for i in resolved) / len(resolved), 2)
        if resolved
        else None
    )

    overall = (
        round((total_readings - total_breached) / total_readings * 100, 2)
        if total_readings
        else None
    )

    report: Dict[str, Any] = {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "period_days": days,
        "period_start": iso(since),
        "period_end": iso(utc_now()),
        "sensors_monitored": len(sensors),
        "total_readings_logged": total_readings,
        "total_readings_breached": total_breached,
        "overall_compliance_percent": overall,
        "incidents_opened": len(incidents),
        "incidents_resolved": len(resolved),
        "incidents_still_open": sum(1 for i in incidents if i.open),
        "mean_response_minutes": mean_response,
        "non_compliant_sensors": len(non_compliant),
        "per_sensor": per_sensor,
        "attestation": (
            "Generated automatically by the CyberLogix AI Autonomous Compliance "
            "Clerk from continuous sensor telemetry. Figures cover only the "
            "period stated above."
        ),
    }

    if narrate:
        fallback = (
            f"{tenant.company_name} monitored {len(sensors)} sensors over "
            f"{days} days, logging {total_readings} readings at "
            f"{overall if overall is not None else 'n/a'}% compliance with "
            f"{len(incidents)} incidents opened and {len(resolved)} resolved."
        )
        prompt = f"""
        You are the CyberLogix AI Autonomous Compliance Clerk writing the
        executive summary that opens a temperature compliance report submitted
        to a regulatory inspector.

        Company: {tenant.company_name}
        Reporting period: {days} days
        Sensors monitored: {len(sensors)}
        Total readings logged: {total_readings}
        Readings outside safe band: {total_breached}
        Overall compliance: {overall}%
        Incidents opened: {len(incidents)}
        Incidents resolved: {len(resolved)}
        Mean response time to resolution: {mean_response} minutes
        Sensors with at least one excursion: {len(non_compliant)}

        Write a factual 3-to-4 sentence executive summary. State the compliance
        posture plainly, name how excursions were detected and answered, and do
        not overstate or editorialise. If compliance was imperfect, say so
        directly. No markdown, no bullet points. Return only the summary.
        """
        summary, source = safe_generate(
            prompt, fallback, purpose="compliance summary",
            tenant_id=tenant.tenant_id,
        )
        report["executive_summary"] = summary
        report["summary_source"] = source

    return report


@router.get("/compliance.csv")
def compliance_csv(
    days: int = Query(7, ge=1, le=90),
    tenant: Tenant = Depends(require_tenant),
):
    """The same report as a spreadsheet, because inspectors want a file.

    One row per sensor plus a totals row, so it can be attached to an audit
    pack or opened in Excel without anyone re-typing figures.
    """
    since = utc_now() - timedelta(days=days)
    sensors = STORE.sensors_for(tenant.tenant_id)
    rows = [_sensor_compliance(s, since) for s in sensors]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Sensor",
            "Location",
            "Industry",
            "Readings logged",
            "Readings in band",
            "Excursions",
            "Compliance %",
            "Min °F",
            "Mean °F",
            "Max °F",
            "Last seen (UTC)",
            "Online",
            "Compliant",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["sensor_id"],
                row["location_name"],
                row["industry_name"],
                row["readings_logged"],
                row["readings_in_band"],
                row["readings_breached"],
                "" if row["compliance_percent"] is None else row["compliance_percent"],
                "" if row["min_temperature"] is None else row["min_temperature"],
                "" if row["mean_temperature"] is None else row["mean_temperature"],
                "" if row["max_temperature"] is None else row["max_temperature"],
                row["last_seen"] or "",
                "yes" if row["currently_online"] else "no",
                "yes" if row["compliant"] else "no",
            ]
        )

    logged = sum(r["readings_logged"] for r in rows)
    breached = sum(r["readings_breached"] for r in rows)
    writer.writerow([])
    writer.writerow(
        [
            "TOTAL",
            tenant.company_name,
            f"{days} days to {iso(utc_now())}",
            logged,
            logged - breached,
            breached,
            round((logged - breached) / logged * 100, 2) if logged else "",
        ]
    )

    buffer.seek(0)
    filename = (
        f"cyberlogix-compliance-{tenant.tenant_id}-"
        f"{utc_now().strftime('%Y%m%d')}.csv"
    )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def sweep_tenant(tenant: Tenant, auto_escalate: bool = True) -> Dict[str, Any]:
    """One unattended operations pass over a tenant's estate.

    Reports offline sensors and answers incidents that have gone
    unacknowledged. Shared by the endpoint and the in-process scheduler, so
    a sweep run on a timer behaves exactly like one an operator triggered.
    """
    now = utc_now()
    actions: List[Dict[str, Any]] = []

    offline = [s for s in STORE.sensors_for(tenant.tenant_id) if s.offline(now)]
    for sensor in offline:
        actions.append(
            {
                "action": "sensor_offline_flagged",
                "sensor_id": sensor.sensor_id,
                "location_name": sensor.location_name,
                "last_seen": iso(sensor.last_seen),
                "detail": (
                    f"No telemetry for over {SENSOR_OFFLINE_AFTER_MINUTES} "
                    "minutes. A silent sensor cannot warn you: check power and "
                    "gateway connectivity."
                ),
            }
        )

    voice_allowed = tenant.entitlements()["voice_escalation"]
    escalated = 0

    for incident in STORE.open_incidents(tenant.tenant_id):
        if incident.voice_escalated_at is not None:
            continue
        if incident.minutes_open(now) < VOICE_ESCALATION_GRACE_MINUTES:
            continue

        if not auto_escalate:
            actions.append(
                {
                    "action": "voice_escalation_due",
                    "incident_id": incident.incident_id,
                    "sensor_id": incident.sensor_id,
                    "minutes_unacknowledged": incident.minutes_open(now),
                    "detail": "Escalation withheld: auto_escalate is disabled.",
                }
            )
            continue

        if not voice_allowed:
            actions.append(
                {
                    "action": "voice_escalation_blocked",
                    "incident_id": incident.incident_id,
                    "sensor_id": incident.sensor_id,
                    "minutes_unacknowledged": incident.minutes_open(now),
                    "detail": (
                        f"The {tenant.entitlements()['name']} plan does not "
                        "include voice escalation. Upgrade to unlock it."
                    ),
                }
            )
            continue

        outcome = dispatch_voice_call(incident, tenant)
        escalated += 1

        actions.append(
            {
                "action": "voice_escalation_dispatched",
                "incident_id": incident.incident_id,
                "sensor_id": incident.sensor_id,
                "call_to": (
                    outcome["delivery"]["to"] if outcome["delivery"] else None
                ),
                "minutes_unacknowledged": incident.minutes_open(now),
                "voice_script": outcome["script"],
                "voice_dispatch_source": outcome["source"],
                "voice_delivery": outcome["delivery"],
            }
        )

    return {
        "status": "sweep_complete",
        "swept_at": iso(now),
        "tenant_id": tenant.tenant_id,
        "sensors_checked": STORE.seat_count(tenant.tenant_id),
        "sensors_offline": len(offline),
        "open_incidents": len(STORE.open_incidents(tenant.tenant_id)),
        "voice_calls_placed": escalated,
        "actions_taken": len(actions),
        "actions": actions,
    }


@router.post("/sweep")
def autopilot_sweep(
    auto_escalate: bool = Query(
        True, description="Place voice calls for incidents past the grace window."
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """Run one sweep now, on demand.

    The same pass the built-in scheduler runs on a timer; exposed so an
    operator can force one, and so an external scheduler can drive it
    instead.
    """
    return sweep_tenant(tenant, auto_escalate)
