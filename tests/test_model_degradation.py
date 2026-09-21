"""Noticing when the model stops answering.

Every AI-authored message in this product degrades to a deterministic
template rather than failing, which is the right call: an alert worded
from a template still saves the freezer. It also means the failure is
silent, and one failure and ten thousand failures look identical from
outside.

That matters on a clock. Named models are retired on a schedule. When
this one is, every call raises, every caller quietly serves its
template, and the service keeps answering 200 -- a summariser that
stopped summarising, reporting itself healthy, because "ready" meant a
client object had been constructed and that stays true forever.
"""

import gemini


def _reset():
    gemini._consecutive_failures = 0
    gemini._last_error = None
    gemini._last_success_at = None


def test_a_healthy_model_reports_no_failures():
    _reset()
    gemini._note_success()

    status = gemini.dispatch_status()

    assert status["consecutive_failures"] == 0
    assert status["degraded"] is False
    assert status["last_error"] is None
    assert status["last_success_at"] is not None


def test_one_failure_is_not_a_retired_model():
    """A single timeout is a Tuesday, not a deprecation."""
    _reset()
    gemini._note_failure(TimeoutError("read timed out"))

    status = gemini.dispatch_status()

    assert status["consecutive_failures"] == 1
    assert status["degraded"] is False


def test_failing_every_time_is_visible_rather_than_silent():
    """The case this exists for: the model is gone and nothing says so."""
    _reset()
    for _ in range(gemini.FAILURES_BEFORE_DEGRADED):
        gemini._note_failure(ValueError("404 model not found"))

    status = gemini.dispatch_status()

    assert status["degraded"] is True
    assert "404 model not found" in status["last_error"]
    assert status["model"] == gemini.GEMINI_MODEL


def test_a_success_clears_the_run():
    """Degraded must be recoverable, or it is just a latch."""
    _reset()
    for _ in range(gemini.FAILURES_BEFORE_DEGRADED + 4):
        gemini._note_failure(ValueError("down"))
    assert gemini.dispatch_status()["degraded"] is True

    gemini._note_success()

    status = gemini.dispatch_status()
    assert status["degraded"] is False
    assert status["consecutive_failures"] == 0
    assert status["last_error"] is None


def test_the_error_is_recorded_but_not_a_whole_stack():
    """Enough to name the cause, bounded so a chatty error cannot grow."""
    _reset()
    gemini._note_failure(ValueError("x" * 5000))

    assert len(gemini.dispatch_status()["last_error"]) <= 300


def test_a_failing_call_counts_as_a_failure(monkeypatch):
    """Through safe_generate, not just the bookkeeping underneath it."""
    _reset()

    class Exploding:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("404 model not found")

    monkeypatch.setattr(gemini, "client", Exploding)

    text, source = gemini.safe_generate(
        "summarise this", "THE TEMPLATE", "test_purpose")

    # The caller is still served -- that is the whole design.
    assert text == "THE TEMPLATE"
    assert source == "fallback_template"
    # And the failure is now countable rather than only logged.
    assert gemini.dispatch_status()["consecutive_failures"] == 1


def test_health_reports_the_real_state(api):
    """/api/health said "ready" for a model that answers nothing."""
    body = api.get("/api/health").json()

    assert "ai" in body, "health does not report the model layer at all"
    for field in ("configured", "model", "degraded", "consecutive_failures"):
        assert field in body["ai"], field


def test_readiness_names_a_retired_model(monkeypatch):
    """So it surfaces where somebody is already looking, not in a log."""
    import readiness

    monkeypatch.setattr(gemini, "dispatch_status", lambda: {
        "configured": True, "model": "gemini-1.0-retired",
        "consecutive_failures": 9, "degraded": True,
        "last_error": "404 model not found", "last_success_at": None,
    })

    row = next(c for c in readiness.checks() if c["key"] == "ai_model")

    assert row["state"] == readiness.WARNS
    assert "gemini-1.0-retired" in row["detail"]
    assert "CYBERLOGIX_GEMINI_MODEL" in row["fix"]
