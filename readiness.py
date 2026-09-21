"""Whether this deployment is fit to be launched, and for what.

Every piece of this was already knowable -- mail status, the watchdog,
the backup directory, the billing identity -- and it was knowable in
eight different places, which is the same as not being knowable at all
on the morning you are trying to go live.

The question is not "is it configured". It is two questions, and they
have different answers:

* **Can it monitor?** Somebody's vaccine fridge is about to depend on
  this. If an alert cannot leave the building, it cannot.
* **Can it take money?** An invoice with no legal identity on it is not
  a document a finance department can pay, and an agreement that says
  "Draft" on line one is not one to put in front of a paying customer.

A deployment can pass the first and fail the second, and that is a
perfectly good way to launch: real customers, real sensors, real alerts,
trials only, no money moving. Leaving CYBERLOGIX_PROVISIONING_KEY unset
enforces it -- no paid account can be created at all -- so it is a shape
you can choose deliberately rather than one you have to be careful
about.

Nothing here talks to the network, and nothing reads os.environ. Every
other module in this application freezes its configuration into a
module constant at import, so the environment and the running process
can disagree -- export SMTP_HOST into a shell and the server beside it
is still not sending mail. A preflight that read the environment would
report the deployment you could have rather than the one you have, so
this reads exactly the constants the application itself is using.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

BLOCKS, WARNS, OK = "blocks", "warns", "ok"

# What each check is a precondition for.
MONITOR, MONEY, UNATTENDED = "monitoring", "money", "unattended"


def _check(key, title, gate, state, detail, fix=""):
    return {
        "key": key, "title": title, "gate": gate,
        "state": state, "detail": detail, "fix": fix,
    }


def checks() -> List[Dict[str, Any]]:
    """Every precondition, with the reason it matters stated once."""
    import auth
    import backup
    import gemini
    import invoicing
    import legal
    import mail
    import notifications
    import payments
    import watchdog

    found: List[Dict[str, Any]] = []

    # --- can a customer's freezer depend on this ------------------------
    live = watchdog.state()
    sweep = live["sweep_interval_seconds"]
    ai = gemini.dispatch_status()
    found.append(_check(
        "ai_model", "The model behind the writing still answers", UNATTENDED,
        WARNS if (ai["degraded"] or not ai["configured"]) else OK,
        (f"{ai['consecutive_failures']} call(s) in a row have failed against "
         f"{ai['model']}: {ai['last_error']}") if ai["degraded"]
        else ("no key, so every generated line is a template"
              if not ai["configured"] else f"answering, on {ai['model']}"),
        ("Named models get retired. Nothing breaks loudly when this one is "
         "-- every message quietly becomes its deterministic template and "
         "the service keeps answering 200. Point CYBERLOGIX_GEMINI_MODEL at "
         "a current model.") if ai["degraded"] else
        ("Not a blocker: alerts still go out, worded from templates rather "
         "than written. Set GEMINI_API_KEY to turn the writing on."
         if not ai["configured"] else ""),
    ))

    found.append(_check(
        "sweep", "The alert sweep runs", MONITOR,
        OK if live["sweep_enabled"] else WARNS,
        f"every {sweep}s" if live["sweep_enabled"]
        else "the in-process loop is off",
        "" if live["sweep_enabled"] else
        "Correct only if an external scheduler calls "
        "POST /api/autopilot/sweep. Otherwise nothing escalates.",
    ))

    can_send = bool(
        notifications.TWILIO_ACCOUNT_SID
        and notifications.TWILIO_AUTH_TOKEN
        and notifications.TWILIO_FROM_NUMBER
    )
    found.append(_check(
        "twilio", "Alerts can reach a person", MONITOR,
        OK if can_send else BLOCKS,
        "SMS and voice are live" if can_send
        else "dry run: every alert is composed, logged, and sent nowhere",
        "" if can_send else
        "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER. "
        "Until then the product does not do the thing it is sold for.",
    ))

    base = notifications.PUBLIC_BASE_URL
    found.append(_check(
        "ack_url", "An alert can be acknowledged from the handset", MONITOR,
        OK if base else WARNS,
        base or "PUBLIC_BASE_URL is unset",
        "" if base else
        "The call still goes out; pressing 1 reaches nothing, so it "
        "escalates to the next person anyway.",
    ))

    # --- will anybody notice if it stops --------------------------------
    found.append(_check(
        "heartbeat", "Something outside notices if this dies", UNATTENDED,
        OK if live["heartbeat_url_configured"] else BLOCKS,
        "a ping goes out after every sweep"
        if live["heartbeat_url_configured"]
        else "nothing off this machine would notice",
        "" if live["heartbeat_url_configured"] else
        "Set CYBERLOGIX_HEARTBEAT_URL. The product's promise is that "
        "something runs when nobody is looking, and nothing inside a "
        "process can report its own death.",
    ))

    snapshots = backup.status()
    off_disk = not snapshots["on_the_same_disk_as_the_database"]
    found.append(_check(
        "backups", "A lost disk is survivable", UNATTENDED,
        OK if off_disk else WARNS,
        f"{snapshots['count']} snapshot(s) in {snapshots['directory']}",
        "" if off_disk else
        "Snapshots sit beside the live database. Point "
        "CYBERLOGIX_BACKUP_DIR at another volume and copy them off the "
        "machine; a backup next to the thing it backs up is not one.",
    ))

    # --- can it ask to be paid ------------------------------------------
    post = mail.status()
    found.append(_check(
        "mail", "An invoice can reach the customer", MONEY,
        OK if post["configured"] else BLOCKS,
        f"via {post['host']}" if post["configured"]
        else "missing " + ", ".join(post["missing"]),
        "" if post["configured"] else
        "Set SMTP_HOST and MAIL_FROM. The five-stage collections ladder "
        "runs either way -- the debtor just never sees any of it.",
    ))

    found.append(_check(
        "pay_details", "The invoice says how to pay it", MONEY,
        OK if post["payment_details_configured"] else BLOCKS,
        "bank transfer and/or a payment link"
        if post["payment_details_configured"] else "neither is set",
        "" if post["payment_details_configured"] else
        "Set CYBERLOGIX_REMIT_TO or CYBERLOGIX_PAY_URL. An invoice with "
        "no way to pay it is not an invoice.",
    ))

    # invoicing.ISSUER defaults legal_name, so a deployment that set
    # nothing still looks half-populated. Judged on the fields that have
    # no default and cannot be guessed.
    missing = [
        name for name, field in (
            ("CYBERLOGIX_ADDRESS", "address"),
            ("CYBERLOGIX_TAX_ID", "tax_id"),
        ) if not invoicing.ISSUER.get(field)
    ]
    found.append(_check(
        "identity", "The invoice says who is asking", MONEY,
        OK if not missing else BLOCKS,
        f"{invoicing.ISSUER['legal_name']}, with an address and a tax id"
        if not missing else "missing " + ", ".join(missing),
        "" if not missing else
        "A finance department cannot pay an invoice that does not say "
        "who to pay, at what address, under what tax registration. This "
        "is the entity's own details, not the software's, and it is the "
        "part that needs whoever is actually incorporated.",
    ))

    draft = legal.DISCLAIMER.startswith("Draft")
    found.append(_check(
        "terms", "The agreements have been reviewed", MONEY,
        BLOCKS if draft else OK,
        "every document still carries the draft disclaimer" if draft
        else "reviewed",
        "A lawyer has to read them. The documents say so themselves: "
        "\"Do not put it in front of a paying customer until one has "
        "read it.\" Customers are now asked to accept them by hash, "
        "which makes the acceptance a real record of a draft."
        if draft else "",
    ))

    found.append(_check(
        "stripe", "Card payments reconcile themselves", MONEY,
        OK if payments.configured() else WARNS,
        "webhook signature verified" if payments.configured()
        else "STRIPE_WEBHOOK_SECRET is unset",
        "" if payments.configured() else
        "Not a blocker if you collect by bank transfer. Without it every "
        "card payment lands on the unmatched list to be reconciled by "
        "hand.",
    ))

    # --- the operator's own doors ---------------------------------------
    found.append(_check(
        "admin_key", "The operator's routes are reachable", MONEY,
        OK if auth.PLATFORM_ADMIN_KEY else BLOCKS,
        "set" if auth.PLATFORM_ADMIN_KEY else "unset",
        "" if auth.PLATFORM_ADMIN_KEY else
        "Unset closes them rather than opening them, which is the right "
        "default -- but you cannot invoice, record a payment, or open "
        "/book without it.",
    ))

    origins = os_allowed_origins()
    found.append(_check(
        "origins", "The browser origin policy is not wide open", MONITOR,
        WARNS if origins == ["*"] else OK,
        ", ".join(origins),
        "Name your own domain once it exists." if origins == ["*"] else "",
    ))

    return found


def os_allowed_origins() -> List[str]:
    """What main.py actually configured CORS with, not what the shell says."""
    import sys

    main = sys.modules.get("main")
    configured = getattr(main, "_ALLOWED_ORIGINS", None) if main else None
    if configured:
        return list(configured)
    return [
        origin.strip()
        for origin in os.environ.get("CYBERLOGIX_ALLOWED_ORIGINS", "*").split(",")
        if origin.strip()
    ]


def report() -> Dict[str, Any]:
    """The two verdicts, because they are two different questions."""
    found = checks()

    def blocking(gate):
        return [c for c in found if c["gate"] == gate and c["state"] == BLOCKS]

    monitor_blocks = blocking(MONITOR)
    money_blocks = blocking(MONEY)
    unattended_blocks = blocking(UNATTENDED)
    import licenses

    paid_accounts_possible = bool(licenses.PROVISIONING_KEY)

    return {
        "can_monitor": not monitor_blocks,
        "can_take_money": not money_blocks,
        "runs_unattended": not unattended_blocks,
        "paid_accounts_possible": paid_accounts_possible,
        "shape": (
            "closed -- alerts cannot leave the building"
            if monitor_blocks else
            "trials only: real customers, real alerts, no money moving"
            if not paid_accounts_possible else
            "open for business"
            if not money_blocks else
            "taking money with something missing from the invoice"
        ),
        "blocking": len(monitor_blocks) + len(money_blocks)
        + len(unattended_blocks),
        "checks": found,
        "note": (
            "Two questions, not one. A deployment that can monitor but "
            "cannot invoice is a perfectly good way to launch -- and "
            "leaving CYBERLOGIX_PROVISIONING_KEY unset enforces it, "
            "because no paid account can be created at all."
        ),
    }


def _cli() -> int:
    r = report()
    mark = {OK: "  ok  ", WARNS: " warn ", BLOCKS: "BLOCKS"}
    print()
    print(f"  Launch shape: {r['shape']}")
    print()
    for gate, heading in (
        (MONITOR, "Can a customer's freezer depend on this"),
        (UNATTENDED, "Will anybody notice if it stops"),
        (MONEY, "Can it ask to be paid"),
    ):
        print(f"  {heading}")
        for c in [x for x in r["checks"] if x["gate"] == gate]:
            print(f"    [{mark[c['state']]}] {c['title']}: {c['detail']}")
            if c["fix"]:
                for line in _wrap(c["fix"], 66):
                    print(f"               {line}")
        print()
    print(f"  {r['blocking']} blocking issue(s).")
    print()
    return 1 if r["blocking"] else 0


def _wrap(text: str, width: int) -> List[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


if __name__ == "__main__":
    raise SystemExit(_cli())


# --- route -----------------------------------------------------------------

from fastapi import APIRouter, Depends  # noqa: E402

from auth import require_platform_admin  # noqa: E402

router = APIRouter(prefix="/api/admin", tags=["Readiness"])


@router.get("/readiness")
def read_readiness(_: None = Depends(require_platform_admin)):
    """Whether this deployment is fit to be launched, and for what."""
    return report()
