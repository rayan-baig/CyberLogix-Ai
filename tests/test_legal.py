"""The agreement has to say what the code does.

A document that promises something the product does not do is a promise a
customer can enforce. A document that omits a protection the product needs
is protection given away. Both happen the same way: someone edits a
constant and nobody edits the prose.

These tests move the constants and check the prose moves with them.
"""

import pytest

import assurance
import invoicing
import legal


def _flat(text: str) -> str:
    """One line, so a content assertion is not defeated by line wrapping."""
    return " ".join(text.split())


def test_every_document_renders_and_is_hashed(api):
    body = api.get("/api/legal").json()
    assert {d["slug"] for d in body["documents"]} == set(legal.DOCUMENTS)
    for row in body["documents"]:
        doc = api.get(f"/api/legal/{row['slug']}").json()
        assert doc["sha256"] == row["sha256"]
        assert len(doc["body_markdown"]) > 500
        assert doc["draft"] is True


def test_terms_are_readable_without_an_account(api):
    """Terms you cannot read before signing up are a surprise, not terms."""
    assert api.get("/api/legal").status_code == 200
    assert api.get("/api/legal/terms").status_code == 200


def test_unknown_document_is_a_404_that_lists_the_real_ones(api):
    resp = api.get("/api/legal/refund-policy")
    assert resp.status_code == 404
    assert "terms" in resp.json()["detail"]


# ---- the documents cannot drift from the code --------------------------


def test_the_payout_cap_in_the_terms_is_the_one_the_code_pays(monkeypatch):
    monkeypatch.setattr(assurance, "ASSURANCE_PAYOUT_CAP_USD", 25000.0)
    monkeypatch.setattr(legal, "ASSURANCE_PAYOUT_CAP_USD", 25000.0)
    assert "$25,000" in legal.assurance_terms()

    monkeypatch.setattr(legal, "ASSURANCE_PAYOUT_CAP_USD", 60000.0)
    text = legal.assurance_terms()
    assert "$60,000" in text
    assert "$25,000" not in text, (
        "The cap moved in the code and the agreement still quotes the old "
        "figure — which is the number a customer would hold us to."
    )


def test_the_dispatch_commitment_matches_the_sla_constant(monkeypatch):
    monkeypatch.setattr(legal, "DISPATCH_SLA_SECONDS", 90)
    assert "within 90 seconds" in _flat(legal.terms_of_service())
    assert "90 seconds" in _flat(legal.service_level())


def test_payment_terms_and_late_charge_come_from_the_billing_code(monkeypatch):
    monkeypatch.setattr(legal, "PAYMENT_TERMS_DAYS", 45)
    monkeypatch.setattr(legal, "LATE_FEE_MONTHLY_PERCENT", 2.0)
    text = _flat(legal.terms_of_service())
    assert "net 45 days" in text
    assert "2% per month" in text


def test_the_exclusions_listed_are_the_ones_the_code_applies(monkeypatch):
    """Every voiding condition in `evaluate_cover` appears in the terms."""
    text = _flat(legal.assurance_terms()).lower()
    for phrase in (
        "has not reported",
        "battery is below",
        "never reported a reading",
        "nobody is on the on-call roster",
        "trial rather than a paid plan",
    ):
        assert phrase in text, f"'{phrase}' is enforced in code but not disclosed."

    monkeypatch.setattr(legal, "COVER_LAPSES_AFTER_MINUTES", 120)
    assert "120 minutes" in legal.assurance_terms()


def test_the_delinquency_threshold_is_disclosed(monkeypatch):
    monkeypatch.setattr(legal, "DELINQUENT_AFTER_DAYS", 90)
    for text in (legal.terms_of_service(), legal.acceptable_use()):
        assert "90 days" in _flat(text)


def test_the_hash_moves_when_the_product_does(monkeypatch):
    before = legal.document("assurance")["sha256"]
    monkeypatch.setattr(legal, "ASSURANCE_PAYOUT_CAP_USD", 99000.0)
    after = legal.document("assurance")["sha256"]
    assert before != after, (
        "The terms changed and the hash did not, so a customer could not "
        "tell they had been re-priced."
    )


def test_the_issuer_on_the_agreement_is_the_one_on_the_invoice(monkeypatch):
    monkeypatch.setitem(invoicing.ISSUER, "legal_name", "CyberLogix AI, Inc.")
    assert "CyberLogix AI, Inc." in legal.terms_of_service()


# ---- the clauses that protect the company ------------------------------


def test_the_liability_cap_is_actually_in_the_agreement():
    text = _flat(legal.terms_of_service())
    assert "Limitation of liability" in text
    assert f"{legal.LIABILITY_CAP_MONTHS} months" in text
    assert "lost profits" in text
    assert "consequential" in text


def test_loss_assurance_sits_outside_the_general_cap():
    """Burying the guarantee under the cap would make it worthless."""
    terms = _flat(legal.terms_of_service())
    assurance_text = _flat(legal.assurance_terms())
    assert "Except for the Loss Assurance add-on" in terms
    assert "outside the general limitation of liability" in assurance_text


def test_the_terms_say_it_is_not_a_safety_system():
    text = _flat(legal.terms_of_service())
    assert "not a substitute" in text
    assert "safety instrumented system" in text
    assert "medical device" in text


def test_the_terms_promise_alerting_survives_non_payment():
    """The clause the whole company's reputation rests on."""
    for text in (legal.terms_of_service(), legal.acceptable_use()):
        low = _flat(text).lower()
        assert "never withheld for non-payment" in low or (
            "monitoring and alerting continue" in low
        )


# Every third party that receives customer data, and what reaches them.
# This list is the reviewed one: a name here has been thought about. The
# test below fails when the table gains or loses a row, which is the
# point — the privacy statement went a whole release naming two
# sub-processors while the mail host was reading every invoice, every
# contact address and every weekly report, because nothing structural
# was watching the table.
DISCLOSED_SUB_PROCESSORS = {
    "Twilio": "the mobile number and the text of the alert",
    "Google (Gemini API)": "the wording of an alert being drafted",
    "Our mail host": "the recipient's address and the whole message",
}


def _sub_processor_rows():
    """The body rows of the sub-processor table, as (name, what, why)."""
    rows = []
    inside = False
    for line in legal.privacy_statement().splitlines():
        if line.startswith("| Sub-processor"):
            inside = True
            continue
        if inside:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue  # the header rule
            rows.append(cells)
    return rows


def test_every_sub_processor_is_disclosed_and_no_more():
    """A new third party with customer data must reach this document.

    Structural rather than a list of names to grep for: the failure this
    replaces was a table that stayed accurate about what it said and
    silently stopped being complete.
    """
    named = [row[0] for row in _sub_processor_rows()]

    assert named, "the privacy statement has no sub-processor table"
    assert set(named) == set(DISCLOSED_SUB_PROCESSORS), (
        "the sub-processor table and the reviewed list disagree. If a new "
        "third party now receives customer data, disclose it in "
        "privacy_statement() and add it here; if one is gone, remove both."
    )
    for row in _sub_processor_rows():
        assert len(row) == 3 and all(row), (
            f"a sub-processor row does not say what reaches them and why: {row}"
        )


def test_the_mail_host_disclosure_says_what_it_actually_gets():
    """Not "email" — an invoice is what you owe, and a weekly report is
    how much of your estate stopped reporting."""
    text = _flat(legal.privacy_statement())
    assert "what you owe" in text
    assert "which units went quiet" in text


def test_backups_do_not_quietly_contradict_the_deletion_promise():
    """"Ask us to delete and we delete everything" is untrue of any
    system that can survive losing a disk — which is every system worth
    trusting with a compliance record."""
    from backup import BACKUP_KEEP

    text = _flat(legal.privacy_statement())
    assert "snapshot" in text
    assert f"keep the last {BACKUP_KEEP}" in text
    assert f"gone within {BACKUP_KEEP} days" in text
    assert "do not restore a backup to bring back" in text


def test_the_privacy_statement_still_refuses_the_obvious_things():
    text = _flat(legal.privacy_statement())
    assert "do not sell" in text
    assert "do not use it to train anything" in text


def test_every_document_is_marked_a_draft():
    """An 11-year-old's AI-written contract is not a reviewed contract."""
    for slug in legal.DOCUMENTS:
        doc = legal.document(slug)
        assert doc["draft"] is True
        assert "not reviewed by a lawyer" in doc["body_markdown"]


# ---- acceptance --------------------------------------------------------


def test_acceptance_records_the_exact_text(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    resp = api.post(
        "/api/legal/accept",
        headers={**headers, **owner},
        json={"accepted_by": "Dana Reyes", "title": "COO"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["documents"]["terms"] == legal.document("terms")["sha256"]

    status_body = api.get("/api/legal/acceptance/status", headers=headers).json()
    assert status_body["all_current"] is True
    assert all(r["accepted"] for r in status_body["documents"])


def test_a_stale_acceptance_stops_being_current(
    api, tenant_factory, owner_headers, monkeypatch
):
    """If the cap moved after they signed, ask again rather than assume."""
    headers, _ = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    api.post(
        "/api/legal/accept",
        headers={**headers, **owner},
        json={"accepted_by": "Dana"},
    )
    assert api.get(
        "/api/legal/acceptance/status", headers=headers
    ).json()["all_current"] is True

    monkeypatch.setattr(legal, "ASSURANCE_PAYOUT_CAP_USD", 5000.0)
    body = api.get("/api/legal/acceptance/status", headers=headers).json()
    assert body["all_current"] is False
    stale = [r for r in body["documents"] if not r["current"]]
    assert [r["slug"] for r in stale] == ["assurance"]


def test_acceptance_needs_a_credential(api):
    assert api.post(
        "/api/legal/accept", json={"accepted_by": "Nobody"}
    ).status_code == 401


def test_a_machine_key_cannot_sign_a_contract(api, tenant_factory):
    """A contract attested by "API key" is attested by nobody."""
    headers, _ = tenant_factory(plan="enterprise")
    resp = api.post(
        "/api/legal/accept", headers=headers, json={"accepted_by": "A Robot"}
    )
    assert resp.status_code in (401, 403)


def test_accepting_an_unknown_document_is_refused(api, tenant_factory,
                                                 owner_headers):
    headers, _ = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    resp = api.post(
        "/api/legal/accept",
        headers={**headers, **owner},
        json={"accepted_by": "Dana", "documents": ["terms", "made-up"]},
    )
    assert resp.status_code == 400


def test_acceptance_is_in_the_audit_log(api, tenant_factory, owner_headers):
    headers, _ = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    api.post(
        "/api/legal/accept",
        headers={**headers, **owner},
        json={"accepted_by": "Dana Reyes", "title": "COO"},
    )
    audit = api.get("/api/accounts/audit", headers={**headers, **owner}).json()
    accepted = [e for e in audit["entries"] if e["action"] == "legal.accepted"]
    assert len(accepted) == 1
    assert "Dana Reyes (COO)" in accepted[0]["detail"]
    assert accepted[0]["actor_role"] == "owner"


def test_nobody_elses_acceptance_shows_on_this_account(
    api, tenant_factory, owner_headers
):
    first, _ = tenant_factory(company_name="First Co")
    second, _ = tenant_factory(company_name="Second Co")
    first_owner = owner_headers(first, email="a@first.example")
    api.post(
        "/api/legal/accept",
        headers={**first, **first_owner},
        json={"accepted_by": "A"},
    )
    body = api.get("/api/legal/acceptance/status", headers=second).json()
    assert body["all_current"] is False
    assert not any(r["accepted"] for r in body["documents"])


@pytest.mark.parametrize("slug", sorted(legal.DOCUMENTS))
def test_no_document_contains_an_unrendered_placeholder(slug):
    """A stray brace in an f-string ships '{cap}' to a customer."""
    body = legal.document(slug)["body_markdown"]
    assert "{" not in body and "}" not in body
    assert "None" not in body
