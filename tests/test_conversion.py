"""Asking for the order, and the ways an automated sequence gets it wrong.

The book at /book could already say "this trial ends Thursday, call
them". That is a fine instruction for a person who opens it on Thursday.
These tests are about what happens when nobody does.

The failure worth guarding hardest is not a missing email — it is the
ladder firing all at once. A customer who receives "a week left", "three
days left" and "your trial has ended" in the same minute has learned
something true and fatal: nothing here is actually watching anything.
"""

from datetime import timedelta

import pytest

import conversion
from conversion import run_trial_conversion, trial_evidence
from store import STORE, utc_now


def _trial(api, company="Northgate Foods", email="dana@northgate.example"):
    """A trial that came through the public door, as a real one would."""
    resp = api.post(
        "/api/signup",
        json={
            "company_name": company,
            "full_name": "Dana Reyes",
            "email": email,
            "password": "correct-horse-battery",
            "contact_phone": "+1-555-0100",
            "industry_vertical": "restaurant",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return STORE.get_tenant(body["tenant"]["tenant_id"]), body


def _days_left(tenant, days):
    """Put the trial's end exactly that many days out.

    A minute inside the boundary rather than on it: days remaining is
    rounded up, so a trial ending in exactly seven days and one second
    has eight days left, and the test would be asserting on the window
    next door.
    """
    tenant.expires_at = utc_now() + timedelta(days=days) - timedelta(minutes=1)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())
    return tenant


def _stages(tenant_id=None):
    """Which rungs of the ladder have been sent, oldest first.

    The log itself is newest-first, which is right for an operator
    reading it and wrong for asserting on a sequence.
    """
    return [
        m.dedupe_key
        for m in reversed(STORE.mail_log(limit=1000))
        if m.dedupe_key.startswith("trial:")
        and (tenant_id is None or tenant_id in m.dedupe_key)
    ]


# ---- the welcome --------------------------------------------------------


def test_signing_up_gets_the_setup_commands_by_email(api, mailbox):
    """The one moment they are certainly paying attention."""
    tenant, _ = _trial(api)

    assert len(mailbox) == 1
    body = mailbox[0].get_content()
    assert "curl -X POST https://hub.example/api/licenses/me/sensors" in body
    assert tenant.api_key in body


def test_the_welcome_speaks_the_sector_they_chose(api, mailbox):
    """A restaurant is not told about vaccine fridges."""
    _trial(api)
    assert "restaurant" in mailbox[0].get_content()


def test_a_broken_mail_host_cannot_fail_a_signup(api, monkeypatch):
    """An account that exists matters more than a greeting that arrived."""

    def _explode(*args, **kwargs):
        raise RuntimeError("mail is down")

    monkeypatch.setattr(conversion, "welcome", _explode)

    resp = api.post(
        "/api/signup",
        json={
            "company_name": "Northgate Foods",
            "full_name": "Dana Reyes",
            "email": "dana@northgate.example",
            "password": "correct-horse-battery",
            "contact_phone": "+1-555-0100",
        },
    )
    assert resp.status_code == 201


# ---- the ladder ---------------------------------------------------------


@pytest.mark.parametrize(
    "days,expected",
    [(7, "week"), (5, "week"), (3, "three"), (2, "three"), (1, "last")],
)
def test_each_window_sends_its_own_message(api, mailbox, days, expected):
    tenant, _ = _trial(api)
    _days_left(tenant, days)

    run_trial_conversion()

    assert _stages(tenant.tenant_id)[-1].endswith(expected)


def test_a_missed_window_is_skipped_not_caught_up_on(api, mailbox):
    """The single worst thing an unattended sequence can do.

    Nobody ran the pass for a week and a half. The customer gets the
    message that fits today, not the three it slept through fired in the
    same minute.
    """
    tenant, _ = _trial(api)
    _days_left(tenant, 1)

    run_trial_conversion()

    sent = [k for k in _stages(tenant.tenant_id) if not k.endswith("welcome")]
    assert sent == [f"trial:{tenant.tenant_id}:last"]


def test_the_same_stage_is_never_sent_twice(api, mailbox):
    """The pass runs hourly. The customer hears from it once."""
    tenant, _ = _trial(api)
    _days_left(tenant, 3)

    for _ in range(24):
        run_trial_conversion()

    assert _stages(tenant.tenant_id).count(f"trial:{tenant.tenant_id}:three") == 1


def test_the_ladder_advances_as_the_trial_runs_down(api, mailbox):
    """One message per window, in order, over the life of a trial."""
    tenant, _ = _trial(api)

    seen = []
    for days in (7, 3, 1):
        _days_left(tenant, days)
        run_trial_conversion()
        seen.append(_stages(tenant.tenant_id)[-1].rsplit(":", 1)[1])

    assert seen == ["week", "three", "last"]


# ---- after it ends ------------------------------------------------------


def test_an_expired_trial_is_told_monitoring_is_still_running(api, mailbox):
    """Grace is the product's own promise, and worth saying out loud."""
    tenant, _ = _trial(api)
    tenant.expires_at = utc_now() - timedelta(days=2)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    run_trial_conversion()

    body = mailbox[-1].get_content()
    assert _stages(tenant.tenant_id)[-1].endswith("grace")
    assert "still running" in body


def test_a_lapsed_trial_is_told_plainly_that_nothing_is_watched(api, mailbox):
    """The most important email this system sends, and the least welcome."""
    tenant, _ = _trial(api)
    tenant.expires_at = utc_now() - timedelta(days=90)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    run_trial_conversion()

    assert _stages(tenant.tenant_id)[-1].endswith("dark")
    assert "has stopped" in mailbox[-1].get_content()


def test_a_suspended_account_hears_nothing(api, mailbox):
    """Suspension is a decision somebody made. Do not sell into it."""
    tenant, _ = _trial(api)
    _days_left(tenant, 1)
    tenant.suspended = True
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    run_trial_conversion()

    assert _stages(tenant.tenant_id) == [f"trial:{tenant.tenant_id}:welcome"]


def test_a_paying_customer_is_left_alone(api, tenant_factory, mailbox):
    """A renewal is a conversation, and the book already surfaces it."""
    tenant_factory(plan="growth")

    assert run_trial_conversion()["sent_count"] == 0
    assert mailbox == []


# ---- the activation nudge ----------------------------------------------


def test_a_trial_with_nothing_registered_is_nudged(api, mailbox):
    """A trial with no sensor in it never converts, and usually stalls
    on the first step rather than on the product."""
    tenant, _ = _trial(api)
    tenant.activated_at = utc_now() - timedelta(days=3)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    run_trial_conversion()

    assert _stages(tenant.tenant_id)[-1].endswith("activation")


def test_nobody_is_badgered_on_their_first_afternoon(api, mailbox):
    tenant, _ = _trial(api)
    run_trial_conversion()
    assert _stages(tenant.tenant_id) == [f"trial:{tenant.tenant_id}:welcome"]


def test_a_trial_that_registered_something_is_not_nudged(
    api, mailbox, sensor_factory
):
    tenant, body = _trial(api)
    tenant.activated_at = utc_now() - timedelta(days=3)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())
    sensor_factory(
        {"X-CyberLogix-Key": body["api_key"]}, "FRIDGE-1", "restaurant"
    )

    run_trial_conversion()

    assert not any(k.endswith("activation") for k in _stages(tenant.tenant_id))


# ---- the numbers in the message ----------------------------------------


def test_the_sales_line_is_built_from_their_own_readings(
    api, mailbox, sensor_factory
):
    """The email that closes a monitoring contract says *we caught this*.

    It only works if it is true, so every figure comes from the tenant's
    own records rather than a brochure.
    """
    tenant, body = _trial(api)
    key = {"X-CyberLogix-Key": body["api_key"]}
    sensor_factory(key, "FRIDGE-1", "restaurant")
    api.post("/api/sensor-pulse", headers=key,
             json={"sensor_id": "FRIDGE-1", "temperature_fahrenheit": 38.0})
    api.post("/api/sensor-pulse", headers=key,
             json={"sensor_id": "FRIDGE-1", "temperature_fahrenheit": 61.0})

    evidence = trial_evidence(STORE.get_tenant(tenant.tenant_id))
    assert evidence["units"] == 1
    assert evidence["readings"] == 2
    assert evidence["incidents"] == 1
    assert evidence["monthly_usd"] > 0

    _days_left(tenant, 3)
    run_trial_conversion()

    text = mailbox[-1].get_content()
    assert "1 excursion" in text
    assert f"${evidence['monthly_usd']:,.2f} a month" in text


def test_the_last_day_is_measured_in_hours_not_rounded_days(api, mailbox):
    """Two hours left and twenty-three both round to "1 day".

    Telling somebody their trial ends tomorrow when it ends before lunch
    is a small lie that costs the sale.
    """
    tenant, _ = _trial(api)
    tenant.expires_at = utc_now() + timedelta(hours=3)
    STORE._db.put("tenant", tenant.tenant_id, tenant.to_row())

    run_trial_conversion()

    assert "ends today" in mailbox[-1]["Subject"]


def test_a_trial_that_caught_nothing_is_told_the_truth(api, mailbox):
    """No readings is not a reason to invent a number."""
    tenant, _ = _trial(api)
    _days_left(tenant, 3)

    run_trial_conversion()

    assert "has not taken any readings yet" in mailbox[-1].get_content()


# ---- the ladder is commercial mail -------------------------------------


def test_unsubscribing_stops_the_sequence(api, mailbox):
    """It is mail we chose to send them, so declining it must work."""
    tenant, _ = _trial(api)
    STORE.suppress_address(tenant.contact_email, "unsubscribed")
    _days_left(tenant, 3)

    assert run_trial_conversion()["sent_count"] == 0


# ---- robustness ---------------------------------------------------------


def test_one_broken_account_does_not_silence_the_rest(api, mailbox, monkeypatch):
    """The pass writes to every trial on the book. One bad row must not
    cost the company every other conversation that week."""
    first, _ = _trial(api, "Northgate Foods", "a@northgate.example")
    second, _ = _trial(api, "Harbour Cold", "b@harbour.example")
    _days_left(first, 1)
    _days_left(second, 1)

    real = conversion.trial_evidence

    def _selective(tenant):
        if tenant.tenant_id == first.tenant_id:
            raise RuntimeError("this estate cannot be priced")
        return real(tenant)

    monkeypatch.setattr(conversion, "trial_evidence", _selective)

    result = run_trial_conversion()

    assert result["failed_tenants"] == [first.tenant_id]
    assert result["sent_count"] == 1
    assert any(k.endswith("last") for k in _stages(second.tenant_id))


def test_the_operator_can_see_what_the_next_pass_would_say(
    api, admin_headers, mailbox
):
    """The first thing anybody sensibly wants from a mail robot."""
    tenant, _ = _trial(api)
    _days_left(tenant, 2)

    body = api.get("/api/conversion/due", headers=admin_headers).json()

    assert body["count"] == 1
    assert body["due"][0]["stage"] == "three"
    assert body["due"][0]["already_sent"] is False


def test_the_preview_is_not_public(api):
    assert api.get("/api/conversion/due").status_code == 401
    assert api.post("/api/conversion/run").status_code == 401
