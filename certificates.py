"""A page a customer shows other people.

Everything else in this application is read by the person who bought it.
This is the one surface aimed at everybody else: the health inspector,
the insurer's adjuster, the auditor, the corporate buyer running a
supplier check, and the diner who scans a code by the door.

The market sells compliance as a cost. Every competitor hands you a CSV
or a PDF -- documents you could have typed yourself, which is why an
adjuster discounts them. This is the same record with the property that
makes it worth something: the readings are hash-chained, the chain head
was attested before today, and the recipient can re-derive the whole
thing at /api/vault/verify without an account and without trusting
either party.

That inversion is the product. A restaurant stops filing paperwork it
resents and starts publishing something that wins it business, and every
page it shares carries the thing that made it checkable.

Three rules this file does not bend.

**Opt in, and revocable.** A compliance record is not ours to publish. It
is off until an owner turns it on, the URL carries an unguessable token
rather than a company id, and revoking it is one call.

**No people on it.** The record proves what was recorded, not who was on
shift. Staff names, contact details and addresses stay out -- a page
naming the kitchen porter who found the door open would be a privacy
problem dressed up as transparency.

**It says what it does not prove.** A chain proves the readings were not
edited after the fact. It says nothing about whether the sensor was
where it claims, or whether anybody unplugged it. Overstating that is
how the whole thing stops being worth anything.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Tenant, User, iso, utc_now

router = APIRouter(prefix="/api/certificate", tags=["Public proof"])

KIND = "certificate"
DEFAULT_WINDOW_DAYS = 30


def _row(tenant_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(KIND, tenant_id)


def by_token(token: str) -> Optional[Dict[str, Any]]:
    """The certificate a public token belongs to, if it is still live."""
    if not token or len(token) < 20:
        return None
    for row in STORE._db.all(KIND):
        # secrets.compare_digest, because this token is the whole
        # credential and a timing oracle over a public endpoint is free
        # to probe.
        if row.get("live") and secrets.compare_digest(
            str(row.get("token", "")), token
        ):
            return row
    return None


def build(tenant: Tenant, days: int = DEFAULT_WINDOW_DAYS) -> Dict[str, Any]:
    """The record itself: what was measured, what went wrong, what was done.

    Assembled from the same functions the private console and the signed
    attestation use, so a public page cannot drift into flattering the
    customer -- there is only one set of numbers.
    """
    from automation import compliance_report
    from vault import estate_attestation

    since = utc_now() - timedelta(days=days)
    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    resolved = [i for i in incidents if i.resolved_at is not None]
    answered = [i for i in incidents if i.corrective_action]
    reviewed = [i for i in incidents if i.corrective_action and i.reviewed_by]

    report = compliance_report(days=days, narrate=False, tenant=tenant)
    attestation = estate_attestation(days=days, tenant=tenant)

    return {
        "company_name": tenant.company_name,
        "period_days": days,
        "period_start": iso(since),
        "issued_at": iso(utc_now()),
        "assets_monitored": report["sensors_monitored"],
        "readings": attestation["readings"],
        "within_band_percent": attestation["within_band_percent"],
        "excursions": len(incidents),
        "excursions_resolved": len(resolved),
        "excursions_with_a_recorded_action": len(answered),
        "excursions_reviewed_by_a_manager": len(reviewed),
        "median_minutes_to_answer": (
            round(sum(i.minutes_open() for i in resolved) / len(resolved), 1)
            if resolved else None
        ),
        # The part that makes it worth more than a PDF.
        "estate_digest": attestation["estate_digest"],
        "signature": attestation["signature"],
        "signing": attestation["signing"],
        "chain": [
            {
                "asset": entry["sensor_id"],
                "readings": entry["readings"],
                "excursions": entry["excursions"],
                "digest": entry["chain_head"],
            }
            for entry in attestation["entries"]
        ],
        "proves": (
            "Every reading in this period is hashed together with the one "
            "before it, so altering any of them changes every digest after "
            "it. The digest below was recorded when it was issued, before "
            "today, which is what makes the comparison mean anything. "
            "Anyone can re-derive it at /api/vault/verify without an "
            "account and without trusting either party."
        ),
        "does_not_prove": (
            "That a sensor was where it says it was, or that nobody "
            "unplugged one. A chain shows the record was not edited after "
            "the fact; it cannot show what happened in a room."
        ),
    }


# --- the owner's control over it -------------------------------------------


class CertificateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live: bool = Field(..., description="Whether the public page answers.")
    period_days: int = Field(DEFAULT_WINDOW_DAYS, ge=7, le=365)


@router.get("")
def read_certificate(tenant: Tenant = Depends(require_tenant)):
    """Whether this estate publishes a proof page, and where."""
    row = _row(tenant.tenant_id)
    return {
        "live": bool(row and row.get("live")),
        "url": f"/proof/{row['token']}" if row and row.get("live") else None,
        "period_days": (row or {}).get("period_days", DEFAULT_WINDOW_DAYS),
        "issued_at": (row or {}).get("issued_at"),
        "note": (
            "Off until you turn it on. The link carries a token rather "
            "than your company id, so it cannot be guessed from the "
            "outside, and turning it off stops it answering immediately."
        ),
    }


@router.post("")
def set_certificate(
    payload: CertificateSettings,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Publish the proof page, or take it down.

    Owner only. Putting a compliance record in public is a decision about
    the business, not an operational one.
    """
    row = _row(tenant.tenant_id) or {}
    token = row.get("token") or secrets.token_urlsafe(24)
    row = {
        "tenant_id": tenant.tenant_id,
        "token": token,
        "live": payload.live,
        "period_days": payload.period_days,
        "issued_at": row.get("issued_at") or iso(utc_now()),
        "updated_at": iso(utc_now()),
    }
    STORE._db.put(KIND, tenant.tenant_id, row)
    write_audit(tenant, operator, "certificate.published"
                if payload.live else "certificate.withdrawn", token[:8])
    return {
        "live": payload.live,
        "url": f"/proof/{token}" if payload.live else None,
        "message": (
            "Published. Anyone with the link can check the record without "
            "an account." if payload.live else "Taken down."
        ),
    }


@router.post("/rotate")
def rotate_token(
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Issue a new link and kill the old one.

    For when a link has gone somewhere it should not have.
    """
    row = _row(tenant.tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="There is no proof page to rotate yet.",
        )
    row = {**row, "token": secrets.token_urlsafe(24),
           "updated_at": iso(utc_now())}
    STORE._db.put(KIND, tenant.tenant_id, row)
    write_audit(tenant, operator, "certificate.rotated", row["token"][:8])
    return {"url": f"/proof/{row['token']}",
            "message": "New link issued. The previous one no longer answers."}


# --- what the public sees ---------------------------------------------------


@router.get("/public/{token}", include_in_schema=False)
def public_certificate(token: str, days: Optional[int] = Query(None)):
    """The record behind a proof page. No account, by design."""
    row = by_token(token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published record answers to this link.",
        )
    tenant = STORE.get_tenant(row["tenant_id"])
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No published record answers to this link.")
    window = days or row.get("period_days") or DEFAULT_WINDOW_DAYS
    return build(tenant, days=max(7, min(int(window), 365)))
