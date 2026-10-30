"""One person, several businesses.

An owner with a restaurant and a pharmacy, or a manager looking after
three shops that are separate companies, used to need a separate login
for each: a different email address per business, and signing out and in
again to look from one to the other. Here they sign in once and switch.

How it stays safe. Each business still has its own record for the person
(see `User.linked_to` in store.py), with its own role, so being an owner
at one business makes nobody an owner at another, and every existing
permission check keeps asking the same question it always did. Switching
does not widen a session: it ends the session for one business and starts
one for the other, as the record that person holds there. The businesses
themselves never share anything. The only thing one business's page ever
learns about another is what this file's overview shows the person who
has access to both — the name, their role, and whether anything needs
them there.

Adding a business starts it on the same trial as the front door, under
the same terms, so this is not a back way round sign-up.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role, require_session
from industries import INDUSTRY_PROFILES
from store import STORE, LoginSession, User, iso

logger = logging.getLogger("cyberlogix.businesses")

router = APIRouter(prefix="/api/accounts/businesses", tags=["Several Businesses"])


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Every business added here is a new trial. A ceiling keeps one person from
# turning the button into a trial dispenser; a real group with more than
# this is a conversation with sales, not a form.
MAX_BUSINESSES_PER_PERSON = _env_int("CYBERLOGIX_MAX_BUSINESSES", 10)


class NewBusiness(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str = Field(..., min_length=1, max_length=200)
    industry_vertical: Optional[str] = Field(
        None, description="Which sector, so it opens on the right limits."
    )
    contact_phone: Optional[str] = Field(
        None, min_length=5, max_length=40,
        description="Defaults to the phone of the business you are in now.",
    )


def _signed_in(session: LoginSession = Depends(require_session)) -> User:
    """The person behind the session, whichever business it is open on.

    Deliberately not `require_operator`: that refuses a session on a
    suspended business, and somebody whose one business was suspended
    must still be able to see their others and move to one.
    """
    user = STORE.get_user(session.user_id)
    if user is None or not STORE.person_active(user):
        STORE.revoke_session(session.token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account is no longer active.",
        )
    return user


def _summary(record: User, current_tenant_id: str) -> Optional[dict]:
    """One line of the overview: a business, and whether it needs you."""
    from auth import grace_days_left, licence_state

    tenant = STORE.get_tenant(record.tenant_id)
    if tenant is None:
        return None
    if record.disabled:
        # A business that has removed this person is still listed, so they
        # can see why it vanished from their switcher, but nothing about how
        # it is doing: they used to keep seeing its alerts and sensors live.
        return {
            "tenant_id": tenant.tenant_id,
            "company_name": tenant.company_name,
            "industry": None, "role": None, "current": False,
            "can_open": False, "removed": True, "licence": None,
            "grace_days_left": None, "sensors": 0, "sensors_offline": 0,
            "open_alerts": 0, "needs_attention": False,
        }
    sensors = STORE.sensors_for(tenant.tenant_id)
    offline = sum(1 for s in sensors if s.offline())
    alerts = sum(1 for i in STORE.incidents_for(tenant.tenant_id) if i.unresolved)
    state = licence_state(tenant)
    sector = INDUSTRY_PROFILES.get(tenant.industry_vertical or "", {})
    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "industry": sector.get("name"),
        "role": record.role,
        "current": tenant.tenant_id == current_tenant_id,
        "can_open": state != "suspended",
        "removed": False,
        "licence": state,
        "grace_days_left": grace_days_left(tenant),
        "sensors": len(sensors),
        "sensors_offline": offline,
        "open_alerts": alerts,
        "needs_attention": bool(alerts or offline or state in ("grace", "lapsed")),
    }


@router.get("")
def my_businesses(
    user: User = Depends(_signed_in),
):
    """Every business this person can open, with what needs them in each."""
    person = STORE.person_of(user)
    rows: List[dict] = [
        row for row in (
            _summary(record, user.tenant_id) for record in STORE.logins_of(user)
        ) if row is not None
    ]
    return {
        "person": {"full_name": person.full_name, "email": person.email},
        "current_tenant_id": user.tenant_id,
        "count": len(rows),
        "businesses": rows,
        "totals": {
            "open_alerts": sum(r["open_alerts"] for r in rows),
            "sensors": sum(r["sensors"] for r in rows),
            "sensors_offline": sum(r["sensors_offline"] for r in rows),
            "needing_attention": sum(1 for r in rows if r["needs_attention"]),
        },
        "can_add": (
            user.role == "owner" and len(rows) < MAX_BUSINESSES_PER_PERSON
        ),
        "max_businesses": MAX_BUSINESSES_PER_PERSON,
    }


def _open(record: User, previous: User, session: LoginSession) -> dict:
    """End the session on one business and start one on another."""
    tenant = STORE.get_tenant(record.tenant_id)
    # No later than the session it replaces: switching used to start a fresh
    # twelve hours, so a stolen session could be kept alive for ever by
    # switching back and forth.
    new_session = STORE.start_session(record, not_after=session.expires_at)
    STORE.revoke_session(session.token)
    if previous.tenant_id != record.tenant_id:
        came_from = STORE.get_tenant(previous.tenant_id)
        STORE.record_audit(
            tenant_id=record.tenant_id,
            actor=f"{record.full_name} <{record.email}>",
            actor_role=record.role,
            action="account.switched_in",
            # Which of their other businesses they came from is theirs to
            # know, not this business's: the entry says only that they did.
            detail="Switched in from another of their businesses.",
        )
        logger.info(
            "Business switch: %s from %s to %s",
            record.email, came_from.tenant_id if came_from else "?",
            record.tenant_id,
        )
    return {
        "token": new_session.token,
        "expires_at": iso(new_session.expires_at),
        "user": record.public(),
        "tenant": tenant.public(sensor_count=STORE.seat_count(tenant.tenant_id)),
    }


@router.post("/{tenant_id}/switch")
def switch_business(
    tenant_id: str,
    session: LoginSession = Depends(require_session),
    user: User = Depends(_signed_in),
):
    """Open another of your businesses. Your current session ends."""
    from auth import licence_state

    record = STORE.login_in(user, tenant_id)
    # The same answer whether the business does not exist or is simply not
    # one of yours, so this cannot be used to discover other customers.
    if record is None or record.disabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That is not one of your businesses.",
        )
    tenant = STORE.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That is not one of your businesses.",
        )
    if licence_state(tenant) == "suspended":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"The licence for {tenant.company_name} is suspended.",
        )
    return _open(record, user, session)


@router.post("", status_code=status.HTTP_201_CREATED)
def add_business(
    payload: NewBusiness,
    request: Request,
    session: LoginSession = Depends(require_session),
    user: User = Depends(require_role("owner")),
):
    """Start another business under your sign-in, on the usual trial.

    Owners only: adding a business is an owner's decision, and an operator
    at somebody else's company should not be starting trials from it. You
    become the new business's owner and land in it.
    """
    import signup

    if len(STORE.logins_of(user)) >= MAX_BUSINESSES_PER_PERSON:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have {MAX_BUSINESSES_PER_PERSON} businesses. "
                "Talk to us about a group account for more."
            ),
        )
    signup.check_rate(request)

    vertical = (payload.industry_vertical or "").strip().lower()
    if vertical and vertical not in INDUSTRY_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.industry_vertical}' is not a sector we cover. "
                f"Choose one of {sorted(INDUSTRY_PROFILES)}."
            ),
        )

    person = STORE.person_of(user)
    here = STORE.get_tenant(user.tenant_id)
    tenant = STORE.create_tenant(
        company_name=payload.company_name,
        contact_name=person.full_name,
        contact_phone=payload.contact_phone or (here.contact_phone if here else ""),
        contact_email=person.email,
        plan=signup.PUBLIC_PLAN,
        industry_vertical=vertical or None,
    )
    try:
        record = STORE.link_user(person, tenant.tenant_id, "owner")
    except Exception:
        logger.exception("Adding a business failed after the tenant was made; undoing.")
        STORE.delete_tenant(tenant.tenant_id)
        raise

    signup.record_acceptance(tenant, record)
    STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=f"{record.full_name} <{record.email}>",
        actor_role="owner",
        action="account.business_added",
        detail=f"{tenant.company_name} added by an existing CyberLogix customer.",
    )
    signup.send_welcome(tenant)

    opened = _open(record, user, session)
    opened.update({
        "message": f"{tenant.company_name} is added, and open.",
        # Shown once, as sign-up does: a sensor cannot report without it.
        "api_key": tenant.api_key,
    })
    return opened


class JoinRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=8, max_length=120)


class JoinCodeRequest(BaseModel):
    role: str = Field("operator", description="The role the person will have here.")


@router.post("/join-codes", status_code=status.HTTP_201_CREATED)
def make_join_code(
    payload: JoinCodeRequest,
    owner: User = Depends(require_role("owner")),
):
    """A one-time code for somebody who already has a CyberLogix sign-in.

    Give it to them yourself; they enter it from their own account, and this
    business is added to the ones they can switch between. It works once and
    expires in three days. Owners only.
    """
    from accounts import _validate_role

    role = _validate_role(payload.role)
    code = STORE.issue_join_code(owner, role)
    STORE.record_audit(
        tenant_id=owner.tenant_id,
        actor=f"{owner.full_name} <{owner.email}>",
        actor_role=owner.role,
        action="account.join_code_issued",
        detail=f"Made a one-time join code for a new {role}.",
    )
    return {
        "code": code,
        "role": role,
        "expires_in_hours": 72,
        "message": "Shown once. It works once and expires in three days.",
    }


@router.post("/join")
def join_business(
    payload: JoinRequest,
    user: User = Depends(_signed_in),
):
    """Add a business to your sign-in with the code its owner gave you."""
    from auth import licence_state

    code = STORE.redeem_join_code(payload.code)
    tenant = STORE.get_tenant(code.tenant_id) if code else None
    if code is None or tenant is None or licence_state(tenant) == "suspended":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That code is not valid. Ask for a new one.",
        )
    if STORE.login_in(user, code.tenant_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have access to that business.",
        )
    if len(STORE.logins_of(user)) >= MAX_BUSINESSES_PER_PERSON:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have {MAX_BUSINESSES_PER_PERSON} businesses.",
        )
    record = STORE.link_user(user, code.tenant_id, code.role)
    STORE.record_audit(
        tenant_id=code.tenant_id,
        actor=f"{record.full_name} <{record.email}>",
        actor_role=record.role,
        action="account.joined",
        detail=f"Joined as {record.role} with a join code.",
    )
    return {
        "message": f"{tenant.company_name} is added. Switch to it from your businesses.",
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "role": record.role,
    }
