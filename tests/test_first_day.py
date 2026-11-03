"""A new business's first day: the front page, the industry pages, the
checklist, the test alert and the simulator.

Each of these is something a stranger meets before they have any reason
to trust the product, so each is tested on the thing that would cost
that trust: a price the product does not charge, a limit it does not
alarm on, a box that ticks without the thing being done, a test that
pages somebody for real, and a pretend reading that ends up in a real
record.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest

import firstday
import industries
import simulator
from pricing import PRICE_BOOK, headline
from store import INDUSTRY_PROFILES, PLAN_TIERS, STORE

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = (ROOT / "static" / "console.html").read_text()
LANDING = (ROOT / "static" / "index.html").read_text()


def _sign_up(api, email="sam@harbour.example", company="Harbour Street Kitchen"):
    resp = api.post("/api/signup", json={
        "company_name": company, "full_name": "Sam Reyes",
        "email": email, "password": "correct-horse-battery",
        "contact_phone": "+15550142", "industry_vertical": "restaurant",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return ({"Authorization": f"Bearer {body['token']}"},
            body["tenant"]["tenant_id"], {"X-CyberLogix-Key": body["api_key"]})


def _add_person(api, owner, email, role):
    made = api.post("/api/accounts/users", headers=owner, json={
        "email": email, "full_name": role.title(),
        "password": "correct-horse-battery", "role": role})
    assert made.status_code in (200, 201), made.text
    token = api.post("/api/accounts/login", json={
        "email": email, "password": "correct-horse-battery"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def wire(monkeypatch):
    """Twilio, configured, with every text and call it was handed kept."""
    import notifications

    sent = {"sms": [], "voice": []}

    class _Result:
        sid = "SM-TEST"
        status = "queued"

    class _Messages:
        def create(self, **kwargs):
            sent["sms"].append(kwargs)
            return _Result()

    class _Calls:
        def create(self, **kwargs):
            sent["voice"].append(kwargs)
            return _Result()

    class _Client:
        messages = _Messages()
        calls = _Calls()

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", _Client())
    monkeypatch.setattr(notifications, "_client_error", None)
    return sent


def _steps(api, headers):
    body = api.get("/api/console/overview", headers=headers).json()["first_day"]
    return {s["key"]: s["done"] for s in body["steps"]}, body["complete"]


def _age_tests(tenant_id, minutes):
    for entry in STORE.audit_for(tenant_id, limit=10_000):
        if entry.action in (firstday.SENT, firstday.FAILED):
            entry.at -= timedelta(minutes=minutes)


# --- C2: the front page, in one screen ---------------------------------------


def test_the_first_screen_says_who_it_is_for_the_price_and_the_trial(api):
    page = api.get("/").text
    facts = headline()
    hero = page[page.index('<section class="hero">'):page.index("</section>")]

    assert "Who it is for" in hero
    assert f"From {facts['from_label']} a month per {facts['from_unit']}" in hero
    assert (f"{facts['trial_days']} days on up to {facts['trial_units']} "
            "sensors. No card.") in hero
    assert "%%" not in page, "a token was left unsubstituted"


def test_the_figures_come_off_the_rate_card_not_the_page(api, monkeypatch):
    """Change the rate card and the trial, and the front page follows.
    A figure typed into the HTML would not."""
    monkeypatch.setitem(PRICE_BOOK["restaurant"], "monthly_usd", 199.0)
    monkeypatch.setitem(PLAN_TIERS["trial"], "term_days", 21)
    monkeypatch.setitem(PLAN_TIERS["trial"], "max_sensors", 7)

    page = api.get("/").text

    assert "From $199 a month per location" in page
    assert "21 days on up to 7 sensors" in page
    assert not re.search(r"\$\d", LANDING), "the page source carries a price"
    hero = LANDING[LANDING.index('<section class="hero">'):]
    hero = hero[:hero.index("</section>")]
    words = re.sub(r"<[^>]+>", " ", hero)
    assert not re.search(r"\d", words), (
        "the first screen's source carries a figure of its own")


def test_the_trades_named_on_the_front_page_are_ones_the_product_covers(api):
    for vertical in ("restaurant", "pharmacy", "medical_lab", "cybersecurity"):
        assert vertical in INDUSTRY_PROFILES
    more = len(INDUSTRY_PROFILES) - 4
    assert f"data centres and\n          {more} more" in api.get("/").text


def test_the_trial_on_the_front_page_needs_no_card():
    """"No card" is a claim about the sign-up form: it has no field for one."""
    from signup import SignupRequest

    fields = set(SignupRequest.model_fields)
    assert not fields & {"card", "card_number", "payment_method", "cvc"}
    assert headline()["card_required"] is False


# --- C3: each industry page, the problem and the fix -------------------------


@pytest.mark.parametrize("vertical", sorted(INDUSTRY_PROFILES))
def test_each_industry_page_states_the_fix_with_the_limit_it_alarms_on(vertical):
    page = industries.page(vertical)
    profile = INDUSTRY_PROFILES[vertical]

    assert page["loses"] and page["catastrophe"]
    fix = page["fix"]
    assert profile["asset_noun"] in fix
    for bound in (profile["danger_above"], profile["danger_below"]):
        if bound is not None:
            assert f"{bound:g}°F" in fix, f"{vertical}: the fix omits {bound}"
    assert "None" not in fix and "None" not in page["band"]
    assert page["band"].endswith("°F"), "a limit with no unit is not a limit"


def test_the_industry_page_leads_with_the_problem_then_the_fix(api):
    html = api.get("/for/restaurant").text
    problem = html.index("<h2>The problem</h2>")
    fix = html.index("<h2>The fix</h2>")
    assert problem < fix
    assert "${esc(d.fix)}" in html[fix:fix + 200]
    assert "under 32°F" in api.get("/api/for/restaurant").json()["band"]


# --- C4: the checklist ticks itself -------------------------------------------


def test_a_new_business_starts_with_nothing_ticked(api):
    owner, _, _ = _sign_up(api)

    steps, complete = _steps(api, owner)

    assert steps == {"site": False, "sensor": False, "oncall": False, "test": False}
    assert complete is False


def test_each_box_ticks_from_what_the_account_holds(api, wire):
    owner, tenant_id, _ = _sign_up(api)

    api.post("/api/sites", headers=owner, json={"name": "Harbour Street"})
    assert _steps(api, owner)[0]["site"] is True

    api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
        "location_name": "Kitchen walk-in"})
    assert _steps(api, owner)[0]["sensor"] is True

    made = api.post("/api/contacts", headers=owner,
                    json={"full_name": "Ana", "phone": "+15550111"})
    assert _steps(api, owner)[0]["oncall"] is True

    assert api.post("/api/contacts/test-alert", headers=owner).status_code == 200
    steps, complete = _steps(api, owner)
    assert steps["test"] is True and complete is True

    # Ticked from state, not stored as ticked: take the person off the
    # roster and the box is empty again.
    api.patch(f"/api/contacts/{made.json()['contact_id']}", headers=owner,
              json={"active": False})
    assert _steps(api, owner)[0]["oncall"] is False


def test_a_test_that_reached_nobody_does_not_tick_the_box(api, wire):
    owner, tenant_id, _ = _sign_up(api)
    usage = STORE.usage_for(tenant_id)
    usage.sms_sent = 10_000  # far past any daily cap

    out = api.post("/api/contacts/test-alert", headers=owner).json()

    assert all(r["status"] == "budget_exceeded" for r in out["results"])
    assert _steps(api, owner)[0]["test"] is False


def test_the_console_hides_the_checklist_once_it_is_complete():
    render = CONSOLE[CONSOLE.index("function renderFirstDay"):]
    render = render[:render.index("\n}\n")]

    assert "complete" in render and ".hidden = true" in render
    # Every row is read from the server's answer; nothing in the page
    # decides a step is done by itself.
    assert "step.done" in render
    assert "localStorage" not in render


# --- C5: the test alert -------------------------------------------------------


def test_the_test_text_goes_to_the_roster_and_says_it_is_a_test(api, wire):
    owner, tenant_id, _ = _sign_up(api)
    for name, phone in (("Ana", "+15550111"), ("Bo", "+15550122"),
                        ("Ana again", "+1 555 0111")):
        api.post("/api/contacts", headers=owner, json={"full_name": name, "phone": phone})

    resp = api.post("/api/contacts/test-alert", headers=owner)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["test"] is True and body["incident_opened"] is False
    # One text per number, however many times it is on the roster.
    assert sorted(m["to"] for m in wire["sms"]) == ["+15550111", "+15550122"]
    for message in wire["sms"]:
        assert message["body"].startswith("TEST from CyberLogix: nothing is wrong")
    assert wire["voice"] == []


def test_a_test_opens_no_incident_and_posts_to_no_channel(api, wire, monkeypatch):
    import webhooks

    owner, tenant_id, _ = _sign_up(api)
    api.post("/api/webhooks", headers=owner,
             json={"kind": "generic", "target": "https://hooks.example.com/x"})
    posted = []
    monkeypatch.setattr(webhooks, "_post", lambda *a, **k: posted.append(a) or (True, 200))

    api.post("/api/contacts/test-alert", headers=owner)

    assert STORE.incidents_for(tenant_id) == []
    assert posted == []
    assert api.get("/api/console/overview", headers=owner).json()["summary"][
        "readings_logged"] == 0


def test_a_test_is_counted_against_the_spend_caps(api, wire):
    owner, tenant_id, _ = _sign_up(api)
    api.post("/api/contacts", headers=owner, json={"full_name": "Ana", "phone": "+15550111"})
    before = STORE.usage_for(tenant_id).sms_sent

    api.post("/api/contacts/test-alert", headers=owner)

    assert STORE.usage_for(tenant_id).sms_sent == before + 1


def test_a_test_past_the_cap_sends_nothing(api, wire):
    from costs import caps_for

    owner, tenant_id, _ = _sign_up(api)
    STORE.usage_for(tenant_id).sms_sent = caps_for(tenant_id)["sms"]

    body = api.post("/api/contacts/test-alert", headers=owner).json()

    assert wire["sms"] == []
    assert body["results"][0]["status"] == "budget_exceeded"


def test_only_an_owner_or_operator_can_send_one(api, wire):
    owner, _, key = _sign_up(api)
    operator = _add_person(api, owner, "op@harbour.example", "operator")
    viewer = _add_person(api, owner, "view@harbour.example", "viewer")

    assert api.post("/api/contacts/test-alert", headers=viewer).status_code == 403
    assert api.post("/api/contacts/test-alert", headers=key).status_code == 401
    assert wire["sms"] == []
    assert api.post("/api/contacts/test-alert", headers=operator).status_code == 200


def test_a_second_test_straight_away_is_refused_and_says_when(api, wire):
    owner, tenant_id, _ = _sign_up(api)
    assert api.post("/api/contacts/test-alert", headers=owner).status_code == 200

    again = api.post("/api/contacts/test-alert", headers=owner)

    assert again.status_code == 429
    assert int(again.headers["Retry-After"]) > 0
    assert len(wire["sms"]) == 1

    _age_tests(tenant_id, firstday.TEST_ALERT_COOLDOWN_SECONDS // 60 + 1)
    assert api.post("/api/contacts/test-alert", headers=owner).status_code == 200


def test_no_more_than_a_handful_a_day(api, wire):
    owner, tenant_id, _ = _sign_up(api)
    for _ in range(firstday.TEST_ALERTS_PER_DAY):
        assert api.post("/api/contacts/test-alert", headers=owner).status_code == 200
        _age_tests(tenant_id, firstday.TEST_ALERT_COOLDOWN_SECONDS // 60 + 1)

    refused = api.post("/api/contacts/test-alert", headers=owner)

    assert refused.status_code == 429
    assert "last day" in refused.json()["detail"]


def test_the_limit_survives_a_restart(api, wire):
    """Read from the audit trail, not from memory."""
    owner, tenant_id, _ = _sign_up(api)
    api.post("/api/contacts/test-alert", headers=owner)
    firstday.forget()

    assert api.post("/api/contacts/test-alert", headers=owner).status_code == 429


def test_a_test_call_needs_a_plan_with_calls(api, wire):
    owner, _, _ = _sign_up(api)  # a trial: texts only

    body = api.post("/api/contacts/test-alert", headers=owner, json={"call": True}).json()

    assert wire["voice"] == []
    assert "does not include phone calls" in body["message"]


def test_a_test_call_rings_the_first_person_and_says_it_is_a_test(
        api, wire, operator_factory):
    owner, tenant, _ = operator_factory(plan="enterprise")
    api.post("/api/contacts", headers=owner,
             json={"full_name": "Ana", "phone": "+15550111", "escalation_order": 1})
    api.post("/api/contacts", headers=owner,
             json={"full_name": "Bo", "phone": "+15550122", "escalation_order": 2})

    body = api.post("/api/contacts/test-alert", headers=owner, json={"call": True}).json()

    assert [c["to"] for c in wire["voice"]] == ["+15550111"]
    assert "This is a test call" in wire["voice"][0]["twiml"]
    assert "<Gather" not in wire["voice"][0]["twiml"], "nothing to acknowledge"
    assert STORE.usage_for(tenant["tenant_id"]).voice_calls == 1
    assert any(r["channel"] == "voice" for r in body["results"])


def test_a_long_company_name_cannot_push_the_reassurance_off_the_text():
    from notifications import MAX_SMS_CHARACTERS
    from store import Tenant

    tenant = Tenant.__new__(Tenant)
    tenant.company_name = "X" * 160
    text = firstday.sms_text(tenant)[:MAX_SMS_CHARACTERS]

    assert text.startswith("TEST from CyberLogix: nothing is wrong, no action needed.")


def test_with_texts_switched_off_the_test_is_written_and_the_box_ticks(api):
    """No Twilio on this server: a dry run is all a test can be, and a
    checklist that could never be finished there would be a lie."""
    owner, _, _ = _sign_up(api)

    body = api.post("/api/contacts/test-alert", headers=owner).json()

    assert body["dry_run"] is True
    assert body["results"][0]["status"] == "not_configured"
    assert _steps(api, owner)[0]["test"] is True


def test_the_console_offers_the_test_and_labels_it(api):
    assert 'id="test-alert"' in CONSOLE
    assert re.search(r'api\(\s*"/api/contacts/test-alert"', CONSOLE)


# --- C8: the simulator --------------------------------------------------------


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(simulator, "clock", lambda: now["t"])

    def advance(seconds):
        now["t"] += seconds
    return advance


def test_an_owner_can_put_a_simulated_unit_up_and_it_says_so(api, clock):
    owner, _, _ = _sign_up(api)

    made = api.post("/api/simulator/units", headers=owner)

    assert made.status_code == 201, made.text
    unit = made.json()
    assert unit["simulated"] is True
    assert unit["sensor_id"].startswith("SIM-")
    assert "Simulated" in unit["notice"]
    assert unit["breaching"] is False
    assert unit["industry_vertical"] == "restaurant"  # the sector they signed up with


def test_only_the_owner_can_run_the_simulator(api, clock):
    owner, _, key = _sign_up(api)
    operator = _add_person(api, owner, "op@harbour.example", "operator")
    viewer = _add_person(api, owner, "view@harbour.example", "viewer")

    for who in (operator, viewer):
        assert api.post("/api/simulator/units", headers=who).status_code == 403
        assert api.get("/api/simulator", headers=who).status_code == 403
    assert api.post("/api/simulator/units", headers=key).status_code == 401


def test_a_broken_unit_breaches_and_says_what_would_have_been_sent(api, clock, wire):
    owner, _, _ = _sign_up(api)
    api.post("/api/contacts", headers=owner, json={"full_name": "Ana", "phone": "+15550111"})
    unit = api.post("/api/simulator/units", headers=owner).json()["sensor_id"]

    api.post(f"/api/simulator/units/{unit}/break", headers=owner)
    clock(simulator.TICK_SECONDS * 8)
    shown = api.get("/api/simulator", headers=owner).json()["units"][0]

    assert shown["breaching"] is True
    assert shown["would_alert"]["text"].startswith("SIMULATED")
    assert shown["would_alert"]["sent"] is False
    assert shown["would_alert"]["recipients"] == ["Ana"]
    assert wire["sms"] == [] and wire["voice"] == []

    api.post(f"/api/simulator/units/{unit}/fix", headers=owner)
    assert api.get("/api/simulator", headers=owner).json()["units"][0]["breaching"] is False


def test_nothing_simulated_reaches_a_record(api, clock, wire):
    """The rule the simulator exists to keep."""
    owner, tenant_id, _ = _sign_up(api)
    before = {path: api.get(path, headers=owner).text for path in (
        "/api/autopilot/compliance?days=7", "/api/vault/attestation?days=30")}

    for _ in range(simulator.MAX_UNITS):
        unit = api.post("/api/simulator/units", headers=owner).json()["sensor_id"]
        api.post(f"/api/simulator/units/{unit}/break", headers=owner)
    clock(simulator.TICK_SECONDS * 20)
    assert all(u["breaching"] for u in
               api.get("/api/simulator", headers=owner).json()["units"])

    assert STORE.sensors_for(tenant_id) == []
    assert STORE.seat_count(tenant_id) == 0
    assert STORE.incidents_for(tenant_id) == []
    assert not any(r.sensor_id.startswith("SIM-") for r in STORE.anchors_for_sensor("SIM-1"))
    overview = api.get("/api/console/overview", headers=owner).json()
    assert overview["sensors"] == [] and overview["summary"]["readings_logged"] == 0
    for path, text in before.items():
        after = api.get(path, headers=owner).text
        assert "SIM-" not in after
        # Ids, signatures and every timestamp: a report's window is "the
        # last N days from now", so it moves when the clock crosses a
        # second between the two reads, which CI caught as a flake.
        strip = lambda t: re.sub(  # noqa: E731
            r'"(generated_at|issued_at|[a-z_]*digest[a-z_]*|attestation_id|signature)":"[^"]*"'
            r'|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', "", t)
        assert strip(after) == strip(text), path
    export = api.get("/api/accounts/business/export", headers=owner)
    assert export.status_code == 200 and "SIM-" not in export.text
    assert wire["sms"] == [] and wire["voice"] == []


def test_no_more_than_three_at_once_and_each_can_be_removed(api, clock):
    owner, _, _ = _sign_up(api)
    for _ in range(simulator.MAX_UNITS):
        assert api.post("/api/simulator/units", headers=owner).status_code == 201

    assert api.post("/api/simulator/units", headers=owner).status_code == 409

    assert api.delete("/api/simulator/units/SIM-1", headers=owner).status_code == 200
    assert len(api.get("/api/simulator", headers=owner).json()["units"]) == 2
    assert api.delete("/api/simulator", headers=owner).json()["removed"] == 2
    assert api.get("/api/simulator", headers=owner).json()["units"] == []


def test_one_business_cannot_touch_another_s_simulation(api, clock):
    owner, _, _ = _sign_up(api)
    other, _, _ = _sign_up(api, email="lee@other.example", company="Other Diner")
    unit = api.post("/api/simulator/units", headers=owner).json()["sensor_id"]

    assert api.post(f"/api/simulator/units/{unit}/break", headers=other).status_code == 404
    assert api.get("/api/simulator", headers=other).json()["units"] == []


def test_a_forgotten_simulation_clears_itself(api, clock):
    owner, _, _ = _sign_up(api)
    api.post("/api/simulator/units", headers=owner)

    clock(simulator.LIFETIME_SECONDS + 1)

    assert api.get("/api/simulator", headers=owner).json()["units"] == []


def test_an_unknown_sector_is_refused(api, clock):
    owner, _, _ = _sign_up(api)
    resp = api.post("/api/simulator/units", headers=owner,
                    json={"industry_vertical": "submarines"})
    assert resp.status_code == 400


def test_the_console_marks_every_simulated_card(api):
    card = CONSOLE[CONSOLE.index("function simCard"):]
    card = card[:card.index("\n}\n")]
    assert "is-simulated" in card and "SIMULATED" in card
    # A simulated card carries none of a real sensor's controls.
    for control in ("data-pulse", "data-tune", "data-rekey", "data-retire", "data-detail"):
        assert control not in card


# --- C1: from sign-up to a first reading, with fewer steps --------------------

SIGNUP = (ROOT / "static" / "signup.html").read_text()


def _const(source, name):
    found = re.search(rf'const {name} = "([^"]+)";', source)
    assert found, name
    return found.group(1)


def test_sign_up_lands_in_the_console_with_the_key_handed_over_once():
    """No panel of curl commands and no "Open the console" click between
    signing up and the first thing to do. The key goes by sessionStorage
    for one load, and the console deletes it as it reads it."""
    assert _const(SIGNUP, "NEW_KEY") == _const(CONSOLE, "NEW_KEY_STORAGE")
    done = SIGNUP[SIGNUP.index("function showDone"):]
    done = done[:done.index("\n}\n")]
    assert 'sessionStorage.setItem(NEW_KEY, body.api_key)' in done
    assert "localStorage.setItem(NEW_KEY" not in done
    assert 'if (kept && handed) {\n    location.replace("/console");' in done
    # Storage refused: the panel that shows the key is still there.
    assert 'id="apikey"' in done
    assert "sessionStorage.removeItem(NEW_KEY_STORAGE)" in CONSOLE


def test_a_unit_can_be_added_with_one_field():
    form = CONSOLE[CONSOLE.index('<form id="add-form">'):]
    form = form[:form.index("</form>")]
    assert re.search(r'<input id="add-loc" required', form)
    assert not re.search(r'<input id="add-id"[^>]*required', form)
    assert form.index('id="add-loc"') < form.index('id="add-id"')
    assert '$("add-id").value.trim() || sensorIdFrom(' in CONSOLE


def test_a_first_reading_is_shown_when_it_lands_not_on_the_next_poll():
    watch = CONSOLE[CONSOLE.index("function watchForFirstReading"):]
    watch = watch[:watch.index("\n}\n")]
    assert 'api("/api/console/overview")' in watch
    assert "10 * 60 * 1000" in watch, "the fast poll must stop by itself"
    assert ".then(watchForFirstReading)" in CONSOLE


def test_no_button_writes_a_made_up_reading_into_a_real_record():
    """"Send safe pulse" and "Simulate failure" posted invented readings
    through the real pipeline, into the compliance log and the vault. The
    simulator replaces them, and keeps its readings out of every record."""
    assert "data-pulse" not in CONSOLE
    assert "Send safe pulse" not in CONSOLE
