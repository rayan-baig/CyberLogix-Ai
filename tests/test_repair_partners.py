"""Repair partners: technicians who pay us for leads.

A unit the forecaster or the pattern check calls failing or drifting warm
offers "Get a repair quote". Only when the business confirms does a lead
go to one approved partner covering that address, picked in turn. The
lead holds the business name, the contact and address the business chose
and a plain summary, nothing else. Each delivered lead is owed at the
partner's fee; a partner can mark one bad within seven days, and the
operator's refusal means no charge. Nothing moves money.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

import digest
import repairs
from store import STORE, evaluate_breach, iso, utc_now

APPLICATION = {
    "company_name": "Polar Fix Refrigeration", "contact_name": "Jo Banks",
    "contact_email": "jo@polarfix.example", "contact_phone": "+1-555-0144",
    "website": "polarfix.example", "areas": "021, 022, Boston",
    "message": "Licensed and insured, 12 vans.",
}


@pytest.fixture()
def inbox(monkeypatch, mailbox):
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "operator@cyberlogix.example")
    return mailbox


def _lead_mails(inbox, to="jo@polarfix.example"):
    return [m for m in inbox.to(to) if str(m["Subject"]).startswith("Repair lead:")]


def _apply(api, **changes):
    return api.post("/api/repair-partners/apply", json={**APPLICATION, **changes})


def _approve(api, admin_headers, email, postcodes=("021",), cities=(), fee=None, **apply):
    repairs.APPLY_LIMIT.reset()
    assert _apply(api, contact_email=email, **apply).status_code == 202
    row = next(r for r in STORE._db.all(repairs.PARTNER_KIND)
               if r["contact_email"] == email)
    body = {"postcodes": list(postcodes), "cities": list(cities)}
    if fee is not None:
        body["lead_fee_usd"] = fee
    resp = api.post(f"/api/repair-partners/{row['partner_id']}/approve",
                    headers=admin_headers, json=body)
    assert resp.status_code == 200, resp.text
    return {"id": row["partner_id"], "key": resp.json()["key"],
            "headers": {"X-CyberLogix-Repair-Partner": resp.json()["key"]}}


@pytest.fixture()
def partner(api, admin_headers, inbox):
    return _approve(api, admin_headers, "jo@polarfix.example")


def _seed(sensor_id, temps, spacing_minutes=5):
    sensor = STORE.get_sensor(sensor_id)
    now = utc_now()
    for index, temp in enumerate(temps):
        STORE.record_reading(
            sensor=sensor, temperature_fahrenheit=temp, humidity_percent=50.0,
            breached=evaluate_breach(sensor.industry_vertical, temp) is not None,
            at=now - timedelta(minutes=(len(temps) - 1 - index) * spacing_minutes))


@pytest.fixture()
def shop(api, operator_factory, sensor_factory):
    """A restaurant with a failing walk-in, a drifting freezer and a fine one."""
    headers, tenant, user = operator_factory(company_name="Harbor Grill",
                                             email="dana@harborgrill.example")
    sensor_factory(headers, "HG-WALKIN", "restaurant", "Walk-in freezer", above=32.0)
    sensor_factory(headers, "HG-CHEST", "restaurant", "Chest freezer", above=32.0)
    sensor_factory(headers, "HG-FINE", "restaurant", "Bar freezer", above=32.0)
    # Sat at 0°F, then went over its 32°F limit 35 minutes ago and stayed.
    _seed("HG-WALKIN", [0.0] * 6 + [38.0, 40.0, 42.0, 44.0, 46.0, 48.0, 50.0, 51.0])
    # Creeping up 5°F an hour, in range, heading for 32°F.
    _seed("HG-CHEST", [10.0 + i * 0.5 for i in range(25)], spacing_minutes=6)
    _seed("HG-FINE", [0.0] * 20)
    return {"headers": headers, "tenant_id": tenant["tenant_id"], "user": user}


def _quote(api, shop, sensor_id="HG-WALKIN", **changes):
    body = {"sensor_id": sensor_id, "contact_name": "Dana Reyes",
            "contact_phone": "+1-555-0101", "contact_email": "",
            "street": "12 Harbor St", "city": "Boston", "postcode": "02110",
            "consent": True}
    body.update(changes)
    return api.post("/api/repairs/quote", headers=shop["headers"], json=body)


# ---- when to offer ----------------------------------------------------------


def test_a_failing_unit_and_a_drifting_one_are_offered_a_quote_and_a_fine_one_is_not(api, shop):
    cards = {s["sensor_id"]: s for s in
             api.get("/api/console/overview", headers=shop["headers"]).json()["sensors"]}
    assert cards["HG-WALKIN"]["repair"] == {"offer": True, "kind": "failing"}
    assert cards["HG-CHEST"]["repair"] == {"offer": True, "kind": "drifting"}
    assert cards["HG-FINE"]["repair"] is None


def test_the_confirm_screen_shows_exactly_what_would_be_sent_and_sends_nothing(
        api, shop, partner, inbox):
    q = api.get("/api/repairs/quote/HG-WALKIN", headers=shop["headers"])
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["can_send"] and body["needs_repair"]
    assert body["business_name"] == "Harbor Grill"
    assert "freezer" in body["problem"] and "Walk-in freezer" in body["problem"]
    assert "has not come back down" in body["problem"] and "Since:" in body["problem"]
    assert "do not vouch" in body["not_a_repairer"]
    assert body["suggested"]["contact_email"] == "dana@harborgrill.example"
    assert STORE._db.all(repairs.LEAD_KIND) == []
    assert not _lead_mails(inbox)


def test_a_fine_unit_has_nothing_to_send(api, shop, partner):
    body = api.get("/api/repairs/quote/HG-FINE", headers=shop["headers"]).json()
    assert not body["can_send"] and body["problem"] == ""
    resp = _quote(api, shop, sensor_id="HG-FINE")
    assert resp.status_code == 409 and "looks fine" in resp.json()["detail"]


# ---- consent and contents ---------------------------------------------------


def test_nothing_is_sent_without_the_box_ticked(api, shop, partner, inbox):
    resp = _quote(api, shop, consent=False)
    assert resp.status_code == 400 and "Nothing has been sent" in resp.json()["detail"]
    assert STORE._db.all(repairs.LEAD_KIND) == [] and not _lead_mails(inbox)


def test_a_phone_or_an_email_is_needed(api, shop, partner):
    resp = _quote(api, shop, contact_phone="", contact_email="")
    assert resp.status_code == 400 and "phone number or an email" in resp.json()["detail"]


def test_a_lead_holds_only_what_the_business_chose(api, shop, partner, inbox, caplog):
    with caplog.at_level(logging.INFO, logger="cyberlogix.repairs"):
        resp = _quote(api, shop, contact_email="Kitchen@HarborGrill.example")
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["partner"]["company_name"] == "Polar Fix Refrigeration"
    assert "do not vouch" in out["message"]

    [mail] = _lead_mails(inbox)
    text = mail.get_body(preferencelist=("plain",)).get_content()
    for wanted in ("Harbor Grill", "Dana Reyes", "+1-555-0101",
                   "kitchen@harborgrill.example", "12 Harbor St", "Boston", "02110",
                   "Walk-in freezer", "has not come back down", "$35.00"):
        assert wanted in text, wanted
    # Not the sensor, its key, the readings, the owner's sign-in or the
    # business's own phone on file.
    sensor = STORE.get_sensor("HG-WALKIN")
    # The summary names the last reading, which the business saw before
    # confirming; never the history behind it.
    for never in ("HG-WALKIN", sensor.ingest_key or "never-a-key", "dana@harborgrill.example",
                  "+1-555-0100", shop["tenant_id"], "48.0", "44.0", "38.0"):
        assert never not in text, never
    # And the log masks the contact rather than printing it.
    assert "kitchen@harborgrill.example" not in caplog.text
    assert "+1-555-0101" not in caplog.text


def test_the_lead_email_is_blanked_from_our_mail_log_once_sent(api, shop, partner, inbox):
    assert _quote(api, shop).status_code == 201
    logged = [m for m in STORE.mail_log(limit=50) if m.dedupe_key.startswith("repair-lead:")]
    assert logged and "+1-555-0101" not in (logged[0].body or "")


# ---- partners see no sensor data --------------------------------------------


def test_the_partner_dashboard_shows_the_lead_and_no_sensor_data(api, shop, partner):
    assert _quote(api, shop).status_code == 201
    resp = api.get("/api/repair-partners/me", headers=partner["headers"])
    assert resp.status_code == 200, resp.text
    board = resp.json()
    [lead] = board["leads"]
    assert lead["business_name"] == "Harbor Grill"
    assert lead["contact"] == {"contact_name": "Dana Reyes", "contact_phone": "+1-555-0101",
                               "contact_email": ""}
    assert lead["address"] == {"street": "12 Harbor St", "city": "Boston", "postcode": "02110"}
    raw = resp.text
    for never in ("HG-WALKIN", "HG-CHEST", '"sensor_id"', '"readings"', '"spark"',
                  "temperature_fahrenheit", '"tenant_id"', shop["tenant_id"], "ingest",
                  "api_key", "dana@harborgrill.example"):
        assert never not in raw, never


def test_the_partner_key_opens_nothing_inside_a_business(api, shop, partner):
    for path in ("/api/console/overview", "/api/console/sensor/HG-WALKIN",
                 "/api/forecast/fleet", "/api/repairs/quote/HG-WALKIN",
                 "/api/insurance-partners/me", "/api/repair-partners"):
        resp = api.get(path, headers=partner["headers"])
        assert resp.status_code in (401, 403), (path, resp.status_code)


def test_a_wrong_or_switched_off_key_is_refused(api, admin_headers, partner):
    assert api.get("/api/repair-partners/me",
                   headers={"X-CyberLogix-Repair-Partner": "clx_rep_nope"}).status_code == 401
    api.post(f"/api/repair-partners/{partner['id']}/status", headers=admin_headers,
             json={"active": False})
    assert api.get("/api/repair-partners/me", headers=partner["headers"]).status_code == 403


def test_a_new_key_retires_the_old_one(api, admin_headers, partner):
    resp = api.post(f"/api/repair-partners/{partner['id']}/rotate-key", headers=admin_headers)
    assert resp.status_code == 200
    assert api.get("/api/repair-partners/me", headers=partner["headers"]).status_code == 401
    fresh = {"X-CyberLogix-Repair-Partner": resp.json()["key"]}
    assert api.get("/api/repair-partners/me", headers=fresh).status_code == 200


def test_a_deleted_business_takes_its_details_but_the_fee_stays(api, shop, partner):
    assert _quote(api, shop).status_code == 201
    STORE.erase_tenant(shop["tenant_id"])
    [lead] = api.get("/api/repair-partners/me", headers=partner["headers"]).json()["leads"]
    assert lead["removed"] and lead["business_name"] == "" and lead["contact"] == {}
    assert repairs.ledger(partner["id"])["charged_usd"] == 35.0


# ---- fair rotation and areas ------------------------------------------------


def test_areas_match_by_postcode_prefix_or_city():
    p = {"postcodes": ["021", "SW1A"], "cities": ["cambridge"]}
    assert repairs.covers(p, "02110", "")
    assert repairs.covers(p, "02139-4307", "")
    assert repairs.covers(p, "sw1a 1aa", "London")
    assert repairs.covers(p, "", "  Cambridge ")
    assert not repairs.covers(p, "02210", "Boston")
    assert not repairs.covers(p, "", "")
    assert repairs.clean_areas([" 021 ", "021", ""], ["New  York", "new york"]) == {
        "postcodes": ["021"], "cities": ["new york"]}


def test_leads_take_turns_between_partners_covering_an_area(api, admin_headers, inbox,
                                                           operator_factory, sensor_factory):
    a = _approve(api, admin_headers, "a@one.example", postcodes=("021",))
    b = _approve(api, admin_headers, "b@two.example", cities=("Boston",))
    c = _approve(api, admin_headers, "c@three.example", postcodes=("021",))
    _approve(api, admin_headers, "far@away.example", postcodes=("941",))
    order = []
    for n in range(6):
        headers, _, _ = operator_factory(company_name=f"Shop {n}", email=f"o{n}@shops.example")
        sensor_factory(headers, f"S{n}-F", "restaurant", "Freezer", above=32.0)
        _seed(f"S{n}-F", [0.0] * 6 + [40.0 + i for i in range(8)])
        resp = _quote(api, {"headers": headers}, sensor_id=f"S{n}-F")
        assert resp.status_code == 201, resp.text
        order.append(resp.json()["partner"]["company_name"])
        lead = next(r for r in STORE._db.all(repairs.LEAD_KIND) if r["sensor_id"] == f"S{n}-F")
        order[-1] = lead["partner_id"]
    # Three partners cover Boston 02110; each gets one in three, in turn,
    # and the one in another city gets none.
    assert order[:3] == [a["id"], b["id"], c["id"]]
    assert order[3:] == order[:3]


def test_a_switched_off_partner_is_skipped(api, admin_headers, inbox, shop):
    a = _approve(api, admin_headers, "a@one.example")
    b = _approve(api, admin_headers, "b@two.example")
    api.post(f"/api/repair-partners/{a['id']}/status", headers=admin_headers,
             json={"active": False})
    assert _quote(api, shop).status_code == 201
    [lead] = STORE._db.all(repairs.LEAD_KIND)
    assert lead["partner_id"] == b["id"]


def test_nobody_covering_the_address_sends_nothing(api, shop, partner, inbox):
    resp = _quote(api, shop, postcode="94110", city="San Francisco")
    assert resp.status_code == 409 and "Nothing was sent" in resp.json()["detail"]
    assert STORE._db.all(repairs.LEAD_KIND) == [] and STORE._db.all(repairs.FEE_KIND) == []


def test_areas_can_be_changed_by_the_operator(api, admin_headers, partner):
    resp = api.post(f"/api/repair-partners/{partner['id']}/areas", headers=admin_headers,
                    json={"postcodes": ["941"], "cities": ["Oakland"]})
    assert resp.status_code == 200
    assert resp.json()["postcodes"] == ["941"] and resp.json()["cities"] == ["oakland"]
    empty = api.post(f"/api/repair-partners/{partner['id']}/areas", headers=admin_headers,
                     json={"postcodes": [], "cities": []})
    assert empty.status_code == 400


# ---- rate limits ------------------------------------------------------------


def test_one_lead_per_unit_every_three_days(api, shop, partner, inbox):
    assert _quote(api, shop).status_code == 201
    again = _quote(api, shop)
    assert again.status_code == 429 and "ask again after" in again.json()["detail"]
    card = next(s for s in api.get("/api/console/overview", headers=shop["headers"]).json()
                ["sensors"] if s["sensor_id"] == "HG-WALKIN")
    assert card["repair"]["offer"] is False and card["repair"]["sent_to"] == "Polar Fix Refrigeration"
    # Another unit is its own lead.
    assert _quote(api, shop, sensor_id="HG-CHEST").status_code == 201
    # Three days on, the same unit may go again.
    lead = next(r for r in STORE._db.all(repairs.LEAD_KIND) if r["sensor_id"] == "HG-WALKIN")
    lead["delivered_at"] = iso(utc_now() - timedelta(days=3, minutes=1))
    STORE._db.put(repairs.LEAD_KIND, lead["lead_id"], lead)
    assert _quote(api, shop).status_code == 201


def test_the_application_form_is_rate_limited(api, inbox):
    for n in range(3):
        assert _apply(api, contact_email=f"tech{n}@fix.example").status_code == 202
    refused = _apply(api, contact_email="tech9@fix.example")
    assert refused.status_code == 429 and refused.headers.get("Retry-After")


def test_a_viewer_cannot_send_a_lead(api, shop, partner):
    made = api.post("/api/accounts/users", headers=shop["headers"], json={
        "email": "v@harborgrill.example", "full_name": "Viewer",
        "password": "correct-horse-battery", "role": "viewer"})
    assert made.status_code in (200, 201), made.text
    token = api.post("/api/accounts/login", json={
        "email": "v@harborgrill.example", "password": "correct-horse-battery"}).json()["token"]
    resp = _quote(api, {"headers": {"Authorization": f"Bearer {token}"}})
    assert resp.status_code == 403


def test_a_business_cannot_ask_about_another_business_s_unit(api, shop, partner,
                                                             operator_factory):
    other, _, _ = operator_factory(company_name="Other", email="x@other.example")
    assert api.get("/api/repairs/quote/HG-WALKIN", headers=other).status_code == 404
    assert _quote(api, {"headers": other}).status_code == 404


# ---- applying and approving -------------------------------------------------


def test_an_application_waits_and_is_mailed_to_the_operator(api, inbox, caplog):
    with caplog.at_level(logging.INFO, logger="cyberlogix.repairs"):
        assert _apply(api).status_code == 202
    [row] = STORE._db.all(repairs.PARTNER_KIND)
    assert row["status"] == "applied" and not row["active"] and row["key_hash"] is None
    [mail] = inbox.to("operator@cyberlogix.example")
    text = mail.get_body(preferencelist=("plain",)).get_content()
    assert "Polar Fix Refrigeration" in mail["Subject"]
    assert "021, 022, Boston" in text and "/book" in text
    assert "jo@polarfix.example" not in caplog.text
    # Applying again says the same and files nothing new.
    assert _apply(api).status_code == 202
    assert len(STORE._db.all(repairs.PARTNER_KIND)) == 1


def test_approval_needs_an_area_and_mails_the_key_once(api, admin_headers, inbox):
    assert _apply(api).status_code == 202
    [row] = STORE._db.all(repairs.PARTNER_KIND)
    path = f"/api/repair-partners/{row['partner_id']}/approve"
    assert api.post(path, headers=admin_headers, json={}).status_code == 400
    ok = api.post(path, headers=admin_headers, json={"postcodes": ["021"]})
    assert ok.status_code == 200
    body = ok.json()
    assert body["key"].startswith("clx_rep_") and body["lead_fee_usd"] == 35.0
    [mail] = inbox.to("jo@polarfix.example")
    assert body["key"] in mail.get_body(preferencelist=("plain",)).get_content()
    assert api.post(path, headers=admin_headers, json={"postcodes": ["021"]}).status_code == 409
    listing = api.get("/api/repair-partners", headers=admin_headers).json()
    assert [p["partner_id"] for p in listing["partners"]] == [row["partner_id"]]
    assert "key_hash" not in str(listing)


def test_declining_sends_a_polite_note(api, admin_headers, inbox):
    assert _apply(api).status_code == 202
    [row] = STORE._db.all(repairs.PARTNER_KIND)
    resp = api.post(f"/api/repair-partners/{row['partner_id']}/decline", headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "declined"
    assert inbox.to("jo@polarfix.example")
    assert api.post(f"/api/repair-partners/{row['partner_id']}/decline",
                    headers=admin_headers).status_code == 409


def test_the_operator_routes_need_the_platform_key(api, partner):
    for method, path in (("get", "/api/repair-partners"),
                         ("post", f"/api/repair-partners/{partner['id']}/statement"),
                         ("post", f"/api/repair-partners/{partner['id']}/payments")):
        assert getattr(api, method)(path, headers=partner["headers"]).status_code in (401, 403)


# ---- fees and bad leads -----------------------------------------------------


def _leads(api, shop, count, partner_fee_ok=True):
    ids = []
    sensors = ["HG-WALKIN", "HG-CHEST"]
    for sensor_id in sensors[:count]:
        resp = _quote(api, shop, sensor_id=sensor_id)
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["lead_id"])
    return ids


def test_each_delivered_lead_is_owed_at_the_partner_s_fee(api, admin_headers, shop, inbox):
    p = _approve(api, admin_headers, "jo@polarfix.example", fee=42.5)
    _leads(api, shop, 2)
    money = repairs.ledger(p["id"])
    assert money == {"lead_count": 2, "charged_usd": 85.0, "in_question_usd": 0.0,
                     "refused_usd": 0.0, "paid_usd": 0.0, "owed_usd": 85.0,
                     "disputed_count": 0}


def test_a_bad_lead_refused_by_the_operator_is_not_charged(api, admin_headers, shop, partner,
                                                          inbox):
    first, second = _leads(api, shop, 2)
    resp = api.post(f"/api/repair-partners/me/leads/{first}/bad", headers=partner["headers"],
                    json={"reason": "The number did not work."})
    assert resp.status_code == 200 and resp.json()["status"] == "disputed"
    assert inbox.to("operator@cyberlogix.example")[-1]["Subject"].startswith(
        "Repair lead marked bad")
    assert repairs.ledger(partner["id"])["charged_usd"] == 35.0
    assert repairs.ledger(partner["id"])["in_question_usd"] == 35.0
    assert [d["lead_id"] for d in api.get("/api/repair-partners", headers=admin_headers)
            .json()["disputes"]] == [first]

    decided = api.post(f"/api/repair-partners/leads/{first}/decision", headers=admin_headers,
                       json={"refuse": True, "note": "Checked with the business."})
    assert decided.status_code == 200 and decided.json()["status"] == "refused"
    money = repairs.ledger(partner["id"])
    assert money["charged_usd"] == 35.0 and money["refused_usd"] == 35.0
    assert money["owed_usd"] == 35.0 and money["in_question_usd"] == 0.0

    # A charge the operator keeps is charged.
    api.post(f"/api/repair-partners/me/leads/{second}/bad", headers=partner["headers"],
             json={"reason": "Not a real job."})
    kept = api.post(f"/api/repair-partners/leads/{second}/decision", headers=admin_headers,
                    json={"refuse": False})
    assert kept.json()["status"] == "kept"
    # The refused one is not charged, the kept one is.
    assert repairs.ledger(partner["id"])["owed_usd"] == 35.0
    assert repairs.ledger(partner["id"])["refused_usd"] == 35.0
    # Decided is decided.
    assert api.post(f"/api/repair-partners/leads/{second}/decision", headers=admin_headers,
                    json={"refuse": True}).status_code == 409


def test_a_lead_can_be_marked_bad_only_for_seven_days_and_only_once(api, shop, partner, inbox):
    first, second = _leads(api, shop, 2)
    fee = STORE._db.get(repairs.FEE_KIND, first)
    fee["delivered_at"] = iso(utc_now() - timedelta(days=7, minutes=1))
    STORE._db.put(repairs.FEE_KIND, first, fee)
    late = api.post(f"/api/repair-partners/me/leads/{first}/bad", headers=partner["headers"],
                    json={"reason": "Too late now."})
    assert late.status_code == 409 and "7 days" in late.json()["detail"]
    board = api.get("/api/repair-partners/me", headers=partner["headers"]).json()
    states = {lead["lead_id"]: lead["can_mark_bad"] for lead in board["leads"]}
    assert states == {first: False, second: True}
    assert api.post(f"/api/repair-partners/me/leads/{second}/bad", headers=partner["headers"],
                    json={"reason": "No answer."}).status_code == 200
    assert api.post(f"/api/repair-partners/me/leads/{second}/bad", headers=partner["headers"],
                    json={"reason": "Again."}).status_code == 409


def test_a_partner_cannot_mark_another_partner_s_lead(api, admin_headers, shop, partner, inbox):
    [lead] = _leads(api, shop, 1)
    other = _approve(api, admin_headers, "z@other.example", postcodes=("941",))
    resp = api.post(f"/api/repair-partners/me/leads/{lead}/bad", headers=other["headers"],
                    json={"reason": "Not mine."})
    assert resp.status_code == 404


def test_payments_are_recorded_by_hand_and_the_statement_says_what_is_owed(
        api, admin_headers, shop, partner, inbox):
    _leads(api, shop, 2)
    paid = api.post(f"/api/repair-partners/{partner['id']}/payments", headers=admin_headers,
                    json={"amount_usd": 35.0, "reference": "TRANSFER-77"})
    assert paid.status_code == 201
    assert paid.json()["owed_usd"] == 35.0 and paid.json()["paid_usd"] == 35.0
    sent = api.post(f"/api/repair-partners/{partner['id']}/statement", headers=admin_headers)
    assert sent.status_code == 200 and sent.json()["mailed"]
    mail = inbox.to("jo@polarfix.example")[-1]
    text = mail.get_body(preferencelist=("plain",)).get_content()
    assert "$35.00 to pay" in mail["Subject"]
    assert "TRANSFER-77" in text and "Balance to pay: $35.00" in text
    assert "Harbor Grill" in text
    board = api.get("/api/repair-partners/me", headers=partner["headers"]).json()
    assert board["owed_usd"] == 35.0 and board["payments"][0]["reference"] == "TRANSFER-77"
    assert "automatically" in board["how_billing_works"]


def test_the_fee_setting_is_kept_with_each_partner(api, admin_headers, inbox, monkeypatch):
    first = _approve(api, admin_headers, "one@fix.example")
    monkeypatch.setattr(repairs, "DEFAULT_LEAD_FEE_USD", 50.0)
    second = _approve(api, admin_headers, "two@fix.example")
    assert repairs._partner(first["id"])["lead_fee_usd"] == 35.0
    assert repairs._partner(second["id"])["lead_fee_usd"] == 50.0


# ---- the pages --------------------------------------------------------------


def test_the_public_page_says_we_do_not_do_repairs_or_vouch(api):
    page = api.get("/repair-partners")
    assert page.status_code == 200
    text = page.text
    assert "$35 for each lead" in text and "within 7 days" in text
    assert "We do not do repairs ourselves" in text and "do not vouch" in text
    assert "/static/repairs.js" in text and "style=" not in text and "<script>" not in text


def test_the_dashboard_page_holds_no_key(api):
    page = api.get("/repair-partners/dashboard")
    assert page.status_code == 200
    assert "clx_rep_" not in page.text and "sessionStorage" not in page.text
    js = api.get("/static/repair-dashboard.js").text
    assert "sessionStorage." in js and "localStorage." not in js
