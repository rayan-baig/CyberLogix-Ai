"""What was done about it, which is the part an inspector reads.

A temperature log is not what a health inspector is checking. HACCP
principles 4 and 7 make the corrective action and its review the record:
what happened to the product, who did it, and which manager signed. An
excursion with a temperature and no entry is the line that gets written
up, and this application recorded the temperature and nothing else.

It is also where the two halves of this market fail to meet. The
hardware vendors -- Monnit, Swift Sensors, TempGenius, Dickson -- log
temperatures and stop. The food-safety workflow platforms capture
corrective actions but cannot see a freezer, so somebody types the
readings in by hand, which is the step that gets skipped on a bad night
and the gap the inspector finds.
"""

import pytest

from store import STORE


@pytest.fixture()
def excursion(api, tenant_factory, owner_headers, sensor_factory):
    """One real breach on a restaurant walk-in."""
    headers, tenant = tenant_factory(plan="enterprise", company_name="Bell St")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")
    hot = api.post("/api/sensor-pulse", headers=headers,
                   json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})
    assert hot.status_code == 200, hot.text
    incidents = STORE.open_incidents(tenant["tenant_id"])
    assert incidents, "the premise: a breach opens an incident"
    return {**headers, **owner}, incidents[0]


def test_a_new_excursion_has_an_incomplete_record(excursion):
    """And says which parts are missing, rather than looking finished."""
    _, incident = excursion
    shown = incident.public()

    assert shown["record_complete"] is False
    assert "a corrective action" in shown["record_missing"]
    assert "a manager's review" in shown["record_missing"]


def test_recording_what_was_done(api, excursion):
    headers, incident = excursion

    done = api.post(f"/api/voice/corrective-action/{incident.incident_id}",
                    headers=headers,
                    json={"action": "Moved product to the back walk-in",
                          "product_disposition": "moved"})

    assert done.status_code == 200, done.text
    shown = done.json()["incident"]
    assert shown["corrective_action"] == "Moved product to the back walk-in"
    assert shown["product_disposition"] == "moved"
    assert shown["corrective_action_by"]
    assert shown["corrective_action_at"]


def test_a_manager_signs_it_off(api, excursion):
    headers, incident = excursion
    api.post(f"/api/voice/corrective-action/{incident.incident_id}",
             headers=headers, json={"action": "Discarded affected product",
                                    "product_disposition": "discarded"})

    signed = api.post(f"/api/voice/review/{incident.incident_id}",
                      headers=headers)

    assert signed.status_code == 200, signed.text
    shown = signed.json()["incident"]
    assert shown["reviewed_by"]
    assert shown["record_complete"] is True
    assert shown["record_missing"] == []


def test_a_review_needs_something_to_review(api, excursion):
    """Signing off on nothing is how a log becomes decoration."""
    headers, incident = excursion

    early = api.post(f"/api/voice/review/{incident.incident_id}",
                     headers=headers)

    assert early.status_code == 400
    assert "no corrective action" in early.json()["detail"]


def test_a_review_cannot_be_signed_by_a_machine(api, excursion,
                                                tenant_factory):
    """Principle 7 asks for a named person's review, and a signature
    attributed to "API key" is not one."""
    headers, incident = excursion
    api.post(f"/api/voice/corrective-action/{incident.incident_id}",
             headers=headers, json={"action": "Door closed"})
    machine = {"X-CyberLogix-Key": headers["X-CyberLogix-Key"]}

    refused = api.post(f"/api/voice/review/{incident.incident_id}",
                       headers=machine)

    assert refused.status_code == 403
    assert "named person" in refused.json()["detail"]


def test_an_unknown_disposition_is_refused(api, excursion):
    headers, incident = excursion

    refused = api.post(f"/api/voice/corrective-action/{incident.incident_id}",
                       headers=headers,
                       json={"action": "Something", "product_disposition": "eaten"})

    assert refused.status_code == 422


def test_the_suggestions_match_the_sector(api, excursion):
    """A restaurant is offered restaurant answers. A log where every
    entry is identical is one an inspector reads twice, so these are
    suggestions and free text stays available."""
    headers, _ = excursion

    offered = api.get("/api/voice/corrective-actions", headers=headers).json()

    assert "Discarded affected product" in offered["suggestions"]
    assert "quarantined" in offered["dispositions"]
    assert "not a fixed list" in offered["note"]


def test_the_corrective_action_reaches_the_audit_trail(api, excursion):
    """An entry edited after an inspection is worth nothing, so both the
    action and its review are written to the trail as they happen."""
    headers, incident = excursion
    api.post(f"/api/voice/corrective-action/{incident.incident_id}",
             headers=headers, json={"action": "Moved product"})

    trail = api.get("/api/accounts/audit?limit=20", headers=headers).json()
    actions = [e["action"] for e in trail["entries"]]

    assert "incident.corrective_action" in actions


def test_one_tenant_cannot_sign_off_another_ones_excursion(
    api, excursion, tenant_factory, owner_headers
):
    _, incident = excursion
    other, _ = tenant_factory(plan="enterprise", company_name="Someone Else")
    theirs = {**other, **owner_headers(other, email="them@example.com")}

    refused = api.post(f"/api/voice/corrective-action/{incident.incident_id}",
                       headers=theirs, json={"action": "Nothing to do with me"})

    assert refused.status_code == 404


# --- the report the inspector reads ---------------------------------------


def test_the_compliance_report_names_the_gaps(api, excursion):
    """Finding this here is the difference between finding it during an
    inspection. A report that stays silent about a missing corrective
    action is a report that helps you fail."""
    headers, incident = excursion

    report = api.get("/api/autopilot/compliance?days=7",
                     headers=headers).json()["corrective_actions"]

    assert report["excursions"] == 1
    assert report["without_an_action"] == 1
    assert report["complete"] == 0
    assert report["gaps"][0]["incident_id"] == incident.incident_id
    assert "an inspector would ask about" in report["note"]


def test_a_complete_record_reports_clean(api, excursion):
    headers, incident = excursion
    api.post(f"/api/voice/corrective-action/{incident.incident_id}",
             headers=headers, json={"action": "Discarded product",
                                    "product_disposition": "discarded"})
    api.post(f"/api/voice/review/{incident.incident_id}", headers=headers)

    report = api.get("/api/autopilot/compliance?days=7",
                     headers=headers).json()["corrective_actions"]

    assert report["without_an_action"] == 0
    assert report["awaiting_review"] == 0
    assert report["complete"] == 1
    assert report["gaps"] == []


def test_an_action_without_a_review_is_still_a_gap(api, excursion):
    """Two separate requirements. The person who moved the product and
    the person who signs that it was handled are usually not the same."""
    headers, incident = excursion
    # Disposition supplied too, so the review is the only thing left
    # missing and the assertion below is about exactly that.
    api.post(f"/api/voice/corrective-action/{incident.incident_id}",
             headers=headers, json={"action": "Moved product",
                                    "product_disposition": "moved"})

    report = api.get("/api/autopilot/compliance?days=7",
                     headers=headers).json()["corrective_actions"]

    assert report["without_an_action"] == 0
    assert report["awaiting_review"] == 1
    assert report["complete"] == 0

    # And the incident's own flag agrees, because that is what a screen
    # showing a green tick would read. A mutation that called this
    # complete on the action alone passed every other test here.
    shown = STORE.get_incident(incident.incident_id).public()
    assert shown["record_complete"] is False
    assert shown["record_missing"] == ["a manager's review"]
