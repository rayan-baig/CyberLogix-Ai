"""The console's screen for rotations and quiet hours.

oncall.py and the /api/contacts/rotations endpoints had no screen: a
business could only take turns being on call by calling the API. The
"On call now" card (People tab) sets each person's quiet hours, builds a
rotation, says who is on call now and next, and shows the warnings --
an empty rotation, a muted person with turns.

These hold the card to the API it reads (a name the endpoint does not
return renders as `undefined`, and nothing in JavaScript objects), and
hold the roles: owners and operators change it, a viewer only reads it,
on the page and at the server. The browser smoke test
(tools/console_smoke.js) drives the card itself at phone and desktop
size.
"""

from pathlib import Path

from pages import page_source

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "static" / "console.html"
SCRIPT = (ROOT / "static" / "console.js").read_text(encoding="utf-8")
NY = "America/New_York"


def _person(api, headers, name, phone, **extra):
    resp = api.post("/api/contacts", headers=headers,
                    json={"full_name": name, "phone": phone, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _viewer(api, owner_headers_):
    made = api.post("/api/accounts/users", headers=owner_headers_, json={
        "email": "viewer@example.com", "full_name": "Vic Viewer",
        "password": "correct-horse-battery", "role": "viewer"})
    assert made.status_code == 201, made.text
    signed = api.post("/api/accounts/login", json={
        "email": "viewer@example.com", "password": "correct-horse-battery"})
    assert signed.status_code == 200, signed.text
    return {"Authorization": f"Bearer {signed.json()['token']}"}


def test_the_card_is_on_the_people_tab_with_its_forms():
    page = page_source(CONSOLE)
    assert 'id="find-on-call-rotation" data-group="people"' in page
    for element in ("rotation-body", "rotation-form", "rot-scope", "rot-tz",
                    "rot-handover", "rot-days", "rot-start", "rot-members",
                    "rot-add", "quiet-body", "quiet-form", "qh-person",
                    "qh-start", "qh-end", "qh-tz", "qh-clear", "rotation-viewer"):
        assert f'id="{element}"' in page, element


def test_the_console_loads_and_paints_the_rotations():
    assert 'rotations: () => api("/api/contacts/rotations")' in SCRIPT
    assert 'paint("rotations", () => renderRotations(got.rotations, got.roster))' in SCRIPT
    # A licence that lapsed answers 402 here as for the roster: the card
    # says so in its place rather than taking the page down.
    assert 'rotations: "rotation-body"' in SCRIPT


def test_the_console_writes_through_the_endpoints_that_exist():
    assert 'api("/api/contacts/rotations", {\n      method: "PUT"' in SCRIPT
    assert "/api/contacts/rotations/${encodeURIComponent(d.rotaStop)}" in SCRIPT
    assert "quiet_hours: null" in SCRIPT


def test_a_viewer_is_shown_no_form_and_no_button():
    """The page decides from the role, the server enforces it anyway."""
    assert 'Boolean(state.user && state.user.role !== "viewer") && !state.wall' in SCRIPT
    assert '$("rotation-form").hidden = !editor' in SCRIPT
    assert '$("quiet-form").hidden = !editor' in SCRIPT
    # The Change and Stop buttons are only drawn for an editor.
    block = SCRIPT[SCRIPT.index("function renderRotations"):
                   SCRIPT.index("function loadRotationIntoForm")]
    assert "const buttons = editor" in block
    assert "${editor ? `<button" in block


def test_the_rotation_api_returns_every_name_the_card_reads(
        api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    a = _person(api, headers, "Alex", "+15550801")
    b = _person(api, headers, "Blair", "+15550802")
    _person(api, headers, "Casey", "+15550803",
            quiet_hours={"start": "22:00", "end": "07:00", "timezone": NY})
    saved = api.put("/api/contacts/rotations", headers=headers, json={
        "timezone": NY, "handover": "08:00", "shift_days": 7,
        "members": [a["contact_id"], b["contact_id"]]})
    assert saved.status_code == 200, saved.text

    body = api.get("/api/contacts/rotations", headers=headers).json()
    assert set(body) >= {"rotations", "warnings"}
    rotation = body["rotations"][0]
    for key in ("scope", "site_id", "timezone", "handover", "shift_days",
                "starts_on", "members", "on_shift_now", "schedule"):
        assert key in rotation, key
    assert rotation["scope"] == "estate"
    assert {"contact_id", "full_name"} <= set(rotation["members"][0])
    assert rotation["on_shift_now"]["full_name"] in ("Alex", "Blair")
    shift = rotation["schedule"][0]
    for key in ("contact_id", "full_name", "starts_local", "ends_local"):
        assert key in shift, key
    # The quiet-hours list is read off the roster.
    roster = api.get("/api/contacts", headers=headers).json()["contacts"]
    casey = next(c for c in roster if c["full_name"] == "Casey")
    assert casey["quiet_hours"] == {"start": "22:00", "end": "07:00", "timezone": NY}
    assert {"contact_id", "full_name", "active"} <= set(casey)


def test_a_viewer_sees_the_rotation_and_cannot_change_it(
        api, operator_factory, sensor_factory):
    owner, _, _ = operator_factory()
    sensor_factory(owner, sensor_id="RACK-01")
    a = _person(api, owner, "Alex", "+15550901")
    assert api.put("/api/contacts/rotations", headers=owner, json={
        "timezone": NY, "members": [a["contact_id"]]}).status_code == 200
    api.patch(f"/api/contacts/{a['contact_id']}", headers=owner, json={"active": False})
    viewer = _viewer(api, owner)

    seen = api.get("/api/contacts/rotations", headers=viewer)
    assert seen.status_code == 200
    assert seen.json()["rotations"][0]["on_shift_now"]["full_name"] == "Alex"
    assert any("muted but has turns" in w for w in seen.json()["warnings"])

    assert api.put("/api/contacts/rotations", headers=viewer, json={
        "timezone": NY, "members": []}).status_code == 403
    assert api.delete("/api/contacts/rotations/estate", headers=viewer).status_code == 403
    assert api.patch(f"/api/contacts/{a['contact_id']}", headers=viewer, json={
        "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": NY},
    }).status_code == 403
    # Still there, unchanged.
    assert api.get("/api/contacts/rotations", headers=owner).json()["rotations"]


def test_an_operator_can_change_the_rotation_and_quiet_hours(
        api, operator_factory):
    owner, _, _ = operator_factory()
    made = api.post("/api/accounts/users", headers=owner, json={
        "email": "op@example.com", "full_name": "Olly Operator",
        "password": "correct-horse-battery", "role": "operator"})
    assert made.status_code == 201, made.text
    token = api.post("/api/accounts/login", json={
        "email": "op@example.com", "password": "correct-horse-battery"}).json()["token"]
    operator = {"Authorization": f"Bearer {token}"}
    a = _person(api, operator, "Alex", "+15551001")

    assert api.put("/api/contacts/rotations", headers=operator, json={
        "timezone": NY, "members": [a["contact_id"]]}).status_code == 200
    quiet = api.patch(f"/api/contacts/{a['contact_id']}", headers=operator, json={
        "quiet_hours": {"start": "23:00", "end": "06:30", "timezone": NY}})
    assert quiet.status_code == 200
    assert quiet.json()["quiet_hours"]["end"] == "06:30"
    assert api.delete("/api/contacts/rotations/estate", headers=operator).status_code == 200
