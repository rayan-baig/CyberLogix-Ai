"""The help page tells the truth, because it is built from the code.

Each answer that quotes a figure is checked here against the constant
the product actually runs on. Change the trial, a price or an escalation
timing and the page changes with it; write a number into the page by
hand and one of these fails.
"""

import re
from pathlib import Path

import pytest

import helpdesk
from pages import page_source

ROOT = Path(__file__).resolve().parent.parent


def _answer(item_id):
    item = next(i for i in helpdesk.faq() if i["id"] == item_id)
    return " ".join(item["a"])


def test_there_are_twelve_to_fifteen_real_questions():
    items = helpdesk.faq()
    assert 12 <= len(items) <= 15
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        assert item["q"].strip() and item["a"]


def test_the_page_renders_every_question(api):
    resp = api.get("/help")
    assert resp.status_code == 200
    for item in helpdesk.faq():
        assert f'id="{item["id"]}"' in resp.text
    assert 'id="sensor-offline"' in resp.text
    assert "%%" not in resp.text


def test_the_trial_answer_is_the_trial(monkeypatch):
    from auth import LICENCE_GRACE_DAYS
    from domain import PLAN_TIERS

    text = _answer("trial")
    assert f"{PLAN_TIERS['trial']['term_days']} days" in text
    assert f"{PLAN_TIERS['trial']['max_sensors']} sensors" in text
    assert f"{LICENCE_GRACE_DAYS} more days" in text
    assert "card" in text

    # And it follows the constant, rather than matching it by luck.
    monkeypatch.setitem(PLAN_TIERS["trial"], "term_days", 21)
    assert "21 days" in _answer("trial")


def test_the_trial_really_takes_no_card():
    from pricing import headline
    from signup import SignupRequest

    assert headline()["card_required"] is False
    fields = set(SignupRequest.model_fields)
    assert not fields & {"card", "card_number", "payment_method"}


def test_the_prices_are_the_rate_card():
    from pricing import PRICE_BOOK, headline

    text = _answer("price")
    assert headline()["from_label"] in text
    top = max(entry["monthly_usd"] for entry in PRICE_BOOK.values())
    assert f"${top:,.0f}" in text
    assert 'href="/pricing"' in text


def test_the_extras_and_the_prepay_discount_are_the_rate_card():
    from pricing import ADD_ONS, ANNUAL_PREPAY_DISCOUNT_PERCENT

    text = _answer("extras")
    for add_on in ADD_ONS.values():
        assert add_on["name"] in text
        assert f"${add_on['monthly_usd']:,.0f}" in text
    assert f"{ANNUAL_PREPAY_DISCOUNT_PERCENT:g}%" in text


def test_the_plans_answer_lists_every_plan_and_who_gets_calls():
    from domain import PLAN_TIERS

    text = _answer("plans")
    for tier in PLAN_TIERS.values():
        assert f"{tier['name']}: up to {tier['max_sensors']:,} sensors" in text


def test_the_escalation_answer_is_the_escalation():
    from domain import (DISPATCH_SLA_SECONDS, PLAN_TIERS, SMS_RETRY_AFTER_MINUTES,
                        VOICE_ESCALATION_GRACE_MINUTES)

    assert f"within {DISPATCH_SLA_SECONDS} seconds" in _answer("alerts")
    text = _answer("escalation")
    assert f"within {VOICE_ESCALATION_GRACE_MINUTES} minutes" in text
    assert "presses 1" in text
    for minutes in SMS_RETRY_AFTER_MINUTES:
        assert str(minutes) in text
    # Calls are named only for the plans that have them.
    for tier in PLAN_TIERS.values():
        assert tier["name"] in text


def test_the_test_alert_answer_is_the_test_alert():
    from firstday import TEST_ALERT_COOLDOWN_SECONDS, TEST_ALERTS_PER_DAY, sms_text

    text = _answer("test-alert")
    assert f"every {TEST_ALERT_COOLDOWN_SECONDS // 60} minutes" in text
    assert f"up to {TEST_ALERTS_PER_DAY} a day" in text
    assert "Send a test alert" in text
    # The text really starts the way the answer says.
    assert sms_text(type("T", (), {"company_name": "X"})()).startswith("TEST")


def test_the_console_has_the_buttons_the_answers_name():
    console = page_source(ROOT / "static" / "console.html")
    for label in ("Send a test alert", "Download everything", "Delete everything",
                  "Download or delete everything", "Who gets called",
                  "What you pay", "Fetch from a sensor cloud", "New key",
                  "Forgotten your password?"):
        assert label in console, label


def test_the_data_answers_match_the_export_and_erase_rules():
    from store import MAX_READINGS_PER_SENSOR

    assert f"newest {MAX_READINGS_PER_SENSOR} readings" in _answer("export")
    text = _answer("delete")
    assert "name" in text and "password" in text and "cannot be undone" in text


def test_the_export_and_erase_routes_the_answers_rely_on_exist(api):
    from test_every_route_is_tested import _routes

    routes = set(_routes(api.app.routes))
    assert ("GET", "/api/accounts/business/export") in routes
    assert ("POST", "/api/accounts/business/erase") in routes
    assert ("POST", "/api/contacts/test-alert") in routes
    assert ("POST", "/api/accounts/forgot") in routes
    assert ("POST", "/api/licenses/me/plan") in routes


def test_help_uses_the_support_address_when_one_is_set(monkeypatch):
    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "support@cyberlogix.example")
    text = _answer("help")
    assert 'href="mailto:support@cyberlogix.example"' in text


def test_help_falls_back_to_the_reply_address_then_the_invoice(monkeypatch):
    import mail

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "")
    monkeypatch.setattr(mail, "MAIL_REPLY_TO", "replies@cyberlogix.example")
    assert "replies@cyberlogix.example" in _answer("help")

    monkeypatch.setattr(mail, "MAIL_REPLY_TO", "")
    text = _answer("help")
    assert "mailto:" not in text and "invoice" in text


def test_an_address_is_escaped(monkeypatch):
    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", 'a"><script>x</script>@b.example')
    assert "<script>" not in _answer("help")


def test_help_is_linked_from_the_console_and_the_landing_page(api):
    assert 'href="/help"' in api.get("/console").text
    assert 'href="/help"' in api.get("/").text


def test_the_help_page_has_no_inline_style_or_script():
    page = page_source(ROOT / "static" / "help.html")
    assert "<style" not in page and "style=" not in page
    assert "<script>" not in page


# ---- a sensor is offline ----------------------------------------------------

def test_the_offline_steps_are_served_for_the_console(api):
    body = api.get("/api/help/sensor-offline").json()
    assert body["steps"] == helpdesk.offline_steps()
    assert "30 minutes" in body["summary"]
    assert body["page"] == "/help#sensor-offline"


def test_the_offline_summary_follows_the_threshold():
    from domain import SENSOR_OFFLINE_AFTER_MINUTES

    assert f"{SENSOR_OFFLINE_AFTER_MINUTES} minutes" in helpdesk.offline_summary()


def test_the_offline_steps_name_the_real_battery_threshold():
    from records import Sensor

    power = helpdesk.offline_steps()[0]["detail"]
    assert f"{Sensor.LOW_BATTERY_PERCENT:g}%" in power


def test_the_docs_carry_the_same_offline_steps():
    doc = (ROOT / "docs" / "SENSOR-OFFLINE.md").read_text()
    flat = re.sub(r"\s+", " ", doc)
    for number, step in enumerate(helpdesk.offline_steps(), 1):
        assert f"### {number}. {step['title']}" in doc, step["title"]
        assert re.sub(r"\s+", " ", step["detail"]) in flat, step["title"]
    assert "SENSOR-OFFLINE.md" in (ROOT / "README.md").read_text()


def test_an_offline_sensor_links_to_the_steps_in_the_console():
    console = page_source(ROOT / "static" / "console.html")
    # On the card, only when it is offline.
    assert '${s.online ? "" : `<a class="offline-help" href="/help#sensor-offline"' in console
    # In the drawer, expandable, filled from the served list.
    assert '<details class="offline-box"' in console
    assert 'api("/api/help/sensor-offline")' in console


def test_the_simple_view_links_a_quiet_sensor_to_the_steps():
    simple = page_source(ROOT / "static" / "simple.html")
    assert 'help: "/help#sensor-offline"' in simple


@pytest.mark.parametrize("path", ["/help", "/api/help/sensor-offline"])
def test_help_needs_no_sign_in(api, path):
    assert api.get(path).status_code == 200
