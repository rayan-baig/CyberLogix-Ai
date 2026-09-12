"""Getting back in, without an owner to ask.

A reset could only be issued by an owner — which is precisely the person
who cannot ask for one when it is their own password that is gone. A
single-owner account whose owner forgot their password was unreachable
forever, and every one of those is a customer lost for a reason that has
nothing to do with the product.

Three things this endpoint must not become, in order of how much they
would cost:

  * a way to find out who our customers are, one address at a time;
  * a way to mail-bomb somebody who has an account with us;
  * a way to take an account over, which is what a reset link that
    outlives its use or leaks through a Referer header amounts to.
"""

import re

import pytest

import accounts
from store import STORE


@pytest.fixture(autouse=True)
def clean_forgot_window():
    accounts.reset_rate_limits()
    yield
    accounts.reset_rate_limits()


@pytest.fixture()
def locked_out(api, tenant_factory):
    """An owner who cannot sign in and has nobody to ask."""
    headers, tenant = tenant_factory(plan="growth")
    made = api.post(
        "/api/accounts/bootstrap",
        headers=headers,
        json={
            "email": "dana@northgate.example",
            "full_name": "Dana Reyes",
            "password": "correct-horse-battery",
            "role": "owner",
        },
    )
    assert made.status_code == 201, made.text
    return tenant, "dana@northgate.example"


def _link(mailbox):
    body = mailbox[-1].get_content()
    found = re.search(r"/reset\?token=(clr_[A-Za-z0-9_-]+)", body)
    assert found, f"no reset link in:\n{body}"
    return found.group(1)


# ---- it works -----------------------------------------------------------


def test_a_locked_out_owner_can_let_themselves_back_in(api, mailbox, locked_out):
    _, email = locked_out

    asked = api.post("/api/accounts/forgot", json={"email": email})
    assert asked.status_code == 200

    token = _link(mailbox)
    done = api.post(
        "/api/accounts/reset",
        json={"token": token, "new_password": "a-whole-new-passphrase"},
    )
    assert done.status_code == 200

    signed_in = api.post(
        "/api/accounts/login",
        json={"email": email, "password": "a-whole-new-passphrase"},
    )
    assert signed_in.status_code == 200


def test_the_link_works_once(api, mailbox, locked_out):
    _, email = locked_out
    api.post("/api/accounts/forgot", json={"email": email})
    token = _link(mailbox)

    first = api.post(
        "/api/accounts/reset",
        json={"token": token, "new_password": "a-whole-new-passphrase"},
    )
    second = api.post(
        "/api/accounts/reset",
        json={"token": token, "new_password": "somebody-elses-choice"},
    )

    assert first.status_code == 200
    assert second.status_code == 400


def test_asking_twice_produces_two_working_links(api, mailbox, locked_out):
    """One email and then silence is indistinguishable from being ignored.

    Somebody who does not see the first message asks again, and the
    second attempt must not be swallowed by the send deduplication.
    """
    _, email = locked_out

    api.post("/api/accounts/forgot", json={"email": email})
    first = _link(mailbox)
    api.post("/api/accounts/forgot", json={"email": email})
    second = _link(mailbox)

    assert first != second
    assert len(mailbox) == 2


# ---- what it must not leak ---------------------------------------------


def test_an_unknown_address_gets_the_same_answer(api, mailbox, locked_out):
    """Otherwise the endpoint is a customer list with a free guess."""
    _, email = locked_out

    known = api.post("/api/accounts/forgot", json={"email": email})
    unknown = api.post(
        "/api/accounts/forgot", json={"email": "nobody@example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mailbox) == 1  # and only the real one was written to


def test_a_disabled_account_gets_the_same_answer_and_no_link(
    api, mailbox, locked_out
):
    """Disabling somebody must not be reversible by the person disabled."""
    _, email = locked_out
    user = STORE.user_by_email(email)
    STORE.set_user_disabled(user, True)

    resp = api.post("/api/accounts/forgot", json={"email": email})

    assert resp.status_code == 200
    assert mailbox == []


def test_being_rate_limited_looks_exactly_like_not_being(
    api, mailbox, locked_out
):
    """A 429 for one address and a 200 for another answers the question
    the response is otherwise careful not to."""
    _, email = locked_out

    seen = []
    for _ in range(accounts.FORGOT_PER_ADDRESS_PER_HOUR + 3):
        resp = api.post("/api/accounts/forgot", json={"email": email})
        seen.append((resp.status_code, resp.json()))

    assert len({str(s) for s in seen}) == 1
    assert len(mailbox) == accounts.FORGOT_PER_ADDRESS_PER_HOUR


def test_the_limit_is_per_address_not_global(api, mailbox, api_second_tenant):
    """One customer asking three times must not lock out the next."""
    first, second = api_second_tenant

    for _ in range(accounts.FORGOT_PER_ADDRESS_PER_HOUR + 2):
        api.post("/api/accounts/forgot", json={"email": first})
    api.post("/api/accounts/forgot", json={"email": second})

    recipients = {m["To"] for m in mailbox}
    assert second in recipients


@pytest.fixture()
def api_second_tenant(api, tenant_factory, locked_out):
    _, first = locked_out
    headers, _ = tenant_factory(plan="growth", company_name="Harbour Cold")
    api.post(
        "/api/accounts/bootstrap",
        headers=headers,
        json={
            "email": "sam@harbour.example",
            "full_name": "Sam Okafor",
            "password": "correct-horse-battery",
            "role": "owner",
        },
    )
    return first, "sam@harbour.example"


def test_the_endpoint_refuses_anything_but_an_address(api, mailbox, locked_out):
    """No second field to probe, and no second field to get wrong."""
    _, email = locked_out
    resp = api.post(
        "/api/accounts/forgot",
        json={"email": email, "tenant_id": "TEN-000001"},
    )
    assert resp.status_code == 422


# ---- the mail itself ----------------------------------------------------


def test_the_email_says_what_using_it_does(api, mailbox, locked_out):
    """Signing out every other session is the point, if it was not you."""
    _, email = locked_out
    api.post("/api/accounts/forgot", json={"email": email})

    body = mailbox[-1].get_content()
    assert "signs out every other session" in body
    assert "if this was not you" in body


def test_a_reset_is_transactional_and_survives_an_unsubscribe(
    api, mailbox, locked_out
):
    """Declining marketing must not lock somebody out of their account."""
    _, email = locked_out
    STORE.suppress_address(email, "unsubscribed")

    api.post("/api/accounts/forgot", json={"email": email})

    assert len(mailbox) == 1


def test_the_request_is_written_into_the_audit_trail(api, mailbox, locked_out):
    tenant, email = locked_out
    api.post("/api/accounts/forgot", json={"email": email})

    actions = [e.action for e in STORE.audit_for(tenant["tenant_id"])]
    assert "account.reset_requested" in actions


# ---- the page -----------------------------------------------------------


def test_the_reset_page_is_served_and_kept_out_of_search(api):
    page = api.get("/reset")
    assert page.status_code == 200
    assert 'name="robots" content="noindex"' in page.text


def test_the_page_scrubs_the_token_out_of_the_address_bar(api):
    """A one-time credential in the URL is one shoulder, one screenshot or
    one Referer header away from being somebody else's."""
    js = api.get("/reset").text
    assert "history.replaceState" in js


def test_the_console_offers_the_way_out(api):
    """The link cannot live behind the form somebody cannot get past."""
    assert 'href="/reset"' in api.get("/console").text
