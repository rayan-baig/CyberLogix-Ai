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

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from store import STORE, Tenant, User


def _reject_inactive(tenant: Tenant) -> Tenant:
    """Distinguish a billing lapse from a bad credential."""
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


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(None, 1)[1].strip()


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
        return _reject_inactive(tenant)

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
    return _reject_inactive(tenant)


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
) -> None:
    """Record who did something, whether a person or a machine."""
    STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=actor_label(user, fallback_actor),
        actor_role=user.role if user else "machine",
        action=action,
        detail=detail,
    )
