"""Operator accounts, roles and the audit trail.

Until now the only credential was a tenant API key, so every action was
attributed to "Console operator". A compliance report is only as good as
its provenance, so humans now sign in as themselves and every state change
lands in a durable audit trail with their name against it.

Two credentials coexist deliberately:

* the **tenant API key** identifies a machine — sensors, webhooks, the
  autopilot scheduler — and carries no human identity;
* a **session token** identifies a person, and is what the console uses.

Endpoints that record who did something require the person.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from store import ROLES, STORE, LoginSession, Tenant, User, verify_password

logger = logging.getLogger("cyberlogix.accounts")

router = APIRouter(prefix="/api/accounts", tags=["Operator Accounts"])

MIN_PASSWORD_LENGTH = 10

# Login throttling. A shared in-process window is enough for a single
# instance and costs nothing; a distributed deployment would move this to
# the same store the sessions live in.
MAX_LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300
_login_attempts: Dict[str, List[float]] = defaultdict(list)


def _throttle(key: str) -> None:
    """Reject a caller that has failed too often in the recent window."""
    now = time.monotonic()
    recent = [t for t in _login_attempts[key] if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[key] = recent
    if len(recent) >= MAX_LOGIN_ATTEMPTS:
        oldest = min(recent)
        wait = int(LOGIN_WINDOW_SECONDS - (now - oldest)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-in attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )


def _record_failure(key: str) -> None:
    _login_attempts[key].append(time.monotonic())


def _clear_failures(key: str) -> None:
    _login_attempts.pop(key, None)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)
    role: str = Field("operator", description="owner, operator or viewer")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class RoleChange(BaseModel):
    role: str = Field(..., description="owner, operator or viewer")


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


class ResetRedeem(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


def _validate_role(role: str) -> str:
    key = (role or "").strip().lower()
    if key not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown role. Allowed roles: {list(ROLES)}",
        )
    return key


def require_session(
    authorization: Optional[str] = Header(
        None, description="Bearer <session token> issued by /api/accounts/login."
    ),
) -> LoginSession:
    """Resolve the signed-in operator's session from the Authorization header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Sign in at /api/accounts/login.",
        )

    session = STORE.session_by_token(authorization.split(None, 1)[1].strip())
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is invalid or expired. Sign in again.",
        )
    return session


def require_operator(session: LoginSession = Depends(require_session)) -> User:
    """The signed-in user, rejecting a disabled account or a lapsed license."""
    user = STORE.get_user(session.user_id)
    if user is None or user.disabled:
        STORE.revoke_session(session.token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account is no longer active.",
        )

    tenant = STORE.get_tenant(user.tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tenant."
        )
    if not tenant.active:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"License for {tenant.company_name} is not active.",
        )
    return user


def require_role(required: str):
    """Build a dependency asserting at least `required` privilege."""

    def _dependency(user: User = Depends(require_operator)) -> User:
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


def tenant_of(user: User) -> Tenant:
    tenant = STORE.get_tenant(user.tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found."
        )
    return tenant


def audit(user: User, action: str, detail: str) -> None:
    """Write one durable record of what this operator just did."""
    STORE.record_audit(
        tenant_id=user.tenant_id,
        actor=f"{user.full_name} <{user.email}>",
        actor_role=user.role,
        action=action,
        detail=detail,
    )


# The first user of a tenant is created with the tenant API key (there is no
# operator yet to authorise it); every later one needs an owner.
from auth import require_tenant  # noqa: E402


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap_owner(payload: UserCreate, tenant: Tenant = Depends(require_tenant)):
    """Create a tenant's first operator, using the tenant API key.

    Only works while the tenant has no users; after that, an owner invites
    the rest so a leaked API key cannot mint new humans.
    """
    if STORE.users_for(tenant.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This tenant already has operators. An owner must invite "
                "further users via POST /api/accounts/users."
            ),
        )

    if STORE.user_by_email(str(payload.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered.",
        )

    user = STORE.create_user(
        tenant_id=tenant.tenant_id,
        email=str(payload.email),
        full_name=payload.full_name,
        role="owner",
        password=payload.password,
    )
    STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=f"{user.full_name} <{user.email}>",
        actor_role="owner",
        action="account.bootstrap",
        detail="First owner created with the tenant API key.",
    )
    return user.public()


@router.post("/login")
def login(payload: LoginRequest):
    """Exchange an email and password for a bearer session token."""
    throttle_key = str(payload.email).strip().lower()
    _throttle(throttle_key)

    user = STORE.user_by_email(str(payload.email))

    # Verify against a decoy hash when the user is unknown so a wrong email
    # and a wrong password take the same time to answer.
    stored = user.password_hash if user else "scrypt$00$00"
    correct = verify_password(payload.password, stored)

    if user is None or not correct or user.disabled:
        _record_failure(throttle_key)
        logger.warning("Failed login for %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )

    _clear_failures(throttle_key)

    tenant = STORE.get_tenant(user.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="The license for this account is not active.",
        )

    session = STORE.start_session(user)
    audit(user, "account.login", "Signed in to the console.")
    return {
        "token": session.token,
        "expires_at": session.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": user.public(),
        "tenant": tenant.public(sensor_count=STORE.seat_count(tenant.tenant_id)),
    }


@router.post("/logout")
def logout(session: LoginSession = Depends(require_session)):
    """Revoke the calling session token."""
    user = STORE.get_user(session.user_id)
    if user is not None:
        audit(user, "account.logout", "Signed out.")
    STORE.revoke_session(session.token)
    return {"message": "Signed out."}


@router.get("/me")
def whoami(user: User = Depends(require_operator)):
    """The signed-in operator and their tenant."""
    tenant = tenant_of(user)
    return {
        "user": user.public(),
        "tenant": tenant.public(sensor_count=STORE.seat_count(tenant.tenant_id)),
    }


@router.post("/me/password")
def change_password(
    payload: PasswordChange, user: User = Depends(require_operator)
):
    """Rotate your own password, invalidating other sessions is not implied."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect.",
        )
    STORE.set_user_password(user, payload.new_password)
    audit(user, "account.password_changed", "Changed their own password.")
    return {"message": "Password updated."}


@router.get("/users")
def list_users(user: User = Depends(require_operator)):
    """Everyone with access to this tenant."""
    people = STORE.users_for(user.tenant_id)
    return {"count": len(people), "users": [u.public() for u in people]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def invite_user(payload: UserCreate, owner: User = Depends(require_role("owner"))):
    """Add an operator to this tenant. Owners only."""
    role = _validate_role(payload.role)

    if STORE.user_by_email(str(payload.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered.",
        )

    created = STORE.create_user(
        tenant_id=owner.tenant_id,
        email=str(payload.email),
        full_name=payload.full_name,
        role=role,
        password=payload.password,
    )
    audit(owner, "account.invited", f"Added {created.email} as {role}.")
    return created.public()


@router.post("/users/{user_id}/role")
def change_role(
    user_id: str, payload: RoleChange, owner: User = Depends(require_role("owner"))
):
    """Change an operator's role. An owner cannot demote themselves."""
    role = _validate_role(payload.role)
    target = STORE.get_user(user_id)

    if target is None or target.tenant_id != owner.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such user."
        )

    if target.user_id == owner.user_id and role != "owner":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You cannot remove your own owner role. Promote another owner "
                "first, so the tenant is never left without one."
            ),
        )

    previous = target.role
    STORE.set_user_role(target, role)
    audit(owner, "account.role_changed", f"{target.email}: {previous} -> {role}.")
    return target.public()


@router.post("/users/{user_id}/disable")
def disable_user(user_id: str, owner: User = Depends(require_role("owner"))):
    """Revoke an operator's access immediately."""
    target = STORE.get_user(user_id)

    if target is None or target.tenant_id != owner.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such user."
        )

    if target.user_id == owner.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot disable your own account.",
        )

    STORE.set_user_disabled(target, True)
    audit(owner, "account.disabled", f"Disabled {target.email}.")
    return target.public()


@router.post("/users/{user_id}/reset")
def issue_reset(user_id: str, owner: User = Depends(require_role("owner"))):
    """Issue a one-time password reset for someone who is locked out.

    The token is returned once, for the owner to hand over out of band. Only
    its hash is stored, so a copy of the database cannot replay it.
    """
    target = STORE.get_user(user_id)
    if target is None or target.tenant_id != owner.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such user."
        )

    token = STORE.issue_reset(target)
    audit(owner, "account.reset_issued", f"Issued a password reset for {target.email}.")
    return {
        "reset_token": token,
        "user": target.public(),
        "expires_in_hours": 24,
        "message": (
            "Give this token to the user out of band. It is shown once, works "
            "once, and expires in 24 hours."
        ),
    }


@router.post("/reset")
def redeem_reset(payload: ResetRedeem):
    """Set a new password with a reset token. No sign-in required."""
    user = STORE.redeem_reset(payload.token, payload.new_password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That reset token is invalid, already used, or expired.",
        )

    STORE.record_audit(
        tenant_id=user.tenant_id,
        actor=f"{user.full_name} <{user.email}>",
        actor_role=user.role,
        action="account.password_reset",
        detail="Password set with a reset token; existing sessions revoked.",
    )
    return {
        "message": "Password updated. Sign in with your new password.",
        "user": user.public(),
    }


@router.get("/audit")
def audit_trail(limit: int = 100, user: User = Depends(require_operator)):
    """Who did what, newest first. Durable across restarts."""
    limit = max(1, min(limit, 500))
    entries = STORE.audit_for(user.tenant_id, limit=limit)
    return {"count": len(entries), "entries": [e.public() for e in entries]}
