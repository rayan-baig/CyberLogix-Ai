"""A customer who agreed to the old terms and never saw the new ones.

Sign-up records an acceptance, pinning the SHA-256 of each document, and
`/api/legal/acceptance/status` already knew how to spot a stale one. But
it answered about one tenant, behind that tenant's own credential, and
nothing ever asked it. So a terms change -- this repository has had one,
v1.0 to v1.1 -- silently left the entire book agreed to something that
is no longer the agreement, and the only way to find out was to log in
as each customer in turn.

`record_acceptance` even promises the recovery: "the acceptance can be
taken again from the console". No console offered it.

For a company that intends to be sold, "which of your customers has
accepted your current terms" is one question from a buyer's lawyer, and
the answer has to be a list rather than a shrug.
"""

import pytest

import legal


@pytest.fixture()
def signed_up(api):
    """A customer who accepted the terms as they stood on the way in."""
    resp = api.post("/api/signup", json={
        "company_name": "Bell Street Diner",
        "full_name": "Ann Petrou",
        "email": "ann@bellstreet.example",
        "password": "correct-horse-battery",
        "contact_phone": "+1-555-0133",
        "industry_vertical": "restaurant",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_fresh_signup_is_current(api, signed_up, admin_headers):
    """The premise: nothing is outstanding until a document changes."""
    body = api.get("/api/legal/acceptance/outstanding",
                   headers=admin_headers).json()

    assert body["count"] == 0, body["outstanding"]


def test_changing_a_document_puts_every_customer_on_the_list(
    api, signed_up, admin_headers, monkeypatch
):
    """The bug. One edit to the terms and the whole book is stale, and
    before this endpoint existed nothing anywhere said so."""
    original = legal.DOCUMENTS["terms"]
    monkeypatch.setitem(
        legal.DOCUMENTS, "terms",
        (original[0], lambda: original[1]() + "\n\nA new clause.\n"),
    )

    body = api.get("/api/legal/acceptance/outstanding",
                   headers=admin_headers).json()

    assert body["count"] == 1
    row = body["outstanding"][0]
    assert row["company_name"] == "Bell Street Diner"
    assert "terms" in row["out_of_date"]
    assert row["never_accepted"] == []
    # And the operator can act on it, which means knowing who to write to.
    assert row["contact_email"] == "ann@bellstreet.example"


def test_an_account_that_never_accepted_is_distinguished(
    api, tenant_factory, admin_headers
):
    """Provisioned by the operator rather than signed up: nobody ever
    clicked anything. That is a different conversation from a customer
    who agreed to an older version, so it is a different field."""
    tenant_factory(plan="enterprise", company_name="Harbor Cold Store")

    body = api.get("/api/legal/acceptance/outstanding",
                   headers=admin_headers).json()

    row = [r for r in body["outstanding"]
           if r["company_name"] == "Harbor Cold Store"][0]
    assert row["never_accepted"], "never accepted anything"
    assert row["out_of_date"] == []


def test_the_customer_can_see_it_too(api, signed_up, monkeypatch):
    """Per-tenant staleness must keep working: it is what the customer's
    own banner is driven from."""
    original = legal.DOCUMENTS["privacy"]
    monkeypatch.setitem(
        legal.DOCUMENTS, "privacy",
        (original[0], lambda: original[1]() + "\n\nA new paragraph.\n"),
    )
    headers = {"Authorization": f"Bearer {signed_up['token']}"}

    body = api.get("/api/legal/acceptance/status", headers=headers).json()

    assert body["all_current"] is False
    stale = [d for d in body["documents"] if d["accepted"] and not d["current"]]
    assert [d["slug"] for d in stale] == ["privacy"]


def test_the_fleet_view_needs_the_platform_key(api):
    """It lists every customer's contact details."""
    assert api.get("/api/legal/acceptance/outstanding").status_code == 401


def test_accepting_again_clears_it(api, signed_up, admin_headers, monkeypatch):
    """The whole point of raising it is that it can be put right."""
    original = legal.DOCUMENTS["terms"]
    monkeypatch.setitem(
        legal.DOCUMENTS, "terms",
        (original[0], lambda: original[1]() + "\n\nA new clause.\n"),
    )
    headers = {"Authorization": f"Bearer {signed_up['token']}"}
    assert api.get("/api/legal/acceptance/outstanding",
                   headers=admin_headers).json()["count"] == 1

    again = api.post("/api/legal/accept", headers=headers, json={
        "documents": list(legal.DOCUMENTS),
        "accepted_by": "Ann Petrou",
        "title": "Owner",
    })
    assert again.status_code == 201, again.text

    assert api.get("/api/legal/acceptance/outstanding",
                   headers=admin_headers).json()["count"] == 0


# --- the surfaces that ask ----------------------------------------------

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_the_customer_console_asks_and_the_operator_console_counts():
    """Both halves. The customer needs somewhere to accept; the operator
    needs to know who has not, because one account at a time it is
    invisible and fleet-wide it is one question from a buyer's lawyer."""
    console = (ROOT / "static" / "console.html").read_text()
    book = (ROOT / "static" / "book.html").read_text()

    assert "/api/legal/acceptance/status" in console
    assert "/api/legal/accept" in console
    assert 'id="terms-banner"' in console
    assert "/api/legal/acceptance/outstanding" in book


def test_only_an_owner_is_offered_the_button():
    """A record attested by a viewer is not the record the terms
    describe -- the server enforces it, and the console should not
    present a control that will be refused."""
    console = (ROOT / "static" / "console.html").read_text()
    js = console[console.index("<script>"):console.rindex("</script>")]
    block = js[js.index("async function checkTerms()"):]
    block = block[:block.index("async function acceptTerms()")]

    assert 'role === "owner"' in block
    assert '$("terms-accept").hidden = !owner' in block


def test_the_documents_are_opened_before_they_are_accepted():
    """A one-click accept on text nobody was shown is worth less the
    moment it is questioned."""
    console = (ROOT / "static" / "console.html").read_text()
    js = console[console.index("<script>"):console.rindex("</script>")]
    block = js[js.index("async function acceptTerms()"):]
    block = block[:block.index("async function api(")]

    assert 'window.open("/legal"' in block
    assert "window.confirm(" in block
    # The POST specifically -- "/api/legal/acceptance/status" is read at
    # the top of the same function and shares the prefix.
    post = 'api("/api/legal/accept", {'
    assert post in block
    assert block.index('window.open("/legal"') < block.index(post)
    assert block.index("window.confirm(") < block.index(post)


def test_the_console_offers_a_password_change(api, signed_up):
    """The endpoint has always existed and nothing called it. Somebody
    who thinks their password is known had one route -- the forgotten-
    password email, which needs mail configured and does not help if
    they are already signed in on a shared machine."""
    console = (ROOT / "static" / "console.html").read_text()
    assert 'id="password-form"' in console
    assert "/api/accounts/me/password" in console

    headers = {"Authorization": f"Bearer {signed_up['token']}"}
    wrong = api.post("/api/accounts/me/password", headers=headers, json={
        "current_password": "not-it", "new_password": "a-fresh-passphrase",
    })
    assert wrong.status_code == 403

    right = api.post("/api/accounts/me/password", headers=headers, json={
        "current_password": "correct-horse-battery",
        "new_password": "a-fresh-passphrase",
    })
    assert right.status_code == 200, right.text

    again = api.post("/api/accounts/login", json={
        "email": "ann@bellstreet.example", "password": "a-fresh-passphrase",
    })
    assert again.status_code == 200, again.text
