"""Every kind of email CyberLogix sends, through a mail server, read back.

One look for all of them: a plain-text part and an HTML part, the HTML
in one column no wider than 600px, the "CyberLogix is a product of MTRX
Horizyn" footer once, the same words and links in both parts, nothing
left unfilled, and a way to stop or manage it on anything that is not a
must-send alert or receipt.

The server is a real SMTP listener on the loopback address, so each
message is the bytes that crossed a socket, parsed back the way a mail
client parses them -- not the object before it was sent.
"""

from __future__ import annotations

import email
import html
import re
import socketserver
import threading
from datetime import date, timedelta
from email import policy
from pathlib import Path

import pytest

import automation
import conversion
import digest
import faults
import mail
import monthly
import notifications
import people
import pollers
from contracts import run_billing, run_dunning
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
FOOTER = "CyberLogix is a product of MTRX Horizyn."
OWNER = "ops@example.com"
OPERATOR = "founder@cyberlogix.example"
ERRORS = "errors@cyberlogix.example"


class _SMTPSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.received = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: EHLO, MAIL, RCPT, DATA, QUIT."""

    def _say(self, line: str) -> None:
        self.wfile.write(line.encode("ascii") + b"\r\n")

    def handle(self) -> None:
        self._say("220 sink ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            verb = line.decode("latin-1").strip().upper()
            if verb.startswith(("EHLO", "HELO")):
                self._say("250-sink")
                self._say("250 8BITMIME")
            elif verb == "DATA":
                self._say("354 go ahead")
                raw = []
                while True:
                    chunk = self.rfile.readline()
                    if not chunk or chunk in (b".\r\n", b".\n"):
                        break
                    # Undo SMTP's dot-stuffing.
                    raw.append(chunk[1:] if chunk.startswith(b"..") else chunk)
                self.server.received.append(
                    email.message_from_bytes(b"".join(raw), policy=policy.default))
                self._say("250 queued")
            elif verb == "QUIT":
                self._say("221 bye")
                return
            else:
                self._say("250 OK")


@pytest.fixture()
def smtp(monkeypatch):
    sink = _SMTPSink()
    thread = threading.Thread(target=sink.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(mail, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(mail, "SMTP_PORT", sink.server_address[1])
    monkeypatch.setattr(mail, "SMTP_STARTTLS", False)
    monkeypatch.setattr(mail, "SMTP_USERNAME", "")
    monkeypatch.setattr(mail, "SMTP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(mail, "MAIL_FROM", "hello@cyberlogix.example")
    monkeypatch.setattr(mail, "PUBLIC_BASE_URL", "https://cyberlogix.example.com")
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://cyberlogix.example.com")
    monkeypatch.setattr(mail, "PAY_URL", "https://pay.example/{invoice}")
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", OPERATOR)
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 0)
    monkeypatch.setenv("CYBERLOGIX_ERRORS_EMAIL", ERRORS)
    try:
        yield sink.received
    finally:
        sink.shutdown()
        sink.server_close()


def _sign(api, headers, owner):
    resp = api.post("/api/contracts", headers={**headers, **owner},
                    json={"term_years": 1, "escalator_percent": 5.0})
    assert resp.status_code == 201, resp.text


def _send_every_kind(api, tenant_factory, owner_headers, sensor_factory):
    """Make the application send one of everything it can send."""
    # A paying business: an invoice, a breach, its licences, its reports.
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St Grill")
    owner = owner_headers(headers, email="owner@bellst.example")
    both = {**headers, **owner}
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    run_billing()                                                    # invoice
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 140.0})
    assert resp.status_code in (200, 201), resp.text                 # incident-alert
    the_tenant = STORE.get_tenant(tenant["tenant_id"])
    incident = STORE.incidents_for(tenant["tenant_id"])[0]
    automation._tell_owner_nobody_was_texted(the_tenant, incident)  # incident-undelivered
    pollers._tell_the_owner_it_stopped(the_tenant, {                 # poller-stopped
        "name": "Vendor cloud", "consecutive_failures": 5,
        "last_detail": "401 Unauthorized", "source_id": "SRC-1",
        "stopped_at": "2026-11-03T10:00:00Z"})

    night = api.post("/api/contacts", headers=both,
                     json={"full_name": "Night Engineer", "phone": "+15550101"}).json()
    api.patch(f"/api/contacts/{night['contact_id']}", headers=both,
              json={"active": False})                               # roster-gap

    person = api.post("/api/people", headers=both, json={
        "full_name": "Dana Reyes", "role": "Head chef",
        "email": "dana@bell.example", "phone": "+15550100"}).json()["person"]
    api.post("/api/people/credentials", headers=both, json={
        "staff_id": person["staff_id"], "name": "Food handler card",
        "expires_on": (utc_now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        "required_to_work": True})
    people.send_licence_alerts()                                     # licence-alert

    digest.send_customer_reports()                                   # report
    digest.send_operator_digest()                                    # digest:operator
    today = utc_now().date()
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    last = today.replace(day=1) - timedelta(days=1)
    monthly.send_for(the_tenant, first, last)                        # monthly

    # The daily report to OmniArmor has not got through for two days.
    import omniarmor

    STORE._db.put(omniarmor.CONNECTION_KIND, the_tenant.tenant_id, {
        "tenant_id": the_tenant.tenant_id, "base_url": "https://omniarmor.example.com",
        "enabled": True, "last_detail": "OmniArmor answered 503."})
    for back in (2, 1):
        day = (today - timedelta(days=back)).isoformat()
        STORE._db.put(omniarmor.DAY_KIND, f"{the_tenant.tenant_id}:{day}", {
            "tenant_id": the_tenant.tenant_id, "date": day, "state": "failed",
            "last_outcome": "failed", "sent_fingerprint": None})
    assert omniarmor.maybe_email_owners(the_tenant)                  # omniarmor-failing

    # The bill goes unpaid.
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]
    invoice.due_at = utc_now() - timedelta(days=30)
    invoice.issued_at = invoice.due_at - timedelta(days=30)
    STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
    run_dunning()                                                    # dunning

    api.post("/api/accounts/forgot", json={"email": "owner@bellst.example"})  # reset

    # A trial, through every rung of the ladder.
    resp = api.post("/api/signup", json={
        "company_name": "Corner Deli", "full_name": "Ann Petrou",
        "email": "ann@cornerdeli.example", "password": "correct-horse-battery",
        "contact_phone": "+1-555-0133", "industry_vertical": "restaurant"})
    assert resp.status_code == 201, resp.text                        # trial welcome
    trial = STORE.get_tenant(resp.json()["tenant"]["tenant_id"])
    ev = conversion.trial_evidence(trial)
    conversion._activation(trial, ev)
    conversion._week(trial, ev, 7)
    conversion._three(trial, ev, 3)
    conversion._last(trial, ev, 5.0)
    conversion._grace(trial, ev, 4)
    conversion._dark(trial, ev)

    try:                                                             # error-report
        raise RuntimeError("a bug")
    except RuntimeError as exc:
        row = faults.record("GET /api/x", exc)
        faults.mail_report(row, exc, "GET", "/api/x")


# What every message is, by the start of its key, and how it is stopped.
KINDS = {
    "invoice:": "must",
    "incident-alert:": "must",
    "incident-undelivered:": "must",
    "poller-stopped:": "must",
    "roster-gap:": "must",
    "dunning:": "must",
    "reset:": "must",
    "error-report:": "must",
    "licence-alert:": "manage",
    "digest:operator:": "manage",
    "omniarmor-failing:": "manage",
    "report:": "unsubscribe",
    "monthly:": "unsubscribe",
    "trial:": "unsubscribe",
}
TRIAL_STAGES = ("welcome", "activation", "week", "three", "last", "grace", "dark")


def _kind(key: str) -> str:
    return next(prefix for prefix in KINDS if key.startswith(prefix))


def _visible_text(markup: str) -> str:
    """What a reader of the HTML part sees, as plain words."""
    body = markup.split("<body", 1)[1].split(">", 1)[1]
    body = re.sub(r"<br\s*/?>", "\n", body)
    return html.unescape(re.sub(r"<[^>]+>", " ", body))


def _parts(message):
    plain = message.get_body(preferencelist=("plain",))
    rich = message.get_body(preferencelist=("html",))
    return plain, rich


def _subject(value) -> str:
    """A subject as a reader sees it. A long one is folded onto a second
    header line, and some Python versions read the fold back as a space
    at the start."""
    return " ".join(str(value).split())


@pytest.fixture()
def every_email(api, smtp, tenant_factory, owner_headers, sensor_factory):
    # The reset is limited per address per hour, and every test here asks.
    import accounts

    accounts.reset_rate_limits()
    _send_every_kind(api, tenant_factory, owner_headers, sensor_factory)
    keys = {_subject(m.subject): m.dedupe_key for m in STORE.mail_log(limit=500)}
    received = [(keys[_subject(m["Subject"])], m) for m in smtp]
    assert len(received) == len(STORE.mail_log(limit=500)), "every message arrived"
    return received


def test_one_of_every_kind_was_sent(every_email):
    kinds = {_kind(key) for key, _ in every_email}
    assert kinds == set(KINDS)
    stages = {key.rsplit(":", 1)[1] for key, _ in every_email if key.startswith("trial:")}
    assert stages == set(TRIAL_STAGES)


def test_the_list_of_kinds_is_the_code_s_list():
    """A new kind of email is added here, and so gets checked, or this fails."""
    found = set()
    for path in ROOT.glob("*.py"):
        for key in re.findall(r'dedupe_key=f?"([a-z-]+:(?:operator:)?)', path.read_text()):
            found.add(key)
    assert found == set(KINDS)
    must = {k for k, v in KINDS.items() if v == "must"}
    assert must == set(mail.MUST_SEND)
    assert {k for k, v in KINDS.items() if v == "manage"} == set(mail.MANAGED)


def test_each_has_a_plain_text_part_and_an_html_part(every_email):
    for key, message in every_email:
        assert message.get_content_type() == "multipart/alternative", key
        plain, rich = _parts(message)
        assert plain is not None and plain.get_content_type() == "text/plain", key
        assert rich is not None and rich.get_content_type() == "text/html", key
        assert plain.get_content().strip() and rich.get_content().strip(), key


# Left for the reader on purpose: the sign-up key is never emailed, so the
# setup commands say where it goes.
DELIBERATE = {"YOUR_API_KEY"}
UNFILLED = re.compile(
    r"%%\w+%%|\{\{|\}\}|\{[a-z_]+\}|\$[A-Z_]{3,}|\bNone\b|\bnull\b|\bundefined\b"
    r"|\bNaN\b|\[object Object\]|<missing>|\bTODO\b")


def test_nothing_is_left_unfilled(every_email):
    for key, message in every_email:
        plain, rich = _parts(message)
        for name, text in (("subject", str(message["Subject"])),
                           ("text", plain.get_content()),
                           ("html", _visible_text(rich.get_content()))):
            for word in DELIBERATE:
                text = text.replace(word, "")
            found = UNFILLED.findall(text)
            assert not found, f"{key} {name}: {found}"


def test_the_footer_is_there_once_in_each_part(every_email):
    for key, message in every_email:
        plain, rich = _parts(message)
        assert plain.get_content().count(FOOTER) == 1, key
        assert _visible_text(rich.get_content()).count(FOOTER) == 1, key
        assert "Horizon" not in plain.get_content() + rich.get_content(), key


def test_nothing_is_wider_than_600px(every_email):
    for key, message in every_email:
        markup = _parts(message)[1].get_content()
        widths = [int(w) for w in re.findall(r'\bwidth="(\d+)"', markup)]
        widths += [int(w) for w in re.findall(r"(?<![\w-])(?:min-|max-)?width:\s*(\d+)px", markup)]
        assert widths and max(widths) <= 600, (key, widths)
        assert "600" in markup, "the column is set, not left to the client"
        assert "<img" not in markup and "<style" not in markup, key


def test_both_parts_say_the_same_words_with_the_same_links(every_email):
    for key, message in every_email:
        plain, rich = _parts(message)
        text, markup = plain.get_content(), rich.get_content()
        assert re.findall(r"\w+", text) == re.findall(r"\w+", _visible_text(markup)), key
        hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', markup)]
        assert hrefs == mail.urls_in(text), key
        for link in hrefs:
            assert link.startswith("https://"), (key, link)


def test_a_way_to_stop_or_manage_it_unless_it_must_be_sent(every_email):
    for key, message in every_email:
        plain, rich = _parts(message)
        both = plain.get_content() + _visible_text(rich.get_content())
        how = KINDS[_kind(key)]
        if how == "unsubscribe":
            assert "https://cyberlogix.example.com/api/mail/unsubscribe?" in both, key
            assert message["List-Unsubscribe"], key
        elif how == "manage":
            path, label = mail.MANAGED[_kind(key)]
            assert f"{label}: https://cyberlogix.example.com{path}" in plain.get_content(), key
            assert f'href="https://cyberlogix.example.com{path}"' in rich.get_content(), key
            assert message["List-Unsubscribe"] is None, key
        else:
            assert "/api/mail/unsubscribe" not in both, key
            assert message["List-Unsubscribe"] is None, key
            assert "sent regardless of marketing preferences" in plain.get_content(), key


def test_what_a_customer_typed_is_escaped_in_the_html(api, smtp, tenant_factory):
    resp = api.post("/api/signup", json={
        "company_name": "<b>Grill</b> & Co", "full_name": "Ann <i>P</i>",
        "email": "ann@grill.example", "password": "correct-horse-battery",
        "contact_phone": "+1-555-0133", "industry_vertical": "restaurant"})
    assert resp.status_code == 201, resp.text
    [message] = smtp
    markup = _parts(message)[1].get_content()
    assert "<b>Grill</b>" not in markup and "<i>P</i>" not in markup
    assert "&lt;i&gt;P&lt;/i&gt;" in markup
    assert "<b>Grill</b>" in str(message["Subject"]), "a subject is not HTML"


def test_date_is_not_reread_as_a_placeholder():
    """The checks above look for {name}; a real date must not trip them."""
    assert not UNFILLED.findall(f"Due {date(2026, 11, 3).isoformat()}")
