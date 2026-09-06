"""The on-call roster and alert fan-out."""

import pytest

@pytest.fixture()
def sent(monkeypatch):
    """Capture every message the platform tries to deliver."""
    log = {"sms": [], "voice": []}

    def _sms(to, body, tenant_id=None):
        log["sms"].append(to)
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued",
                "provider_sid": f"SM{len(log['sms'])}", "detail": "ok"}

    def _call(to, spoken, tenant_id=None, action_url=None):
        log["voice"].append(to)
        # Only the last number on the ladder answers.
        reached = to.endswith("0003")
        return {"channel": "voice", "to": to, "delivered": reached,
                "status": "queued" if reached else "call_failed",
                "provider_sid": "CA1" if reached else None, "detail": "ok"}

    monkeypatch.setattr("telemetry.send_sms", _sms)
    monkeypatch.setattr("voice_dispatch.place_voice_call", _call)
    return log


def add(api, headers, name, phone, order=1, sms=True, voice=True):
    resp = api.post(
        "/api/contacts",
        headers=headers,
        json={"full_name": name, "phone": phone, "escalation_order": order,
              "receives_sms": sms, "receives_voice": voice},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def breach(api, headers, sensor_factory, sensor_id="RACK-01"):
    sensor_factory(headers, sensor_id=sensor_id)
    return api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": 94.0},
    ).json()


def test_empty_roster_falls_back_to_the_tenant_contact(
    api, operator_factory, sensor_factory, sent
):
    """Alerting must never depend on setup that hasn't happened."""
    headers, _, _ = operator_factory()
    body = breach(api, headers, sensor_factory)

    assert sent["sms"] == ["+1-555-0100"]
    assert body["sms_delivery"]["delivered"] is True

    preview = api.get("/api/contacts/preview", headers=headers).json()
    assert preview["sms_recipients"][0]["phone"] == "+1-555-0100"


def test_sms_goes_to_everyone_on_the_roster(
    api, operator_factory, sensor_factory, sent
):
    headers, _, _ = operator_factory()
    add(api, headers, "Night Engineer", "+15550001", order=1)
    add(api, headers, "Site Manager", "+15550002", order=2)
    add(api, headers, "Regional Director", "+15550003", order=3)

    body = breach(api, headers, sensor_factory)

    assert sent["sms"] == ["+15550001", "+15550002", "+15550003"]
    assert len(body["sms_fanout"]) == 3
    assert body["notified_count"] == 3
    assert body["sms_fanout"][0]["contact_name"] == "Night Engineer"


def test_sms_skips_contacts_who_opted_out(
    api, operator_factory, sensor_factory, sent
):
    headers, _, _ = operator_factory()
    add(api, headers, "Texts only", "+15550001", sms=True, voice=False)
    add(api, headers, "Calls only", "+15550002", sms=False, voice=True)

    breach(api, headers, sensor_factory)
    assert sent["sms"] == ["+15550001"]


def test_voice_ladder_stops_at_the_first_person_reached(
    api, operator_factory, sensor_factory, sent
):
    """A dead line must not end the escalation."""
    headers, _, _ = operator_factory()
    add(api, headers, "First", "+15550001", order=1)
    add(api, headers, "Second", "+15550002", order=2)
    add(api, headers, "Third", "+15550003", order=3)
    add(api, headers, "Fourth", "+15550004", order=4)

    incident_id = breach(api, headers, sensor_factory)["incident_id"]
    body = api.post(
        f"/api/voice/escalate/{incident_id}?force=true", headers=headers
    ).json()

    # Tried in order, stopped once the third answered; the fourth is spared.
    assert sent["voice"] == ["+15550001", "+15550002", "+15550003"]
    assert body["call_recipient"] == "Third"
    assert body["voice_delivery"]["delivered"] is True
    assert len(body["escalation_attempts"]) == 3


def test_inactive_contacts_are_skipped(api, operator_factory, sensor_factory, sent):
    headers, _, _ = operator_factory()
    first = add(api, headers, "On leave", "+15550001", order=1)
    add(api, headers, "Covering", "+15550002", order=2)

    api.patch(
        f"/api/contacts/{first['contact_id']}", headers=headers, json={"active": False}
    )
    breach(api, headers, sensor_factory)
    assert sent["sms"] == ["+15550002"]


def test_roster_is_ordered_and_editable(api, operator_factory):
    headers, _, _ = operator_factory()
    add(api, headers, "Third", "+15550003", order=3)
    add(api, headers, "First", "+15550001", order=1)

    roster = api.get("/api/contacts", headers=headers).json()
    assert [c["full_name"] for c in roster["contacts"]] == ["First", "Third"]
    assert roster["using_fallback"] is False

    target = roster["contacts"][1]["contact_id"]
    updated = api.patch(
        f"/api/contacts/{target}", headers=headers, json={"escalation_order": 0 + 1}
    ).json()
    assert updated["escalation_order"] == 1
    # An untouched field keeps its value.
    assert updated["full_name"] == "Third"


def test_empty_patch_is_rejected(api, operator_factory):
    headers, _, _ = operator_factory()
    contact = add(api, headers, "Someone", "+15550001")
    resp = api.patch(f"/api/contacts/{contact['contact_id']}", headers=headers, json={})
    assert resp.status_code == 400


def test_contact_removal(api, operator_factory):
    headers, _, _ = operator_factory()
    contact = add(api, headers, "Leaver", "+15550001")
    resp = api.delete(f"/api/contacts/{contact['contact_id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["remaining"] == 0


def test_viewer_cannot_edit_the_roster(api, operator_factory):
    owner, _, _ = operator_factory()
    api.post(
        "/api/accounts/users",
        headers=owner,
        json={"email": "v@example.com", "full_name": "Vic", "role": "viewer",
              "password": "a-long-viewer-password"},
    )
    login = api.post(
        "/api/accounts/login",
        json={"email": "v@example.com", "password": "a-long-viewer-password"},
    ).json()
    viewer = {"Authorization": f"Bearer {login['token']}"}

    assert api.get("/api/contacts", headers=viewer).status_code == 200
    resp = api.post(
        "/api/contacts",
        headers=viewer,
        json={"full_name": "X", "phone": "+15550009"},
    )
    assert resp.status_code == 403


def test_roster_is_scoped_to_the_tenant(api, operator_factory):
    alice, _, _ = operator_factory(company_name="Alice", email="a@example.com")
    bob, _, _ = operator_factory(company_name="Bob", email="b@example.com")
    contact = add(api, alice, "Alice contact", "+15550001")

    assert api.get("/api/contacts", headers=bob).json()["count"] == 0
    assert api.delete(
        f"/api/contacts/{contact['contact_id']}", headers=bob
    ).status_code == 404
