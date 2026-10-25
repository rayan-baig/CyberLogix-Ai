"""The two summaries that arrive without anybody opening a browser.

The person who owns this company is at school for six hours of every
working day, which makes "check the dashboard" a plan that fails five
days a week. The operator digest is the book in an email; the customer
report is the evidence that the invoice was worth paying.

The failure worth guarding hardest is the quiet one. A digest that is
silently not sent is indistinguishable from a day with nothing in it,
and the whole point of the thing is to be the channel that tells you
when something is wrong — including when the mail transport itself is.
"""

from datetime import timedelta

import pytest

import digest
from contracts import run_billing
from digest import (
    customer_report,
    operator_digest,
    run_digests,
    send_customer_reports,
    send_operator_digest,
)
from store import STORE, utc_now


@pytest.fixture()
def operator_address(monkeypatch):
    """An operator to write to, and no hour to wait for.

    The send hour is pinned as well as the address. Without it every
    test in this file would pass or fail depending on what time of day
    the suite happens to run, which is the kind of flake that costs an
    afternoon to find and teaches somebody to rerun until it goes green.
    The hour has its own tests, below.
    """
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "founder@cyberlogix.example")
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 0)
    return "founder@cyberlogix.example"


def _sign(api, headers, owner, **body):
    payload = {"term_years": 1, "escalator_percent": 5.0}
    payload.update(body)
    resp = api.post("/api/contracts", headers={**headers, **owner}, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def paying(api, tenant_factory, sensor_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    _sign(api, headers, owner)
    run_billing()
    return headers, tenant


# ---- the operator digest ------------------------------------------------


def test_the_digest_carries_the_book_and_the_money(mailbox, operator_address, paying):
    _, tenant = paying

    result = send_operator_digest()

    assert result["sent"] is True
    body = mailbox[-1].get_content()
    assert "a year booked" in body
    assert "1 invoice(s) issued" in body


def test_the_digest_names_who_to_call_and_how(mailbox, operator_address, paying):
    """A worklist without a phone number is a reading exercise."""
    _, tenant = paying
    STORE.get_tenant(tenant["tenant_id"]).expires_at = utc_now() + timedelta(days=3)

    send_operator_digest()

    body = mailbox[-1].get_content()
    assert tenant["company_name"] in body
    assert tenant["contact_email"] in body
    assert tenant["contact_phone"] in body


def test_only_one_digest_a_day(mailbox, operator_address, paying):
    """The money pass runs hourly. The founder gets one email."""
    for _ in range(24):
        send_operator_digest()

    assert len([m for m in mailbox if "booked" in m["Subject"]]) == 1


def test_a_missing_operator_address_is_reported_rather_than_ignored(
    mailbox, monkeypatch
):
    """A digest that silently does not exist is the failure it exists for.

    The hour is pinned so this asserts on the ordering rather than on
    what time the suite happens to run: before seven in the morning both
    faults are true, and the one that will still be true tomorrow is the
    one worth reporting.
    """
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "")
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 7)

    result = send_operator_digest(now=utc_now().replace(hour=3))

    assert result["sent"] is False
    assert result["status"] == "no_operator_address"
    assert "CYBERLOGIX_OPERATOR_EMAIL" in result["detail"]


def test_the_digest_says_when_the_mail_transport_is_broken(
    mailbox, operator_address, paying, monkeypatch
):
    """The one warning that explains why nothing else arrived.

    Mail that cannot be sent looks exactly like mail nobody needed, right
    up until a quarter's invoices turn out to have queued.
    """
    import mail as mail_module

    monkeypatch.setattr(mail_module, "SMTP_HOST", "")
    warnings = operator_digest()["warnings"]
    assert any("No mail transport is configured" in w for w in warnings)


def test_the_digest_says_when_nobody_can_pay_us(operator_address, mailbox, paying):
    warnings = operator_digest()["warnings"]
    assert any("No payment details" in w for w in warnings)


def test_a_sensor_that_went_quiet_is_not_confused_with_one_never_installed(
    mailbox, operator_address, paying, sensor_factory
):
    """Two problems wearing the same word.

    A sensor that has never reported is an installation somebody did not
    finish. One that reported and then stopped is a unit that has failed.
    Counted together the number is always non-zero and therefore always
    ignored.
    """
    headers, _ = paying
    tenant_id = STORE.list_tenants()[0].tenant_id
    sensor_factory(headers, "RACK-02", "cybersecurity")

    quiet = STORE.sensors_for(tenant_id)[0]
    quiet.last_seen = utc_now() - timedelta(days=5)

    warnings = operator_digest()["warnings"]
    assert any("reported and then stopped" in w for w in warnings)
    assert any("never reported at all" in w for w in warnings)


def test_the_customer_report_keeps_the_two_apart_as_well(
    mailbox, paying, sensor_factory
):
    """Telling somebody their new sensor "stopped reporting" reads as our
    fault; telling them a failed one is "not set up yet" reads as theirs."""
    headers, tenant = paying
    sensor_factory(headers, "RACK-02", "cybersecurity")
    quiet = STORE.sensors_for(tenant["tenant_id"])[0]
    quiet.last_seen = utc_now() - timedelta(days=5)

    send_customer_reports()

    body = mailbox[-1].get_content()
    assert "UNITS THAT HAVE STOPPED REPORTING" in body
    assert "UNITS THAT HAVE NEVER REPORTED" in body


def test_a_long_book_is_trimmed_and_the_rest_counted(
    api, mailbox, operator_address, tenant_factory, sensor_factory, owner_headers
):
    """Twenty rows in an email is a wall nobody reads."""
    for index in range(digest.DIGEST_ROWS + 3):
        headers, _ = tenant_factory(
            plan="growth", company_name=f"Company {index}"
        )
        sensor_factory(headers, f"UNIT-{index}", "restaurant")

    body = operator_digest()
    assert len(body["rows"]) == digest.DIGEST_ROWS
    assert body["overflow"] >= 3

    send_operator_digest()
    assert "more. The whole book is at /book" in mailbox[-1].get_content()


def test_a_quiet_day_says_so(mailbox, operator_address):
    send_operator_digest()
    assert "Nothing needs a person today" in mailbox[-1].get_content()


def test_the_subject_leads_with_what_is_wrong(
    mailbox, operator_address, paying, monkeypatch
):
    """It gets truncated, so the order decides what survives.

    The first version read "CyberLogix: $35,964 booked, 3 need a person
    (1 urgent), and something is broken" — folded across two header
    lines and cut off by every client at roughly the point where it
    stopped being reassuring.
    """
    send_operator_digest()

    subject = mailbox[-1]["Subject"]
    assert len(subject) < 78, subject
    assert "\n" not in subject
    assert subject.index("to fix") < subject.index("booked")


def test_a_quiet_healthy_day_still_says_the_number(
    mailbox, operator_address, monkeypatch
):
    import mail as mail_module

    monkeypatch.setattr(mail_module, "PAY_URL", "https://pay.example")
    send_operator_digest()
    assert "booked" in mailbox[-1]["Subject"]


def test_an_estate_that_measured_nothing_is_not_told_it_was_fine(
    mailbox, paying
):
    """The most damaging sentence a monitoring product can send.

    "Everything stayed inside its limits" went to an estate that had
    taken no readings at all. Nothing stayed inside anything — nothing
    was measured.
    """
    send_customer_reports()

    body = mailbox[-1].get_content()
    assert "stayed inside its limits" not in body
    assert "Nothing was measured this week" in body
    assert "not the same as nothing going wrong" in body


def test_an_estate_that_measured_something_clean_is_told_so(
    api, mailbox, paying
):
    headers, _ = paying
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RACK-01", "temperature_fahrenheit": 68.0})

    send_customer_reports()

    assert "stayed inside its limits" in mailbox[-1].get_content()


def test_the_digest_waits_for_the_hour_it_was_asked_for(
    mailbox, operator_address, monkeypatch, paying
):
    """The money pass runs hourly, so without a floor the digest arrives
    whenever the process happened to start — and a 3am email is one
    nobody reads and everybody learns to ignore."""
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 7)
    early = utc_now().replace(hour=3)

    result = send_operator_digest(now=early)

    assert result["sent"] is False
    assert result["status"] == "too_early"
    # The invoice from the fixture's billing run is in there; the digest
    # is not.
    assert not [m for m in mailbox if "booked" in m["Subject"]]


def test_a_late_pass_still_sends_rather_than_skipping_the_day(
    mailbox, operator_address, monkeypatch, paying
):
    """A floor, not an exact time. Missing a day because the process was
    restarting at seven is worse than arriving at nine."""
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 7)

    assert send_operator_digest(now=utc_now().replace(hour=9))["sent"] is True


def test_the_operator_pressing_the_button_is_not_told_to_come_back_later(
    api, admin_headers, mailbox, operator_address, monkeypatch, paying
):
    """The hour is a floor on the unattended pass, not a rule about what
    somebody may ask for at six in the morning."""
    monkeypatch.setattr(digest, "DIGEST_HOUR_UTC", 23)

    assert api.post("/api/digest/operator", headers=admin_headers).json()["sent"]


# ---- the customer's weekly report ---------------------------------------


def test_the_report_counts_what_actually_happened(api, mailbox, paying):
    headers, tenant = paying
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RACK-01", "temperature_fahrenheit": 68.0})
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RACK-01", "temperature_fahrenheit": 130.0})

    report = customer_report(STORE.get_tenant(tenant["tenant_id"]))

    assert report["readings"] == 2
    assert report["breaching_readings"] == 1
    assert report["incidents"] == 1


def test_the_report_names_units_that_have_stopped_reporting(
    api, mailbox, paying
):
    """The most valuable line in it, and the one the product cannot alarm."""
    _, tenant = paying
    sensor = STORE.sensors_for(tenant["tenant_id"])[0]
    sensor.last_seen = utc_now() - timedelta(days=5)

    send_customer_reports()

    body = mailbox[-1].get_content()
    assert "STOPPED REPORTING" in body
    assert "RACK-01" in body


def test_one_report_a_week(mailbox, paying):
    for _ in range(50):
        send_customer_reports()

    assert len([m for m in mailbox if "readings" in m["Subject"]]) == 1


def test_a_trial_gets_the_conversion_ladder_not_a_report(
    api, mailbox, tenant_factory, sensor_factory
):
    """Two emails a week to somebody still evaluating is one too many."""
    headers, _ = tenant_factory(plan="trial", company_name="Trying It Out")
    sensor_factory(headers, "FRIDGE-1", "restaurant")

    assert send_customer_reports()["sent_count"] == 0


def test_an_estate_with_nothing_registered_gets_nothing(
    mailbox, tenant_factory
):
    """A report of zero readings across zero units is spam."""
    tenant_factory(plan="growth")
    assert send_customer_reports()["sent_count"] == 0


def test_unsubscribing_stops_the_weekly_report(mailbox, paying):
    """It is useful, not required, so declining it must work."""
    _, tenant = paying
    STORE.suppress_address(tenant["contact_email"], "unsubscribed")

    assert send_customer_reports()["sent_count"] == 0


def test_one_broken_estate_does_not_stop_the_others(
    api, mailbox, paying, tenant_factory, sensor_factory, monkeypatch
):
    first = STORE.list_tenants()[0]
    headers, second = tenant_factory(plan="growth", company_name="Harbour Cold")
    sensor_factory(headers, "FRIDGE-9", "restaurant")

    real = digest.customer_report

    def _selective(tenant, now=None):
        if tenant.tenant_id == first.tenant_id:
            raise RuntimeError("this estate cannot be summarised")
        return real(tenant, now)

    monkeypatch.setattr(digest, "customer_report", _selective)

    result = send_customer_reports()

    assert result["failed_tenants"] == [first.tenant_id]
    assert result["sent_count"] == 1


# ---- the pass and the routes -------------------------------------------


def test_the_pass_runs_both_and_survives_either_failing(
    mailbox, operator_address, paying, monkeypatch
):
    def _explode(*args, **kwargs):
        raise RuntimeError("the book cannot be built")

    monkeypatch.setattr(digest, "operator_digest", _explode)

    result = run_digests()

    assert result["operator"]["sent"] is False
    assert result["customer_reports"]["sent_count"] == 1


def test_the_digest_is_not_readable_without_the_platform_key(api):
    """It is every customer's contact details and what they owe."""
    assert api.get("/api/digest/operator").status_code == 401
    assert api.post("/api/digest/operator").status_code == 401
    assert api.post("/api/digest/reports").status_code == 401


def test_the_operator_can_read_it_without_sending_it(
    api, admin_headers, paying, mailbox
):
    body = api.get("/api/digest/operator", headers=admin_headers).json()

    assert body["digest"]["arr_usd"] > 0
    assert "CYBERLOGIX_OPERATOR_EMAIL is not set" in body["delivery"]
    assert not [m for m in mailbox if "booked" in m["Subject"]]
