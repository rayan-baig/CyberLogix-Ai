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
that implement them. If the cap changes in `assurance.py`, the agreement
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

from assurance import (
    ASSURANCE_MONTHLY_PER_UNIT_USD,
    ASSURANCE_PAYOUT_CAP_USD,
    COVER_LAPSES_AFTER_MINUTES,
    DISPATCH_SLA_SECONDS,
)
from accounts import require_role
from auth import require_tenant_any_state, write_audit
from contracts import (
    DELINQUENT_AFTER_DAYS,
    LATE_FEE_MONTHLY_PERCENT,
    REMINDER_DAYS,
)
from backup import BACKUP_KEEP
from invoicing import PAYMENT_TERMS_DAYS, issuer_block
from store import (
    SENSOR_OFFLINE_AFTER_MINUTES,
    STORE,
    Tenant,
    User,
    VOICE_ESCALATION_GRACE_MINUTES,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/legal", tags=["Legal"])

# Bumped by hand when the *meaning* changes. The body hash moves on its own
# whenever a figure underneath it does, which is the point: a customer can
# tell a rewording from a re-pricing.
# Bumped when the documents say something materially different, not when
# a sentence is tidied. 1.1 added the mail host as a sub-processor and
# said honestly what a backup does to a deletion request — both are
# things a customer would want to be asked about again rather than have
# quietly changed under an acceptance they already gave.
TERMS_VERSION = "1.1"
EFFECTIVE_DATE = "2026-09-12"

# The single most valuable clause in the whole set, and the reason this
# module exists at all.
#
# A cryostorage customer's tank is worth more than this company will earn
# in its lifetime. Without a cap, one claim ends everything — not the
# contract, the company. With one, the exposure is a known number that can
# be insured against and priced into the rate.
#
# Twelve months of fees is the ordinary enterprise-software position and
# the one a customer's counsel expects to see. The Loss Assurance add-on
# sits *outside* it deliberately: that is the product actually accepting a
# defined risk, and burying it under the general cap would make the
# add-on meaningless.
LIABILITY_CAP_MONTHS = 12

# Uptime the platform commits to, and what missing it costs. Credits, not
# damages — the standard structure, and one that cannot compound into
# something unpayable.
UPTIME_TARGET_PERCENT = 99.5
SERVICE_CREDIT_TABLE = [
    (99.5, 99.0, 10),
    (99.0, 95.0, 25),
    (95.0, 0.0, 50),
]
MAX_CREDIT_PERCENT = 50

DISCLAIMER = (
    "Draft. Generated from the running system so the figures cannot drift "
    "from the product, but not reviewed by a lawyer. Do not put it in "
    "front of a paying customer until one has read it."
)


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


# ---- the documents -----------------------------------------------------


def terms_of_service() -> str:
    return f"""# Master Subscription Agreement

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

This agreement is between {issuer_block().get('legal_name')} ("we", "us")
and the organisation that subscribes to the service ("you").

## 1. What the service is

We monitor temperature and humidity readings sent to us by sensors you
operate, compare them against limits you set, and attempt to notify the
people you nominate when a reading falls outside those limits.

## 2. What the service is not

**It is not a substitute for your own equipment, alarms, or staff.** We
observe your equipment; we do not control it, repair it, or operate it.
A failure we do not detect, or detect but cannot deliver a message about,
is still your equipment's failure. You remain responsible for the goods
in your care.

**It depends on things we do not control.** Notifications travel over
mobile networks and the public internet, and both fail. Sensors run on
batteries and go silent. If a person you nominated does not answer their
phone, we cannot make them answer it.

**It is not certified.** Nothing in the service is approved as a medical
device, a safety instrumented system, or a substitute for a regulatory
control required of you.

## 3. What we commit to

We will attempt to dispatch a notification within {DISPATCH_SLA_SECONDS}
seconds of receiving a reading that breaches a limit you set. An
unacknowledged breach is escalated to a voice call after
{VOICE_ESCALATION_GRACE_MINUTES:g} minutes. A sensor that has not reported
for {SENSOR_OFFLINE_AFTER_MINUTES} minutes is reported to you as offline.

Availability of the platform is covered by the Service Level Commitment,
which is part of this agreement.

## 4. What you commit to

* Keep at least one reachable person on the on-call roster, with a
  correct phone number.
* Keep sensor batteries above the warning level and act when we tell you
  a sensor has gone quiet.
* Set limits that reflect what you are actually storing.
* Tell us within thirty days if you dispute an invoice.

These are not formalities. Each of them is a way the service silently
stops working, and each is one we report to you before an event rather
than after one — see the Loss Assurance terms.

## 5. Fees

Fees are as set out in your order. Invoices are payable net
{PAYMENT_TERMS_DAYS} days. Overdue amounts carry a late charge of
{LATE_FEE_MONTHLY_PERCENT:g}% per month, or the maximum your jurisdiction
allows if that is lower.

An account more than {DELINQUENT_AFTER_DAYS} days overdue loses access to
reporting and certification features — benchmarks, attestations, exports —
until the balance is cleared.

**Monitoring, alerting and escalation are never withheld for non-payment.**
We will chase you for money. We will not stop watching your freezers to do
it.

## 6. Your data

You own your readings. We store them to run the service, and we keep the
hash chain that makes them tamper-evident. What we share, with whom, and
for how long we keep it is in the Privacy and Data Processing statement.

If you leave, you can export everything, and we delete it on request —
except records we are required to keep, and the anonymised aggregates
already contributed to sector benchmarks, which cannot be traced back to
you and cannot be unmixed.

## 7. Limitation of liability

**Read this clause. It is the one that matters.**

Except for the Loss Assurance add-on, which is a separate and deliberate
commitment described in its own terms, our total liability to you for
everything arising out of this agreement is limited to the fees you paid
us in the {LIABILITY_CAP_MONTHS} months before the claim.

We are not liable for lost profits, lost revenue, lost or spoiled goods,
business interruption, or any indirect or consequential loss, even where
we were told it could happen.

This limit does not apply to death or personal injury caused by our
negligence, to fraud, or to anything else a court will not allow us to
limit.

**Why this is here, in plain terms.** A single tank of stored embryos is
worth more than this company will ever earn. If one claim could be
unlimited, no monitoring service could exist at this price — and the
alternative you would be offered is no service at all. The cap is what
makes the price possible. The Loss Assurance add-on is where we accept a
defined, funded share of that risk on purpose.

## 8. No warranty beyond this

The service is provided as described here and in the Service Level
Commitment, and otherwise without warranty. We do not promise it will be
uninterrupted or error-free.

## 9. Term and termination

Your order sets the term. Either of us may terminate for a material breach
the other has not fixed within thirty days of being told about it. On
termination you owe the fees already invoiced; we stop billing you for
periods after it.

## 10. General

Neither of us is liable for a failure caused by something genuinely
outside our control. Notices go to the addresses on the order. This
agreement is the whole of what we have agreed, and changes to it have to
be in writing.
"""


def assurance_terms() -> str:
    return f"""# Loss Assurance Terms

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

Loss Assurance is an optional add-on at
{_fmt_money(ASSURANCE_MONTHLY_PER_UNIT_USD)} per covered unit per month.

## What it commits us to

If a covered unit records a breach and **no alert is dispatched to
anybody**, we reimburse your insurance deductible for that event, up to
{_fmt_money(ASSURANCE_PAYOUT_CAP_USD)}.

This sits outside the general limitation of liability in the Master
Subscription Agreement. It is a real commitment, funded by the fee, and
capping it under the general limit would make it worthless.

## What it does not commit us to

It pays a deductible. It is not insurance, we are not an insurer, and it
does not make you whole for the loss itself. Keep your own cover.

It pays when **we** failed to raise the alarm. It does not pay when the
alarm was raised and nobody acted on it.

## What voids cover, and when you find out

Cover on a unit lapses while any of the following is true:

* the sensor has not reported for {COVER_LAPSES_AFTER_MINUTES} minutes —
  a silent sensor cannot warn anyone;
* its battery is below the warning level;
* it has never reported a reading;
* nobody is on the on-call roster for its site;
* the account is on a trial rather than a paid plan.

**Every one of these is computed continuously and shown to you before an
event, not produced as an excuse after one.** `GET /api/assurance/cover`
returns the live list, naming the specific unit and the specific reason.
A guarantee whose exclusions only surface at claim time is a trick. This
one tells you what to fix this morning.

## Making a claim

Tell us within thirty days of the event. We produce the evidence packet
from the tamper-evident record — the readings, the hash chain, and what
was or was not dispatched — and it is the same record either way, whether
it helps our case or yours.
"""


def service_level() -> str:
    rows = "\n".join(
        f"| below {high:g}% down to {low:g}% | {credit}% |"
        for high, low, credit in SERVICE_CREDIT_TABLE
    )
    return f"""# Service Level Commitment

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## The commitment

The ingest and alerting path will be available at least
{UPTIME_TARGET_PERCENT:g}% of each calendar month.

"Available" means we accept readings and attempt to dispatch alerts.
Scheduled maintenance announced at least 48 hours ahead does not count
against it, and neither does an outage in a network or telephony carrier
we do not operate.

## If we miss it

| Monthly availability | Credit against that month's fee |
|---|---|
{rows}

Credits are the only remedy for missed availability, cap out at
{MAX_CREDIT_PERCENT}% of the month, and are applied to your next invoice.
Ask for one within thirty days of the month in question.

## What is deliberately not in here

We do not commit to delivering a message to a human being. We commit to
attempting dispatch within {DISPATCH_SLA_SECONDS} seconds. Carriers drop
texts and people silence phones, and a commitment we cannot keep is worth
less than an honest one — which is exactly why the escalation ladder ends
in a voice call after {VOICE_ESCALATION_GRACE_MINUTES:g} minutes rather
than in a promise.
"""


def privacy_statement() -> str:
    return f"""# Privacy and Data Processing

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## What we hold

**Readings.** Temperature, humidity, battery level, a timestamp, and which
sensor sent them. This is equipment data, not personal data.

**People.** For each person on your on-call roster: name, mobile number,
email, role. This is personal data, and it exists for one purpose — so we
can reach a human being when your equipment fails.

**Operators.** Name, email and a hashed password for each person who signs
in, plus an audit record of what they did.

**Mail we send you.** The address, the subject and the body of every
invoice, notice and report we send, and whether the mail host accepted
it. We keep it so that "we never received that invoice" has an answer
other than both of us guessing. If an address bounces or you unsubscribe,
we keep that address on a suppression list — deleting the record of your
having said stop would mean writing to you again.

We do not sell any of it, and we do not use it to train anything.

## Who else sees it

| Sub-processor | What reaches them | Why |
|---|---|---|
| Twilio | The mobile number, and the text of the alert (which names the site and the temperature) | Sending the text and placing the call |
| Google (Gemini API) | The wording of an alert being drafted: the sector, the reading and the limit | Writing the alert in language the recipient will act on |
| Our mail host | The recipient's address and the whole message, which for an invoice is what you owe and for a weekly report is how many readings your estate took and which units went quiet | Delivering it |

Sensor readings in bulk, your customer list, and your operators'
credentials are sent to neither.

## Anonymised benchmarks

If you use Sector Benchmarks, your estate's aggregate statistics — how
often units breach, how quickly they are acknowledged — are mixed into
sector-wide figures. Nothing identifying you goes in, and a figure that
could be traced back to a single operator is suppressed rather than shown.
Once mixed, a contribution cannot be extracted again.

## How long

Readings and incidents are kept for the life of the account and for seven
years after it, because that is how long an insurer or an inspector may
ask about an event. Audit records the same. Sign-in sessions expire on
their own. Ask us to delete and we delete everything we are not required
to keep.

**Backups are the honest exception.** We take a verified snapshot of the
whole database daily and keep the last {BACKUP_KEEP}. A deletion takes
effect immediately in the live system and then propagates as those
snapshots age out, so the last copy of deleted data is gone within
{BACKUP_KEEP} days. We do not restore a backup to bring back something
you asked us to delete. Saying "we delete everything" without this
paragraph would have been untrue of any system that can survive losing a
disk, which is every system worth trusting with a compliance record.

## Your rights

Ask for a copy, a correction, or a deletion, and we will do it. Everything
in the account is exportable through the API without asking us at all.

## Where it lives

On our infrastructure and our sub-processors'. Tell us before you sign if
you need it kept in a particular country — we would rather say no than
find out afterwards.
"""


def acceptable_use() -> str:
    reminders = ", ".join(f"day {d}" for d in REMINDER_DAYS)
    return f"""# Acceptable Use and Operating Requirements

**Version {TERMS_VERSION} · Effective {EFFECTIVE_DATE}**

> {DISCLAIMER}

## Do not

* Point the service at equipment where a missed alert would injure or kill
  someone, unless an independent safety system is also in place. We are a
  second pair of eyes, never the only pair.
* Register sensors you do not operate, or send readings for someone else's
  estate.
* Use alert routing to send anything that is not an operational alert.
  The on-call roster is not a marketing list, and abusing it will get the
  telephony account shut down for every customer on it.
* Share one login between people. Every operator gets their own, because
  the audit record is only worth something if a name in it means a person.

## Keep working

* At least one reachable person on the roster, per site.
* Batteries above the warning level.
* Limits set to what you actually store.
* Sensor ingest keys kept secret, and rotated when someone leaves.

## If you do not pay

Reminders go out on {reminders} past due. A late charge of
{LATE_FEE_MONTHLY_PERCENT:g}% per month applies from day
{REMINDER_DAYS[2]}. At {DELINQUENT_AFTER_DAYS} days past due the account
is delinquent and reporting features are withheld.

Monitoring and alerting continue regardless. We will not put your stored
goods at risk to collect an invoice.
"""


DOCUMENTS = {
    "terms": ("Master Subscription Agreement", terms_of_service),
    "assurance": ("Loss Assurance Terms", assurance_terms),
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
            f"{LIABILITY_CAP_MONTHS} months of fees, with Loss Assurance "
            "outside it."
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


@router.get("/acceptance/status")
def acceptance_status(tenant: Tenant = Depends(require_tenant_any_state)):
    """Whether this customer has accepted the text that is live today.

    A stale acceptance is worth knowing about: if the payout cap moved
    after they signed, the hash they accepted no longer matches, and the
    honest thing is to ask them again rather than to assume.
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
