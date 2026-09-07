"""No response may echo a credential.

The tenant API key, session tokens, password hashes, the partner key, a
webhook target and the incident keypress secret are all credentials.
Leaking one to a lower-privileged principal inside the same tenant is the
quiet version of this failure — a viewer who can read the API key has the
whole estate.
"""


def test_no_endpoint_echoes_a_credential(api, operator_factory):
    from store import STORE

    headers, tenant, _ = operator_factory()
    api_key = STORE.get_tenant(tenant["tenant_id"]).api_key
    owner_token = headers["Authorization"].split()[1]

    api.post("/api/accounts/users", headers=headers,
             json={"email": "v@example.com", "full_name": "Val",
                   "role": "viewer", "password": "correct-horse-battery"})
    viewer_token = api.post("/api/accounts/login",
                            json={"email": "v@example.com",
                                  "password": "correct-horse-battery"}
                            ).json()["token"]
    viewer = {"Authorization": f"Bearer {viewer_token}"}

    site = api.post("/api/sites", headers=headers, json={"name": "Boca"}).json()
    api.post("/api/licenses/me/sensors", headers=headers,
             json={"sensor_id": "FRZ-1", "industry_vertical": "restaurant",
                   "location_name": "Walk-In"})
    api.post("/api/webhooks", headers=headers,
             json={"kind": "slack",
                   "target": "https://hooks.slack.com/services/T/B/SUPERSECRET"})
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1",
                          "temperature_fahrenheit": 55.0}).json()
    api.post(f"/api/voice/escalate/{body['incident_id']}?force=true",
             headers=headers)
    ack_token = STORE.get_incident(body["incident_id"]).ack_token
    password_hash = next(iter(STORE._users.values())).password_hash

    secrets = {
        "tenant api key": api_key,
        "owner session": owner_token,
        "viewer session": viewer_token,
        "password hash": password_hash,
        "webhook target": "SUPERSECRET",
        "keypress secret": ack_token,
    }

    paths = [
        "/api", "/api/health", "/api/console/overview",
        "/api/console/sensor/FRZ-1", "/api/licenses/me/sensors",
        "/api/accounts/me", "/api/accounts/users", "/api/accounts/audit",
        "/api/contacts", "/api/contacts/preview", "/api/sites",
        f"/api/sites/{site['site_id']}", "/api/webhooks", "/api/costs",
        "/api/billing", "/api/billing/add-ons", "/api/billing/deal",
        "/api/voice/incidents", "/api/vault/attestation",
        "/api/vault/attestation/FRZ-1", "/api/invoices",
        "/api/assurance/cover", "/api/claims/eligible",
        "/api/autopilot/compliance?days=7",
    ]

    leaks = []
    for label, who in (("owner", headers), ("viewer", viewer)):
        for path in paths:
            resp = api.get(path, headers=who)
            if resp.status_code != 200:
                continue
            for name, value in secrets.items():
                if value and value in resp.text:
                    leaks.append(f"{label} {path} -> {name}")

    assert not leaks, "credentials echoed back:\n  " + "\n  ".join(leaks)


def test_a_viewer_never_sees_the_tenant_api_key(api, operator_factory):
    """One leak here hands the whole estate to the lowest role."""
    from store import STORE

    headers, tenant, _ = operator_factory()
    api_key = STORE.get_tenant(tenant["tenant_id"]).api_key
    api.post("/api/accounts/users", headers=headers,
             json={"email": "v@example.com", "full_name": "Val",
                   "role": "viewer", "password": "correct-horse-battery"})
    token = api.post("/api/accounts/login",
                     json={"email": "v@example.com",
                           "password": "correct-horse-battery"}).json()["token"]
    viewer = {"Authorization": f"Bearer {token}"}

    everything = "".join(
        api.get(p, headers=viewer).text
        for p in ("/api/licenses/me", "/api/licenses/me/sensors",
                  "/api/console/overview", "/api/accounts/me",
                  "/api/accounts/users", "/api/billing")
    )
    assert api_key not in everything


def test_a_webhook_target_is_masked_everywhere_it_appears(api,
                                                          operator_factory):
    """The URL is the credential."""
    headers, _, _ = operator_factory()
    secret = "https://hooks.slack.com/services/T00/B00/qqqqqqSECRETqqqqqq"
    api.post("/api/webhooks", headers=headers,
             json={"kind": "slack", "target": secret, "label": "Ops"})
    for path in ("/api/webhooks", "/api/console/overview"):
        assert "qqqqqqSECRETqqqqqq" not in api.get(path, headers=headers).text
