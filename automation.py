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

from fastapi import APIRouter, Depends, Query

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
from voice_dispatch import build_voice_script

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
            prompt, fallback, purpose="compliance summary"
        )
        report["executive_summary"] = summary
        report["summary_source"] = source

    return report


@router.post("/sweep")
def autopilot_sweep(
    auto_escalate: bool = Query(
        True, description="Place voice calls for incidents past the grace window."
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """Run one unattended operations pass over the tenant's estate.

    Intended to be called on a schedule (Cloud Scheduler hitting this
    endpoint every few minutes). Reports offline sensors and answers
    incidents that have gone unacknowledged.
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

        script, source = build_voice_script(incident, tenant)
        incident.voice_escalated_at = utc_now()
        incident.voice_script = script
        incident.voice_dispatch_source = source
        escalated += 1

        logger.critical(
            "Autopilot voice escalation: incident=%s sensor=%s tenant=%s",
            incident.incident_id,
            incident.sensor_id,
            tenant.tenant_id,
        )

        actions.append(
            {
                "action": "voice_escalation_dispatched",
                "incident_id": incident.incident_id,
                "sensor_id": incident.sensor_id,
                "call_to": tenant.contact_phone,
                "minutes_unacknowledged": incident.minutes_open(now),
                "voice_script": script,
                "voice_dispatch_source": source,
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
