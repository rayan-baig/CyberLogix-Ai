"""The front door: how a stranger becomes a customer without asking anyone.

Every call to action on the landing page — "Start on Growth", "Open the
console", the plan cards, the closing button — pointed at `/console`,
which is a password field. A prospect who had read the whole page, agreed
with all of it and wanted to pay had no way to do so. The only path into
the product ran through somebody manually POSTing to
`/api/licenses/tenants`, which means the company could only ever have as
many customers as its founder had mornings.

That endpoint was also wide open. Unauthenticated, unthrottled, and it
took the plan as a parameter — so anyone who read the API docs could mint
themselves an unlimited Enterprise licence, for free, in a loop. It could
not be advertised because it could not be defended.

Both halves are fixed here:

* **This module** is the public door. It creates a *trial* — the plan is
  not a field a caller can set — bootstraps the owner, signs them in, and
  hands back everything needed to send a first reading in under a minute.
  It is rate limited per caller and in total.

* **`licenses.py`** keeps the provisioning endpoint for the flows that
  need it, but a paid plan now requires the provisioning key. An
  unconfigured deployment cannot be farmed for free Enterprise accounts.

No demo data is seeded into a real account. Fake sensors in a live estate
either look like the customer's own equipment or start reporting
themselves offline into the compliance log, and a monitoring product whose
first act is a false alarm has taught the wrong lesson. What the response
carries instead is the exact command that puts a *real* reading through
their own estate, so the first thing they see working is the product.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Deque, Dict, List, Optional

from collections import deque

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from accounts import MIN_PASSWORD_LENGTH
from store import INDUSTRY_PROFILES, PLAN_TIERS, STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.signup")

router = APIRouter(prefix="/api/signup", tags=["Sign-up"])

# The public door only ever opens onto a trial. Not a default a caller can
# override — the field does not exist. Moving to a paid plan is a decision
# with a contract attached, and it happens through /api/contracts.
PUBLIC_PLAN = "trial"

# Per-caller and total ceilings, per hour. Generous enough that a real
# person retrying a typo never meets them, low enough that a script cannot
# fill the database overnight.
SIGNUPS_PER_CALLER_PER_HOUR = 3
SIGNUPS_TOTAL_PER_HOUR = 60
RATE_WINDOW_SECONDS = 3600

# Whether to believe X-Forwarded-For. Off by default, and that default is
# the important one: a forwarded header the deployment does not actually
# set is attacker-controlled, so trusting it turns the per-caller limit
# into no limit at all. Behind a load balancer that overwrites the header,
# set this to 1. The total ceiling holds either way, which is why there is
# a total ceiling.
TRUST_PROXY_HEADER = os.environ.get("CYBERLOGIX_TRUST_PROXY_HEADER", "") == "1"

_lock = threading.Lock()
_by_caller: Dict[str, List[float]] = defaultdict(list)
_all: Deque[float] = deque()


def caller_key(request: Request) -> str:
    if TRUST_PROXY_HEADER:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0]
        if forwarded.strip():
            return forwarded.strip()
    client = request.client
    return client.host if client else "unknown"


def _prune(bucket, now: float) -> None:
    while bucket and now - bucket[0] >= RATE_WINDOW_SECONDS:
        bucket.popleft() if isinstance(bucket, deque) else bucket.pop(0)


def check_rate(request: Request) -> None:
    """Refuse a caller, or the world, that is signing up too fast.

    Counted and recorded under one lock. Checking and then recording in two
    steps would let a burst of concurrent requests all read the same count
    and all decide they were under the limit — which is exactly the shape
    of traffic a script produces.
    """
    key = caller_key(request)
    now = time.monotonic()
    with _lock:
        mine = _by_caller[key]
        _prune(mine, now)
        _prune(_all, now)

        if len(mine) >= SIGNUPS_PER_CALLER_PER_HOUR:
            wait = int(RATE_WINDOW_SECONDS - (now - mine[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"{SIGNUPS_PER_CALLER_PER_HOUR} trials have already been "
                    f"started from here in the last hour. Try again in "
                    f"{wait} seconds, or get in touch and we will set the "
                    "account up directly."
                ),
                headers={"Retry-After": str(wait)},
            )
        if len(_all) >= SIGNUPS_TOTAL_PER_HOUR:
            wait = int(RATE_WINDOW_SECONDS - (now - _all[0])) + 1
            logger.error(
                "Global sign-up ceiling hit: %d in the last hour. This is "
                "either very good news or an attack.",
                len(_all),
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Sign-ups are temporarily rate limited. Try again "
                    f"in {wait} seconds."
                ),
                headers={"Retry-After": str(wait)},
            )

        mine.append(now)
        _all.append(now)


def reset_rate_limits() -> None:
    """Empty the windows. For tests, and for a deliberate operator reset."""
    with _lock:
        _by_caller.clear()
        _all.clear()


class SignupRequest(BaseModel):
    """Everything needed to become a customer, and nothing else.

    There is deliberately no `plan` field. A public caller cannot choose
    what they are given.
    """

    company_name: str = Field(..., min_length=1, max_length=160)
    full_name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=40)
    industry_vertical: Optional[str] = Field(
        None, description="Which sector, so the console opens on the right limits."
    )


@router.get("/sectors")
def sectors():
    """What to offer in the sector picker, and the trial's shape."""
    tier = PLAN_TIERS[PUBLIC_PLAN]
    return {
        "plan": PUBLIC_PLAN,
        "plan_name": tier["name"],
        "trial_days": tier["term_days"],
        "max_sensors": tier["max_sensors"],
        "min_password_length": MIN_PASSWORD_LENGTH,
        "sectors": [
            {
                "key": key,
                "name": profile["name"],
                "slogan": profile.get("slogan", ""),
            }
            for key, profile in sorted(
                INDUSTRY_PROFILES.items(), key=lambda kv: kv[1]["name"]
            )
        ],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def start_trial(payload: SignupRequest, request: Request):
    """Create a trial estate, its owner, and a signed-in session.

    One call rather than three. The old sequence — onboard the tenant, then
    bootstrap an owner with the returned API key, then log in — is fine for
    a script and hopeless for a form: a browser that dies between step one
    and step two leaves a tenant nobody can sign into and an API key on the
    floor.
    """
    check_rate(request)

    email = str(payload.email).strip().lower()
    if STORE.user_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That email address already has an account. Sign in at "
                "/console, or reset the password if it has been forgotten."
            ),
        )

    vertical = (payload.industry_vertical or "").strip().lower()
    if vertical and vertical not in INDUSTRY_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.industry_vertical}' is not a sector we cover. "
                f"Choose one of {sorted(INDUSTRY_PROFILES)}."
            ),
        )

    tenant = STORE.create_tenant(
        company_name=payload.company_name.strip(),
        contact_name=payload.full_name.strip(),
        contact_phone=payload.contact_phone.strip(),
        contact_email=email,
        plan=PUBLIC_PLAN,
    )

    try:
        user = STORE.create_user(
            tenant_id=tenant.tenant_id,
            email=email,
            full_name=payload.full_name.strip(),
            role="owner",
            password=payload.password,
        )
        session = STORE.start_session(user)
    except Exception:
        # A tenant with no owner is unreachable: nobody can sign in, and it
        # sits in the fleet being counted forever. Undo it rather than
        # leave one behind on every failed sign-up.
        logger.exception("Sign-up failed after the tenant was created; undoing.")
        STORE.delete_tenant(tenant.tenant_id)
        raise

    STORE.record_audit(
        tenant_id=tenant.tenant_id,
        actor=f"{user.full_name} <{user.email}>",
        actor_role="owner",
        action="account.signed_up",
        detail=(
            f"Self-serve trial started for {tenant.company_name}"
            + (f" ({INDUSTRY_PROFILES[vertical]['name']})." if vertical else ".")
        ),
    )
    logger.info(
        "Trial started: %s (%s) tenant=%s",
        tenant.company_name, email, tenant.tenant_id,
    )

    tier = PLAN_TIERS[PUBLIC_PLAN]
    example_vertical = vertical or "restaurant"
    return {
        "message": (
            f"{tenant.company_name} is live on a "
            f"{tier['term_days']}-day trial. You are signed in."
        ),
        "token": session.token,
        "expires_at": iso(session.expires_at),
        "user": user.public(),
        "tenant": tenant.public(sensor_count=0),
        # Shown once, exactly as the provisioning endpoint does. A sensor
        # cannot report without it and no later endpoint will echo it.
        "api_key": tenant.api_key,
        "next_steps": first_steps(tenant.api_key, example_vertical),
    }


def first_steps(api_key: str, vertical: str) -> Dict[str, Any]:
    """The shortest path from signing up to seeing the product work.

    Real readings on their own estate, not seeded demo sensors. A fake
    freezer in a live account either gets mistaken for a real one or starts
    reporting itself offline into the compliance log — and a monitoring
    product whose first act is a false alarm has taught the wrong lesson
    on day one.
    """
    profile = INDUSTRY_PROFILES[vertical]
    above = profile.get("danger_above")
    below = profile.get("danger_below")

    # A reading safely inside the limits, and one safely outside, whichever
    # side this sector's limit is on. A sector bounded above wants a cold
    # nominal and a hot breach; one bounded below wants the reverse.
    if above is not None and below is not None:
        nominal = round((above + below) / 2, 1)
        breaching = round(above + 20, 1)
    elif above is not None:
        nominal = round(above - 20, 1)
        breaching = round(above + 20, 1)
    elif below is not None:
        nominal = round(below + 20, 1)
        breaching = round(below - 20, 1)
    else:  # pragma: no cover - every profile sets at least one bound
        nominal, breaching = 40.0, 120.0
    return {
        "summary": (
            "Register one sensor, send it a good reading, then send it a "
            "bad one. The second should produce an incident before you have "
            "finished reading this."
        ),
        "steps": [
            {
                "title": "Add somebody to call",
                "detail": (
                    "An alert with nobody on the roster reaches only the "
                    "contact captured at sign-up."
                ),
                "command": (
                    "curl -X POST $HOST/api/contacts "
                    f'-H "X-CyberLogix-Key: {api_key}" '
                    '-H "Content-Type: application/json" '
                    '-d \'{"full_name":"You","phone":"+15550100",'
                    '"role":"owner"}\''
                ),
            },
            {
                "title": "Register a sensor",
                "detail": (
                    f"One {profile['asset_noun']} on the "
                    f"{profile['name']} limits."
                ),
                "command": (
                    "curl -X POST $HOST/api/licenses/me/sensors "
                    f'-H "X-CyberLogix-Key: {api_key}" '
                    '-H "Content-Type: application/json" '
                    f'-d \'{{"sensor_id":"UNIT-1","industry_vertical":"{vertical}",'
                    '"location_name":"Main site"}\''
                ),
            },
            {
                "title": "Send a normal reading",
                "detail": f"{nominal}\u00b0F is inside the limits, so nothing happens.",
                "command": (
                    "curl -X POST $HOST/api/sensor-pulse "
                    f'-H "X-CyberLogix-Key: {api_key}" '
                    '-H "Content-Type: application/json" '
                    f'-d \'{{"sensor_id":"UNIT-1","temperature_fahrenheit":{nominal}}}\''
                ),
            },
            {
                "title": "Now break it",
                "detail": (
                    f"{breaching}\u00b0F is past the limit. This opens an incident "
                    "and texts the roster."
                ),
                "command": (
                    "curl -X POST $HOST/api/sensor-pulse "
                    f'-H "X-CyberLogix-Key: {api_key}" '
                    '-H "Content-Type: application/json" '
                    f'-d \'{{"sensor_id":"UNIT-1","temperature_fahrenheit":{breaching}}}\''
                ),
            },
        ],
        "then": (
            "Open /console to watch it. Texts and calls need Twilio "
            "credentials configured; everything else works without them."
        ),
        "generated_at": iso(utc_now()),
    }
