"""Licences that expire, and the person who left but still gets the call.

Three things a health inspector, a labour board and a plaintiff's lawyer
all ask about, and none of which a temperature sensor can see.

The one that matters most is the last. A person who no longer works here
is still on the on-call roster, because HR and monitoring live in
different applications: payroll removes them, the escalation ladder does
not. At 3am the freezer fails, the call goes to somebody who handed
their keys back in March, nobody answers, and the dashboard says the
estate is covered.

No HR system can see that. No monitoring product thinks to look. This
one holds both halves, so it checks rather than asking somebody to
remember -- and that tick is read from the roster rather than ticked by
hand, because a checklist item you can tick without doing is decoration.
"""

from datetime import timedelta

import pytest

import people
from store import STORE, utc_now


def day(offset: int) -> str:
    return (utc_now() + timedelta(days=offset)).strftime("%Y-%m-%d")


@pytest.fixture()
def kitchen(api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")
    return {**headers, **owner}, tenant


@pytest.fixture()
def dana(api, kitchen):
    headers, _ = kitchen
    made = api.post("/api/people", headers=headers, json={
        "full_name": "Dana Reyes", "role": "Head chef",
        "email": "dana@bell.example", "phone": "+15550100"})
    assert made.status_code == 201, made.text
    return made.json()["person"]


# --- the calendar ---------------------------------------------------------


def test_a_licence_expiry_lands_on_the_calendar(api, kitchen, dana):
    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(20), "required_to_work": True})

    book = api.get("/api/people/calendar", headers=headers).json()

    assert book["credentials"] == 1
    assert book["expiring_within_30_days"] == 1
    entry = book["entries"][0]
    assert entry["full_name"] == "Dana Reyes"
    assert entry["state"] == "soon"
    assert entry["days_left"] == 20


def test_the_calendar_groups_by_month(api, kitchen, dana):
    """A rota is planned a month at a time, so that is the shape."""
    headers, _ = kitchen
    for offset in (10, 40, 100):
        api.post("/api/people/credentials", headers=headers, json={
            "staff_id": dana["staff_id"], "name": f"Card {offset}",
            "expires_on": day(offset)})

    book = api.get("/api/people/calendar", headers=headers).json()

    assert len(book["by_month"]) >= 2
    months = [m["month"] for m in book["by_month"]]
    assert months == sorted(months)


def test_an_expired_licence_that_blocks_the_job_is_called_out(
    api, kitchen, dana
):
    """The difference between a reminder and a shutdown risk."""
    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(-3), "required_to_work": True})

    book = api.get("/api/people/calendar", headers=headers).json()

    assert book["expired"] == 1
    assert book["expired_and_blocking"] == 1
    assert "may not be allowed to be" in book["note"]


def test_an_expired_nice_to_have_is_not_a_shutdown_risk(api, kitchen, dana):
    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Fire safety training",
        "expires_on": day(-3), "required_to_work": False})

    book = api.get("/api/people/calendar", headers=headers).json()

    assert book["expired"] == 1
    assert book["expired_and_blocking"] == 0


def test_a_leaver_drops_off_the_calendar(api, kitchen, dana):
    """Chasing a renewal for somebody who left is noise."""
    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(5)})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    book = api.get("/api/people/calendar", headers=headers).json()

    assert book["credentials"] == 0


def test_the_suggestions_match_the_sector(api, kitchen):
    headers, _ = kitchen

    offered = api.get("/api/people", headers=headers).json()

    assert "Food handler card" in offered["suggested_credentials"]


# --- the warning ladder ---------------------------------------------------


def test_a_warning_is_raised_weeks_out_not_on_the_day(api, kitchen, dana):
    """A renewal is paperwork and a waiting list, not a same-day errand."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(30), "required_to_work": True})

    lines = people.due_warnings(tenant["tenant_id"])

    assert any("30 day(s)" in line and "required for their job" in line
               for line in lines)


def test_an_expired_licence_keeps_warning(api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(-5)})

    lines = people.due_warnings(tenant["tenant_id"])

    assert any("expired 5 day(s) ago" in line for line in lines)


def test_the_daily_digest_carries_it(api, kitchen, dana):
    """It has to reach somebody who has not opened a page."""
    from digest import _render_operator, operator_digest

    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(-1), "required_to_work": True})

    text = _render_operator(operator_digest())

    assert "may not be allowed to be" in text


# --- the leaver still on the roster ---------------------------------------


def test_a_leaver_still_on_the_roster_is_flagged(api, kitchen, dana):
    """The check no HR system can run and no monitoring product thinks
    to. An alert routed to somebody who handed their keys back is an
    alert nobody answers."""
    headers, _ = kitchen
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15550100"})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0), "reason": "dismissed"})

    leavers = api.get("/api/people/departures", headers=headers).json()

    assert leavers["still_reachable_by_alerts"] == 1
    row = leavers["departures"][0]
    assert row["still_on_the_roster"]
    assert "Nobody is coming" in row["urgent"]


def test_removing_them_from_the_roster_clears_it(api, kitchen, dana):
    """And it clears because the roster changed, not because somebody
    ticked a box. A checklist item you can tick without doing is
    decoration."""
    headers, _ = kitchen
    made = api.post("/api/contacts", headers=headers,
                    json={"full_name": "Dana Reyes", "phone": "+15550100"})
    contact_id = made.json()["contact_id"]
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    api.delete(f"/api/contacts/{contact_id}", headers=headers)

    leavers = api.get("/api/people/departures", headers=headers).json()
    assert leavers["still_reachable_by_alerts"] == 0
    steps = {s["key"]: s for s in leavers["departures"][0]["steps"]}
    assert steps["roster_removed"]["done"] is True
    assert steps["roster_removed"]["verified_by_the_system"] is True


def test_the_roster_step_cannot_be_ticked_by_hand(api, kitchen, dana):
    headers, _ = kitchen
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15550100"})
    opened = api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)}).json()
    off_id = opened["offboarding"]["offboarding_id"]

    refused = api.post(f"/api/people/departures/{off_id}/step",
                       headers=headers,
                       json={"step": "roster_removed", "done": True})

    assert refused.status_code == 400
    assert "read from the on-call roster" in refused.json()["detail"]


def test_the_other_obligations_are_tracked(api, kitchen, dana):
    """Final pay, accrued time, benefits notice, equipment, access."""
    headers, _ = kitchen
    opened = api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)}).json()
    off_id = opened["offboarding"]["offboarding_id"]
    assert opened["offboarding"]["outstanding"] >= 5

    ticked = api.post(f"/api/people/departures/{off_id}/step",
                      headers=headers, json={"step": "final_pay"})

    assert ticked.status_code == 200
    steps = {s["key"]: s for s in ticked.json()["offboarding"]["steps"]}
    assert steps["final_pay"]["done"] is True
    assert steps["final_pay"]["done_at"]


def test_no_deadline_is_asserted_as_law(api, kitchen, dana):
    """Final-pay and benefits windows differ by jurisdiction and by what
    was signed. An application that hardcoded one would be wrong
    somewhere, and confidently wrong is worse than silent."""
    headers, _ = kitchen
    opened = api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)}).json()

    steps = {s["key"]: s for s in opened["offboarding"]["steps"]}
    assert "differ by jurisdiction" in steps["final_pay"]["note"]
    assert "window" in steps["benefits_notice"]["note"]


def test_a_departure_needs_an_owner(api, kitchen, dana, tenant_factory):
    """It starts obligations that cost money."""
    headers, _ = kitchen
    machine = {"X-CyberLogix-Key": headers["X-CyberLogix-Key"]}

    refused = api.post("/api/people/departures", headers=machine, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    assert refused.status_code in (401, 403)


def test_one_tenant_cannot_see_anothers_staff(api, kitchen, dana,
                                              tenant_factory, owner_headers):
    other, _ = tenant_factory(plan="enterprise", company_name="Someone Else")
    theirs = {**other, **owner_headers(other, email="them@example.com")}

    listed = api.get("/api/people", headers=theirs).json()

    assert listed["count"] == 0


def test_a_roster_with_other_people_on_it_does_not_break(api, kitchen, dana):
    """This one shipped and 500'd on the first real roster.

    The match also compared an email address, which a roster Contact has
    never had -- it is a phone ladder with a name on it. Every test
    passed because the leaver's phone matched on the first contact and
    the branch was never reached. Put somebody else on the roster first
    and the whole endpoint fell over.
    """
    headers, _ = kitchen
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Someone Else", "phone": "+15559999"})
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15550100"})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    listed = api.get("/api/people/departures", headers=headers)

    assert listed.status_code == 200, listed.text
    assert listed.json()["still_reachable_by_alerts"] == 1


def test_a_number_typed_differently_is_the_same_number(api, kitchen, dana):
    """A roster entry typed by a different person on a different day is
    the normal case, not the exception."""
    headers, _ = kitchen
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+1 (555) 0100"})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    listed = api.get("/api/people/departures", headers=headers).json()

    assert listed["still_reachable_by_alerts"] == 1


def test_the_same_name_on_a_different_number_is_a_question(api, kitchen, dana):
    """Somebody can sit on the ladder under a personal mobile that never
    reached their staff record. Worth raising, not worth asserting."""
    headers, _ = kitchen
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15557777"})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(0)})

    row = api.get("/api/people/departures", headers=headers).json()["departures"][0]

    assert row["still_on_the_roster"] == []
    assert row["possibly_on_the_roster"]
    assert "different number" in row["urgent"]


# --- the activator: the part that goes out on its own ----------------------
#
# The calendar above answers the question correctly and answers it only to
# somebody who opened a browser and thought to look. A licence nobody
# renewed is not found by looking. It is found by an inspector, or by an
# insurer reading the roster after a claim. So it has to arrive on its own,
# and these are the tests that it does -- and, just as much, that it stops.


def mail_to(address: str):
    """Oldest first. `mail_log` is newest first, which reads backwards
    when the point of the test is the order things were sent in."""
    return [m for m in reversed(STORE.mail_log(limit=1000))
            if m.to_address == address]


def licence_mail(address: str):
    return [m for m in mail_to(address) if "licence-alert:" in m.dedupe_key]


class TestWhichRungFires:
    """`_alert_horizon` is the whole schedule, so it is tested alone."""

    def test_it_fires_the_rung_just_crossed(self):
        assert people._alert_horizon(30) == 30
        assert people._alert_horizon(14) == 14
        assert people._alert_horizon(0) == 0

    def test_a_day_between_rungs_reports_the_one_above_it(self):
        """So the notice is sent once, on the first pass past the rung.

        29 days out is inside the 30-day rung: it has been sent, and the
        dedupe key is the same one, so nothing goes out again.
        """
        assert people._alert_horizon(29) == 30
        assert people._alert_horizon(15) == 30
        assert people._alert_horizon(13) == 14

    def test_an_outage_does_not_deliver_a_backlog(self):
        """31 days when the process died, 13 when it came back.

        The 14-day notice is the true one. Sending the 30-day notice as
        well, two weeks late, is how an alert list teaches its reader
        that the dates in it are wrong.
        """
        assert people._alert_horizon(13) == 14

    def test_nothing_fires_beyond_the_furthest_rung(self):
        assert people._alert_horizon(61) is None
        assert people._alert_horizon(900) is None


class TestTheNagLadder:
    def test_it_repeats_daily_for_the_first_week(self):
        today = utc_now().date()
        assert people._nag_stamp(1, today) == today.isoformat()
        assert people._nag_stamp(7, today) == today.isoformat()

    def test_and_weekly_after_that(self):
        """Not because it stopped mattering.

        A daily email nobody acts on is the one that gets filtered, and
        it takes the next real alert with it.
        """
        today = utc_now().date()
        assert people._nag_stamp(8, today) == today.strftime("%G-W%V")
        assert people._nag_stamp(90, today) == today.strftime("%G-W%V")


def test_a_renewal_notice_reaches_the_owner_without_being_asked(
        api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(30), "required_to_work": True})

    people.send_licence_alerts()

    body = licence_mail(tenant["contact_email"])[-1]
    assert "Food handler card" in body.body
    assert "Dana Reyes" in body.body
    assert "30 day(s)" in body.body


def test_the_same_notice_is_not_sent_twice(api, kitchen, dana):
    """Run hourly, so this is the property the whole design rests on."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(30), "required_to_work": True})

    for _ in range(5):
        people.send_licence_alerts()

    assert len(licence_mail(tenant["contact_email"])) == 1


def test_the_next_rung_down_is_a_new_notice(api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(30), "required_to_work": True})
    people.send_licence_alerts()

    # The same licence a fortnight later, without touching the record.
    later = utc_now() + timedelta(days=17)
    people.send_licence_alerts(now=later)

    sent = licence_mail(tenant["contact_email"])
    assert len(sent) == 2
    assert "13 day(s)" in sent[-1].body


def test_an_expiry_day_that_was_missed_still_sends_one_notice(
        api, kitchen, dana):
    """The process was down on the day. It owes one email, not two."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "First aid",
        "expires_on": day(-3), "required_to_work": False})

    people.send_licence_alerts()
    people.send_licence_alerts()

    sent = licence_mail(tenant["contact_email"])
    assert len(sent) == 1
    assert "expired 3 day(s) ago" in sent[0].body


def test_an_expired_licence_that_stops_the_job_is_urgent_and_repeats(
        api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food protection manager",
        "expires_on": day(-2), "required_to_work": True})

    people.send_licence_alerts()
    people.send_licence_alerts(now=utc_now() + timedelta(days=1))

    sent = licence_mail(tenant["contact_email"])
    assert len(sent) == 2
    assert "may not be allowed on shift" in sent[0].body
    assert "needing attention today" in sent[0].subject


def test_after_a_week_the_urgent_notice_drops_to_weekly(api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food protection manager",
        "expires_on": day(-20), "required_to_work": True})

    people.send_licence_alerts()
    before = len(licence_mail(tenant["contact_email"]))
    # The next day, inside the same ISO week: nothing new.
    people.send_licence_alerts(now=utc_now() + timedelta(days=1))

    assert len(licence_mail(tenant["contact_email"])) == before


def test_nobody_is_emailed_about_the_licences_of_somebody_who_left(
        api, kitchen, dana):
    """A leaver does not need their first aid certificate renewed."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(7), "required_to_work": True})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(-1),
        "reason": "Dismissed"})

    people.send_licence_alerts()

    assert not [m for m in licence_mail(tenant["contact_email"])
                if "Food handler card" in m.body]


def test_a_leaver_still_on_the_roster_is_urgent_and_names_the_number(
        api, kitchen, dana):
    """The one an owner cannot find any other way.

    Payroll removed them. The escalation ladder did not. The email has
    to carry the number, because "someone is still on the roster" is not
    something you can act on at 7am.
    """
    headers, tenant = kitchen
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Dana Reyes", "phone": "+1 555-0100",
        "channel": "sms", "escalation_order": 1})
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(-3),
        "reason": "Dismissed"})

    people.send_licence_alerts()

    body = licence_mail(tenant["contact_email"])[-1].body
    assert "still on the on-call roster" in body
    assert "555-0100" in body


def test_a_leaver_off_the_roster_produces_no_email(api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(-3),
        "reason": "Dismissed"})

    people.send_licence_alerts()

    assert licence_mail(tenant["contact_email"]) == []


def test_nothing_goes_out_before_the_send_hour(api, kitchen, dana):
    """An email saying somebody cannot legally work, at 3am, reads as an
    emergency and is not one."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(7), "required_to_work": True})

    early = utc_now().replace(hour=3)
    out = people.send_licence_alerts(now=early, hour_utc=7)

    assert out["skipped"] == "before_send_hour"
    assert licence_mail(tenant["contact_email"]) == []

    out = people.send_licence_alerts(now=utc_now().replace(hour=9), hour_utc=7)
    assert out["skipped"] is None
    assert len(licence_mail(tenant["contact_email"])) == 1


def test_a_suspended_account_is_not_emailed(api, kitchen, dana):
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(7), "required_to_work": True})
    STORE.set_suspended(STORE.get_tenant(tenant["tenant_id"]), True)

    people.send_licence_alerts()

    assert licence_mail(tenant["contact_email"]) == []


def test_the_console_shows_exactly_what_would_be_emailed(api, kitchen, dana):
    """An alerter you cannot inspect is one you have to trust."""
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food protection manager",
        "expires_on": day(-2), "required_to_work": True})
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Allergen awareness",
        "expires_on": day(14), "required_to_work": False})

    shown = api.get("/api/people/alerts", headers=headers).json()
    people.send_licence_alerts()
    body = " ".join(m.body for m in licence_mail(tenant["contact_email"]))

    assert shown["count"] == 2
    assert len(shown["urgent"]) == 1 and len(shown["advisory"]) == 1
    for item in shown["urgent"] + shown["advisory"]:
        assert item["line"] in body


def test_one_broken_account_does_not_stop_the_others(
        api, tenant_factory, owner_headers, monkeypatch):
    """The whole point of an unattended pass."""
    first, tenant_a = tenant_factory(plan="enterprise", company_name="A St")
    second, tenant_b = tenant_factory(plan="enterprise", company_name="B St")
    # A distinct owner per tenant: `owner_headers` bootstraps by email,
    # and one email across both signs the second tenant's records into
    # the first account, which makes the test pass for the wrong reason.
    for headers, name, who in ((first, "Ann Lee", "a@ex.com"),
                               (second, "Bo Chen", "b@ex.com")):
        head = {**headers, **owner_headers(headers, email=who)}
        made = api.post("/api/people", headers=head, json={
            "full_name": name, "role": "Cook", "phone": "+15550199"})
        api.post("/api/people/credentials", headers=head, json={
            "staff_id": made.json()["person"]["staff_id"],
            "name": "Food handler card", "expires_on": day(7),
            "required_to_work": True})

    real = people.send_mail

    def explode(*args, **kwargs):
        if kwargs.get("tenant_id") == tenant_a["tenant_id"]:
            raise RuntimeError("mail host fell over")
        return real(*args, **kwargs)

    monkeypatch.setattr(people, "send_mail", explode)
    out = people.send_licence_alerts()

    assert tenant_a["tenant_id"] in out["failed_tenants"]
    assert licence_mail(tenant_b["contact_email"])


def test_a_queued_alert_is_not_counted_as_a_sent_one(api, kitchen, dana):
    """The figure that would otherwise hide a dead mail host.

    With no SMTP configured every message queues. A single "sent" count
    that includes them reads as a working alerter right up to the day
    somebody asks why nobody was warned about anything.
    """
    headers, tenant = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(7), "required_to_work": True})

    out = people.send_licence_alerts()

    assert out["sent_count"] == 0
    assert out["queued_count"] == 1
    assert licence_mail(tenant["contact_email"])


def test_a_duplicate_is_counted_as_neither(api, kitchen, dana):
    headers, _ = kitchen
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": dana["staff_id"], "name": "Food handler card",
        "expires_on": day(7), "required_to_work": True})
    people.send_licence_alerts()

    again = people.send_licence_alerts()

    assert again["sent_count"] == 0
    assert again["queued_count"] == 0


def test_a_leaver_with_no_phone_number_does_not_break_the_pass(
        api, kitchen):
    """A staff record without a number is the normal case, not an edge.

    There is no way to tell whether they are on the roster, and both
    guesses are wrong: claiming they are cries wolf, and reading the
    empty string as a roster entry takes the whole fleet-wide pass down
    with it, so nobody on any account is warned about anything.
    """
    headers, tenant = kitchen
    made = api.post("/api/people", headers=headers, json={
        "full_name": "Kit Marlow", "role": "Porter"})
    assert made.status_code == 201, made.text
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": made.json()["person"]["staff_id"],
        "left_on": day(-3), "reason": "Dismissed"})

    out = people.send_licence_alerts()

    assert out["failed_tenants"] == []
    assert licence_mail(tenant["contact_email"]) == []


def test_taking_the_leaver_off_the_roster_stops_the_alert(api, kitchen, dana):
    """The alert has to end when the thing it is about is dealt with.

    Deactivating the contact is the fix. An alerter that keeps arriving
    after the owner has done exactly what it asked is one they turn off,
    and then it is not there for the next leaver either.
    """
    headers, tenant = kitchen
    made = api.post("/api/contacts", headers=headers, json={
        "full_name": "Dana Reyes", "phone": "+1 555-0100",
        "channel": "sms", "escalation_order": 1})
    contact_id = made.json()["contact_id"]
    api.post("/api/people/departures", headers=headers, json={
        "staff_id": dana["staff_id"], "left_on": day(-3),
        "reason": "Dismissed"})
    people.send_licence_alerts()
    raised = len(licence_mail(tenant["contact_email"]))
    assert raised == 1

    api.patch(f"/api/contacts/{contact_id}", headers=headers,
              json={"active": False})
    # The next day, which would otherwise be a fresh nag stamp.
    people.send_licence_alerts(now=utc_now() + timedelta(days=1))

    assert len(licence_mail(tenant["contact_email"])) == raised
