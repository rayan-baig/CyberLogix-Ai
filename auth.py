"""Credential resolution shared by every tenant-scoped router.

Two credentials reach the same endpoints:

* a **tenant API key** in `X-CyberLogix-Key` identifies a machine — sensors,
  webhooks, the autopilot scheduler — and carries no human identity;
* a **bearer session token** in `Authorization` identifies a signed-in
  person, and is what the console uses.

`require_tenant` accepts either, so a route does not care which arrived.
`optional_operator` returns the human when there is one, which is how
actions get attributed to a name instead of "Console operator".
"""

from __future__ import annotations

import math
import os
from datetime import timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from store import STORE, Tenant, User, utc_now


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# How long an estate keeps being *watched* after its licence runs out.
#
# Before this, expiry was a cliff: the day after a trial ended, a vaccine
# fridge reporting 75°F got a 402 and nobody was told anything. That is
# the exact failure the product exists to prevent, caused by the product,
# and it directly contradicts the Master Subscription Agreement this same
# codebase generates — which promises in section 5 that monitoring and
# alerting are never withheld for non-payment.
#
# During the grace window the estate is monitored exactly as before.
# What it loses is the reporting: benchmarks, attestations, exports. Those
# are the things whose absence costs money and nothing else, which is the
# same line delinquency draws in contracts.py.
LICENCE_GRACE_DAYS = _env_int("CYBERLOGIX_LICENCE_GRACE_DAYS", 14)


def licence_state(tenant: Tenant, now=None) -> str:
    """"active", "grace", "lapsed" or "suspended"."""
    if tenant.suspended:
        return "suspended"
    if not tenant.expired:
        return "active"
    now = now or utc_now()
    if tenant.expires_at is None:
        return "lapsed"
    if now <= tenant.expires_at + timedelta(days=LICENCE_GRACE_DAYS):
        return "grace"
    return "lapsed"


def grace_days_left(tenant: Tenant, now=None) -> int:
    """Days of monitoring left after expiry, or 0 if it is not in grace.

    Rounded up. Truncating says "9 days" when there are ten days minus a
    few seconds, and the figure goes straight onto a banner telling
    somebody how long they have.
    """
    if licence_state(tenant, now) != "grace":
        return 0
    ends = tenant.expires_at + timedelta(days=LICENCE_GRACE_DAYS)
    return max(0, math.ceil((ends - (now or utc_now())).total_seconds() / 86400))


def _reject_inactive(tenant: Tenant) -> Tenant:
    """Distinguish a billing lapse from a bad credential.

    A lapse inside the grace window is not a refusal: the estate is still
    watched. Past the window it is, and the message says the two things
    somebody in that position needs — that monitoring has stopped, and
    exactly which route restores it, because that route is deliberately
    still open.
    """
    state = licence_state(tenant)
    if state == "suspended":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"License for {tenant.company_name} is suspended.",
        )
    if state == "lapsed":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"License for {tenant.company_name} expired on "
                f"{tenant.expires_at.date()} and the "
                f"{LICENCE_GRACE_DAYS}-day grace period has run out, so this "
                "estate is no longer being monitored. POST /api/licenses/me/"
                "plan to move onto a paid plan — that route still works."
            ),
        )
    return tenant


def require_tenant_any_state(
    x_cyberlogix_key: Optional[str] = Header(
        None, description="Tenant API key issued at onboarding."
    ),
    authorization: Optional[str] = Header(None),
) -> Tenant:
    """Resolve the tenant without checking whether it has paid.

    For the handful of routes that are the way *back*: changing plan,
    signing a contract, settling an invoice, reading and accepting the
    terms, and the console page that carries all of those. Refusing them
    because the account has lapsed is a deadlock — the customer cannot pay
    because they have not paid — and it was a real one: an expired trial
    could not upgrade itself, so the only route from a finished trial to a
    paying customer ran through a human being.

    Suspension is still refused. A lapse is something that happened to a
    customer; a suspension is something somebody decided about them —
    abuse, fraud, or their own request — and undoing it is a conversation
    rather than a card payment.
    """
    tenant = _resolve_tenant(x_cyberlogix_key, authorization)
    if tenant.suspended:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"License for {tenant.company_name} is suspended.",
        )
    return tenant


def _bearer(authorization: Optional[str]) -> Optional[str]:
    """The token out of an Authorization header, or None.

    `split(None, 1)` collapses runs of whitespace, so a header that is
    exactly "Bearer " passes the startswith check and then indexes past
    the end of a one-element list. That is an IndexError on the
    authentication path — an unauthenticated 500 available to anyone on
    every protected endpoint in the application, from six characters and
    a space.

    Returns None for anything that is not a bearer scheme followed by a
    non-empty token, so a malformed header is refused rather than raising.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def optional_operator(
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """The signed-in human, or None when a machine credential was used."""
    token = _bearer(authorization)
    if not token:
        return None

    session = STORE.session_by_token(token)
    if session is None:
        return None

    user = STORE.get_user(session.user_id)
    if user is None or user.disabled:
        return None
    return user


def require_tenant(
    x_cyberlogix_key: Optional[str] = Header(
        None, description="Tenant API key issued at onboarding."
    ),
    authorization: Optional[str] = Header(
        None, description="Bearer <session token> from /api/accounts/login."
    ),
) -> Tenant:
    """Resolve the calling tenant from either credential."""
    return _reject_inactive(_resolve_tenant(x_cyberlogix_key, authorization))


def _resolve_tenant(
    x_cyberlogix_key: Optional[str], authorization: Optional[str]
) -> Tenant:
    """Credential to tenant. Says nothing about whether they have paid."""
    token = _bearer(authorization)
    if token:
        session = STORE.session_by_token(token)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token is invalid or expired. Sign in again.",
            )
        user = STORE.get_user(session.user_id)
        if user is None or user.disabled:
            STORE.revoke_session(session.token)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account is no longer active.",
            )
        tenant = STORE.get_tenant(session.tenant_id)
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tenant."
            )
        return tenant

    if not x_cyberlogix_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Missing credentials. Send X-CyberLogix-Key for machine access "
                "or an Authorization bearer token for an operator."
            ),
        )

    tenant = STORE.tenant_by_key(x_cyberlogix_key)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unrecognised API key.",
        )
    return tenant


class IngestPrincipal:
    """Who is submitting a reading, and what they are allowed to submit.

    `sensor` is set only when a per-sensor key was used, and then that key
    may report for that one asset and nothing else.
    """

    __slots__ = ("tenant", "sensor")

    def __init__(self, tenant: Tenant, sensor=None) -> None:
        self.tenant = tenant
        self.sensor = sensor

    def authorise(self, sensor_id: str) -> None:
        """Refuse a scoped key being pointed at somebody else's asset."""
        if self.sensor is None:
            return
        if (sensor_id or "").strip() != self.sensor.sensor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This sensor key may only report for "
                    f"'{self.sensor.sensor_id}'."
                ),
            )


def require_ingest(
    x_cyberlogix_sensor_key: Optional[str] = Header(
        None,
        description=(
            "Per-sensor ingest key, issued once when the sensor is "
            "registered. Prefer this over the tenant key on the device."
        ),
    ),
    x_cyberlogix_key: Optional[str] = Header(
        None, description="Tenant API key. Speaks for the whole estate."
    ),
    authorization: Optional[str] = Header(None),
) -> IngestPrincipal:
    """Resolve who may write a reading.

    A tenant API key is a master key: it registers assets, retunes alarm
    thresholds and can suspend the licence. Until now it was also the only
    credential a sensor could carry, which put all of that inside a box
    bolted to the wall of a walk-in freezer — reachable by anyone with a
    screwdriver and a serial cable, in a room the public can often walk
    into.

    So a sensor now gets its own key, scoped to itself and able to do one
    thing: report readings. The tenant key still works here, because a
    fleet installer bringing up fifty devices from a script is a real
    thing and breaking it would push people back to the master key by
    another route — but it is no longer what the hardware should hold.
    """
    if x_cyberlogix_sensor_key:
        sensor = STORE.sensor_by_ingest_key(x_cyberlogix_sensor_key.strip())
        if sensor is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unrecognised sensor key.",
            )
        tenant = STORE.get_tenant(sensor.tenant_id)
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tenant."
            )
        return IngestPrincipal(_reject_inactive(tenant), sensor)

    return IngestPrincipal(
        require_tenant(
            x_cyberlogix_key=x_cyberlogix_key, authorization=authorization
        )
    )


def require_role_or_machine(required: str):
    """Assert the caller is a machine, or a person senior enough.

    The licence router was the one place with no role check at all. Every
    other router that changes something asks `require_role`; this one
    asked only `require_tenant`, which is satisfied by any signed-in
    person whatever their role. So a viewer — the read-only role, the
    night manager who is meant to look at the fleet and nothing else —
    could suspend the company's licence, downgrade the plan out of voice
    escalation, decommission a freezer, and, worst of the set, raise a
    vaccine fridge's alarm threshold to 200°F. That last one leaves the
    sensor reporting and the console green while the alarm can never
    fire again: the silence-that-looks-like-safety failure, reachable by
    the least privileged account in the system.

    Roles are checked only when a person is calling. A tenant API key is
    documented as a machine credential — sensors, provisioning scripts,
    the scheduler — and carries no human identity to have a role, so it
    passes through to `require_tenant`, which has already validated it.
    """

    def _dependency(
        authorization: Optional[str] = Header(None),
        user: Optional[User] = Depends(optional_operator),
    ) -> Optional[User]:
        if _bearer(authorization) is None:
            return None  # a machine; require_tenant is the whole check
        if user is None:
            # A bearer token was sent and did not resolve to a live user.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token is invalid or expired. Sign in again.",
            )
        if not user.can(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action needs the '{required}' role; "
                    f"{user.email} is a '{user.role}'."
                ),
            )
        return user

    return _dependency


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


def actor_label(user: Optional[User], fallback: str) -> str:
    """A human-readable actor for an audit entry."""
    if user is not None:
        return f"{user.full_name} <{user.email}>"
    return fallback


def write_audit(
    tenant: Tenant,
    user: Optional[User],
    action: str,
    detail: str,
    fallback_actor: str = "API key",
):
    """Record who did something, whether a person or a machine.

    Returns the entry. Most callers ignore it; the ones that hand a
    reference back to the customer — a recorded acceptance, say — need
    something to point at.
    """
    return STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=actor_label(user, fallback_actor),
        actor_role=user.role if user else "machine",
        action=action,
        detail=detail,
    )
