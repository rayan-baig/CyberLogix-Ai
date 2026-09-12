"""The transport, and the four ways an automated mailer ruins a company.

Collections had a complete escalation ladder that delivered nothing: the
stages were claimed, the notices were composed, and the customer was
never told. The tests here are mostly about what happens now that it does
send — because a billing robot with a mail socket can do considerably
worse than silence.

  * chase the same invoice twice and read as either sloppy or dishonest;
  * report a chase that never left the process;
  * keep writing to an address the host has already refused, until the
    sending domain is blocklisted and none of the mail arrives;
  * let a value somebody typed into the sign-up form become a mail header.
"""

import smtplib
from datetime import timedelta

import pytest

import mail
from contracts import run_billing, run_dunning
from store import MAIL_MAX_AGE_HOURS, MAIL_MAX_ATTEMPTS, STORE, utc_now


def _sign(api, headers, owner, **body):
    payload = {"term_years": 1, "escalator_percent": 5.0}
    payload.update(body)
    resp = api.post("/api/contracts", headers={**headers, **owner}, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _age_invoice(invoice_id, days):
    invoice = STORE.get_invoice(invoice_id)
    invoice.due_at = utc_now() - timedelta(days=days)
    invoice.issued_at = invoice.due_at - timedelta(days=30)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    return invoice


@pytest.fixture()
def overdue(api, tenant_factory, sensor_factory, owner_headers):
    """A real tenant with a real invoice, thirty days past due."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    _age_invoice(invoice.invoice_id, 30)
    return tenant, invoice


# ---- the chase actually leaves the building ----------------------------


def test_a_chase_reaches_the_customer(mailbox, overdue):
    """The bug this module exists to fix: a demand nobody was ever sent."""
    tenant, invoice = overdue

    result = run_dunning()

    assert result["notices_count"] == 1
    assert result["sent"] == 1
    assert len(mailbox) == 1
    assert mailbox[0]["To"] == tenant["contact_email"]
    assert invoice.number in mailbox[0].get_content()


def test_the_notice_says_how_to_pay(mailbox, overdue, monkeypatch):
    """A demand with no remittance details is not a way to collect money."""
    import invoicing

    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "Acme Bank\nAcct 12345678")
    monkeypatch.setattr(mail, "PAY_URL", "https://pay.example/inv")

    run_dunning()

    body = mailbox[0].get_content()
    assert "https://pay.example/inv" in body
    assert "Acct 12345678" in body


def test_an_unconfigured_deployment_admits_it_rather_than_bluffing(
    mailbox, overdue
):
    """Nobody has set remittance details, so the notice says so."""
    run_dunning()
    assert "Payment details are not configured" in mailbox[0].get_content()


def test_the_same_chase_is_never_sent_twice(mailbox, overdue):
    """Two passes in the same hour is one letter, not two."""
    run_dunning()
    run_dunning()
    run_dunning()
    assert len(mailbox) == 1


def test_a_second_pass_cannot_resend_after_a_restart(mailbox, overdue):
    """The stage claim can be lost to a restart. The mail key cannot.

    The dedupe key lives on a persisted row, so even a store that comes
    back with the invoice's reminder counter rolled back does not produce
    a second copy of a notice the customer already has.
    """
    run_dunning()
    invoice = STORE.get_invoice(overdue[1].invoice_id)
    invoice.reminders_sent = 0  # as if the claim never happened
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())

    run_dunning()

    assert len(mailbox) == 1


# ---- suppression --------------------------------------------------------


def test_an_unsubscribe_does_not_stop_an_invoice(mailbox, overdue):
    """A footer link is not a way to opt out of being billed."""
    tenant, _ = overdue
    STORE.suppress_address(tenant["contact_email"], "unsubscribed")

    run_dunning()

    assert len(mailbox) == 1


def test_a_bounce_stops_everything_including_the_invoice(mailbox, overdue):
    """An address the host refused cannot receive an invoice either.

    And continuing to send at it is how the sending domain is blocklisted
    and every other customer stops getting their alerts.
    """
    tenant, _ = overdue
    STORE.suppress_address(tenant["contact_email"], "bounced")

    result = run_dunning()

    assert mailbox == []
    assert result["notices_count"] == 1  # still recorded and still owed
    assert result["sent"] == 0


def test_a_refusal_from_the_host_suppresses_the_address(mailbox, overdue):
    """One refusal is enough. Retrying a dead mailbox is self-harm."""
    mailbox.fail_with = smtplib.SMTPRecipientsRefused({})
    mailbox.fail_times = 1

    run_dunning()

    tenant, _ = overdue
    block = STORE.is_suppressed(tenant["contact_email"], "transactional")
    assert block is not None and block.reason == "bounced"
    # And it is not retried by the queue flush.
    assert mail.flush_queue() == {"attempted": 0, "sent": 0}


def test_a_bounce_outranks_a_later_unsubscribe(mailbox):
    """The stronger block wins, or invoices start leaking to a dead box."""
    STORE.suppress_address("gone@example.com", "bounced")
    STORE.suppress_address("gone@example.com", "unsubscribed")

    block = STORE.is_suppressed("gone@example.com", "transactional")
    assert block.reason == "bounced"


# ---- the unsubscribe link ----------------------------------------------


def test_the_unsubscribe_link_only_works_for_its_own_address(api, mailbox):
    """Otherwise it is a button anyone can press on anyone's behalf."""
    token = mail.unsubscribe_token("dana@example.com")

    forged = api.get(
        "/api/mail/unsubscribe",
        params={"address": "someone.else@example.com", "token": token},
    )
    assert forged.status_code == 403
    assert STORE.is_suppressed("someone.else@example.com", "commercial") is None

    honest = api.get(
        "/api/mail/unsubscribe",
        params={"address": "dana@example.com", "token": token},
    )
    assert honest.status_code == 200
    assert STORE.is_suppressed("dana@example.com", "commercial") is not None
    # ...and still not the invoices.
    assert STORE.is_suppressed("dana@example.com", "transactional") is None


def test_the_unsubscribe_link_survives_a_restart(monkeypatch):
    """A link in an email sent last week has to still work today.

    The secret is persisted rather than generated per process. Without
    that, every restart silently invalidates every unsubscribe link in
    every inbox, and the customer who clicks one reports us as spam
    instead.
    """
    monkeypatch.delenv("CYBERLOGIX_MAIL_SECRET", raising=False)
    first = mail.unsubscribe_token("dana@example.com")
    assert mail.unsubscribe_token("dana@example.com") == first
    # A fresh read of the stored secret, as a new process would do.
    assert STORE._db.get("meta", "mail_secret")["value"]


def test_commercial_mail_carries_an_unsubscribe_header(mailbox):
    """A mail client's own unsubscribe button reads the header, not the body."""
    mail.send(
        to_address="dana@example.com",
        subject="Your trial",
        body="Hello",
        dedupe_key="test:commercial",
        klass="commercial",
    )
    assert "List-Unsubscribe" in mailbox[0]
    assert "Stop these messages" in mailbox[0].get_content()


def test_transactional_mail_carries_no_unsubscribe_header(mailbox):
    """Offering to stop invoices would be a promise we must not keep."""
    mail.send(
        to_address="dana@example.com",
        subject="Invoice",
        body="You owe us",
        dedupe_key="test:transactional",
        klass="transactional",
    )
    assert "List-Unsubscribe" not in mailbox[0]


# ---- header injection ---------------------------------------------------


def test_a_company_name_cannot_smuggle_a_header(mailbox):
    """The subject is assembled from a value somebody typed into a form.

    A newline in it, dropped into a header, adds headers of the sender's
    choosing: a Bcc onto every notice, or a Reply-To pointing at them.
    """
    hostile = "Acme\r\nBcc: attacker@evil.example\r\nX-Injected: yes"

    mail.send(
        to_address="dana@example.com",
        subject=f"{hostile}: invoice INV-1",
        body="Body",
        dedupe_key="test:injection",
        klass="transactional",
    )

    message = mailbox[0]
    assert message["Bcc"] is None
    assert message["X-Injected"] is None
    assert "\n" not in message["Subject"]
    assert "attacker@evil.example" in message["Subject"]  # inert, as text


def test_the_composer_refuses_a_newline_even_when_the_claim_did_not(mailbox):
    """The guard is in two places, and both have to hold.

    `send` cleans the subject before the claim, so a message on disk is
    already single-line — which means the composer's own guard is only
    reached by a message written some other way: a row from an older
    build, or the next caller that claims directly. It is still the last
    thing standing between a form field and a header, so it is tested
    where it lives rather than through the path that pre-empts it.
    """
    message = mail.compose(
        to_address="dana@example.com",
        subject="Acme\r\nBcc: attacker@evil.example",
        body="Body",
        klass="transactional",
    )
    assert message["Bcc"] is None
    assert "\n" not in message["Subject"]
    assert "\r" not in message["Subject"]


def test_an_unusable_address_is_refused_before_anything_is_claimed(mailbox):
    """A blank contact field must not burn the dedupe key for a real send."""
    result = mail.send(
        to_address="   ",
        subject="Invoice",
        body="Body",
        dedupe_key="test:blank",
        klass="transactional",
    )
    assert result["status"] == "bad_address"
    assert mailbox == []
    assert STORE.mail_log() == []


# ---- the queue ----------------------------------------------------------


def test_a_notice_composed_before_smtp_existed_is_not_lost(
    overdue, mailbox, monkeypatch
):
    """The whole reason the queue exists.

    The chase is composed against a deployment with no SMTP configured,
    and somebody configures it afterwards. The flush is what turns a week
    of unsent demands into a week of sent ones, rather than a week of
    money nobody ever asked for.
    """
    monkeypatch.setattr(mail, "SMTP_HOST", "")
    run_dunning()
    monkeypatch.setattr(mail, "SMTP_HOST", "smtp.example.com")
    assert mailbox == []
    queued = STORE.mail_log()
    assert len(queued) == 1 and queued[0].status == "queued"

    assert mail.flush_queue() == {"attempted": 1, "sent": 1}
    assert len(mailbox) == 1


def test_a_transient_failure_is_retried_and_then_given_up_on(mailbox, overdue):
    """Retry forever and the queue becomes a machine for annoying people."""
    mailbox.fail_times = 99

    run_dunning()
    for _ in range(10):
        mail.flush_queue()

    message = STORE.mail_log()[0]
    assert message.attempts == MAIL_MAX_ATTEMPTS
    assert message.status == "failed"
    assert mailbox == []


def test_a_transient_failure_that_clears_gets_delivered(mailbox, overdue):
    mailbox.fail_times = 1

    run_dunning()
    assert mailbox == []

    assert mail.flush_queue()["sent"] == 1
    assert len(mailbox) == 1


def test_a_stale_notice_is_never_sent(overdue, mailbox, monkeypatch):
    """Three days of downtime is a backlog. A month is a mistake.

    Firing a month of stale chases the afternoon somebody finally
    configures SMTP is worse than never having sent them.
    """
    monkeypatch.setattr(mail, "SMTP_HOST", "")
    run_dunning()
    monkeypatch.setattr(mail, "SMTP_HOST", "smtp.example.com")
    message = STORE.mail_log()[0]
    message.created_at = utc_now() - timedelta(hours=MAIL_MAX_AGE_HOURS + 1)
    STORE._db.put("mail", message.message_id, message.to_row())

    assert mail.flush_queue() == {"attempted": 0, "sent": 0}
    assert mailbox == []


# ---- the operator's view ------------------------------------------------


def test_the_log_masks_addresses_and_withholds_bodies(api, admin_headers, mailbox):
    """The question is whether it went out, not what every letter said."""
    mail.send(
        to_address="dana.reyes@example.com",
        subject="Invoice INV-1",
        body="Sensitive detail about their estate",
        dedupe_key="test:log",
        klass="transactional",
    )

    body = api.get("/api/mail/log", headers=admin_headers).json()
    entry = body["messages"][0]

    assert entry["to"] == "da********@example.com"
    assert entry["status"] == "sent"
    assert "Sensitive detail" not in str(body)


def test_the_log_is_not_readable_without_the_platform_key(api):
    """It carries every customer's contact address and what they owe."""
    assert api.get("/api/mail/log").status_code == 401
    assert api.get("/api/mail/suppressions").status_code == 401
    assert api.post("/api/mail/flush").status_code == 401


def test_the_status_says_plainly_when_nothing_can_be_sent(
    api, admin_headers, no_mail_transport
):
    body = api.get("/api/mail/status", headers=admin_headers).json()
    assert body["configured"] is False
    assert sorted(body["missing"]) == ["MAIL_FROM", "SMTP_HOST"]


def test_an_operator_can_suppress_and_release_an_address(api, admin_headers):
    made = api.post(
        "/api/mail/suppressions",
        headers=admin_headers,
        json={"address": "dana@example.com", "reason": "unsubscribed"},
    )
    assert made.status_code == 201
    assert STORE.is_suppressed("dana@example.com", "commercial") is not None

    removed = api.request(
        "DELETE",
        "/api/mail/suppressions",
        headers=admin_headers,
        params={"address": "dana@example.com"},
    )
    assert removed.status_code == 200
    assert STORE.is_suppressed("dana@example.com", "commercial") is None


def test_mail_survives_a_restart(mailbox):
    """The dedupe index is rebuilt from disk, or a restart resends."""
    mail.send(
        to_address="dana@example.com",
        subject="Invoice",
        body="Body",
        dedupe_key="test:durable",
        klass="transactional",
    )

    STORE._mail.clear()
    STORE._mail_keys.clear()
    STORE.load()

    assert mail.send(
        to_address="dana@example.com",
        subject="Invoice",
        body="Body",
        dedupe_key="test:durable",
        klass="transactional",
    )["status"] == "duplicate"
    assert len(mailbox) == 1


def test_a_mail_failure_cannot_stop_the_billing_pass(overdue, monkeypatch):
    """The company must keep invoicing even when it cannot send anything."""

    def _explode(*args, **kwargs):
        raise RuntimeError("the mail module itself is broken")

    monkeypatch.setattr(mail, "compose", _explode)
    monkeypatch.setattr(mail, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mail, "MAIL_FROM", "billing@example.com")

    result = run_dunning()

    assert result["notices_count"] == 1
    assert result["failed_invoices"] == []
