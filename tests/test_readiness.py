"""Whether this deployment is fit to be launched, and for what.

Everything here was already knowable -- mail status, the watchdog, the
backup directory, the billing identity -- and it was knowable in eight
different places, which on the morning you are trying to go live is the
same as not being knowable at all.

The design point is that it is two questions and not one. A deployment
that can monitor but cannot invoice is a perfectly good way to launch:
real customers, real sensors, real alerts, no money moving. Collapsing
that into a single "ready" flag would either block a launch that should
go ahead, or wave through one that takes money on a draft contract.
"""


import readiness

# Every module freezes its configuration at import, so setting an
# environment variable in a test describes a deployment that does not
# exist. These patch the same constants the running application reads,
# which is what a restart would actually produce.


def configure_money(monkeypatch):
    import auth
    import invoicing
    import mail

    monkeypatch.setattr(mail, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mail, "MAIL_FROM", "billing@example.com")
    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", "test-admin-key")
    # remit_to lives on the issuer, not on the mail module -- patching a
    # mail.REMIT_TO that nothing reads is how a test passes for the
    # wrong reason.
    monkeypatch.setitem(invoicing.ISSUER, "remit_to", "Acct 123, Sort 00-00")
    monkeypatch.setitem(invoicing.ISSUER, "legal_name", "CyberLogix AI Ltd")
    monkeypatch.setitem(invoicing.ISSUER, "address", "1 Example Street")
    monkeypatch.setitem(invoicing.ISSUER, "tax_id", "GB000000000")


def configure_delivery(monkeypatch):
    import notifications

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")


def unconfigure_delivery(monkeypatch):
    import notifications

    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                 "TWILIO_FROM_NUMBER"):
        monkeypatch.setattr(notifications, name, "")


def _state(report, key):
    return [c for c in report["checks"] if c["key"] == key][0]["state"]


def test_an_unconfigured_deployment_cannot_monitor(monkeypatch):
    """The default. Alerts are composed, logged, and sent nowhere."""
    unconfigure_delivery(monkeypatch)

    r = readiness.report()

    assert r["can_monitor"] is False
    assert _state(r, "twilio") == readiness.BLOCKS


def test_delivery_alone_makes_it_fit_to_monitor(monkeypatch):
    """And that is a launch: real customers, real alerts, no money."""
    import licenses

    configure_delivery(monkeypatch)
    monkeypatch.setattr(licenses, "PROVISIONING_KEY", "")

    r = readiness.report()

    assert r["can_monitor"] is True
    assert r["can_take_money"] is False
    assert r["paid_accounts_possible"] is False
    assert "trials only" in r["shape"]


def test_the_draft_agreements_block_taking_money(monkeypatch):
    """The documents say so themselves, and a customer is now asked to
    accept them by hash -- which makes the acceptance a real record of
    a draft rather than a placeholder nobody signed."""
    configure_delivery(monkeypatch)
    configure_money(monkeypatch)

    r = readiness.report()

    assert _state(r, "terms") == readiness.BLOCKS
    assert r["can_take_money"] is False
    fix = [c for c in r["checks"] if c["key"] == "terms"][0]["fix"]
    assert "lawyer" in fix


def test_reviewing_the_agreements_is_the_last_gate(monkeypatch):
    """With everything else set, the lawyer is what is left."""
    import legal

    configure_delivery(monkeypatch)
    configure_money(monkeypatch)
    monkeypatch.setattr(legal, "DISCLAIMER", "Reviewed.")

    r = readiness.report()

    assert r["can_take_money"] is True
    assert "open for business" in r["shape"]


def test_a_missing_invoice_identity_blocks_money(monkeypatch):
    """A finance department cannot pay an invoice that does not say who
    to pay, at what address, under what tax registration."""
    import invoicing

    configure_delivery(monkeypatch)
    configure_money(monkeypatch)
    monkeypatch.setitem(invoicing.ISSUER, "tax_id", "")

    r = readiness.report()

    assert _state(r, "identity") == readiness.BLOCKS
    assert "CYBERLOGIX_TAX_ID" in [
        c for c in r["checks"] if c["key"] == "identity"][0]["detail"]


def test_the_unattended_promise_is_its_own_question(monkeypatch):
    """The product's promise is that something runs when nobody is
    looking. Nothing inside a process can report its own death."""
    import watchdog

    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "", raising=False)

    r = readiness.report()

    assert r["runs_unattended"] is False
    assert _state(r, "heartbeat") == readiness.BLOCKS


def test_every_blocking_check_says_how_to_fix_it():
    """A checklist that tells you something is wrong and not what to do
    about it is a checklist you read once."""
    for check in readiness.checks():
        if check["state"] != readiness.OK:
            assert check["fix"], f"{check['key']} has no remedy"


def test_the_endpoint_needs_the_platform_key(api, admin_headers):
    assert api.get("/api/admin/readiness").status_code == 401
    ok = api.get("/api/admin/readiness", headers=admin_headers)
    assert ok.status_code == 200
    assert "shape" in ok.json()


def test_the_cli_exits_nonzero_while_anything_blocks(monkeypatch, capsys):
    """So it can gate a deploy script rather than be read by eye."""
    unconfigure_delivery(monkeypatch)

    assert readiness._cli() == 1
    printed = capsys.readouterr().out
    assert "BLOCKS" in printed
    assert "Launch shape" in printed
