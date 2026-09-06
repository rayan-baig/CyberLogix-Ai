"""The reseller channel.

A partner is a principal with narrower rights than a tenant owner. If the
portal ever returns another partner's book, or a customer's roster, no
servicer will put their client list into it — so that is what most of
these check.
"""

import pytest

import partners


@pytest.fixture()
def admin(monkeypatch):
    monkeypatch.setattr(partners, "PLATFORM_ADMIN_KEY", "root-key")
    return {"X-CyberLogix-Admin": "root-key"}


def make_partner(api, admin, name="Acme Refrigeration"):
    resp = api.post("/api/partners", headers=admin,
                    json={"company_name": name, "contact_name": "Ray Diaz",
                          "contact_email": "ray@acme.example"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body, {"X-CyberLogix-Partner": body["api_key"]}


def attach(api, admin, partner_id, tenant_id):
    return api.post(f"/api/partners/{partner_id}/accounts", headers=admin,
                    json={"tenant_id": tenant_id})


def test_minting_a_partner_needs_the_platform_key(api, admin):
    """Anyone able to mint resellers can mint themselves a revenue share."""
    resp = api.post("/api/partners",
                    json={"company_name": "Rogue", "contact_name": "X",
                          "contact_email": "x@example.com"})
    assert resp.status_code == 401

    resp = api.post("/api/partners", headers={"X-CyberLogix-Admin": "wrong"},
                    json={"company_name": "Rogue", "contact_name": "X",
                          "contact_email": "x@example.com"})
    assert resp.status_code == 401


def test_no_admin_key_configured_closes_the_door(api, monkeypatch):
    """A deployment that forgot the variable must not be wide open."""
    monkeypatch.setattr(partners, "PLATFORM_ADMIN_KEY", "")
    resp = api.post("/api/partners", headers={"X-CyberLogix-Admin": "anything"},
                    json={"company_name": "Rogue", "contact_name": "X",
                          "contact_email": "x@example.com"})
    assert resp.status_code == 503


def test_the_key_is_shown_once_and_masked_after(api, admin):
    body, _ = make_partner(api, admin)
    assert body["api_key"].startswith("clx_ptr_")
    listing = api.get("/api/partners", headers=admin).json()
    assert listing["partners"][0]["api_key"].startswith("...")
    assert body["api_key"] not in api.get("/api/partners", headers=admin).text


def test_a_partner_sees_their_own_book_and_its_commission(
    api, admin, operator_factory, sensor_factory, tenant_factory
):
    body, key = make_partner(api, admin)
    headers, tenant, _ = operator_factory(company_name="Bella Vista")
    for n in range(3):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")
    attach(api, admin, body["partner_id"], tenant["tenant_id"])

    out = api.get("/api/partners/me", headers=key).json()
    assert out["accounts"] == 1
    assert out["units"] == 3
    assert out["monthly_billings_usd"] == 3 * 999.0
    assert out["monthly_commission_usd"] == round(3 * 999.0 * 0.20, 2)
    assert out["book"][0]["company_name"] == "Bella Vista"


def test_a_partner_cannot_see_another_partners_account(
    api, admin, operator_factory, sensor_factory
):
    """The one failure that would kill the channel."""
    theirs, theirs_key = make_partner(api, admin, "Their Refrigeration")
    mine, mine_key = make_partner(api, admin, "My Refrigeration")

    headers, tenant, _ = operator_factory(company_name="Someone Else")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    attach(api, admin, theirs["partner_id"], tenant["tenant_id"])

    assert api.get("/api/partners/me", headers=mine_key).json()["accounts"] == 0
    resp = api.get(f"/api/partners/me/accounts/{tenant['tenant_id']}",
                   headers=mine_key)
    assert resp.status_code == 404


def test_the_portal_withholds_the_customers_roster(
    api, admin, operator_factory, sensor_factory
):
    """A servicer needs to dispatch an engineer, not read the rota."""
    body, key = make_partner(api, admin)
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15550100"})
    attach(api, admin, body["partner_id"], tenant["tenant_id"])

    raw = api.get(f"/api/partners/me/accounts/{tenant['tenant_id']}",
                  headers=key).text
    assert "Dana Reyes" not in raw
    assert "+15550100" not in raw
    assert "FRZ-1" in raw


def test_the_portal_surfaces_what_needs_a_visit(
    api, admin, operator_factory, sensor_factory
):
    body, key = make_partner(api, admin)
    headers, tenant, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0,
                   "battery_percent": 9.0})
    attach(api, admin, body["partner_id"], tenant["tenant_id"])

    out = api.get("/api/partners/me", headers=key).json()
    assert len(out["needs_attention"]) == 1
    assert out["needs_attention"][0]["open_incidents"] == 1
    assert out["needs_attention"][0]["low_battery"] == 1


def test_an_account_cannot_be_claimed_twice(
    api, admin, operator_factory, sensor_factory
):
    first, _ = make_partner(api, admin, "First")
    second, _ = make_partner(api, admin, "Second")
    headers, tenant, _ = operator_factory()
    attach(api, admin, first["partner_id"], tenant["tenant_id"])

    resp = attach(api, admin, second["partner_id"], tenant["tenant_id"])
    assert resp.status_code == 409


def test_a_suspended_partner_loses_the_portal(
    api, admin, operator_factory
):
    body, key = make_partner(api, admin)
    from store import STORE

    partner = STORE.get_partner(body["partner_id"])
    partner.active = False
    STORE.save_partner(partner)

    assert api.get("/api/partners/me", headers=key).status_code == 403


def test_partners_survive_a_restart(tmp_path):
    from db import Database
    from store import HubStore

    path = str(tmp_path / "partners.db")
    first = HubStore(db=Database(path))
    partner = first.create_partner("Acme", "Ray", "ray@acme.example", 25.0)
    tenant = first.create_tenant("Bella", "n", "+1", "a@example.com", "growth")
    first.assign_partner(tenant, partner.partner_id)

    second = HubStore(db=Database(path))
    assert second.partner_by_key(partner.api_key).commission_percent == 25.0
    assert [t.tenant_id for t in second.tenants_for_partner(partner.partner_id)] \
        == [tenant.tenant_id]
