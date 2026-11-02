"""The on-call roster.

Alerts used to go to the single phone number on the tenant record, which
works for a one-site customer and fails everyone with a night shift or a
second site. A roster fans the SMS out to everyone on it and walks the
voice ladder in escalation order until somebody is actually reached.

A tenant with no roster still gets alerted: the store falls back to the
contact captured at onboarding, so alerting never depends on setup that
hasn't happened yet.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Contact, Tenant, User

router = APIRouter(prefix="/api/contacts", tags=["On-Call Roster"])


class ContactCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=5, max_length=40)
    receives_sms: bool = True
    receives_voice: bool = True
    escalation_order: int = Field(
        1, ge=1, le=99, description="Lower numbers are called first."
    )
    site_id: Optional[str] = Field(
        None,
        description=(
            "Restrict this person to one site. Left empty they cover the "
            "whole estate and are the fallback for any site with nobody of "
            "its own."
        ),
    )


class ContactUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=120)
    phone: Optional[str] = Field(None, min_length=5, max_length=40)
    receives_sms: Optional[bool] = None
    receives_voice: Optional[bool] = None
    escalation_order: Optional[int] = Field(None, ge=1, le=99)
    active: Optional[bool] = None
    site_id: Optional[str] = None


def _own_site(site_id: Optional[str], tenant: Tenant) -> Optional[str]:
    """Resolve a site reference, refusing another tenant's sites.

    Silently dropping an unknown site would leave the contact covering the
    whole estate, which is the opposite of what was asked for.
    """
    site_id = (site_id or "").strip() or None
    if site_id is None:
        return None
    site = STORE.get_site(site_id)
    if site is None or site.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Site '{site_id}' not found for this tenant.",
        )
    return site_id


def roster_gaps(tenant: Tenant) -> List[str]:
    """Where an alert would reach nobody from the roster, said in advance.

    A roster where everyone has been muted for leave, or taken off texts,
    does not fail loudly: the store quietly falls back to the number given
    at sign-up, which may be a desk phone or somebody who left. That is
    found out at 3am, when it is too late to fix. So the gap is named while
    it can still be fixed. An empty roster is not a gap: it is the
    documented fallback, and nobody built it to cover more.
    """
    roster = STORE.contacts_for(tenant.tenant_id)
    if not roster:
        return []
    fallback = f"{tenant.contact_name} on {tenant.contact_phone}"
    channels = [("sms", "alert text")]
    if tenant.entitlements()["voice_escalation"]:
        channels.append(("voice", "escalation call"))
    gaps = []
    for channel, label in channels:
        if not any(c.active and getattr(c, f"receives_{channel}") for c in roster):
            gaps.append(
                f"Nobody on the roster gets the {label}: everyone is muted or "
                f"opted out. It would go only to the sign-up contact, {fallback}."
            )
            continue
        # Every place that has a sensor, including sensors on no site.
        watched = sorted({s.site_id or "" for s in STORE.sensors_for(tenant.tenant_id)})
        for site_id in watched:
            if STORE._roster_for_site(tenant, site_id or None, channel):
                continue
            site = STORE.get_site(site_id) if site_id else None
            place = site.name if site else "sensors on no site"
            gaps.append(
                f"Nobody on the roster covers {place} for the {label}: "
                "nobody of its own and nobody covering the whole estate is "
                f"on. It would go only to the sign-up contact, {fallback}."
            )
    return gaps


def _warn_owner_of_new_gaps(tenant: Tenant, before: List[str], operator: User) -> List[str]:
    """Email the account owner when a roster change opens a gap.

    The person who mutes the last colleague is often not the owner, and
    the owner is the one who answers for a freezer nobody was told about.
    Once per distinct gap: the dedupe key is the gap itself.
    """
    import hashlib

    import mail

    after = roster_gaps(tenant)
    new = [g for g in after if g not in before]
    if new:
        write_audit(tenant, operator, "contact.gap", " ".join(new))
        key = hashlib.sha256("\n".join(sorted(after)).encode()).hexdigest()[:16]
        mail.send(
            to_address=tenant.contact_email,
            subject=f"{tenant.company_name}: an alert would reach nobody on your roster",
            body=(
                f"{operator.full_name} changed the on-call roster, and now:\n\n"
                + "\n".join(f"- {g}" for g in new)
                + "\n\nCheck who is on call before you need them: "
                "Console, then Who gets alerted."
            ),
            dedupe_key=f"roster-gap:{tenant.tenant_id}:{key}",
            tenant_id=tenant.tenant_id,
        )
    return after


def _load(contact_id: str, tenant: Tenant) -> Contact:
    contact = STORE.get_contact(contact_id)
    if contact is None or contact.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact '{contact_id}' not found for this tenant.",
        )
    return contact


@router.get("")
def list_contacts(tenant: Tenant = Depends(require_tenant)):
    """The roster, in the order the voice ladder will try it."""
    roster = STORE.contacts_for(tenant.tenant_id)
    return {
        "count": len(roster),
        "using_fallback": not any(c.active for c in roster),
        "fallback": {
            "full_name": tenant.contact_name,
            "phone": tenant.contact_phone,
            "note": (
                "Used when the roster is empty, so alerting never depends on "
                "setup that has not happened yet."
            ),
        },
        "contacts": [c.public() for c in roster],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def add_contact(
    payload: ContactCreate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Add someone to the roster."""
    contact = STORE.add_contact(
        tenant_id=tenant.tenant_id,
        full_name=payload.full_name,
        phone=payload.phone,
        receives_sms=payload.receives_sms,
        receives_voice=payload.receives_voice,
        escalation_order=payload.escalation_order,
        site_id=_own_site(payload.site_id, tenant),
    )
    scope = "estate-wide"
    if contact.site_id:
        site = STORE.get_site(contact.site_id)
        scope = site.name if site else contact.site_id
    write_audit(
        tenant, operator, "contact.added",
        f"{contact.full_name} ({contact.phone}) at position "
        f"{contact.escalation_order}, covering {scope}.",
    )
    return contact.public()


@router.patch("/{contact_id}")
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Change a roster entry. Omitted fields are left alone."""
    contact = _load(contact_id, tenant)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update."
        )

    if "site_id" in changes:
        changes["site_id"] = _own_site(changes["site_id"], tenant)

    before = roster_gaps(tenant)
    for field, value in changes.items():
        setattr(contact, field, value)
    STORE.save_contact(contact)

    write_audit(
        tenant, operator, "contact.updated",
        f"{contact.full_name}: {', '.join(sorted(changes))}.",
    )
    return dict(contact.public(),
                warnings=_warn_owner_of_new_gaps(tenant, before, operator))


@router.delete("/{contact_id}")
def remove_contact(
    contact_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Take someone off the roster."""
    contact = _load(contact_id, tenant)
    before = roster_gaps(tenant)
    STORE.remove_contact(contact_id)
    write_audit(tenant, operator, "contact.removed", f"{contact.full_name} removed.")
    return {
        "message": f"{contact.full_name} removed from the roster.",
        "remaining": len(STORE.contacts_for(tenant.tenant_id)),
        "warnings": _warn_owner_of_new_gaps(tenant, before, operator),
    }


@router.get("/preview")
def preview_routing(tenant: Tenant = Depends(require_tenant)):
    """Who would be alerted right now, without sending anything.

    Worth checking after editing the roster: a rota nobody has verified is
    the reason an alert reaches an empty desk.
    """
    return {
        "sms_recipients": [
            {"name": c.full_name, "phone": c.phone}
            for c in STORE.sms_recipients(tenant)
        ],
        "voice_ladder": [
            {"name": c.full_name, "phone": c.phone, "position": index + 1}
            for index, c in enumerate(STORE.voice_ladder(tenant))
        ],
        "note": (
            "The text goes to everyone listed. The call walks the ladder and "
            "stops at the first person actually reached."
        ),
        "warnings": roster_gaps(tenant),
    }
