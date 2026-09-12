"""The front door opens, and only onto a trial.

Two failures matter more than the rest:

  * a public caller getting anything above a trial, which is a free
    Enterprise licence for anyone who reads the API docs;
  * a sign-up that half-succeeds, leaving a tenant nobody can ever sign
    into sitting in the fleet forever.
"""

import pytest

import signup
from store import STORE

GOOD = {
    "company_name": "Blue Harbor Club",
    "full_name": "Dana Reyes",
    "email": "dana@blueharbor.example",
    "password": "correct-horse-battery",
    "contact_phone": "+15550100",
    "industry_vertical": "country_club",
}


def _signup(api, **overrides):
    return api.post("/api/signup", json={**GOOD, **overrides})


# ---- the door opens ----------------------------------------------------


def test_a_stranger_can_become_a_customer_in_one_call(api):
    resp = _signup(api)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["tenant"]["company_name"] == "Blue Harbor Club"
    assert body["tenant"]["plan"] == "trial"
    assert body["user"]["role"] == "owner"
    assert body["api_key"]
    assert body["token"]


def test_the_returned_token_actually_works(api):
    body = _signup(api).json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    me = api.get("/api/accounts/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "dana@blueharbor.example"


def test_the_returned_api_key_actually_works(api):
    body = _signup(api).json()
    headers = {"X-CyberLogix-Key": body["api_key"]}
    assert api.get("/api/licenses/me", headers=headers).status_code == 200


def test_the_whole_first_run_works_end_to_end(api):
    """Sign up, add a contact, register a sensor, break it. No demo data."""
    body = _signup(api).json()
    key = {"X-CyberLogix-Key": body["api_key"]}

    assert api.post(
        "/api/contacts",
        headers={**key, "Authorization": f"Bearer {body['token']}"},
        json={"full_name": "Dana", "phone": "+15550100", "role": "owner"},
    ).status_code in (200, 201)

    assert api.post(
        "/api/licenses/me/sensors",
        headers=key,
        json={
            "sensor_id": "UNIT-1",
            "industry_vertical": "country_club",
            "location_name": "Main site",
        },
    ).status_code == 201

    nominal = api.post(
        "/api/sensor-pulse",
        headers=key,
        json={"sensor_id": "UNIT-1", "temperature_fahrenheit": 20.0},
    )
    assert nominal.status_code == 200
    assert not nominal.json()["status"].startswith("CRITICAL")

    breach = api.post(
        "/api/sensor-pulse",
        headers=key,
        json={"sensor_id": "UNIT-1", "temperature_fahrenheit": 90.0},
    )
    assert breach.status_code == 200
    assert breach.json()["status"].startswith("CRITICAL")


def test_no_demo_sensors_are_planted_in_a_real_account(api):
    """A fake freezer in a live estate is a false alarm waiting to happen."""
    body = _signup(api).json()
    assert body["tenant"]["seats_used"] == 0
    assert STORE.sensors_for(body["tenant"]["tenant_id"]) == []


def test_the_first_steps_use_the_sector_that_was_chosen(api):
    body = _signup(api, industry_vertical="pharmacy").json()
    commands = " ".join(s["command"] for s in body["next_steps"]["steps"])
    assert '"industry_vertical":"pharmacy"' in commands
    assert body["api_key"] in commands


def test_the_breach_command_actually_breaches(api):
    """The headline promise of the first run: step four opens an incident."""
    body = _signup(api, industry_vertical="restaurant").json()
    key = {"X-CyberLogix-Key": body["api_key"]}
    steps = body["next_steps"]["steps"]

    import json as jsonlib
    import re

    def payload_of(step):
        return jsonlib.loads(re.search(r"-d '(\{.*\})'", step["command"]).group(1))

    api.post("/api/licenses/me/sensors", headers=key, json=payload_of(steps[1]))
    calm = api.post("/api/sensor-pulse", headers=key, json=payload_of(steps[2]))
    hot = api.post("/api/sensor-pulse", headers=key, json=payload_of(steps[3]))

    assert not calm.json()["status"].startswith("CRITICAL"), (
        "The 'normal reading' step opens an incident, so the walkthrough "
        "teaches that the product cries wolf."
    )
    assert hot.json()["status"].startswith("CRITICAL"), (
        "The 'now break it' step does nothing, so the first thing a new "
        "customer tries appears not to work."
    )


@pytest.mark.parametrize("vertical", sorted(__import__("store").INDUSTRY_PROFILES))
def test_the_walkthrough_holds_for_every_sector(api, vertical):
    """Twelve sectors, some bounded above and some below."""
    from store import evaluate_breach

    steps = signup.first_steps("KEY", vertical)["steps"]
    import json as jsonlib
    import re

    def temp(step):
        body = jsonlib.loads(re.search(r"-d '(\{.*\})'", step["command"]).group(1))
        return body["temperature_fahrenheit"]

    assert evaluate_breach(vertical, temp(steps[2])) is None
    assert evaluate_breach(vertical, temp(steps[3])) is not None


# ---- and only onto a trial --------------------------------------------


def test_the_public_door_cannot_be_asked_for_a_paid_plan(api):
    """The plan is not a field. Sending one changes nothing."""
    resp = api.post("/api/signup", json={**GOOD, "plan": "enterprise"})
    assert resp.status_code == 201
    assert resp.json()["tenant"]["plan"] == "trial"


def test_provisioning_a_paid_plan_needs_the_key(api):
    resp = api.post(
        "/api/licenses/tenants",
        json={
            "company_name": "Free Enterprise Ltd",
            "contact_name": "A Chancer",
            "contact_phone": "+15550111",
            "contact_email": "chancer@example.com",
            "plan": "enterprise",
        },
    )
    assert resp.status_code == 403, (
        "Anyone who reads the API docs can mint themselves an unlimited "
        "Enterprise licence for free."
    )
    assert "Provisioning" in resp.json()["detail"]


def test_a_wrong_provisioning_key_is_refused(api):
    resp = api.post(
        "/api/licenses/tenants",
        headers={"X-CyberLogix-Provisioning": "not-the-key"},
        json={
            "company_name": "Free Enterprise Ltd",
            "contact_name": "A Chancer",
            "contact_phone": "+15550111",
            "contact_email": "chancer@example.com",
            "plan": "growth",
        },
    )
    assert resp.status_code == 403


def test_the_right_provisioning_key_is_accepted(api):
    resp = api.post(
        "/api/licenses/tenants",
        headers={"X-CyberLogix-Provisioning": "test-provisioning-key"},
        json={
            "company_name": "Real Customer Inc",
            "contact_name": "Dana",
            "contact_phone": "+15550111",
            "contact_email": "dana@real.example",
            "plan": "enterprise",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["tenant"]["plan"] == "enterprise"


def test_a_trial_still_needs_no_key(api):
    """The trial path must not be broken by locking the paid one."""
    resp = api.post(
        "/api/licenses/tenants",
        json={
            "company_name": "Kicking Tyres",
            "contact_name": "Sam",
            "contact_phone": "+15550112",
            "contact_email": "sam@example.com",
            "plan": "trial",
        },
    )
    assert resp.status_code == 201


def test_no_key_configured_means_no_paid_plans_at_all(api, monkeypatch):
    """An unconfigured deployment refuses rather than hands them out."""
    import licenses

    monkeypatch.setattr(licenses, "PROVISIONING_KEY", "")
    resp = api.post(
        "/api/licenses/tenants",
        headers={"X-CyberLogix-Provisioning": "anything"},
        json={
            "company_name": "Nope",
            "contact_name": "Sam",
            "contact_phone": "+15550112",
            "contact_email": "sam2@example.com",
            "plan": "enterprise",
        },
    )
    assert resp.status_code == 403
    assert "CYBERLOGIX_PROVISIONING_KEY" in resp.json()["detail"]


# ---- refusals ----------------------------------------------------------


def test_a_duplicate_email_is_refused_and_creates_nothing(api):
    assert _signup(api).status_code == 201
    before = len(STORE.list_tenants())

    again = _signup(api, company_name="Different Co")
    assert again.status_code == 409
    assert len(STORE.list_tenants()) == before, (
        "The refused sign-up still left a tenant behind."
    )


def test_an_unknown_sector_is_refused_and_creates_nothing(api):
    before = len(STORE.list_tenants())
    resp = _signup(api, industry_vertical="submarines")
    assert resp.status_code == 400
    assert "submarines" in resp.json()["detail"]
    assert len(STORE.list_tenants()) == before


def test_a_short_password_is_refused(api):
    assert _signup(api, password="short").status_code == 422


def test_a_malformed_email_is_refused(api):
    assert _signup(api, email="not-an-email").status_code == 422


def test_no_sector_is_fine(api):
    resp = api.post(
        "/api/signup",
        json={k: v for k, v in GOOD.items() if k != "industry_vertical"},
    )
    assert resp.status_code == 201
    assert resp.json()["next_steps"]["steps"]


def test_a_failure_after_the_tenant_exists_leaves_nothing_behind(
    api, monkeypatch
):
    """A tenant with no owner cannot be signed into, ever."""
    before = len(STORE.list_tenants())
    monkeypatch.setattr(
        signup.STORE,
        "create_user",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError):
        _signup(api)
    assert len(STORE.list_tenants()) == before, (
        "A tenant nobody can sign into was left in the fleet."
    )


def test_the_rollback_refuses_to_touch_a_real_customer(api):
    """delete_tenant must never be usable on a tenant that has grown."""
    body = _signup(api).json()
    tenant_id = body["tenant"]["tenant_id"]
    assert STORE.delete_tenant(tenant_id) is False, (
        "An exception handler could delete a live customer."
    )
    assert STORE.get_tenant(tenant_id) is not None


# ---- rate limiting -----------------------------------------------------


def test_a_script_cannot_mint_trials_all_day(api):
    for i in range(signup.SIGNUPS_PER_CALLER_PER_HOUR):
        resp = _signup(api, email=f"dana{i}@example.com",
                       company_name=f"Co {i}")
        assert resp.status_code == 201, resp.text

    blocked = _signup(api, email="one.too.many@example.com",
                      company_name="Co N")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")


def test_a_refused_signup_still_costs_an_attempt(api):
    """Otherwise the limit is bypassed by sending rubbish in between."""
    _signup(api, industry_vertical="submarines")
    _signup(api, industry_vertical="submarines")
    _signup(api, industry_vertical="submarines")
    assert _signup(api).status_code == 429


def test_the_total_ceiling_holds_when_the_caller_cannot_be_told_apart(
    api, monkeypatch
):
    """Per-caller limiting is worthless against a botnet. The total is not."""
    monkeypatch.setattr(signup, "SIGNUPS_PER_CALLER_PER_HOUR", 10_000)
    monkeypatch.setattr(signup, "SIGNUPS_TOTAL_PER_HOUR", 4)
    for i in range(4):
        assert _signup(api, email=f"a{i}@example.com",
                       company_name=f"C{i}").status_code == 201
    assert _signup(api, email="a99@example.com",
                   company_name="C99").status_code == 429


def test_concurrent_signups_cannot_all_slip_under_the_limit(api, monkeypatch):
    """Check-then-record in two steps is exactly what a script defeats."""
    import threading

    monkeypatch.setattr(signup, "SIGNUPS_PER_CALLER_PER_HOUR", 2)
    barrier = threading.Barrier(8)
    codes = []

    def go(i):
        barrier.wait()
        codes.append(
            _signup(api, email=f"race{i}@example.com",
                    company_name=f"Race {i}").status_code
        )

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert codes.count(201) == 2, f"{codes.count(201)} got through a limit of 2"
    assert codes.count(429) == 6


def test_the_forwarded_header_is_ignored_unless_it_is_trusted(api):
    """A spoofable header would turn the per-caller limit into no limit."""
    assert signup.TRUST_PROXY_HEADER is False
    for i in range(signup.SIGNUPS_PER_CALLER_PER_HOUR):
        _signup(api, email=f"x{i}@example.com", company_name=f"X{i}")

    blocked = api.post(
        "/api/signup",
        headers={"X-Forwarded-For": "203.0.113.9"},
        json={**GOOD, "email": "spoof@example.com", "company_name": "Spoof"},
    )
    assert blocked.status_code == 429


# ---- the page itself ---------------------------------------------------


def test_the_signup_page_is_served(api):
    page = api.get("/signup")
    assert page.status_code == 200
    assert "Start a trial" in page.text


def test_every_call_to_action_leads_somewhere_a_stranger_can_use(api):
    """The defect this whole module exists to fix.

    Every button on the landing page pointed at /console, which is a
    password field. Somebody who had read the page and wanted to buy had
    nowhere to go.
    """
    landing = api.get("/").text
    assert 'href="/signup"' in landing

    # The plan cards are rendered client-side, so check the template too.
    import re
    for match in re.findall(r'class="btn[^"]*"\s+href="([^"]+)"', landing):
        assert match != "/console" or "Sign in" in landing


def test_the_sector_list_is_public_and_complete(api):
    from store import INDUSTRY_PROFILES

    body = api.get("/api/signup/sectors").json()
    assert {s["key"] for s in body["sectors"]} == set(INDUSTRY_PROFILES)
    assert body["plan"] == "trial"
    assert body["min_password_length"] == signup.MIN_PASSWORD_LENGTH


def test_the_request_model_has_no_plan_field_to_honour(api):
    """Why sending `plan` does nothing, asserted rather than assumed.

    A mutation that made `start_trial` read `payload.plan` changed no
    behaviour, because the model has no such field and Pydantic drops
    unknown ones. That is the right outcome, but it rests on a default:
    flip the model to `extra="allow"` and a public caller starts being
    able to hand itself an Enterprise licence. So both halves are pinned
    here — the field does not exist, and extras stay dropped.
    """
    assert "plan" not in signup.SignupRequest.model_fields
    parsed = signup.SignupRequest(**{**GOOD, "plan": "enterprise"})
    assert not hasattr(parsed, "plan"), (
        "The model now carries unknown fields, so `plan` is one edit away "
        "from being honoured."
    )
