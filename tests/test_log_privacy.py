"""Server logs never carry a customer's full phone number or email address."""

import logging

import notifications


def test_number_tail_keeps_only_the_last_four_digits():
    assert notifications.number_tail("+1 (555) 010-9876") == "...9876"
    assert notifications.number_tail("") == "(no number)"
    assert notifications.number_tail(None) == "(no number)"


def test_a_failed_text_logs_no_full_number(monkeypatch, caplog):
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError(f"Unable to create record: {kwargs['to']} is not a valid phone number")

    monkeypatch.setattr(notifications, "_get_client", lambda: Boom())
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550100000", raising=False)
    with caplog.at_level(logging.INFO, logger="cyberlogix.notifications"):
        notifications.send_sms("+15550109876", "Freezer 2 is too warm")
    text = caplog.text
    assert "...9876" in text, text
    assert "5550109876" not in text and "+15550109876" not in text



def test_a_refused_password_reset_logs_no_full_address(api, caplog):
    import accounts

    accounts.reset_rate_limits()
    with caplog.at_level(logging.INFO, logger="cyberlogix.accounts"):
        for _ in range(accounts.FORGOT_PER_ADDRESS_PER_HOUR + 1):
            api.post("/api/accounts/forgot", json={"email": "someone@limited.example"})
    accounts.reset_rate_limits()
    assert "refused" in caplog.text
    assert "someone@limited.example" not in caplog.text
