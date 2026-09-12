"""What happens when a licence runs out.

Expiry used to be a cliff. The day after a trial ended, a vaccine fridge
reporting 75°F got a 402 and nobody was told anything — the exact failure
the product exists to prevent, caused by the product, and a direct
contradiction of the agreement this same codebase generates.

It was also a deadlock. The expired account could not sign in, could not
change plan and could not sign a contract, so the only route from a
finished trial to a paying customer ran through somebody answering an
email.
"""

from datetime import timedelta

import pytest

from auth import LICENCE_GRACE_DAYS, grace_days_left, licence_state
from store import STORE, utc_now


def _shift_expiry(tenant_id, days):
    tenant = STORE.get_tenant(tenant_id)
    tenant.expires_at = utc_now() + timedelta(days=days)
    STORE._db.put("tenant", tenant_id, tenant.to_row())
    return tenant


@pytest.fixture()
def trial(api):
    body = api.post("/api/signup", json={
        "company_name": "Kicking Tyres", "full_name": "Sam",
        "email": "sam@example.com", "password": "correct-horse-battery",
        "contact_phone": "+15550100", "industry_vertical": "pharmacy",
    }).json()
    key = {"X-CyberLogix-Key": body["api_key"]}
    bearer = {"Authorization": f"Bearer {body['token']}"}
    api.post("/api/licenses/me/sensors", headers=key, json={
        "sensor_id": "FRIDGE-1", "industry_vertical": "pharmacy",
        "location_name": "Dispensary"})
    return key, bearer, body["tenant"]


# ---- the alarm keeps working -------------------------------------------


def test_a_lapsed_estate_is_still_watched(api, trial, configured_twilio):
    """The rule the whole product rests on, and the one expiry broke."""
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -1)
    assert licence_state(STORE.get_tenant(tenant["tenant_id"])) == "grace"

    pulse = api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 75.0})
    assert pulse.status_code == 200, pulse.text
    assert pulse.json()["status"].startswith("CRITICAL")
    assert api.get("/api/voice/incidents", headers=key).json()["incidents"]


def test_the_agreement_and_the_code_agree_about_this(api, trial):
    """The terms promise it in writing; this is the code keeping it."""
    import legal

    text = " ".join(legal.terms_of_service().split())
    assert "never withheld for non-payment" in text

    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -LICENCE_GRACE_DAYS + 1)
    assert api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 75.0
    }).status_code == 200


def test_monitoring_does_stop_once_the_grace_period_is_spent(api, trial):
    """Grace is a window, not an amnesty."""
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 1))
    assert licence_state(STORE.get_tenant(tenant["tenant_id"])) == "lapsed"

    pulse = api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 75.0})
    assert pulse.status_code == 402
    # And it names the route that fixes it.
    assert "/api/licenses/me/plan" in pulse.json()["detail"]


@pytest.mark.parametrize("days,expected", [
    (5, "active"), (0, "grace"), (-1, "grace"),
    (-(LICENCE_GRACE_DAYS - 1), "grace"),
    (-(LICENCE_GRACE_DAYS + 1), "lapsed"),
])
def test_the_state_machine(api, trial, days, expected):
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], days)
    assert licence_state(STORE.get_tenant(tenant["tenant_id"])) == expected


def test_a_suspended_estate_is_not_given_grace(api, trial):
    """Suspension is a decision somebody made, not a bill they missed."""
    key, _, tenant = trial
    STORE.set_suspended(STORE.get_tenant(tenant["tenant_id"]), True)
    assert licence_state(STORE.get_tenant(tenant["tenant_id"])) == "suspended"
    assert api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 75.0
    }).status_code == 402
    assert api.get("/api/console/overview", headers=key).status_code == 402


# ---- the way back is always open ---------------------------------------


def test_a_lapsed_customer_can_still_sign_in(api, trial):
    """Locking them out is how a finished trial becomes a lost customer."""
    _, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    signed_in = api.post("/api/accounts/login", json={
        "email": "sam@example.com", "password": "correct-horse-battery"})
    assert signed_in.status_code == 200, signed_in.text


def test_a_lapsed_customer_can_still_upgrade_and_pay(api, trial):
    """The deadlock: they cannot pay because they have not paid."""
    key, bearer, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    headers = {**key, **bearer}

    moved = api.post("/api/licenses/me/plan", headers=headers,
                     json={"plan": "growth"})
    assert moved.status_code == 200, moved.text

    signed = api.post("/api/contracts", headers=headers,
                      json={"term_years": 1})
    assert signed.status_code == 201, signed.text


def test_upgrading_restores_monitoring_at_once(api, trial):
    key, bearer, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    assert api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 40.0
    }).status_code == 402

    api.post("/api/licenses/me/plan", headers={**key, **bearer},
             json={"plan": "growth"})
    assert api.post("/api/sensor-pulse", headers=key, json={
        "sensor_id": "FRIDGE-1", "temperature_fahrenheit": 40.0
    }).status_code == 200


def test_a_lapsed_customer_can_still_read_and_settle_invoices(api, trial):
    from contracts import run_billing

    key, bearer, tenant = trial
    headers = {**key, **bearer}
    api.post("/api/licenses/me/plan", headers=headers, json={"plan": "growth"})
    api.post("/api/contracts", headers=headers, json={"term_years": 1})
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]

    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    assert api.get("/api/invoices", headers=headers).status_code == 200
    paid = api.post(f"/api/invoices/{invoice.invoice_id}/paid",
                    headers=headers, json={"reference": "WIRE-1"})
    assert paid.status_code == 200, paid.text


def test_a_lapsed_customer_can_still_accept_the_terms(api, trial):
    key, bearer, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    resp = api.post("/api/legal/accept", headers={**key, **bearer},
                    json={"accepted_by": "Sam"})
    assert resp.status_code == 201, resp.text


def test_the_console_still_loads_and_says_what_happened(api, trial):
    """The page that carries the way back cannot itself be behind the wall."""
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -(LICENCE_GRACE_DAYS + 30))
    body = api.get("/api/console/overview", headers=key).json()
    assert body["account"]["licence_state"] == "lapsed"
    assert body["account"]["severity"] == "critical"
    assert "no longer being monitored" in body["account"]["headline"]
    assert body["account"]["monthly_if_paid_usd"] > 0


# ---- what a lapse does cost --------------------------------------------


def test_reporting_is_what_a_lapse_withholds(api, trial):
    key, _, tenant = trial
    assert api.get("/api/vault/attestation", headers=key).status_code == 200

    _shift_expiry(tenant["tenant_id"], -1)
    for path in ("/api/vault/attestation", "/api/benchmarks/pharmacy"):
        resp = api.get(path, headers=key)
        assert resp.status_code == 402, f"{path} -> {resp.status_code}"
        assert "alerting" in resp.json()["detail"]


# ---- the warning before the cliff --------------------------------------


def test_nothing_is_shouted_on_the_first_morning_of_a_trial(api, trial):
    """A banner that is on from the moment you arrive is not a warning."""
    key, _, _ = trial
    account = api.get("/api/console/overview", headers=key).json()["account"]
    assert account["severity"] == "ok"
    assert account["headline"] is None


def test_the_warning_arrives_before_the_trial_ends(api, trial):
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], 3)
    account = api.get("/api/console/overview", headers=key).json()["account"]
    assert account["severity"] == "warning"
    assert "ends in 3 days" in account["headline"]
    assert str(LICENCE_GRACE_DAYS) in account["headline"]


@pytest.mark.parametrize("days,phrase", [
    (3, "ends in 3 days"), (1.5, "ends tomorrow"), (0.4, "ends today"),
])
def test_the_countdown_reads_like_a_person_wrote_it(api, trial, days, phrase):
    """'ends in 0 days' reads as though it has already gone."""
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], days)
    account = api.get("/api/console/overview", headers=key).json()["account"]
    assert phrase in account["headline"]


def test_the_grace_countdown_says_how_long_is_left(api, trial):
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], -4)
    account = api.get("/api/console/overview", headers=key).json()["account"]
    assert account["licence_state"] == "grace"
    assert account["grace_days_left"] == LICENCE_GRACE_DAYS - 4
    assert "Monitoring continues" in account["headline"]
    assert account["alerting_continues"] is True


def test_the_console_prices_carrying_on(api, trial):
    """A warning with no number next to it is not an offer."""
    key, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], 2)
    account = api.get("/api/console/overview", headers=key).json()["account"]
    assert account["monthly_if_paid_usd"] == 1299.0
    assert account["annual_if_paid_usd"] == 1299.0 * 12
    assert account["on_trial"] is True
    assert account["has_contract"] is False


def test_grace_days_left_is_zero_when_not_in_grace(api, trial):
    _, _, tenant = trial
    _shift_expiry(tenant["tenant_id"], 5)
    assert grace_days_left(STORE.get_tenant(tenant["tenant_id"])) == 0
