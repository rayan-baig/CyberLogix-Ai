"""The contract, generated from the code it describes.

Terms of service normally rot. Somebody writes them once, the product
changes underneath them, and two years later the agreement promises a
sixty-second dispatch the code no longer attempts and caps a payout at a
figure nobody has used since. The document then works against the company
that wrote it: every clause that overstates the product is a promise a
customer can enforce, and every clause that understates it is protection
given away for free.

So these documents are not a text file. The numbers in them — the payout
cap, the dispatch window, the payment terms, the late charge, the point at
which reporting is withheld, what voids cover — are read from the modules
that implement them. If a limit changes in the code, the agreement
says the new figure the same day, and the version hash changes so it is
visible that it did.

Each document carries a version, an effective date and a SHA-256 of its
own body. A tenant's acceptance records that hash, so which text a
customer agreed to is a fact rather than a recollection.

THIS IS A DRAFT, NOT ADVICE. A liability cap that is unenforceable is
worse than none, because it is relied on. Every one of these needs a
lawyer's eyes before it is put in front of a paying customer, and the
places most likely to need changing are marked.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import (
    require_platform_admin,
    require_tenant_any_state,
    write_audit,
)
from contracts import (
    DELINQUENT_AFTER_DAYS,
    LATE_FEE_MONTHLY_PERCENT,
    REMINDER_DAYS,
)
from backup import BACKUP_KEEP
from invoicing import PAYMENT_TERMS_DAYS, issuer_block
from store import (
    DISPATCH_SLA_SECONDS,
    MAIL_BODY_KEEP_DAYS,
    MAX_READINGS_PER_SENSOR,
    SENSOR_OFFLINE_AFTER_MINUTES,
    STORE,
    Tenant,
    User,
    VOICE_ESCALATION_GRACE_MINUTES,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/legal", tags=["Legal"])

# Bumped when the documents say something different, not when a sentence
# is tidied. 1.1 added the mail host and what backups do to a deletion.
# 2.0 rewrote all four in plain words, added the owner's "Delete
# everything" and "Download everything" buttons, and took out the Loss
# Assurance add-on, which had been withdrawn from the product but was
# still being promised here.
# 2.1 says how long the text of sent mail is kept (400 days) and that a
# deletion is redone after a backup is restored.
TERMS_VERSION = "2.1"
EFFECTIVE_DATE = "2026-11-01"

# The cap on what we can owe. Without one, a single claim about a lost
# freezer of stock could end the company. Twelve months of fees is the
# usual figure for business software.
LIABILITY_CAP_MONTHS = 12

# Uptime the platform commits to, and what missing it costs. Credits, not
# damages: a refund off the next bill, never a sum that can grow.
UPTIME_TARGET_PERCENT = 99.5
SERVICE_CREDIT_TABLE = [
    (99.5, 99.0, 10),
    (99.0, 95.0, 25),
    (95.0, 0.0, 50),
]
MAX_CREDIT_PERCENT = 50

DISCLAIMER = (
    "Draft, written in plain words. The numbers come straight from the "
    "code, so they match what the service really does. It is not "
    "reviewed by a lawyer."
)


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


# ---- the documents -----------------------------------------------------


def terms_of_service() -> str:
    return f"""# Master Subscription Agreement

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

This is the deal between {issuer_block().get('legal_name')} ("we") and the
business that signs up ("you").

## 1. What we do

Your sensors send us temperature and humidity readings. We check them
against the limits you set. When a reading goes outside a limit, we try
to reach the people you told us to reach.

## 2. What we do not do

**We are not a substitute for your own equipment, alarms or staff.** We
watch your fridges and freezers. We do not control them or fix them. If
something breaks and we miss it, or we can't get the message through, the
goods are still in your care.

**Some things are out of our hands.** Texts and calls travel over phone
networks and the internet, and those sometimes fail. Sensor batteries run
out. If nobody answers the phone, we can't make them.

**We are not certified.** CyberLogix is not a medical device, not a
safety instrumented system, and not a replacement for any check the law
requires you to run yourself.

## 3. What we promise

* We try to send an alert within {DISPATCH_SLA_SECONDS} seconds of a
  reading that breaks one of your limits.
* If nobody acknowledges it, we phone after
  {VOICE_ESCALATION_GRACE_MINUTES:g} minutes.
* If a sensor goes quiet for {SENSOR_OFFLINE_AFTER_MINUTES} minutes, we
  tell you it is offline.

How often the service is up is in the Service Level Commitment, which is
part of this deal.

## 4. What you promise

* Keep at least one person on call who will answer, with the right number.
* Keep sensor batteries charged, and act when we say a sensor went quiet.
* Set limits that fit what you actually store.
* Tell us within thirty days if you think a bill is wrong.

Each of these is a way alerts can quietly stop working, so we warn you
when one slips.

## 5. Paying

Prices are on your order. Bills are due net {PAYMENT_TERMS_DAYS} days.
Late bills get a late charge of {LATE_FEE_MONTHLY_PERCENT:g}% per month,
or less if the law where you are says so.

If a bill is more than {DELINQUENT_AFTER_DAYS} days late, reports,
certificates and downloads pause until it is paid.

**Monitoring and alerting are never withheld for non-payment.** We will
ask you for money. We will never stop watching your freezers to get it.

## 6. Your data

Your readings belong to you. What we keep, who else sees it, and for how
long is in the Privacy statement.

An owner can download everything at any time, and can delete everything
with the "Delete everything" button. The only things kept after that are
the bills we already sent, because the law says we must keep them, and
the anonymous sector averages your numbers were already mixed into, which
can't be traced back to you or pulled back out.

## 7. Limitation of liability

**Please read this part.**

If something goes wrong, the most we will ever owe you, for everything
put together, is what you paid us in the {LIABILITY_CAP_MONTHS} months
before the claim.

We are not responsible for lost profits, lost sales, spoiled or lost
goods, time your business could not run, or any other indirect or
consequential loss, even if someone told us it might happen.

This limit does not cover death or injury caused by our carelessness,
fraud, or anything else the law does not allow us to limit.

**Why this is here.** One freezer of stock can be worth more than this
company will ever earn. Without a limit, one claim could close the
company, and nobody could offer this service at this price. If you need
cover for the goods themselves, that is what insurance is for, and the
claim packet the service builds is made to help you claim on it.

## 8. No other promises

The service comes as described here and in the Service Level Commitment,
and with no other promise. We do not promise it will never go down or
never have a bug.

## 9. Ending the deal

Your order says how long the deal runs. Either of us can end it if the
other breaks it in a big way and does not fix it within thirty days of
being told. You pay bills already sent; we stop billing you after that.

## 10. Everything else

Neither of us is to blame for things truly out of our control. Notices go
to the addresses on the order. This page is the whole deal, and any change
has to be in writing.
"""


def service_level() -> str:
    rows = "\n".join(
        f"| below {high:g}% down to {low:g}% | {credit}% |"
        for high, low, credit in SERVICE_CREDIT_TABLE
    )
    return f"""# Service Level Commitment

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## The promise

Receiving readings and sending alerts will work at least
{UPTIME_TARGET_PERCENT:g}% of every calendar month.

Maintenance we tell you about at least 48 hours ahead does not count, and
neither does an outage at a phone or internet company we don't run.

## If we miss it

| Monthly availability | Credit off that month's bill |
|---|---|
{rows}

A credit is the only thing you get for missed uptime. It is never more
than {MAX_CREDIT_PERCENT}% of the month, and it comes off your next bill.
Ask for it within thirty days of that month.

## What we do not promise

We don't promise a person will read the alert. We promise to try to send
it within {DISPATCH_SLA_SECONDS} seconds. Phone companies drop texts and
people silence phones, which is why an alert nobody answers turns into a
phone call after {VOICE_ESCALATION_GRACE_MINUTES:g} minutes.
"""


def privacy_statement() -> str:
    return f"""# Privacy and Data Processing

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## What we keep

**Readings.** Temperature, humidity, battery level, the time, and which
sensor sent it. This is about machines, not people.

**People on call.** For each person you put on call: name, mobile number,
email and job. We keep these for one reason: to reach a person when a
freezer fails.

**People who sign in.** Name, email, a scrambled (hashed) password, and a
record of what each one did.

**Mail we send you.** Who it went to, the subject, the message, and
whether the mail service took it. That way, "we never got that bill" has
a real answer. The message itself is deleted after
{MAIL_BODY_KEEP_DAYS} days; who it went to, the subject and whether it
was delivered are kept. If an address bounces or asks us to stop, we keep
that address on a "do not mail" list, because forgetting it would mean
writing to it again.

We do not sell any of it, and we do not use it to train anything.

## Who else sees it

| Sub-processor | What reaches them | Why |
|---|---|---|
| Twilio | The mobile number, and the text of the alert (which names the site and the temperature) | Sending the text and making the call |
| Google (Gemini API) | The wording of an alert being drafted: the type of business, the reading and the limit | Writing the alert so the person reading it acts on it |
| Our mail host | The recipient's address and the whole message, which for a bill is what you owe and for a weekly report is how many readings came in and which units went quiet | Delivering it |

Nobody else gets your readings in bulk, your customer list, or your
passwords.

## Anonymous sector averages

If you use Sector Benchmarks, your totals (how often units go out of
range, how fast people respond) are mixed into averages for your whole
industry. Nothing that names you goes in, and a number that could point
back to one business is hidden. Once mixed in, it can't be pulled out.

## How long we keep things

**Readings: only the newest.** We keep the latest
{MAX_READINGS_PER_SENSOR} readings for each sensor and delete older ones.
With a reading every five minutes, that is under two days. If you need a
longer history, download it regularly. Every report says how far back it
can see.

**Alerts, bills and the "who did what" record** are kept while your
account is open, so you can show an inspector or insurer what happened.
When an alert starts and ends, the readings around it are saved with the
alert, so they last longer than the normal readings.

**Sign-ins** run out on their own.

## Download it, or delete it

An owner can press **Download everything** to get one file with every
record we hold for the business (passwords, keys and codes left out).

An owner can press **Delete everything**. It deletes the business, its
sensors, readings, people, alerts, mail and records straight away. To
stop slips, it asks for the business's name and the owner's password, and
it waits until no bill is unpaid. Two things stay:

* **The bills we already sent.** The law makes a seller keep its bills
  for years. Each one shows the company name, what was charged and
  whether it was paid.
* **Anonymous sector averages**, as above.

**Backups.** Every day we save a checked snapshot of the whole database
and keep the last {BACKUP_KEEP}. So after you delete, an old copy sits in
those backups until they are replaced, and it is gone within
{BACKUP_KEEP} days. We do not restore a backup to bring back something
you deleted: if we ever have to restore one, anything deleted since it was
taken is deleted again at once.

You can also just ask us for a copy, a fix, or a deletion.

## Where it is stored

On our servers and our sub-processors' servers. If you need your data
kept in one country, tell us before you sign up. We would rather say no
than surprise you later.
"""


def acceptable_use() -> str:
    reminders = ", ".join(f"day {d}" for d in REMINDER_DAYS)
    return f"""# Acceptable Use and Operating Requirements

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## Please don't

* Use us as the only safety system on equipment where a missed alert
  could hurt or kill someone. We are a second pair of eyes, never the
  only pair.
* Add sensors that aren't yours, or send readings for someone else.
* Use alerts for anything that isn't a real alert. The on-call list is
  not a marketing list, and misuse can get the phone account shut down
  for every customer.
* Share one login between people. Everyone gets their own, so "who did
  what" means something.

## Keep it working

* At least one person on call who will answer, for every location.
* Sensor batteries charged.
* Limits set to what you actually store.
* Sensor keys kept secret, and changed when someone leaves.

## If you don't pay

We send reminders on {reminders} after the due date. A late charge of
{LATE_FEE_MONTHLY_PERCENT:g}% per month starts on day {REMINDER_DAYS[2]}.
At {DELINQUENT_AFTER_DAYS} days late, reports and downloads pause.

Monitoring and alerting continue no matter what. We will never put your
stock at risk to collect a bill.
"""


DOCUMENTS = {
    "terms": ("Master Subscription Agreement", terms_of_service),
    "sla": ("Service Level Commitment", service_level),
    "privacy": ("Privacy and Data Processing", privacy_statement),
    "acceptable-use": ("Acceptable Use and Operating Requirements", acceptable_use),
}


def document(slug: str) -> Dict[str, Any]:
    """One document, with the hash that identifies this exact text."""
    title, builder = DOCUMENTS[slug]
    body = builder()
    return {
        "slug": slug,
        "title": title,
        "version": TERMS_VERSION,
        "effective_date": EFFECTIVE_DATE,
        # Over the body only. Two customers who accepted the same words
        # get the same hash, whatever else changed around it.
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "body_markdown": body,
        "draft": True,
        "disclaimer": DISCLAIMER,
    }


def current_hashes() -> Dict[str, str]:
    return {slug: document(slug)["sha256"] for slug in DOCUMENTS}


# ---- routes ------------------------------------------------------------


class Acceptance(BaseModel):
    accepted_by: str = Field(..., min_length=1, max_length=200)
    title: str = Field("", max_length=120)
    documents: List[str] = Field(default_factory=lambda: list(DOCUMENTS))


@router.get("")
def index():
    """Every document, its version, and the hash of its current text.

    Public and unauthenticated. Terms nobody can read before signing up
    are not terms, they are a surprise.
    """
    return {
        "version": TERMS_VERSION,
        "effective_date": EFFECTIVE_DATE,
        "issuer": issuer_block(),
        "documents": [
            {
                "slug": slug,
                "title": title,
                "sha256": document(slug)["sha256"],
                "url": f"/api/legal/{slug}",
            }
            for slug, (title, _) in DOCUMENTS.items()
        ],
        "draft": True,
        "disclaimer": DISCLAIMER,
        "liability_cap": (
            f"{LIABILITY_CAP_MONTHS} months of fees."
        ),
    }


@router.get("/{slug}")
def read_document(slug: str):
    if slug not in DOCUMENTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such document '{slug}'. Available: {list(DOCUMENTS)}",
        )
    return document(slug)


@router.post("/accept", status_code=status.HTTP_201_CREATED)
def accept(
    payload: Acceptance,
    tenant: Tenant = Depends(require_tenant_any_state),
    operator: User = Depends(require_role("owner")),
):
    """Record which exact text this customer agreed to.

    The hash is the point. "They accepted the terms" is a recollection;
    "they accepted the text whose SHA-256 is 3f2a…, on this date, and here
    it is" is a fact, and it is the same kind of evidence the vault
    produces for readings.

    Owner only, and no machine credential. A signed contract attested by
    "API key" is not attested by anybody, and the whole value of the
    record is that a named person stands behind it.
    """
    unknown = [s for s in payload.documents if s not in DOCUMENTS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No such document(s) {unknown}. Available: {list(DOCUMENTS)}",
        )
    accepted = {slug: document(slug)["sha256"] for slug in payload.documents}
    detail = (
        f"{payload.accepted_by}"
        + (f" ({payload.title})" if payload.title else "")
        + f" accepted v{TERMS_VERSION}: "
        + ", ".join(f"{slug}@{h[:12]}" for slug, h in sorted(accepted.items()))
    )
    entry = write_audit(tenant, operator, "legal.accepted", detail)
    return {
        "message": "Acceptance recorded in the audit log.",
        "version": TERMS_VERSION,
        "accepted_at": iso(utc_now()),
        "accepted_by": payload.accepted_by,
        "documents": accepted,
        "audit_entry_id": getattr(entry, "entry_id", None),
    }


def acceptance_for(tenant: Tenant) -> Dict[str, Any]:
    """Which live documents this tenant has accepted, and which are stale.

    Extracted so the operator can ask it about the whole book. A customer
    who accepted v1.0 and never saw v1.1 is invisible one account at a
    time -- it is only a problem you can see fleet-wide, and it is the
    kind of thing a buyer's lawyer asks for in a single question.
    """
    live = current_hashes()
    accepted: Dict[str, Dict[str, Any]] = {}
    for entry in STORE.audit_for(tenant.tenant_id, limit=1000):
        if entry.action != "legal.accepted":
            continue
        for token in entry.detail.split(":", 1)[-1].split(","):
            token = token.strip()
            if "@" not in token:
                continue
            slug, prefix = token.rsplit("@", 1)
            accepted.setdefault(
                slug.strip(),
                {"hash_prefix": prefix, "at": iso(entry.at)},
            )

    rows = []
    for slug, (title, _) in DOCUMENTS.items():
        seen = accepted.get(slug)
        rows.append(
            {
                "slug": slug,
                "title": title,
                "accepted": seen is not None,
                "current": bool(seen and live[slug].startswith(seen["hash_prefix"])),
                "accepted_at": seen["at"] if seen else None,
            }
        )
    return {
        "version": TERMS_VERSION,
        "all_current": all(r["current"] for r in rows),
        "documents": rows,
        "note": (
            "A document accepted before its text changed shows as not "
            "current. Ask again rather than assuming."
        ),
    }


@router.get("/acceptance/status")
def acceptance_status(tenant: Tenant = Depends(require_tenant_any_state)):
    """Whether this customer has accepted the text that is live today.

    A stale acceptance is worth knowing about: if the payout cap moved
    after they signed, the hash they accepted no longer matches, and the
    honest thing is to ask them again rather than to assume.
    """
    return acceptance_for(tenant)


@router.get("/acceptance/outstanding")
def acceptance_outstanding(_: None = Depends(require_platform_admin)):
    """Every account that has not accepted the text that is live today.

    Sign-up records an acceptance, so this is empty on a fresh estate and
    fills up the moment a document changes -- which is exactly when
    nobody notices. The staleness was already computable per tenant and
    nothing ever asked, so a terms change silently left the whole book
    agreed to something that is no longer the agreement.
    """
    rows = []
    for tenant in STORE.list_tenants():
        state = acceptance_for(tenant)
        if state["all_current"]:
            continue
        rows.append(
            {
                "tenant_id": tenant.tenant_id,
                "company_name": tenant.company_name,
                "contact_name": tenant.contact_name,
                "contact_email": tenant.contact_email,
                "never_accepted": [
                    d["slug"] for d in state["documents"] if not d["accepted"]
                ],
                "out_of_date": [
                    d["slug"] for d in state["documents"]
                    if d["accepted"] and not d["current"]
                ],
            }
        )
    return {
        "version": TERMS_VERSION,
        "count": len(rows),
        "accounts": len(STORE.list_tenants()),
        "outstanding": rows,
        "note": (
            "An acceptance pins the SHA-256 of the exact text. Changing a "
            "document does not invalidate the old agreement -- it means "
            "the customer is still on the old one, which is the thing "
            "worth knowing before somebody relies on a clause that was "
            "added afterwards."
        ),
    }
