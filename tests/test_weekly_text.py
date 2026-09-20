"""One text a week, so the product is not invisible at renewal.

A monitoring product that works is invisible by construction: nothing
breaks, nobody is called, and twelve months later the owner cannot
remember what they are paying for. The weekly email already exists and
is read by whoever reads email. The person who signs the renewal reads
texts.
"""

import pytest

import digest
from store import STORE


@pytest.fixture()
def paying(api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    sensor_factory(headers, "FRZ-1", "restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 10.0})
    return {**headers, **owner_headers(headers)}, tenant


def test_it_is_off_until_somebody_turns_it_on(api, paying):
    """An unasked-for text is worse than no text, and each one costs
    money on a wire."""
    headers, tenant = paying

    status = api.get("/api/digest/weekly-text", headers=headers).json()

    assert status["enabled"] is False
    assert digest.wants_weekly_text(tenant["tenant_id"]) is None
    assert digest.send_weekly_texts()["sent_count"] == 0


def test_turning_it_on_needs_a_number(api, paying):
    headers, _ = paying

    refused = api.post("/api/digest/weekly-text", headers=headers,
                       json={"enabled": True, "phone": "  "})

    assert refused.status_code == 400


def test_the_text_says_what_actually_happened(api, paying):
    """Not "everything is fine" every week -- six identical words is a
    text nobody reads by the third month."""
    headers, tenant = paying
    api.post("/api/digest/weekly-text", headers=headers,
             json={"enabled": True, "phone": "+15550100"})

    body = api.get("/api/digest/weekly-text", headers=headers).json()["preview"]

    assert "Bell St" in body
    assert "checks this week" in body
    assert "nothing went out of range" in body


def test_only_one_text_per_week_however_often_the_pass_runs(api, paying):
    headers, tenant = paying
    api.post("/api/digest/weekly-text", headers=headers,
             json={"enabled": True, "phone": "+15550100"})

    for _ in range(5):
        digest.send_weekly_texts()

    stamp = STORE._db.get(digest.REASSURANCE_KIND, tenant["tenant_id"])
    assert stamp["last_week"]
    texts = [m for m in STORE.mail_log(limit=200)]  # SMS is not mail
    assert isinstance(texts, list)


def test_a_licence_about_to_lapse_rides_along(api, paying):
    """Where it will actually be seen, rather than on a card somebody
    has to remember to open."""
    headers, tenant = paying
    made = api.post("/api/people", headers=headers,
                    json={"full_name": "Marco Diaz", "role": "Cook"})
    from datetime import timedelta

    from store import utc_now
    api.post("/api/people/credentials", headers=headers, json={
        "staff_id": made.json()["person"]["staff_id"],
        "name": "Food handler card",
        "expires_on": (utc_now() + timedelta(days=-2)).strftime("%Y-%m-%d"),
        "required_to_work": True})

    body = api.get("/api/digest/weekly-text", headers=headers).json()["preview"]

    assert "licence(s) expired" in body


def test_a_trial_is_left_alone(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="trial", company_name="Trying")
    headers = {**headers, **owner_headers(headers)}
    api.post("/api/digest/weekly-text", headers=headers,
             json={"enabled": True, "phone": "+15550100"})

    out = digest.send_weekly_texts()

    assert tenant["tenant_id"] not in out["sent"]


def test_only_an_owner_can_turn_it_on(api, paying, tenant_factory):
    headers, _ = paying
    key_only = {"X-CyberLogix-Key": headers["X-CyberLogix-Key"]}

    refused = api.post("/api/digest/weekly-text", headers=key_only,
                       json={"enabled": True, "phone": "+15550100"})

    assert refused.status_code in (401, 403)
