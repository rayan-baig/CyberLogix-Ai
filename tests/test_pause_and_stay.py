"""Pause instead of cancel, the one-time offer to stay, and cancel.

A pause stops the bills (the contract's next billing date moves past it)
and the alerts (readings are kept, nobody is texted or called), keeps all
data, ends on its own on the date chosen, and is preceded by a reminder
three days before. The offer to stay is 25% off the next two months, once
per business ever. Cancel is the same one call it always was, and the
operator's numbers count pauses, saves and cancels.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import automation
import retention
import telemetry
from contracts import run_billing
from store import STORE, iso, utc_now


@pytest.fixture()
def texts(monkeypatch):
    sent = []

    def fake_sms(to, body, tenant_id=None):
        sent.append((to, body))
        return {"status": "sent", "to": to, "channel": "sms"}

    monkeypatch.setattr(telemetry, "send_sms", fake_sms)
    return sent


@pytest.fixture()
def grill(api, operator_factory, sensor_factory):
    """A restaurant on a signed monthly contract, its first month billed."""
    headers, tenant, user = operator_factory(company_name="Harbor Grill",
                                             email="owner@harborgrill.example")
    sensor_factory(headers, "HG-F1", "restaurant", "Walk-in freezer")
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Sam Cook", "phone": "+15550123", "role": "Head chef"})
    signed = api.post("/api/contracts", headers=headers,
                      json={"term_years": 1, "escalator_percent": 0.0})
    assert signed.status_code == 201, signed.text
    tid = tenant["tenant_id"]
    run_billing()
    assert len(STORE.invoices_for(tid)) == 1
    return {"headers": headers, "tenant_id": tid}


def _pause(api, grill, months=1):
    return api.post("/api/leaving/pause", headers=grill["headers"], json={"months": months})


def _breach(api, grill):
    return api.post("/api/sensor-pulse", headers=grill["headers"],
                    json={"sensor_id": "HG-F1", "temperature_fahrenheit": 55.0}).json()


# ---- the screen -------------------------------------------------------------


def test_the_screen_offers_a_pause_and_the_offer_before_cancel(api, grill):
    body = api.get("/api/leaving", headers=grill["headers"]).json()
    assert [o["months"] for o in body["pause_options"]] == [1, 2, 3]
    assert all(o["resume_on"] for o in body["pause_options"])
    assert body["offer"]["available"] and body["offer"]["percent"] == 25.0
    assert body["offer"]["months"] == 2
    # 25% of $149 for two months.
    assert body["offer"]["saving_usd"] == 74.5
    assert body["cancel"]["possible"] and "Cancel any time" in body["cancel"]["rule"]
    assert body["paused"] is None


def test_a_trial_has_nothing_to_pause(api, operator_factory):
    headers, _, _ = operator_factory(plan="trial", email="t@trial.example")
    body = api.get("/api/leaving", headers=headers).json()
    assert body["on_trial"] and body["pause_options"] == []
    resp = api.post("/api/leaving/pause", headers=headers, json={"months": 1})
    assert resp.status_code == 409


def test_only_an_owner_can_pause_take_the_offer_or_cancel(api, grill):
    made = api.post("/api/accounts/users", headers=grill["headers"], json={
        "email": "op@harborgrill.example", "full_name": "Op",
        "password": "correct-horse-battery", "role": "operator"})
    assert made.status_code in (200, 201), made.text
    token = api.post("/api/accounts/login", json={
        "email": "op@harborgrill.example",
        "password": "correct-horse-battery"}).json()["token"]
    op = {"Authorization": f"Bearer {token}"}
    assert api.post("/api/leaving/pause", headers=op, json={"months": 1}).status_code == 403
    assert api.post("/api/leaving/stay", headers=op).status_code == 403
    assert api.post("/api/contracts/cancel", headers=op, json={}).status_code == 403


def test_a_pause_is_one_two_or_three_months(api, grill):
    assert _pause(api, grill, months=4).status_code == 422
    assert _pause(api, grill, months=0).status_code == 422


# ---- a pause stops the alerts -----------------------------------------------


def test_a_pause_stops_alerts_keeps_readings_and_says_so(api, grill, texts):
    resp = _pause(api, grill, months=2)
    assert resp.status_code == 200, resp.text
    assert "No bills and no alerts" in resp.json()["message"]

    out = _breach(api, grill)
    assert out["status"] == "PAUSED"
    assert STORE.incidents_for(grill["tenant_id"]) == []
    assert texts == []
    # The reading was kept.
    assert STORE.readings_for("HG-F1")[-1].temperature_fahrenheit == 55.0

    account = api.get("/api/console/overview", headers=grill["headers"]).json()["account"]
    assert account["severity"] == "critical"
    assert "not texting or calling anyone" in account["headline"]
    assert account["alerting_continues"] is False and account["paused"]

    # The unattended sweep calls nobody either.
    swept = automation.sweep_tenant(STORE.get_tenant(grill["tenant_id"]))
    assert swept["status"] == "paused" and swept["voice_calls_placed"] == 0


def test_alerts_come_back_on_the_date_chosen(api, grill, texts):
    assert _pause(api, grill).status_code == 200
    assert _breach(api, grill)["status"] == "PAUSED"
    # The date arrives: nothing has to run for the pause to be over.
    [row] = STORE._db.all(retention.PAUSE_KIND)
    row["resume_at"] = iso(utc_now() - timedelta(seconds=1))
    STORE._db.put(retention.PAUSE_KIND, row["pause_id"], row)
    out = _breach(api, grill)
    assert out["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert texts and texts[0][0] == "+15550123"
    assert api.get("/api/console/overview",
                   headers=grill["headers"]).json()["account"]["paused"] is None


def test_coming_back_early_turns_alerts_on_at_once(api, grill, texts):
    assert _pause(api, grill, months=3).status_code == 200
    back = api.post("/api/leaving/resume", headers=grill["headers"])
    assert back.status_code == 200 and back.json()["paused"] is None
    assert _breach(api, grill)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert api.post("/api/leaving/resume", headers=grill["headers"]).status_code == 409


def test_a_second_pause_while_paused_is_refused(api, grill):
    assert _pause(api, grill).status_code == 200
    assert _pause(api, grill).status_code == 409


# ---- a pause stops the bills ------------------------------------------------


def test_a_pause_moves_the_next_bill_past_it_and_bills_resume_on_time(api, grill):
    sub = STORE.active_subscription(grill["tenant_id"])
    before = sub.period_start(1)
    now = utc_now()
    assert _pause(api, grill, months=2).status_code == 200
    [row] = STORE._db.all(retention.PAUSE_KIND)
    paused_for = (retention._parse(row["resume_at"]) - retention._parse(row["paused_at"]))
    sub = STORE.active_subscription(grill["tenant_id"])
    # The next bill moves by exactly the pause, so the days paid for are kept.
    assert sub.period_start(1) - before == paused_for
    assert sub.period_start(0) == sub.started_at          # what was billed keeps its date
    assert sub.ends_at - retention.add_months(sub.started_at, 12) == paused_for

    # Through the pause and past where the old bill would have been: nothing.
    for day in (5, 31, 45, 60):
        assert run_billing(now + timedelta(days=day))["invoices_issued"] == 0, day
    assert len(STORE.invoices_for(grill["tenant_id"])) == 1
    # On the new date, the bill comes, once.
    due = sub.period_start(1) + timedelta(minutes=1)
    assert run_billing(due)["invoices_issued"] == 1
    assert run_billing(due)["invoices_issued"] == 0
    assert len(STORE.invoices_for(grill["tenant_id"])) == 2


def test_coming_back_early_gives_back_the_unused_part(api, grill):
    sub = STORE.active_subscription(grill["tenant_id"])
    before = sub.period_start(1)
    assert _pause(api, grill, months=3).status_code == 200
    [row] = STORE._db.all(retention.PAUSE_KIND)
    row["paused_at"] = iso(utc_now() - timedelta(days=10))
    STORE._db.put(retention.PAUSE_KIND, row["pause_id"], row)
    assert api.post("/api/leaving/resume", headers=grill["headers"]).status_code == 200
    moved = STORE.active_subscription(grill["tenant_id"]).period_start(1) - before
    assert timedelta(days=10) - timedelta(seconds=5) < moved < timedelta(days=10, seconds=5)


def test_nothing_is_billed_while_paused_even_a_voided_month(api, grill, admin_headers):
    [invoice] = STORE.invoices_for(grill["tenant_id"])
    assert _pause(api, grill).status_code == 200
    voided = api.post(f"/api/invoices/{invoice.invoice_id}/void",
                      params={"tenant_id": grill["tenant_id"]}, headers=admin_headers,
                      json={"reason": "wrong address"})
    assert voided.status_code == 200, voided.text
    assert run_billing()["invoices_issued"] == 0


def test_data_is_kept_through_a_pause(api, grill):
    readings = len(STORE.readings_for("HG-F1"))
    assert _pause(api, grill).status_code == 200
    assert STORE.get_sensor("HG-F1") is not None
    assert len(STORE.readings_for("HG-F1")) == readings
    assert len(STORE.invoices_for(grill["tenant_id"])) == 1


# ---- the reminder -----------------------------------------------------------


def test_a_reminder_three_days_before_and_a_note_when_it_ends(api, grill, mailbox):
    assert _pause(api, grill).status_code == 200
    [row] = STORE._db.all(retention.PAUSE_KIND)
    resume = retention._parse(row["resume_at"])
    assert retention.run_pauses(resume - timedelta(days=4)) == {"reminded": 0, "resumed": 0}
    assert retention.run_pauses(resume - timedelta(days=2, hours=23)) == {
        "reminded": 1, "resumed": 0}
    [mail] = mailbox.to("ops@example.com")
    text = mail.get_body(preferencelist=("plain",)).get_content()
    assert "comes back on" in mail["Subject"]
    assert "text and call your team" in text and "next bill" in text
    # Once only.
    assert retention.run_pauses(resume - timedelta(days=1))["reminded"] == 0
    assert retention.run_pauses(resume + timedelta(minutes=5)) == {"reminded": 0, "resumed": 1}
    assert retention.run_pauses(resume + timedelta(hours=2)) == {"reminded": 0, "resumed": 0}


def test_the_hourly_pass_runs_the_pause_reminders(api, grill, monkeypatch):
    import scheduler

    called = []
    monkeypatch.setattr(retention, "run_pauses", lambda: called.append(1) or
                        {"reminded": 0, "resumed": 0})
    out = scheduler.run_money_pass()
    assert called and out["pauses"] == {"reminded": 0, "resumed": 0}


# ---- the offer to stay ------------------------------------------------------


def test_the_offer_takes_25_percent_off_the_next_two_months_only(api, grill):
    took = api.post("/api/leaving/stay", headers=grill["headers"])
    assert took.status_code == 200, took.text
    assert "Thank you for staying" in took.json()["message"]
    assert took.json()["offer"]["taken"] and not took.json()["offer"]["available"]
    sub = STORE.active_subscription(grill["tenant_id"])
    totals = []
    for index in (1, 2, 3):
        due = sub.period_start(index) + timedelta(minutes=1)
        assert run_billing(due)["invoices_issued"] == 1
        latest = sorted(STORE.invoices_for(grill["tenant_id"]), key=lambda i: i.number)[-1]
        totals.append(latest.total_usd)
        lines = [line for line in latest.lines if "staying" in line["description"]]
        assert len(lines) == (1 if index < 3 else 0)
    assert totals == [111.75, 111.75, 149.0]


def test_the_offer_is_once_per_business_ever(api, grill):
    assert api.post("/api/leaving/stay", headers=grill["headers"]).status_code == 200
    again = api.post("/api/leaving/stay", headers=grill["headers"])
    assert again.status_code == 409 and "already had the offer" in again.json()["detail"]
    # Not after cancelling and signing again either.
    assert api.post("/api/contracts/cancel", headers=grill["headers"], json={}).status_code == 200
    assert api.post("/api/contracts", headers=grill["headers"],
                    json={"term_years": 1}).status_code == 201
    assert api.get("/api/leaving", headers=grill["headers"]).json()["offer"]["available"] is False
    assert api.post("/api/leaving/stay", headers=grill["headers"]).status_code == 409


def test_a_voided_discounted_month_gets_the_same_discount_and_never_a_third(
        api, grill, admin_headers):
    assert api.post("/api/leaving/stay", headers=grill["headers"]).status_code == 200
    sub = STORE.active_subscription(grill["tenant_id"])
    run_billing(sub.period_start(1) + timedelta(minutes=1))
    second = sorted(STORE.invoices_for(grill["tenant_id"]), key=lambda i: i.number)[-1]
    assert second.total_usd == 111.75
    api.post(f"/api/invoices/{second.invoice_id}/void", params={"tenant_id": grill["tenant_id"]},
             headers=admin_headers, json={"reason": "typo"})
    run_billing(sub.period_start(1) + timedelta(minutes=2))
    reissued = sorted(STORE.invoices_for(grill["tenant_id"]), key=lambda i: i.number)[-1]
    assert reissued.invoice_id != second.invoice_id and reissued.total_usd == 111.75
    run_billing(sub.period_start(2) + timedelta(minutes=1))
    run_billing(sub.period_start(3) + timedelta(minutes=1))
    totals = [i.total_usd for i in sorted(STORE.invoices_for(grill["tenant_id"]),
                                          key=lambda i: i.number) if i.state != "void"]
    assert totals == [149.0, 111.75, 111.75, 149.0]


def test_the_offer_needs_a_contract(api, operator_factory):
    headers, _, _ = operator_factory(email="nocontract@example.com")
    body = api.get("/api/leaving", headers=headers).json()
    assert body["offer"]["available"] is False and body["cancel"]["possible"] is False
    assert api.post("/api/leaving/stay", headers=headers).status_code == 409


# ---- cancel still works -----------------------------------------------------


def test_cancel_is_still_one_call_and_still_stops_the_bills(api, grill):
    resp = api.post("/api/contracts/cancel", headers=grill["headers"], json={})
    assert resp.status_code == 200, resp.text
    assert "Contract cancelled" in resp.json()["message"]
    assert STORE.active_subscription(grill["tenant_id"]) is None
    assert run_billing(utc_now() + timedelta(days=90))["invoices_issued"] == 0
    assert api.get("/api/leaving", headers=grill["headers"]).json()["cancel"]["possible"] is False


def test_cancelling_while_paused_ends_the_pause_and_keeps_only_the_time_used(
        api, grill, texts):
    sub = STORE.active_subscription(grill["tenant_id"])
    paid_until = sub.period_start(1)
    assert _pause(api, grill, months=3).status_code == 200
    resp = api.post("/api/contracts/cancel", headers=grill["headers"], json={})
    assert resp.status_code == 200
    service_until = retention._parse(resp.json()["service_until"])
    assert service_until - paid_until < timedelta(minutes=1)
    assert not retention.is_paused(grill["tenant_id"])
    assert _breach(api, grill)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


# ---- the operator's numbers -------------------------------------------------


def test_pauses_saves_and_cancels_are_counted(api, grill, admin_headers, operator_factory,
                                              sensor_factory):
    assert _pause(api, grill).status_code == 200
    assert api.post("/api/leaving/resume", headers=grill["headers"]).status_code == 200
    assert api.post("/api/leaving/stay", headers=grill["headers"]).status_code == 200
    assert api.post("/api/contracts/cancel", headers=grill["headers"], json={}).status_code == 200
    other, tenant, _ = operator_factory(company_name="Deli", email="o@deli.example")
    sensor_factory(other, "DL-1", "restaurant", "Fridge")
    assert api.post("/api/contracts", headers=other, json={"term_years": 1}).status_code == 201
    assert api.post("/api/leaving/pause", headers=other, json={"months": 2}).status_code == 200

    resp = api.get("/api/leaving/numbers", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["pause"] == {"last_30_days": 2, "ever": 2}
    assert body["save"] == {"last_30_days": 1, "ever": 1}
    assert body["cancel"] == {"last_30_days": 1, "ever": 1}
    assert body["paused_now"] == 1
    # The counts name nobody, so they outlive a business that deletes itself.
    for row in STORE._db.all(retention.EVENT_KIND):
        assert set(row) == {"event", "at", "months"}
    assert api.get("/api/leaving/numbers", headers=grill["headers"]).status_code in (401, 403)


def test_weekly_reassurance_texts_are_not_sent_while_paused(api, grill, monkeypatch):
    import digest

    monkeypatch.setattr(digest, "wants_weekly_text", lambda tenant_id: "+15550123")
    sent = []
    import notifications

    monkeypatch.setattr(notifications, "send_sms",
                        lambda to, body, tenant_id=None: sent.append(to) or {"status": "sent"})
    assert _pause(api, grill).status_code == 200
    digest.send_weekly_texts()
    assert sent == []


# ---- the console ------------------------------------------------------------


def test_the_console_card_puts_the_offers_first_and_cancel_in_plain_sight():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "console.js").read_text()
    start = js.index("function renderLeaving(d)")
    body = js[start:js.index("\n}\n", start)]
    assert body.index("data-pause-months") < body.index("data-stay") < body.rindex(
        "data-cancel-plan")
    assert "Cancel my plan" in body
    assert "/api/contracts/cancel" in js and "/api/leaving/pause" in js


def test_the_morning_email_to_the_operator_counts_them_too(api, grill):
    import digest

    assert _pause(api, grill).status_code == 200
    text = digest._render_operator(digest.operator_digest())
    assert "Last 30 days: 1 pause(s), 0 save(s)" in text
    assert "1 business(es) paused now" in text


def test_the_plain_view_says_alerts_are_paused():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "simple.js").read_text()
    assert "Alerts are paused until" in js and "(data.account || {}).paused" in js
