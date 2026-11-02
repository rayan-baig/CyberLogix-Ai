"""A new business's first day: the checklist, and the test alert.

Two things a business needs to have done before the night a freezer
fails, and that nothing used to tell it to do:

* **The checklist.** Add a site, a sensor, somebody to call, and send a
  test alert. Each line is read from what the account actually holds --
  there is no "mark as done" button, because a box somebody ticked is a
  promise, and a box the roster ticked is a fact. When all four are true
  the checklist is gone, and it does not come back.

* **The test alert.** The roster is the one part of the product a
  customer cannot check by looking at it. A wrong digit in a phone number
  reads perfectly well on screen and reaches nobody, and the first time
  anyone finds out is the night it mattered. So an owner or operator can
  text (and, on a plan with calls, phone) the roster now, with words that
  say it is a test before they say anything else.

  It is deliberately small. It opens no incident, so nothing escalates,
  nothing reaches the compliance record and no Slack channel lights up.
  It goes through the same send functions as a real alert, so it is
  counted against the same daily spend caps and refused by the same
  premium-number guard. And it is rate limited from the audit trail -- a
  record that survives a restart -- because a button that texts every
  person on a roster is a button somebody will press twice.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Contact, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.firstday")

router = APIRouter(prefix="/api/contacts", tags=["On-Call Roster"])

# One test every ten minutes, and five a day. Enough to fix a wrong number
# and try again; not enough to turn the roster into somebody's pager toy.
TEST_ALERT_COOLDOWN_SECONDS = 10 * 60
TEST_ALERTS_PER_DAY = 5

# What the audit trail calls a test that went out. The checklist ticks
# itself from this, so it is a constant rather than a string in two places.
SENT = "alert.test_sent"
FAILED = "alert.test_failed"

# Statuses that mean the test did what it could. "not_configured" is a
# deployment with no Twilio account: the message was composed and recorded
# exactly as it would have been sent, which is all a dry run can do, and a
# checklist that can never be finished on one is a checklist that lies.
_COUNTS_AS_SENT = {"queued", "sent", "delivered", "accepted", "not_configured"}


def sms_text(tenant: Tenant) -> str:
    """The test text. "TEST" and "nothing is wrong" come first.

    A text is cut at one segment on the way out, and the company name is
    the only part of unbounded length, so it goes last: a long name loses
    its own tail, never the words that say nobody needs to get up.
    """
    return (
        "TEST from CyberLogix: nothing is wrong, no action needed. "
        f"Real alerts for {tenant.company_name} will come from this number."
    )


def spoken_text(tenant: Tenant) -> str:
    return (
        "This is a test call from CyberLogix. Nothing is wrong, and you do "
        "not need to do anything. If something does go wrong at "
        f"{tenant.company_name}, a call like this one will say what, and "
        "where. Goodbye."
    )


# --- who has done what ------------------------------------------------------

# Tenants that have sent a test. Only ever grows (an audit entry is never
# taken back), so a positive answer can be remembered and the console's
# twenty-second poll does not rescan the trail for an account that is done.
_tested: Set[str] = set()


def test_alerts_since(tenant_id: str, since) -> List[Any]:
    return [
        e for e in STORE.audit_for(tenant_id, limit=10_000)
        if e.action in (SENT, FAILED) and e.at >= since
    ]


def has_sent_test(tenant_id: str) -> bool:
    if tenant_id in _tested:
        return True
    found = any(e.action == SENT for e in STORE.audit_for(tenant_id, limit=10_000))
    if found:
        _tested.add(tenant_id)
    return found


def checklist(tenant: Tenant) -> Dict[str, Any]:
    """The four things, each read from what the account holds right now."""
    tid = tenant.tenant_id
    steps = [
        {
            "key": "site",
            "label": "Add your site",
            "detail": "Where the fridges are, so an alert names the building.",
            "done": bool(STORE.sites_for(tid)),
        },
        {
            "key": "sensor",
            "label": "Add a sensor",
            "detail": "One fridge or freezer is enough to start.",
            "done": bool(STORE.sensors_for(tid)),
        },
        {
            "key": "oncall",
            "label": "Add someone to call",
            "detail": "Who gets the text at 3am.",
            "done": any(c.active for c in STORE.contacts_for(tid)),
        },
        {
            "key": "test",
            "label": "Send a test alert",
            "detail": "Check the texts really arrive, before a night they have to.",
            "done": has_sent_test(tid),
        },
    ]
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done, "total": len(steps),
            "complete": done == len(steps)}


def forget() -> None:
    """Drop the remembered answers. For tests, which reset the store."""
    _tested.clear()
    with _lock:
        _inflight.clear()


# --- the test alert ----------------------------------------------------------

_lock = threading.Lock()
_inflight: Set[str] = set()


class TestAlertRequest(BaseModel):
    call: bool = False


def sms_roster(tenant: Tenant) -> List[Contact]:
    """Everybody a real text could reach, each number once.

    Across every site, not just the estate-wide names: a test is for
    finding the wrong number, and the wrong number is as likely to be the
    night manager at the second site as anybody.
    """
    seen: Set[str] = set()
    out: List[Contact] = []
    for contact in sorted(STORE.contacts_for(tenant.tenant_id),
                          key=lambda c: (c.escalation_order, c.full_name)):
        if not (contact.active and contact.receives_sms):
            continue
        key = "".join(ch for ch in contact.phone if ch.isdigit())
        if key in seen:
            continue
        seen.add(key)
        out.append(contact)
    return out or STORE.sms_recipients(tenant)


def _refuse_if_too_soon(tenant: Tenant) -> None:
    now = utc_now()
    recent = test_alerts_since(tenant.tenant_id, now - timedelta(days=1))
    if len(recent) >= TEST_ALERTS_PER_DAY:
        oldest = min(e.at for e in recent)
        wait = int((oldest + timedelta(days=1) - now).total_seconds()) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(f"{TEST_ALERTS_PER_DAY} test alerts have gone out in the "
                    "last day, which is the most there can be."),
            headers={"Retry-After": str(max(wait, 1))},
        )
    if recent:
        last = max(e.at for e in recent)
        wait = int((last + timedelta(seconds=TEST_ALERT_COOLDOWN_SECONDS)
                    - now).total_seconds()) + 1
        if wait > 0:
            minutes = max(1, round(wait / 60))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(f"A test alert went out a moment ago. The next one "
                        f"can go in about {minutes} minute"
                        f"{'' if minutes == 1 else 's'}."),
                headers={"Retry-After": str(wait)},
            )


@router.post("/test-alert")
def send_test_alert(
    payload: Optional[TestAlertRequest] = None,
    tenant: Tenant = Depends(require_tenant),
    person: User = Depends(require_role("operator")),
):
    """Text the roster a message that says it is a test. Optionally, phone
    the first person on the call ladder too.

    Owners and operators only. Opens no incident, posts to no channel, and
    writes nothing to the compliance record: the only trace it leaves is an
    audit line, which is also what ticks the checklist.
    """
    from notifications import place_voice_call, send_sms

    wants_call = bool(payload and payload.call)
    with _lock:
        if tenant.tenant_id in _inflight:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="A test alert is already going out.",
                headers={"Retry-After": "10"},
            )
        _refuse_if_too_soon(tenant)
        _inflight.add(tenant.tenant_id)

    try:
        results: List[Dict[str, Any]] = []
        for contact in sms_roster(tenant):
            out = send_sms(contact.phone, sms_text(tenant), tenant_id=tenant.tenant_id)
            results.append({
                "name": contact.full_name, "phone": contact.phone,
                "channel": "sms", "delivered": bool(out.get("delivered")),
                "status": out.get("status"), "detail": out.get("detail"),
            })

        call_note: Optional[str] = None
        if wants_call:
            if not tenant.entitlements().get("voice_escalation"):
                call_note = (f"The {tenant.entitlements()['name']} plan does not "
                             "include phone calls, so only the texts were sent.")
            else:
                ladder = STORE.voice_ladder(tenant)
                first = ladder[0] if ladder else None
                if first is not None:
                    out = place_voice_call(first.phone, spoken_text(tenant),
                                           tenant_id=tenant.tenant_id)
                    results.append({
                        "name": first.full_name, "phone": first.phone,
                        "channel": "voice", "delivered": bool(out.get("delivered")),
                        "status": out.get("status"), "detail": out.get("detail"),
                    })

        went = [r for r in results if r["delivered"] or r["status"] in _COUNTS_AS_SENT]
        dry_run = bool(results) and all(r["status"] == "not_configured" for r in results)
        write_audit(
            tenant, person, SENT if went else FAILED,
            f"Test alert: {len(went)} of {len(results)} "
            f"{'composed (dry run)' if dry_run else 'sent'}"
            + (" including a call" if any(r["channel"] == "voice" for r in results) else "")
            + ". No incident was opened.",
        )
        if went:
            _tested.add(tenant.tenant_id)
    finally:
        with _lock:
            _inflight.discard(tenant.tenant_id)

    reached = sum(1 for r in results if r["delivered"])
    if dry_run:
        message = ("Texts are switched off on this server, so the test was "
                   "written but not sent. Everything else about it worked.")
    elif reached:
        message = (f"Test sent to {reached} of {len(results)}. Ask them "
                   "whether it arrived.")
    else:
        message = "Nothing got through. The reason is next to each name."
    return {
        "test": True,
        "incident_opened": False,
        "message": message + (f" {call_note}" if call_note else ""),
        "dry_run": dry_run,
        "results": results,
        "next_allowed_at": iso(utc_now() + timedelta(seconds=TEST_ALERT_COOLDOWN_SECONDS)),
    }
