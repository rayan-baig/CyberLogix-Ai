"""Outbound alerts into the tools a team already has open.

Most operations teams do not live in another vendor's console. They live
in a Slack channel, a Teams channel, or a PagerDuty rotation. An alert
that needs somebody to log in and look at a dashboard is an alert that
waits, so a breach is pushed to wherever the team already is.

Delivery here follows the same rule as SMS and voice: it never raises. A
Slack outage must not take down the breach handler, so a failed post is
recorded on the hook and the incident continues.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import (
    STORE,
    WEBHOOK_KINDS,
    AlertWebhook,
    Tenant,
    User,
    format_temperature,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["Outbound Alert Webhooks"])

# A hook is a courtesy copy, not the alert itself. It gets a short leash so
# a hanging endpoint cannot stall the breach path behind it.
WEBHOOK_TIMEOUT_SECONDS = 5

PAGERDUTY_ENQUEUE_URL = "https://events.pagerduty.com/v2/enqueue"

# Slack, Teams and PagerDuty all publish over HTTPS. Allowing http:// would
# put a webhook URL — which is itself the credential — on the wire in clear.
ALLOWED_SCHEMES = ("https",)

# A hook URL is chosen by the customer and posted to by our server, which
# is a request forgery primitive: pointed at a metadata service or an
# internal admin port it becomes a way to probe our own network from the
# outside. Private destinations are refused unless a self-hosted
# deployment explicitly opts in for a receiver on its own LAN.
ALLOW_PRIVATE_WEBHOOK_TARGETS = (
    os.environ.get("CYBERLOGIX_ALLOW_PRIVATE_WEBHOOKS", "").strip().lower()
    in ("1", "true", "yes")
)


class WebhookCreate(BaseModel):
    kind: str = Field(..., description=f"One of: {', '.join(WEBHOOK_KINDS)}")
    target: str = Field(
        ...,
        min_length=8,
        max_length=500,
        description=(
            "The incoming-webhook URL, or for PagerDuty the Events API v2 "
            "routing key. Stored as a credential and never returned whole."
        ),
    )
    label: str = Field("", max_length=120)
    site_id: Optional[str] = Field(
        None, description="Restrict to one site. Empty covers the whole estate."
    )


class WebhookUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=120)
    active: Optional[bool] = None
    target: Optional[str] = Field(None, min_length=8, max_length=500)


# ==============================================================================
#  Payload shaping
# ==============================================================================


def _severity_colour(state: str) -> str:
    return {"opened": "D93F0B", "escalated": "B60205"}.get(state, "2EA44F")


def build_payload(
    hook: AlertWebhook, event: Dict[str, Any]
) -> tuple[str, Dict[str, Any]]:
    """Shape one event for one provider, returning (url, json body).

    Each provider wants a different envelope, and getting it wrong means a
    silent 400 rather than a visible failure, so the shaping lives in one
    place with the rest of them.
    """
    state = event["state"]
    title = event["title"]
    body = event["detail"]

    if hook.kind == "pagerduty":
        return PAGERDUTY_ENQUEUE_URL, {
            "routing_key": hook.target,
            # Same key for open and resolve, so PagerDuty closes the alert
            # it opened instead of leaving an orphan on the rotation.
            "dedup_key": event["incident_id"],
            "event_action": (
                "resolve" if state in ("acknowledged", "resolved") else "trigger"
            ),
            "payload": {
                "summary": f"{title} — {body}",
                "severity": "critical" if state == "opened" else "warning",
                "source": event["sensor_id"],
                "component": event.get("location") or "unspecified",
                "group": event.get("site") or event["company"],
                "class": event["industry"],
                "custom_details": event,
            },
            "links": [],
        }

    if hook.kind == "teams":
        return hook.target, {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "themeColor": _severity_colour(state),
            "title": title,
            "text": body,
            "sections": [
                {
                    "facts": [
                        {"name": "Sensor", "value": event["sensor_id"]},
                        {"name": "Location", "value": event.get("location") or "—"},
                        {"name": "Reading", "value": event["reading"]},
                        {"name": "Incident", "value": event["incident_id"]},
                    ]
                }
            ],
        }

    if hook.kind == "slack":
        return hook.target, {
            "text": f"*{title}*\n{body}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{title}*\n{body}"},
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"`{event['sensor_id']}` · "
                                f"{event.get('location') or 'unplaced'} · "
                                f"{event['reading']} · {event['incident_id']}"
                            ),
                        }
                    ],
                },
            ],
        }

    # generic: the whole event, for anyone wiring their own receiver.
    return hook.target, event


def build_event(
    tenant: Tenant, incident, sensor, state: str, note: str = ""
) -> Dict[str, Any]:
    """One neutral description of what happened, before provider shaping."""
    unit = tenant.temperature_unit
    site = STORE.get_site(sensor.site_id) if sensor and sensor.site_id else None
    reading = format_temperature(incident.temperature_fahrenheit, unit)
    headline = {
        "opened": f"CRITICAL: {incident.catastrophe}",
        "escalated": f"UNACKNOWLEDGED: {incident.catastrophe}",
        "acknowledged": f"Acknowledged: {incident.catastrophe}",
        "resolved": f"Resolved: {incident.catastrophe}",
    }[state]

    return {
        "event": f"incident.{state}",
        "state": state,
        "title": f"{headline} — {tenant.company_name}",
        "detail": note or incident.breach_details,
        "company": tenant.company_name,
        "tenant_id": tenant.tenant_id,
        "incident_id": incident.incident_id,
        "sensor_id": incident.sensor_id,
        "industry": incident.industry_vertical,
        "location": sensor.location_name if sensor else None,
        "site": site.name if site else None,
        "site_id": site.site_id if site else None,
        "reading": reading,
        "temperature_unit": unit,
        "breach_details": incident.breach_details,
        "minutes_open": incident.minutes_open(),
        "opened_at": iso(incident.opened_at),
        "occurred_at": iso(utc_now()),
    }


# ==============================================================================
#  Delivery
# ==============================================================================


def _resolves_privately(host: str) -> bool:
    """True when a hostname points anywhere inside our own network."""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        # Unresolvable is not private; the post will simply fail below.
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            return True
    return False


def _post(url: str, body: Dict[str, Any]) -> tuple[bool, str]:
    """POST JSON, returning (delivered, status). Never raises."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False, "refused_insecure_url"
    if not ALLOW_PRIVATE_WEBHOOK_TARGETS and _resolves_privately(parts.hostname):
        logger.warning("Refused a webhook post to a private address (%s).", url)
        return False, "refused_private_address"

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CyberLogix-AI/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=WEBHOOK_TIMEOUT_SECONDS
        ) as response:
            return True, f"http_{response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001 - a hook outage must not kill the alert
        logger.warning("Webhook post failed (%s).", exc)
        return False, "unreachable"


def dispatch_event(
    tenant: Tenant, incident, sensor, state: str, note: str = ""
) -> List[Dict[str, Any]]:
    """Push one incident event to every hook covering it.

    Returns a delivery record per hook. Failures are recorded, not raised:
    the SMS and the phone call are the alert, and a Slack outage must not
    stop either of them.
    """
    hooks = STORE.webhooks_for_site(
        tenant.tenant_id, sensor.site_id if sensor else None
    )
    if not hooks:
        return []

    event = build_event(tenant, incident, sensor, state, note)
    results = []
    for hook in hooks:
        url, body = build_payload(hook, event)
        delivered, http_status = _post(url, body)
        STORE.record_webhook_attempt(hook, delivered, http_status)
        results.append(
            {
                "webhook_id": hook.webhook_id,
                "kind": hook.kind,
                "label": hook.label,
                "delivered": delivered,
                "status": http_status,
            }
        )
    return results


# ==============================================================================
#  Management API
# ==============================================================================


def _load(webhook_id: str, tenant: Tenant) -> AlertWebhook:
    hook = STORE.get_webhook(webhook_id)
    if hook is None or hook.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook '{webhook_id}' not found for this tenant.",
        )
    return hook


def _validate_target(kind: str, target: str) -> str:
    target = (target or "").strip()
    if kind == "pagerduty":
        # A routing key, not a URL: PagerDuty's endpoint is fixed.
        if len(target) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A PagerDuty routing key is required, not a URL.",
            )
        return target
    if not target.lower().startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The webhook URL must be https. The URL is itself the "
                "credential, so it cannot be sent in clear."
            ),
        )
    return target


@router.get("")
def list_webhooks(tenant: Tenant = Depends(require_tenant)):
    """Every hook, with how its last post went. Targets are masked."""
    hooks = STORE.webhooks_for(tenant.tenant_id)
    return {
        "count": len(hooks),
        "failing": [h.webhook_id for h in hooks if h.consecutive_failures >= 3],
        "supported_kinds": list(WEBHOOK_KINDS),
        "webhooks": [h.public() for h in hooks],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: WebhookCreate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Point breaches at a channel or a rotation."""
    kind = (payload.kind or "").strip().lower()
    if kind not in WEBHOOK_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kind must be one of {list(WEBHOOK_KINDS)}.",
        )

    site_id = (payload.site_id or "").strip() or None
    if site_id is not None:
        site = STORE.get_site(site_id)
        if site is None or site.tenant_id != tenant.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Site '{site_id}' not found for this tenant.",
            )

    hook = STORE.add_webhook(
        tenant_id=tenant.tenant_id,
        kind=kind,
        target=_validate_target(kind, payload.target),
        label=payload.label,
        site_id=site_id,
    )
    write_audit(
        tenant, operator, "webhook.added",
        f"{kind} hook {hook.masked_target()} added"
        + (f" for site {site_id}." if site_id else " for the whole estate."),
    )
    return hook.public()


@router.patch("/{webhook_id}")
def update_webhook(
    webhook_id: str,
    payload: WebhookUpdate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Rename, pause, or re-point a hook. Omitted fields are left alone."""
    hook = _load(webhook_id, tenant)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update."
        )

    if "target" in changes:
        changes["target"] = _validate_target(hook.kind, changes["target"])
        # A new target is a new credential; its old failures say nothing
        # about whether this one works.
        hook.consecutive_failures = 0
        hook.last_status = None

    for field, value in changes.items():
        setattr(hook, field, value)
    STORE.save_webhook(hook)

    write_audit(
        tenant, operator, "webhook.updated",
        f"{hook.kind} hook {hook.masked_target()}: {', '.join(sorted(changes))}.",
    )
    return hook.public()


@router.delete("/{webhook_id}")
def remove_webhook(
    webhook_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Remove a hook."""
    hook = _load(webhook_id, tenant)
    STORE.remove_webhook(webhook_id)
    write_audit(
        tenant, operator, "webhook.removed",
        f"{hook.kind} hook {hook.masked_target()} removed.",
    )
    return {
        "message": f"{hook.kind} webhook removed.",
        "remaining": len(STORE.webhooks_for(tenant.tenant_id)),
    }


@router.post("/{webhook_id}/test")
def test_webhook(
    webhook_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Post a harmless test message.

    Worth doing at setup: a hook nobody has fired is a hook that turns out
    to be wrong on the night it matters.
    """
    hook = _load(webhook_id, tenant)
    event = {
        "event": "webhook.test",
        "state": "opened",
        "title": f"CyberLogix AI test — {tenant.company_name}",
        "detail": (
            "This is a test. If you can read it, real breaches will arrive "
            "here too."
        ),
        "company": tenant.company_name,
        "tenant_id": tenant.tenant_id,
        "incident_id": "TEST",
        "sensor_id": "TEST",
        "industry": "test",
        "location": None,
        "site": None,
        "reading": "—",
        "temperature_unit": tenant.temperature_unit,
        "occurred_at": iso(utc_now()),
    }
    url, body = build_payload(hook, event)
    delivered, http_status = _post(url, body)
    if hook.kind == "pagerduty" and delivered:
        # The test fired a real trigger, so close it again rather than
        # leaving an orphan sitting on somebody's rotation.
        _post(url, {**body, "event_action": "resolve"})
    STORE.record_webhook_attempt(hook, delivered, http_status)
    write_audit(
        tenant, operator, "webhook.tested",
        f"{hook.kind} hook {hook.masked_target()}: {http_status}.",
    )
    return {
        "webhook_id": hook.webhook_id,
        "kind": hook.kind,
        "delivered": delivered,
        "status": http_status,
        "message": (
            "Check the channel."
            if delivered
            else "The post did not land. Check the URL or routing key."
        ),
    }
