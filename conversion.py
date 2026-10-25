"""Asking for the order, without anybody having to remember to.

The book at `/book` was already able to say *"this trial ends on
Thursday, call them"*. That is worth having, and it assumes a person who
opens it on Thursday. A trial that ends over a weekend, or while the one
person who reads the book is at school, is a customer who evaluated the
product, found it worked, and then heard nothing at all.

This module is the other half: the trial talks to the customer itself.
Six moments, each sent once:

* **Welcome**, the minute they sign up — with the four commands that
  register a sensor, because a trial with no sensor in it never converts
  and the most common reason is that nobody got past the first step.
* **Activation**, two days in with nothing registered. The same nudge,
  aimed at the trials that are already dying.
* **A week left**, **three days left**, **the last day** — each carrying
  what the trial has actually caught, and the exact monthly price for the
  units they have running. Not a brochure figure: the rate card applied
  to their estate.
* **Grace**, the day it expires, and **dark**, the day monitoring stops.

Every message is keyed on the tenant and the moment, so a pass that runs
twice, or a deployment that restarts mid-sweep, sends one of each. A
missed window is skipped rather than caught up on: a customer who gets
"three days left" and "your trial ended" in the same hour learns that
nothing here is really being watched.

The whole ladder is commercial mail. Unsubscribing stops it, and should.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from auth import LICENCE_GRACE_DAYS, licence_state, require_platform_admin
from mail import send as send_mail
from store import STORE, Tenant, iso, utc_now

logger = logging.getLogger("cyberlogix.conversion")

router = APIRouter(prefix="/api/conversion", tags=["Conversion"])

# A trial with nothing registered this long after sign-up is not
# evaluating the product; it is stuck on the first step. Two days is long
# enough not to badger somebody who signed up on a Friday afternoon.
ACTIVATION_AFTER_DAYS = 2


def _console_url() -> str:
    from mail import PUBLIC_BASE_URL

    return f"{PUBLIC_BASE_URL}/console" if PUBLIC_BASE_URL else "your console"


def trial_evidence(tenant: Tenant) -> Dict[str, Any]:
    """What this trial has actually done, in numbers we can stand behind.

    Every figure comes from the tenant's own records. Nothing here is a
    projection, an industry average, or a number from the marketing page.
    That matters more than it sounds: the one email that closes a
    monitoring contract is the one that says *we caught three excursions
    on your fridges last week*, and it only works if that is true.
    """
    sensors = STORE.sensors_for(tenant.tenant_id)
    since = tenant.activated_at
    readings = 0
    for sensor in sensors:
        readings += len(STORE.readings_for(sensor.sensor_id, since=since))

    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    escalated = sum(1 for i in incidents if i.voice_escalated_at is not None)

    from pricing import build_subscription

    priced = build_subscription(tenant)

    return {
        "units": len(sensors),
        "readings": readings,
        "incidents": len(incidents),
        "escalated": escalated,
        "monthly_usd": priced["monthly_total_usd"],
        "annual_usd": round(priced["monthly_total_usd"] * 12, 2),
        "unit_noun": priced["line_items"][0]["unit"] if priced["line_items"] else "unit",
    }


def _caught_line(ev: Dict[str, Any]) -> str:
    """The sentence that does the selling, or an honest one when it can't."""
    if ev["incidents"]:
        detail = (
            f"{ev['incidents']} excursion"
            f"{'' if ev['incidents'] == 1 else 's'} caught"
        )
        if ev["escalated"]:
            detail += (
                f", {ev['escalated']} of them escalated to a phone call when "
                "the text went unanswered"
            )
        return (
            f"So far your trial has taken {ev['readings']:,} readings across "
            f"{ev['units']} unit{'' if ev['units'] == 1 else 's'}, with "
            f"{detail}."
        )
    if ev["readings"]:
        return (
            f"So far your trial has taken {ev['readings']:,} readings across "
            f"{ev['units']} unit{'' if ev['units'] == 1 else 's'} and found "
            "nothing out of range. That is the result you want, and it is "
            "also the one nobody can prove without a record of it."
        )
    return (
        "Your trial has not taken any readings yet, so there is nothing to "
        "show you — which is worth fixing before it ends."
    )


def _price_line(ev: Dict[str, Any]) -> str:
    if ev["monthly_usd"] <= 0:
        return (
            "Pricing is per unit monitored, so the figure depends on what "
            "you register. Reply and we will price your estate."
        )
    return (
        f"Keeping it running costs ${ev['monthly_usd']:,.2f} a month at the "
        f"{ev['units']} unit{'' if ev['units'] == 1 else 's'} you have live "
        f"— ${ev['annual_usd']:,.2f} a year — and moves with your estate "
        "rather than being a step you have to renegotiate."
    )


def _vertical_for(tenant: Tenant) -> str:
    """The sector to speak to them in.

    What they said at sign-up, then whatever they actually registered,
    then a default. The order matters: a customer who told us they run
    restaurants and has since registered a vaccine fridge is doing both,
    and the words on the sign-up form are the ones they chose.
    """
    from store import INDUSTRY_PROFILES

    stated = (tenant.industry_vertical or "").strip().lower()
    if stated in INDUSTRY_PROFILES:
        return stated
    for sensor in STORE.sensors_for(tenant.tenant_id):
        if sensor.industry_vertical in INDUSTRY_PROFILES:
            return sensor.industry_vertical
    return "restaurant"


def _setup_steps(tenant: Tenant) -> str:
    """The four commands, rendered for a plain-text email.

    The same ones the sign-up response returns, from the same function —
    not a second copy that drifts out of date the first time the API
    changes shape.
    """
    from signup import first_steps

    try:
        guide = first_steps(tenant.api_key, _vertical_for(tenant))
    except Exception:  # noqa: BLE001 - a nudge must not die on formatting
        logger.exception("Could not build first steps for %s.", tenant.tenant_id)
        return f"Open {_console_url()} and register your first unit."

    # The API response leaves $HOST for the reader to fill in, which is
    # right in a terminal and wrong in an email: a command somebody has to
    # edit before it will run is a step that does not get taken.
    from mail import PUBLIC_BASE_URL

    host = PUBLIC_BASE_URL or "$HOST"

    lines = []
    for number, step in enumerate(guide.get("steps", []), start=1):
        lines.append(f"{number}. {step.get('title', '').strip()}")
        detail = (step.get("detail") or "").strip()
        if detail:
            lines.append(f"   {detail}")
        command = step.get("command")
        if command:
            lines.append(f"   {command.replace('$HOST', host)}")
        # A step with no command is one that needs a signed-in person.
        # There is no session to print into an email, so it points at
        # the console rather than at a command that would 401.
        where = step.get("where")
        if where:
            lines.append(f"   {where.replace('$HOST', host)}")
        lines.append("")
    if not lines:
        return f"Open {_console_url()} and register your first unit."
    return "\n".join(lines).rstrip()


# --- the ladder -----------------------------------------------------------


def welcome(tenant: Tenant) -> Dict[str, Any]:
    """Sent the moment a trial is created, from the sign-up handler.

    Not part of the sweep: waiting up to an hour to tell somebody how to
    start the thing they just signed up for wastes the only moment they
    are certain to be paying attention.
    """
    return send_mail(
        to_address=tenant.contact_email,
        subject=f"{tenant.company_name}: your CyberLogix trial is live",
        body=(
            f"{tenant.contact_name},\n\n"
            "Your trial estate is up. It runs for "
            f"{tenant.entitlements()['term_days']} days and needs no card.\n\n"
            "The fastest way to see whether this is useful is to put one "
            "real sensor on it — a fridge, a freezer, a server room, "
            "whatever would cost you the most to lose:\n\n"
            f"{_setup_steps(tenant)}\n\n"
            f"Everything else lives at {_console_url()}.\n\n"
            "If a step does not work, reply to this message. A trial that "
            "stalls on step one is our problem, not yours."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:welcome",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _activation(tenant: Tenant, ev: Dict[str, Any]) -> Dict[str, Any]:
    return send_mail(
        to_address=tenant.contact_email,
        subject=f"{tenant.company_name}: nothing registered yet",
        body=(
            f"{tenant.contact_name},\n\n"
            "Your trial is open but no units are registered, so there is "
            "nothing for it to watch and nothing for you to judge it on.\n\n"
            "One unit is enough to tell whether this is worth having:\n\n"
            f"{_setup_steps(tenant)}\n\n"
            "If the hold-up is the hardware, say so — the platform takes "
            "readings from anything that can make an HTTP request, "
            "including a phone, and we would rather you tested it with "
            "whatever you already own than bought something to find out."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:activation",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _week(tenant: Tenant, ev: Dict[str, Any], days: int) -> Dict[str, Any]:
    return send_mail(
        to_address=tenant.contact_email,
        subject=f"{tenant.company_name}: a week left on your trial",
        body=(
            f"{tenant.contact_name},\n\n"
            f"{_caught_line(ev)}\n\n"
            f"There are {days} days left. {_price_line(ev)}\n\n"
            f"To keep it running, open {_console_url()} and move the "
            "account onto Growth — it takes one click and nothing "
            "restarts. If you would rather have a contract with a term and "
            "a fixed rate, reply and we will send one.\n\n"
            "If it is not for you, that is a useful answer too. Telling us "
            "why is worth more to us than another reminder is to you."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:week",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _three(tenant: Tenant, ev: Dict[str, Any], days: int) -> Dict[str, Any]:
    return send_mail(
        to_address=tenant.contact_email,
        subject=(
            f"{tenant.company_name}: {days} day{'' if days == 1 else 's'} "
            "left on your trial"
        ),
        body=(
            f"{tenant.contact_name},\n\n"
            f"{_caught_line(ev)}\n\n"
            f"The trial ends in {days} day{'' if days == 1 else 's'}. "
            f"{_price_line(ev)}\n\n"
            "What happens at the end is deliberately not a cliff: "
            f"monitoring keeps running for {LICENCE_GRACE_DAYS} days after "
            "the trial expires, because a fridge does not stop needing "
            "watching on the day a licence lapses. After that it stops.\n\n"
            f"Open {_console_url()} to continue, or reply and we will do it "
            "for you."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:three",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _last(tenant: Tenant, ev: Dict[str, Any], hours: float) -> Dict[str, Any]:
    # Phrased from hours rather than days. Days remaining is rounded up,
    # so a trial with two hours to run and one with twenty-three both
    # read as "1 day" — and telling somebody it ends tomorrow when it
    # ends before lunch is the kind of small lie that costs the sale.
    when = "today" if hours <= 12 else "tomorrow"
    return send_mail(
        to_address=tenant.contact_email,
        subject=f"{tenant.company_name}: your trial ends {when}",
        body=(
            f"{tenant.contact_name},\n\n"
            f"Your trial ends {when}. {_caught_line(ev)}\n\n"
            f"{_price_line(ev)}\n\n"
            f"Monitoring continues for {LICENCE_GRACE_DAYS} days after that "
            "so nothing goes unwatched while you decide. Your readings, "
            "your incident history and your audit record all stay where "
            "they are either way — nothing is deleted when a trial ends.\n\n"
            f"One click at {_console_url()} keeps it going."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:last",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _grace(tenant: Tenant, ev: Dict[str, Any], left: int) -> Dict[str, Any]:
    return send_mail(
        to_address=tenant.contact_email,
        subject=(
            f"{tenant.company_name}: monitoring stops in {left} "
            f"day{'' if left == 1 else 's'}"
        ),
        body=(
            f"{tenant.contact_name},\n\n"
            "Your trial has expired. Monitoring is still running — alerts "
            "still go out, escalation still calls — but only for another "
            f"{left} day{'' if left == 1 else 's'}.\n\n"
            f"{_caught_line(ev)}\n\n"
            f"{_price_line(ev)}\n\n"
            f"{_console_url()} will pick up exactly where it left off. "
            "Nothing has to be set up again."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:grace",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _dark(tenant: Tenant, ev: Dict[str, Any]) -> Dict[str, Any]:
    return send_mail(
        to_address=tenant.contact_email,
        subject=f"{tenant.company_name}: monitoring has stopped",
        body=(
            f"{tenant.contact_name},\n\n"
            "Monitoring on your estate has stopped. Nothing is being "
            "watched and no alert will be sent.\n\n"
            "This is the part worth saying plainly, because it is easy to "
            f"miss: your {ev['units']} registered "
            f"unit{'' if ev['units'] == 1 else 's'} "
            f"{'is' if ev['units'] == 1 else 'are'} now unmonitored.\n\n"
            "Your data has not been deleted. Everything the trial recorded "
            "is still here and comes straight back if you restart the "
            f"account at {_console_url()}.\n\n"
            "This is the last message we will send about it."
        ),
        dedupe_key=f"trial:{tenant.tenant_id}:dark",
        klass="commercial",
        tenant_id=tenant.tenant_id,
    )


def _due_stage(
    tenant: Tenant, now: datetime
) -> Optional[str]:
    """Which single message this account has earned, if any.

    One at a time, and the most advanced one that fits. A pass that has
    not run for a week must not deliver the whole ladder in one minute:
    the customer would correctly conclude that nothing here is actually
    watching anything.
    """
    state = licence_state(tenant, now)
    if state == "suspended":
        return None
    if state == "lapsed":
        return "dark"
    if state == "grace":
        return "grace"

    if tenant.expires_at is None:
        return None
    days = math.ceil((tenant.expires_at - now).total_seconds() / 86400)

    if days <= 1:
        return "last"
    if days <= 3:
        return "three"
    if days <= 7:
        return "week"
    if (
        STORE.seat_count(tenant.tenant_id) == 0
        and now - tenant.activated_at >= timedelta(days=ACTIVATION_AFTER_DAYS)
    ):
        return "activation"
    return None


def run_trial_conversion(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Work the whole trial book once. Called from the hourly money pass.

    Trials only. A paying customer's renewal is a conversation with a
    contract behind it, and the book already surfaces it for a person to
    have; automating a mail sequence at somebody mid-term would be a
    worse experience than the silence it replaced.
    """
    now = now or utc_now()
    sent: List[Dict[str, Any]] = []
    failures: List[str] = []

    for tenant in STORE.list_tenants():
        if tenant.plan != "trial":
            continue
        try:
            stage = _due_stage(tenant, now)
            if stage is None:
                continue
            evidence = trial_evidence(tenant)
            result = _send_stage(tenant, stage, evidence, now)
            if result.get("sent"):
                sent.append(
                    {
                        "tenant_id": tenant.tenant_id,
                        "company_name": tenant.company_name,
                        "stage": stage,
                        "units": evidence["units"],
                        "monthly_usd": evidence["monthly_usd"],
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one trial must not stop the rest
            logger.exception(
                "Trial conversion failed for %s (%s).", tenant.tenant_id, exc
            )
            failures.append(tenant.tenant_id)

    if sent:
        logger.info(
            "Trial conversion: %d message(s) sent, $%.2f of monthly revenue "
            "asked for.",
            len(sent),
            sum(s["monthly_usd"] for s in sent),
        )
    return {
        "sent": sent,
        "sent_count": len(sent),
        "failed_tenants": failures,
    }


def _send_stage(
    tenant: Tenant, stage: str, evidence: Dict[str, Any], now: datetime
) -> Dict[str, Any]:
    from auth import grace_days_left

    if stage == "activation":
        return _activation(tenant, evidence)
    if stage == "dark":
        return _dark(tenant, evidence)
    if stage == "grace":
        return _grace(tenant, evidence, max(grace_days_left(tenant, now), 0))

    remaining = (tenant.expires_at - now).total_seconds()
    days = max(math.ceil(remaining / 86400), 0)
    if stage == "week":
        return _week(tenant, evidence, days)
    if stage == "three":
        return _three(tenant, evidence, days)
    return _last(tenant, evidence, max(remaining / 3600.0, 0.0))


@router.post("/run")
def run(_: None = Depends(require_platform_admin)):
    """Work the trial book now, rather than waiting for the hourly pass."""
    return run_trial_conversion()


@router.get("/due")
def due(_: None = Depends(require_platform_admin)):
    """What the next pass would send, without sending it.

    Worth having before an automated sequence writes to every customer on
    the book: the first thing anybody sensibly wants from a mail robot is
    to see what it intends to say.
    """
    now = utc_now()
    rows = []
    for tenant in STORE.list_tenants():
        if tenant.plan != "trial":
            continue
        try:
            stage = _due_stage(tenant, now)
        except Exception:  # noqa: BLE001 - a preview must not fail on one row
            logger.exception("Could not stage %s.", tenant.tenant_id)
            continue
        if stage is None:
            continue
        rows.append(
            {
                "tenant_id": tenant.tenant_id,
                "company_name": tenant.company_name,
                "stage": stage,
                "already_sent": stage in _stages_sent(tenant.tenant_id),
                "units": STORE.seat_count(tenant.tenant_id),
            }
        )
    return {"generated_at": iso(now), "due": rows, "count": len(rows)}


def _stages_sent(tenant_id: str) -> set:
    prefix = f"trial:{tenant_id}:"
    return {
        m.dedupe_key[len(prefix):]
        for m in STORE.mail_log(limit=100000)
        if m.dedupe_key.startswith(prefix)
    }
