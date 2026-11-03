"""A 500 is mailed to one inbox, briefly, and never with anybody's details.

The fault list already wrote every unexpected error down. Nobody reads a
list they have to remember to open, so a 500 is also mailed to
CYBERLOGIX_ERRORS_EMAIL: what broke, where, in which version, with the
reference the customer was shown. Not the request: no body, cookie,
header, caller's address, email address or phone number.
"""

from datetime import timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import faults
from main import app
from store import STORE, utc_now
from version import __version__

INBOX = "errors@cyberlogix.example"
WHO = "leaky.person@customer.example"
PHONE = "+15550199887"
API_KEY = "clx_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _boom_from(request: Request, where: str):
    """Fail the worst way: with the request's own details in the message."""
    raise RuntimeError(
        f"could not handle {where}: query={dict(request.query_params)} "
        f"who={request.headers.get('x-who')} cookies={request.cookies} "
        f"phone={PHONE} key={API_KEY}")


@pytest.fixture()
def exploding(monkeypatch):
    """A GET and a form POST that raise, removed afterwards."""

    def _get(request: Request):
        _boom_from(request, "a page")

    async def _post(request: Request):
        form = await request.form()
        raise RuntimeError(f"form said {dict(form)}")

    app.add_api_route("/api/_test_report/{token}", _get, include_in_schema=False)
    app.add_api_route("/api/_test_report_form", _post, methods=["POST"],
                      include_in_schema=False)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if not getattr(r, "path", "").startswith("/api/_test_report")
        ]


@pytest.fixture()
def errors_inbox(monkeypatch, mailbox):
    monkeypatch.setenv("CYBERLOGIX_ERRORS_EMAIL", INBOX)
    return mailbox


def _reports(mailbox):
    return [m for m in mailbox if m["To"] == INBOX]


def _whole(message) -> str:
    """Every byte a reader could see: headers and both parts."""
    parts = [str(message["Subject"])]
    for part in message.walk():
        if part.get_content_maintype() == "text":
            parts.append(part.get_content())
    return "\n".join(parts)


def _leak_everywhere(client):
    """The same address in the query string, a header, a cookie and a form."""
    client.cookies.set("session_hint", WHO)
    first = client.get(f"/api/_test_report/{API_KEY}",
                       params={"email": WHO, "phone": PHONE},
                       headers={"X-Who": WHO, "Authorization": f"Bearer {API_KEY}"})
    second = client.post("/api/_test_report_form", params={"email": WHO},
                         data={"email": WHO, "phone": PHONE},
                         headers={"X-Who": WHO})
    return first, second


def test_a_500_is_mailed_with_what_an_engineer_needs(exploding, errors_inbox):
    resp = exploding.get("/api/_test_report/abc", params={"q": "1"})
    assert resp.status_code == 500
    fault_id = resp.json()["fault_id"]

    [report] = _reports(errors_inbox)
    text = report.get_body(preferencelist=("plain",)).get_content()
    assert f"Reference: {fault_id}" in text, "the code the customer was shown"
    assert f"Version: {__version__}" in text
    assert "Request: GET /api/_test_report/{token}" in text
    assert "Error: RuntimeError" in text
    assert "Time: " + utc_now().strftime("%Y-%m-%dT") in text
    assert "Stack trace:" in text and "Traceback" in text
    # Paths relative to the application, not to this machine.
    assert 'File "tests/test_error_reports.py"' in text
    assert 'File "/' not in text and "/home/" not in text
    # Fits the fault list rather than replacing it.
    assert STORE._db.get(faults.KIND, fault_id)["seen"] == 1
    assert "/api/admin/faults" in text


def test_nothing_from_the_request_reaches_the_report(exploding, errors_inbox):
    first, second = _leak_everywhere(exploding)
    assert first.status_code == 500 and second.status_code == 500

    reports = _reports(errors_inbox)
    assert len(reports) == 2
    for report in reports:
        seen = _whole(report)
        assert WHO not in seen
        assert "customer.example" not in seen
        assert PHONE not in seen and "5550199887" not in seen
        assert API_KEY not in seen and API_KEY[4:20] not in seen
        assert "X-Who" not in seen and "x-who" not in seen
        assert "testclient" not in seen, "nor the caller's address"
        assert "?" not in seen.split("Request: ", 1)[1].split("\n", 1)[0]
    # The message is kept, with the details taken out of it.
    text = _whole(reports[0])
    assert "<address>" in text and "<number>" in text and "<token>" in text
    # And the fault list itself: it kept a key an error message carried
    # until the token scrubbing was applied to it as well.
    for row in faults.recent():
        for field in ("message", "traceback", "context"):
            assert WHO not in row[field] and API_KEY not in row[field], field


def test_one_email_per_error_an_hour_counted_in_the_database(
    exploding, errors_inbox
):
    for _ in range(5):
        assert exploding.get("/api/_test_report/x").status_code == 500
    assert len(_reports(errors_inbox)) == 1
    fault_id = faults.recent()[0]["fault_id"]
    assert faults.recent()[0]["seen"] == 5, "still counted in the list"

    # A restart forgets everything in memory and nothing in the database.
    STORE.load()
    assert exploding.get("/api/_test_report/x").status_code == 500
    assert len(_reports(errors_inbox)) == 1

    # An hour later the same error is worth telling again.
    row = STORE._db.get(faults._SENT_KIND, fault_id)
    row["sent_at"] = (utc_now() - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put(faults._SENT_KIND, fault_id, row)
    assert exploding.get("/api/_test_report/x").status_code == 500
    assert len(_reports(errors_inbox)) == 2


def test_a_different_error_is_its_own_email(exploding, errors_inbox):
    exploding.get("/api/_test_report/x")
    exploding.post("/api/_test_report_form", data={"a": "b"})
    assert len(_reports(errors_inbox)) == 2


def test_no_more_than_twenty_a_day(monkeypatch, errors_inbox):
    def _fail(n):
        try:
            raise ValueError(f"number {n}")
        except ValueError as exc:
            return exc

    sent = 0
    for n in range(30):
        exc = _fail(n)
        # Thirty distinct faults: a different source each time.
        row = faults.record(f"GET /x{n}", exc)
        if faults.mail_report(row, exc, "GET", f"/x{n}").get("sent"):
            sent += 1
    assert sent == faults.ERROR_MAILS_PER_DAY == 20
    assert len(_reports(errors_inbox)) == 20
    day = utc_now().date().isoformat()
    assert STORE._db.get(faults._DAY_KIND, day)["sent"] == 20


def test_through_the_mail_queue_when_the_host_is_down(
    exploding, errors_inbox
):
    errors_inbox.fail_times = 1
    exploding.get("/api/_test_report/x")
    assert _reports(errors_inbox) == []
    queued = [m for m in STORE.pending_mail() if m.to_address == INBOX]
    assert len(queued) == 1, "waiting for the next pass, not lost"
    import mail

    mail.flush_queue()
    assert len(_reports(errors_inbox)) == 1


def test_unset_sends_nothing(exploding, mailbox, monkeypatch):
    monkeypatch.delenv("CYBERLOGIX_ERRORS_EMAIL", raising=False)
    resp = exploding.get("/api/_test_report/x")
    assert resp.status_code == 500
    assert len(mailbox) == 0
    assert STORE.mail_log(limit=50) == []
    # The fault list still has it.
    assert faults.recent()[0]["exception"] == "RuntimeError"


def test_blank_counts_as_unset(exploding, mailbox, monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_ERRORS_EMAIL", "   ")
    exploding.get("/api/_test_report/x")
    assert len(mailbox) == 0


def test_token_scrubbing():
    text = faults.clean(
        f"Bearer abc.def key={API_KEY} password: hunter2hunter2 "
        f"clr_ZZZZZZZZZZZZ token=xyz {WHO} {PHONE}")
    for leak in (API_KEY, "abc.def", "hunter2", "clr_ZZZ", "xyz", WHO, PHONE):
        assert leak not in text, leak
    # Ordinary words and the fault's own reference survive.
    assert faults.clean("RuntimeError in 1a2b3c4d5e6f7a8b") == \
        "RuntimeError in 1a2b3c4d5e6f7a8b"
