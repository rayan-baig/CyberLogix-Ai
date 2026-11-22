"""A business's own AI key for the alert line (alertkey.py).

The promises: encrypted at rest and bound to its business; shown again only
as its last four characters; never in a log, an answer, the download or the
audit trail; used only for that business's alerts; removable; and when a
call on it fails or hangs, the alert goes out on time with the template
line. Without the server's encryption secret, nothing can be saved and the
console says why.
"""

from __future__ import annotations

import logging
import time

import pytest

import alertkey
import gemini
import wording
from store import STORE

SECRET = "a-long-random-test-secret-for-the-alert-key-only"
KEY = "AIzaTestKey0123456789abcdefghijWXYZ"


@pytest.fixture()
def secret_set(monkeypatch):
    monkeypatch.setenv(alertkey.SETTING, SECRET)
    yield
    alertkey._last.clear()


class _OwnModels:
    """client.models on the business's own key: records what it was given."""

    def __init__(self, behaviour="ok", text="Check the door seal first."):
        self.behaviour = behaviour
        self.text = text
        self.prompts = []

    def generate_content(self, model, contents):
        self.prompts.append(contents)
        if self.behaviour == "boom":
            # A provider that repeats the key back in its error message.
            raise RuntimeError(f"API key {KEY} not valid. Please pass a valid API key.")
        if self.behaviour == "hang":
            time.sleep(3)
        return type("Resp", (), {"text": self.text})()


@pytest.fixture()
def own_client(monkeypatch):
    made = {"keys": [], "models": _OwnModels()}

    def factory(api_key):
        made["keys"].append(api_key)
        return type("C", (), {"models": made["models"]})()

    monkeypatch.setattr(gemini, "_client_for_key", factory)
    return made


def _owner(operator_factory, **kw):
    headers, tenant, _ = operator_factory(**kw)
    return headers, tenant


# ---- encryption ---------------------------------------------------------------


def test_the_key_round_trips_and_is_bound_to_its_business(secret_set):
    sealed = alertkey.seal("TEN-1", KEY)
    assert KEY not in sealed and sealed.startswith("v1.")
    assert alertkey.unseal("TEN-1", sealed) == KEY
    # Another business's row cannot open it, and neither can another secret.
    with pytest.raises(Exception):
        alertkey.unseal("TEN-2", sealed)
    assert alertkey.seal("TEN-1", KEY) != sealed  # a fresh nonce every time


def test_a_changed_secret_reads_as_no_key_never_a_crash(api, operator_factory, secret_set, monkeypatch):
    owner, tenant = _owner(operator_factory)
    assert api.put("/api/ai-key", headers=owner, json={"key": KEY}).status_code == 200
    monkeypatch.setenv(alertkey.SETTING, "a-different-secret")
    assert alertkey.key_for(tenant["tenant_id"]) is None


# ---- saving, showing, removing --------------------------------------------------


def test_save_shows_only_the_last_four_and_stores_no_plain_key(api, operator_factory, secret_set):
    owner, tenant = _owner(operator_factory)
    saved = api.put("/api/ai-key", headers=owner, json={"key": f"  {KEY}  "})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["saved"] is True and body["key_hint"] == KEY[-4:] == "WXYZ"
    assert KEY not in saved.text
    seen = api.get("/api/ai-key", headers=owner)
    assert seen.json()["key_hint"] == "WXYZ" and KEY not in seen.text
    row = STORE._db.get(alertkey.KIND, tenant["tenant_id"])
    assert KEY not in str(row)
    assert alertkey.key_for(tenant["tenant_id"]) == KEY
    # Not in the owner's download, and the audit trail names only the last four.
    export = api.get("/api/accounts/business/export", headers=owner)
    assert export.status_code == 200 and KEY not in export.text and "key_ciphertext" not in export.text
    audit = api.get("/api/accounts/audit?limit=8", headers=owner)
    assert KEY not in audit.text and "ends WXYZ" in audit.text


def test_a_key_can_be_replaced_and_removed(api, operator_factory, secret_set):
    owner, tenant = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY})
    other = "AIzaAnotherKey0123456789abcdefghij1234"
    assert api.put("/api/ai-key", headers=owner, json={"key": other}).json()["key_hint"] == "1234"
    gone = api.delete("/api/ai-key", headers=owner)
    assert gone.status_code == 200 and gone.json()["saved"] is False
    assert STORE._db.get(alertkey.KIND, tenant["tenant_id"]) is None
    assert alertkey.key_for(tenant["tenant_id"]) is None
    assert api.delete("/api/ai-key", headers=owner).status_code == 404


def test_only_an_owner_may_see_or_save_it(api, operator_factory, secret_set):
    owner, _ = _owner(operator_factory)
    api.post("/api/accounts/users", headers=owner, json={
        "email": "sam@example.com", "full_name": "Sam Cole",
        "password": "another-long-password", "role": "operator"}).raise_for_status()
    login = api.post("/api/accounts/login", json={"email": "sam@example.com",
                                                  "password": "another-long-password"}).json()
    operator = {"Authorization": f"Bearer {login['token']}"}
    assert api.get("/api/ai-key", headers=operator).status_code == 403
    assert api.put("/api/ai-key", headers=operator, json={"key": KEY}).status_code == 403


def test_something_that_is_not_a_key_is_refused(api, operator_factory, secret_set):
    owner, _ = _owner(operator_factory)
    for bad in ("short", "has spaces in the middle of it here", "x" * 250):
        assert api.put("/api/ai-key", headers=owner, json={"key": bad}).status_code == 422, bad


def test_without_the_secret_the_option_is_hidden_and_says_why(api, operator_factory, monkeypatch):
    monkeypatch.delenv(alertkey.SETTING, raising=False)
    owner, _ = _owner(operator_factory)
    seen = api.get("/api/ai-key", headers=owner).json()
    assert seen["available"] is False and seen["saved"] is False
    # In words a customer can act on, never the server setting's name
    # (3.26.0: "CYBERLOGIX_KEY_ENCRYPTION_SECRET is not set" was on Setup).
    assert seen["why_not"] == "Not switched on for this server yet. Ask your provider to switch this on."
    refused = api.put("/api/ai-key", headers=owner, json={"key": KEY})
    assert refused.status_code == 503 and "Ask your provider to switch this on" in refused.text
    assert "CYBERLOGIX_" not in refused.text and "CYBERLOGIX_" not in seen["why_not"]
    # The console hides the form and shows the reason instead.
    console = (alertkey.__file__.rsplit("/", 1)[0]) + "/static/console.js"
    js = open(console, encoding="utf-8").read()
    assert '$("aikey-owner").hidden = !owner || !seen.available;' in js
    assert '$("aikey-unavailable").textContent = seen.why_not;' in js


# ---- the alert line ----------------------------------------------------------------


def _breach(api, sensor_factory, headers):
    sensor_factory(headers, "WALKIN-1", "restaurant", "Kitchen walk-in", kind="fridge")
    started = time.monotonic()
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "WALKIN-1", "temperature_fahrenheit": 58.0})
    took = time.monotonic() - started
    assert resp.status_code in (200, 201), resp.text
    incident = STORE.open_incidents(STORE.get_sensor("WALKIN-1").tenant_id)[0]
    return incident, took


def test_the_business_key_words_its_own_alerts_and_overrides_ours(
        api, operator_factory, sensor_factory, secret_set, own_client, stub_gemini):
    owner, tenant = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    incident, _ = _breach(api, sensor_factory, owner)
    assert own_client["keys"] == [KEY]
    assert "Check the door seal first." in incident.sms_text
    assert incident.sms_dispatch_source == "gemini"
    assert stub_gemini.prompts == [], "the service's own key was not used for this business"
    assert alertkey.last_result(tenant["tenant_id"])["worked"] is True


def test_another_business_never_uses_it(
        api, operator_factory, sensor_factory, secret_set, own_client, stub_gemini):
    owner, _ = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    other, _ = _owner(operator_factory, company_name="Other Grill", email="other@example.com")
    sensor_factory(other, "OTHER-1", "restaurant", "Other walk-in", kind="fridge")
    api.post("/api/sensor-pulse", headers=other,
             json={"sensor_id": "OTHER-1", "temperature_fahrenheit": 58.0}).raise_for_status()
    assert own_client["keys"] == []
    assert stub_gemini.prompts, "the other business's line used the service's key"


def test_a_failing_key_falls_back_to_the_template_and_is_never_logged(
        api, operator_factory, sensor_factory, secret_set, own_client, stub_gemini, caplog):
    caplog.set_level(logging.DEBUG)
    owner, tenant = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    own_client["models"].behaviour = "boom"
    incident, _ = _breach(api, sensor_factory, owner)
    template = wording.action_for(STORE.get_sensor("WALKIN-1"))
    assert template in incident.sms_text
    assert incident.sms_dispatch_source == "fallback_template"
    assert stub_gemini.prompts == [], "never falls back to the service's own key"
    assert alertkey.last_result(tenant["tenant_id"])["worked"] is False
    assert KEY not in caplog.text
    assert "own AI key failed" in caplog.text and "[key]" in caplog.text
    # The console's card says it didn't work, and still shows only the last four.
    seen = api.get("/api/ai-key", headers=owner)
    assert seen.json()["last_result"]["worked"] is False and KEY not in seen.text


def test_a_hanging_key_gives_the_template_inside_the_cap(
        api, operator_factory, sensor_factory, secret_set, own_client, monkeypatch):
    monkeypatch.setattr(gemini, "AI_TIMEOUT_SECONDS", 0.3)
    owner, _ = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    own_client["models"].behaviour = "hang"
    incident, took = _breach(api, sensor_factory, owner)
    assert incident.sms_dispatch_source == "fallback_template"
    assert took < 2.5, took


def test_the_cap_and_fallback_are_the_existing_ones():
    """No new timeout: the own-key call shares AI_TIMEOUT_SECONDS (6 s by
    default) and the same worker pool as every alert line."""
    import inspect

    source = inspect.getsource(gemini._generate_on_own_key)
    assert "call.result(timeout=AI_TIMEOUT_SECONDS)" in source
    assert "_calls.submit(" in source
    assert gemini._seconds("CYBERLOGIX_AI_TIMEOUT_SECONDS_UNSET_IN_TESTS", 6.0) == 6.0


def test_try_it_reports_whether_the_key_works(api, operator_factory, secret_set, own_client):
    owner, _ = _owner(operator_factory)
    assert api.post("/api/ai-key/test", headers=owner).status_code == 404
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    worked = api.post("/api/ai-key/test", headers=owner).json()
    assert worked["worked"] is True and worked["line"] == "Check the door seal first."
    own_client["models"].behaviour = "boom"
    failed = api.post("/api/ai-key/test", headers=owner)
    assert failed.json()["worked"] is False and KEY not in failed.text


def test_erasing_the_business_deletes_the_key(api, operator_factory, secret_set):
    owner, tenant = _owner(operator_factory)
    api.put("/api/ai-key", headers=owner, json={"key": KEY}).raise_for_status()
    gone = api.post("/api/accounts/business/erase", headers=owner, json={
        "company_name": tenant["company_name"], "password": "correct-horse-battery"})
    assert gone.status_code == 200, gone.text
    assert STORE._db.get(alertkey.KIND, tenant["tenant_id"]) is None
