"""A roster that would reach nobody is said so before an alert needs it.

Muting everyone for leave, or taking everyone off texts, did not fail
loudly. The store quietly fell back to the number given at sign-up, and
the first anybody learned of it was a breach texted to a desk phone.
"""

import pytest


def _add(api, headers, name, phone, **extra):
    resp = api.post("/api/contacts", headers=headers,
                    json={"full_name": name, "phone": phone, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_muting_the_last_person_warns_and_emails_the_owner(api, operator_factory, mailbox):
    headers, tenant, _ = operator_factory()
    night = _add(api, headers, "Night Engineer", "+15550101")
    assert api.get("/api/contacts/preview", headers=headers).json()["warnings"] == []

    resp = api.patch(f"/api/contacts/{night['contact_id']}", headers=headers,
                     json={"active": False})
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["warnings"]

    assert any("alert text" in w and "+1-555-0100" in w for w in warnings)
    assert api.get("/api/contacts/preview", headers=headers).json()["warnings"] == warnings
    owner_mail = mailbox.to("ops@example.com")
    assert len(owner_mail) == 1
    assert "reach nobody" in owner_mail[0]["Subject"]

    # Told once, not on every edit while the gap stays the same.
    api.patch(f"/api/contacts/{night['contact_id']}", headers=headers,
              json={"full_name": "Night Engineer (leave)"})
    assert len(mailbox.to("ops@example.com")) == 1


def test_a_change_that_leaves_someone_on_call_is_quiet(api, operator_factory, mailbox):
    headers, _, _ = operator_factory()
    _add(api, headers, "Day Lead", "+15550102")
    night = _add(api, headers, "Night Engineer", "+15550101")

    resp = api.patch(f"/api/contacts/{night['contact_id']}", headers=headers,
                     json={"active": False})

    assert resp.json()["warnings"] == []
    assert mailbox.to("ops@example.com") == []


def test_removing_the_last_person_off_texts_warns(api, operator_factory, mailbox):
    headers, _, _ = operator_factory()
    texts = _add(api, headers, "Night Engineer", "+15550101")
    _add(api, headers, "Caller", "+15550103", receives_sms=False)

    resp = api.delete(f"/api/contacts/{texts['contact_id']}", headers=headers)

    assert any("alert text" in w for w in resp.json()["warnings"])
    assert not any("escalation call" in w for w in resp.json()["warnings"])
    assert len(mailbox.to("ops@example.com")) == 1


def test_a_site_nobody_covers_is_named(api, operator_factory, sensor_factory, mailbox):
    headers, _, _ = operator_factory()
    site = api.post("/api/sites", headers=headers,
                    json={"name": "Boynton Beach"}).json()
    other = api.post("/api/sites", headers=headers,
                     json={"name": "Boca Raton"}).json()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    assert api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
                    json={"sensor_id": "FRZ-1"}).status_code == 200
    local = _add(api, headers, "Boynton Manager", "+15550104", site_id=site["site_id"])
    _add(api, headers, "Boca Manager", "+15550105", site_id=other["site_id"])

    resp = api.patch(f"/api/contacts/{local['contact_id']}", headers=headers,
                     json={"active": False})

    warnings = resp.json()["warnings"]
    assert any("Boynton Beach" in w for w in warnings)
    assert not any("Boca Raton" in w for w in warnings)


def test_an_empty_roster_is_the_documented_fallback_not_a_gap(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.get("/api/contacts/preview", headers=headers).json()["warnings"] == []
