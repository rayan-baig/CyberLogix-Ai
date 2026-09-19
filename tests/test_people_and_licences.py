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
from store import utc_now


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
