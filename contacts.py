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

from datetime import date, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

import oncall
from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Contact, Tenant, User, iso, utc_now

router = APIRouter(prefix="/api/contacts", tags=["On-Call Roster"])


class QuietHours(BaseModel):
    """Hours this person is not texted unless nobody else would be."""

    model_config = ConfigDict(extra="forbid")

    start: str = Field(..., description="Local time quiet hours begin, e.g. 22:00.")
    end: str = Field(..., description="Local time they end, e.g. 07:00. Earlier "
                                      "than start means over midnight.")
    timezone: str = Field(..., description="IANA time zone, e.g. Europe/London.")

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, value: str) -> str:
        return oncall.clock(value).strftime("%H:%M")

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        oncall.zone(value)
        return value.strip()


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
    quiet_hours: Optional[QuietHours] = None


class ContactUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=120)
    phone: Optional[str] = Field(None, min_length=5, max_length=40)
    receives_sms: Optional[bool] = None
    receives_voice: Optional[bool] = None
    escalation_order: Optional[int] = Field(None, ge=1, le=99)
    active: Optional[bool] = None
    site_id: Optional[str] = None
    quiet_hours: Optional[QuietHours] = None


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
        return _rotation_gaps(tenant, roster)
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
    return gaps + _rotation_gaps(tenant, roster) + _quiet_hour_notes(tenant)


def _place(site_id: Optional[str]) -> str:
    site = STORE.get_site(site_id) if site_id else None
    return f"the rotation at {site.name}" if site else "the estate's rotation"


def _rotation_gaps(tenant: Tenant, roster: List[Contact]) -> List[str]:
    """A rotation that would leave a shift with nobody on it."""
    by_id = {c.contact_id: c for c in roster}
    gaps = []
    for rotation in oncall.rotations_for(STORE._db, tenant.tenant_id):
        place = _place(rotation.site_id)
        if not rotation.members:
            gaps.append(
                f"{place[0].upper()}{place[1:]} has nobody in it. Add people "
                "to it, or remove it."
            )
            continue
        for member in rotation.members:
            contact = by_id.get(member)
            if contact is None:
                continue
            if not contact.active:
                gaps.append(
                    f"{contact.full_name} is muted but has turns in {place}: "
                    "on their shifts nobody from the rotation is on call."
                )
            elif not contact.receives_sms:
                gaps.append(
                    f"{contact.full_name} does not get alert texts but has "
                    f"turns in {place}: on their shifts nobody from the "
                    "rotation is texted."
                )
    return gaps


def _quiet_hour_notes(tenant: Tenant, now=None) -> List[str]:
    """When everyone a text would reach is in quiet hours, say when.

    Not a gap: the text still goes, to all of them, because nobody being
    woken is worse than somebody being woken. But an owner who set quiet
    hours for everyone should know that is what happens.
    """
    if not any(c.quiet_hours for c in STORE.contacts_for(tenant.tenant_id)):
        return []
    now = now or utc_now()
    notes = []
    places = sorted({s.site_id or "" for s in STORE.sensors_for(tenant.tenant_id)}) or [""]
    for site_id in places:
        for step in range(48):
            moment = now + timedelta(minutes=30 * step)
            roster = STORE._roster_for_site(tenant, site_id or None, "sms", moment)
            if not roster:
                continue
            awake, _ = oncall.awake_first(roster, moment)
            if not awake:
                site = STORE.get_site(site_id) if site_id else None
                where = site.name if site else "the estate"
                quiet = roster[0].quiet_hours or {}
                notes.append(
                    f"Everyone who gets the alert text for {where} is in quiet "
                    f"hours at times (from {quiet.get('start')} "
                    f"{quiet.get('timezone')}). A breach then still texts all "
                    "of them: quiet hours never silence an alert."
                )
                break
    return notes


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
    if payload.quiet_hours is not None:
        contact.quiet_hours = payload.quiet_hours.model_dump()
        STORE.save_contact(contact)
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
    if "quiet_hours" in changes and payload.quiet_hours is not None:
        changes["quiet_hours"] = payload.quiet_hours.model_dump()

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
            "stops at the first person actually reached. Right now: people "
            "in a rotation count only on their shift, and anybody in quiet "
            "hours is texted only if nobody else would be, and called last."
        ),
        "warnings": roster_gaps(tenant),
    }


# ---- rotations ---------------------------------------------------------------


class RotationIn(BaseModel):
    """Take turns being on call. One rotation per site, and one for the estate."""

    model_config = ConfigDict(extra="forbid")

    site_id: Optional[str] = Field(
        None, description="The site this rotation covers; empty for the whole estate.")
    timezone: str = Field(..., description="IANA time zone handovers are in, e.g. America/New_York.")
    handover: str = Field("08:00", description="Local time one shift hands to the next.")
    shift_days: int = Field(7, ge=1, le=oncall.MAX_SHIFT_DAYS,
                            description="Days in one shift: 1 for daily, 7 for weekly.")
    starts_on: Optional[date] = Field(
        None, description="The local date the first member's first shift starts. "
                          "Defaults to the most recent handover there, so the "
                          "first member is on call from now.")
    members: List[str] = Field(default_factory=list, max_length=50,
                               description="Contact ids, in the order they take turns.")

    @field_validator("handover")
    @classmethod
    def _clock(cls, value: str) -> str:
        return oncall.clock(value).strftime("%H:%M")

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        oncall.zone(value)
        return value.strip()


def _rotation_view(rotation: oncall.Rotation, now) -> Dict:
    names = {c.contact_id: c.full_name for c in STORE.contacts_for(rotation.tenant_id)}
    on_shift = rotation.on_shift(now)
    schedule = rotation.schedule(now)
    for shift in schedule:
        shift["full_name"] = names.get(shift["contact_id"], "(removed)")
    return {
        **rotation.to_row(),
        "scope": rotation.site_id or oncall.ESTATE,
        "members": [{"contact_id": m, "full_name": names.get(m, "(removed)")}
                    for m in rotation.members],
        "on_shift_now": ({"contact_id": on_shift, "full_name": names.get(on_shift)}
                         if on_shift else None),
        "next_handover_at": (iso(rotation.next_handover(now))
                             if rotation.members else None),
        "schedule": schedule,
    }


@router.get("/rotations")
def list_rotations(tenant: Tenant = Depends(require_tenant)):
    """Every rotation, who is on shift now, and the next few handovers."""
    now = utc_now()
    return {
        "rotations": [_rotation_view(r, now)
                      for r in oncall.rotations_for(STORE._db, tenant.tenant_id)],
        "note": (
            "People in a rotation are alerted only on their shift; everybody "
            "else on the roster is alerted as usual. Handovers follow the "
            "local clock, through daylight-saving changes."
        ),
        "warnings": roster_gaps(tenant),
    }


@router.put("/rotations")
def set_rotation(
    payload: RotationIn,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Create or replace the rotation for a site, or for the whole estate."""
    site_id = _own_site(payload.site_id, tenant)
    roster = {c.contact_id: c for c in STORE.contacts_for(tenant.tenant_id)}
    members = [m.strip() for m in payload.members]
    if len(set(members)) != len(members):
        raise HTTPException(status_code=400, detail="Somebody is in the rotation twice.")
    unknown = [m for m in members if m not in roster]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"Not on this business's roster: {', '.join(unknown)}.")
    elsewhere = [
        roster[m].full_name
        for r in oncall.rotations_for(STORE._db, tenant.tenant_id)
        if r.rec_id != oncall.rotation_id(tenant.tenant_id, site_id)
        for m in r.members if m in members
    ]
    if elsewhere:
        raise HTTPException(
            status_code=409,
            detail=(f"Already in another rotation: {', '.join(elsewhere)}. "
                    "Somebody takes turns in one rotation at a time."))
    tz = oncall.zone(payload.timezone)
    starts_on = payload.starts_on
    if starts_on is None:
        # From the most recent handover there, so the person listed first
        # is on call from now. Started on today's date before today's
        # handover, the first turn had not begun, and until it did the
        # turn "before" it ran: the person listed last was on call.
        now = utc_now()
        starts_on = now.astimezone(tz).date()
        if oncall.handover_at(starts_on, oncall.clock(payload.handover), tz) > now:
            starts_on -= timedelta(days=1)
    rotation = oncall.Rotation(
        tenant_id=tenant.tenant_id, site_id=site_id, timezone=payload.timezone,
        handover=payload.handover, shift_days=payload.shift_days,
        starts_on=starts_on.isoformat(), members=members,
        updated_at=iso(utc_now()),
    )
    before = roster_gaps(tenant)
    STORE._db.put(oncall.ROTATION_KIND, rotation.rec_id, rotation.to_row())
    write_audit(
        tenant, operator, "rotation.set",
        f"{_place(site_id)}: {len(members)} people, {payload.shift_days}-day "
        f"shifts handing over at {payload.handover} {payload.timezone}.",
    )
    return dict(_rotation_view(rotation, utc_now()),
                warnings=_warn_owner_of_new_gaps(tenant, before, operator))


@router.delete("/rotations/{scope}")
def remove_rotation(
    scope: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Stop a rotation. Its members go back to being on call all the time."""
    site_id = None if scope == oncall.ESTATE else _own_site(scope, tenant)
    rec_id = oncall.rotation_id(tenant.tenant_id, site_id)
    if STORE._db.get(oncall.ROTATION_KIND, rec_id) is None:
        raise HTTPException(status_code=404, detail="There is no rotation there.")
    STORE._db.delete(oncall.ROTATION_KIND, rec_id)
    write_audit(tenant, operator, "rotation.removed", f"{_place(site_id)} removed.")
    return {"removed": True, "warnings": roster_gaps(tenant)}
