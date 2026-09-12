"""Who is allowed to change things.

The product has three roles — owner, operator, viewer — and the whole
point of the third is that it changes nothing. That was enforced router
by router, by remembering to ask, and one router forgot.

Two tests here. One sweeps every state-changing route with a viewer's
token and checks it is refused. The other is structural, and matters
more: it fails when somebody adds a *new* route without a role gate,
which is how the gap appeared in the first place.
"""

import pytest
from fastapi.routing import APIRoute

from main import app

MUTATING = {"POST", "PATCH", "PUT", "DELETE"}


# Routes that are deliberately reachable without a role, each with the
# reason. Anything not on this list must carry a gate; adding a route
# here is a decision somebody has to write down.
UNGATED_BY_DESIGN = {
    "/api/licenses/tenants": (
        "provisioning: a trial needs no account to exist yet, and anything "
        "paid needs the provisioning key instead of a role"
    ),
    "/api/signup": (
        "the public front door — there is by definition no account to hold "
        "a role yet. Guarded instead by being trial-only and rate limited"
    ),
    "/api/accounts/bootstrap": "creates the first owner, gated by the tenant API key",
    "/api/accounts/login": "public by definition",
    "/api/accounts/logout": "ends your own session, nobody else's",
    "/api/accounts/me/password": "changes your own password",
    "/api/accounts/reset": "redeems a reset token; the token is the credential",
    "/api/sensor-pulse": "machine ingest — this is what a sensor does",
    "/api/v1/bridge/sensor-webhook-ingest": "machine ingest from third-party hardware",
    "/api/v1/bridge/summarize-transcript": "produces a document, changes no state",
    "/api/voice/keypress/{incident_id}/{token}": (
        "Twilio's callback, verified by request signature and a per-incident secret"
    ),
    "/api/vault/verify": "a public verifier — being checkable by outsiders is the point",
    "/api/claims/{incident_id}/packet": "assembles a document, changes no state",
    "/api/shortcuts/{vertical}": "produces a document, changes no state",
}


def _gates(route):
    """Every role-checking dependency reachable from this route."""
    found = []

    def walk(dep):
        call = getattr(dep, "call", None)
        name = getattr(call, "__qualname__", "") or ""
        if "require_role" in name or "require_admin" in name:
            found.append(name)
        for sub in getattr(dep, "dependencies", []):
            walk(sub)

    walk(route.dependant)
    return found


def _mutating_routes():
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.methods & MUTATING
        and r.path.startswith("/api/")
    ]


def test_every_state_changing_route_is_gated_or_listed():
    """The structural half, and the one that catches the next mistake.

    The licence router — the one that owns the plan, the seats and the
    alarm thresholds — was written with `require_tenant` and no role
    check at all, which any signed-in person satisfies. It went
    unnoticed because every other router happened to remember. A test
    that only checks the routes that exist today would not have caught
    it either; this one fails the moment a new route appears without a
    gate or an explicit reason for not having one.
    """
    ungated = []
    for route in _mutating_routes():
        if _gates(route):
            continue
        if route.path in UNGATED_BY_DESIGN:
            continue
        ungated.append(f"{sorted(route.methods)[0]} {route.path}")

    assert not ungated, (
        "these routes change state and check no role. Add a role "
        "dependency, or add the path to UNGATED_BY_DESIGN with the "
        "reason:\n  " + "\n  ".join(sorted(ungated))
    )


def test_the_exemption_list_does_not_rot():
    """An exemption for a route that no longer exists hides a real gap."""
    live = {r.path for r in _mutating_routes()}
    stale = sorted(set(UNGATED_BY_DESIGN) - live)
    assert not stale, (
        "UNGATED_BY_DESIGN names routes that no longer exist, so the "
        "exemption may now be covering something else: " + ", ".join(stale)
    )


@pytest.fixture()
def three_roles(api, operator_factory):
    """An owner, an operator and a viewer on one tenant."""
    owner, tenant, _ = operator_factory(email="owner@example.com")

    def sign_in(email, role):
        made = api.post(
            "/api/accounts/users",
            headers=owner,
            json={
                "email": email,
                "full_name": role.title(),
                "password": "correct-horse-battery",
                "role": role,
            },
        )
        assert made.status_code in (200, 201), made.text
        token = api.post(
            "/api/accounts/login",
            json={"email": email, "password": "correct-horse-battery"},
        ).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return {
        "owner": owner,
        "operator": sign_in("op@example.com", "operator"),
        "viewer": sign_in("viewer@example.com", "viewer"),
        "tenant": tenant,
    }


def test_a_viewer_cannot_silence_a_freezer(api, three_roles, sensor_factory):
    """The worst of the set, and the least obvious.

    A viewer could not only delete a sensor — they could raise its alarm
    threshold to 200°F. The sensor keeps reporting, the console stays
    green, and the alarm can never fire again. Nothing looks wrong; that
    is what makes it the dangerous one.
    """
    owner, viewer = three_roles["owner"], three_roles["viewer"]
    sensor = sensor_factory(owner, sensor_id="VACCINE-01", vertical="pharmacy")
    sid = sensor["sensor_id"]

    blocked = api.post(
        f"/api/licenses/me/sensors/{sid}/thresholds",
        headers=viewer,
        json={"danger_above": 200.0},
    )
    assert blocked.status_code == 403, (
        "a read-only account raised a vaccine fridge's alarm limit to 200°F"
    )

    # And the limit really is untouched, not merely reported as refused.
    breach = api.post(
        "/api/sensor-pulse",
        headers=owner,
        json={"sensor_id": sid, "temperature_fahrenheit": 71.0},
    )
    assert breach.json()["status"].startswith("CRITICAL"), (
        "the threshold was changed despite the 403"
    )


def test_a_viewer_cannot_touch_the_estate_or_the_licence(api, three_roles,
                                                         sensor_factory):
    """Everything else the licence router left open."""
    owner, viewer = three_roles["owner"], three_roles["viewer"]
    sensor = sensor_factory(owner, sensor_id="FRIDGE-01", vertical="pharmacy")

    refusals = {
        "register a sensor": api.post(
            "/api/licenses/me/sensors", headers=viewer,
            json={"sensor_id": "SNEAK-01", "industry_vertical": "pharmacy",
                  "location_name": "x"}),
        "decommission a sensor": api.delete(
            f"/api/licenses/me/sensors/{sensor['sensor_id']}", headers=viewer),
        "downgrade the plan": api.post(
            "/api/licenses/me/plan", headers=viewer, json={"plan": "trial"}),
        "suspend the licence": api.post("/api/licenses/me/suspend", headers=viewer),
        "change the display unit": api.post(
            "/api/licenses/me/temperature-unit", headers=viewer, json={"unit": "C"}),
        "run a fleet sweep that places calls": api.post(
            "/api/autopilot/sweep", headers=viewer),
    }
    wrong = {
        what: r.status_code for what, r in refusals.items() if r.status_code != 403
    }
    assert not wrong, f"a viewer was allowed to: {wrong}"

    # The licence really is untouched.
    me = api.get("/api/licenses/me", headers=owner).json()
    tenant = me.get("tenant") or me
    assert tenant["plan"] == "enterprise" and not tenant["suspended"]


def test_an_operator_can_run_the_estate_but_not_the_money(api, three_roles):
    """The role hierarchy is only real if the middle rung is a real rung."""
    operator = three_roles["operator"]

    allowed = api.post(
        "/api/licenses/me/sensors", headers=operator,
        json={"sensor_id": "OP-01", "industry_vertical": "pharmacy",
              "location_name": "Hall B"})
    assert allowed.status_code == 201, allowed.text

    for what, resp in (
        ("change the plan",
         api.post("/api/licenses/me/plan", headers=operator, json={"plan": "growth"})),
        ("suspend the licence",
         api.post("/api/licenses/me/suspend", headers=operator)),
    ):
        assert resp.status_code == 403, f"an operator could {what}"


def test_a_machine_key_still_provisions(api, tenant_factory):
    """Roles are checked for people. A provisioning script has no role.

    The tenant API key is documented as a machine credential, and a
    fleet installer registering fifty sensors from a script is the
    reason it exists. Closing the viewer hole must not close that.
    """
    key_headers, _ = tenant_factory()
    made = api.post(
        "/api/licenses/me/sensors", headers=key_headers,
        json={"sensor_id": "SCRIPTED-01", "industry_vertical": "pharmacy",
              "location_name": "Bay 4"})
    assert made.status_code == 201, made.text


def test_a_viewer_cannot_halt_an_escalation(api, three_roles, sensor_factory):
    """Acknowledging is not "I have seen this".

    It means "somebody who can deal with this has it", and it stops the
    product waking anyone else. A viewer cannot change a threshold, cannot
    decommission an asset and cannot escalate — so letting them halt the
    ladder halts it on behalf of someone who is not able to act.

    This was left open deliberately at first, on the argument that making
    acknowledgement harder during an emergency is its own hazard. That
    argument is real, and it is answered by the path that actually matters
    at 3am staying open: the person on call presses 1 on the handset, and
    that callback is authenticated by Twilio's signature and a
    per-incident secret, not by a role. Nobody is locked out of
    acknowledging the call they are being woken by.
    """
    owner, operator, viewer = (three_roles["owner"], three_roles["operator"],
                               three_roles["viewer"])
    sensor = sensor_factory(owner, sensor_id="LADDER-01", vertical="pharmacy")
    incident = api.post(
        "/api/sensor-pulse", headers=owner,
        json={"sensor_id": sensor["sensor_id"], "temperature_fahrenheit": 71.0},
    ).json()["incident_id"]

    blocked = api.post(f"/api/voice/acknowledge/{incident}", headers=viewer,
                       json={"acknowledged_by": "Night Manager"})
    assert blocked.status_code == 403, "a read-only account stopped the ladder"

    stopped = api.post(f"/api/voice/resolve/{incident}", headers=viewer,
                       json={"resolved_by": "Night Manager"})
    assert stopped.status_code == 403, (
        "a read-only account wrote itself into the compliance record as the "
        "person who closed out a loss"
    )

    # It is still open in every sense — nothing was quietly recorded.
    listed = api.get("/api/voice/incidents", headers=owner).json()
    live = next(i for i in listed["incidents"] if i["incident_id"] == incident)
    assert live["acknowledged_at"] is None and live["resolved_at"] is None

    # And an operator can, which is the point of the middle rung.
    assert api.post(f"/api/voice/acknowledge/{incident}", headers=operator,
                    json={"acknowledged_by": "Ops"}).status_code == 200


def test_the_tenant_api_key_cannot_switch_the_company_off(api, tenant_factory,
                                                          owner_headers):
    """Two routes refuse a machine credential outright.

    Everywhere else the tenant key passes role checks, because a
    provisioning script has no human identity to have a role and that is
    the credential's whole purpose. But turning off a company's monitoring,
    or moving it onto a plan without voice escalation, is not an action
    whose audit record should read "API key".
    """
    key_headers, _ = tenant_factory()

    for what, resp in (
        ("suspend the licence", api.post("/api/licenses/me/suspend",
                                         headers=key_headers)),
        ("change the plan", api.post("/api/licenses/me/plan", headers=key_headers,
                                     json={"plan": "trial"})),
    ):
        assert resp.status_code in (401, 403), (
            f"a machine credential could {what} ({resp.status_code})"
        )

    # The licence is untouched, and a named owner can still do both.
    owner = owner_headers(key_headers)
    assert api.post("/api/licenses/me/suspend", headers=owner).status_code == 200
