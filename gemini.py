"""Shared Google GenAI client with fail-open generation.

Every AI-authored message in the suite is safety-critical: an alert that is
worded poorly still saves the asset, but an alert that never fires does not.
`safe_generate` therefore never raises. It returns the model's text when the
call succeeds and a caller-supplied deterministic fallback when it does not,
alongside the source so the API response can say which was used.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from google import genai

from store import STORE

logger = logging.getLogger("cyberlogix.gemini")

GEMINI_MODEL = os.environ.get("CYBERLOGIX_GEMINI_MODEL", "gemini-2.5-flash")

# Falling back is normal once. Falling back every time is a different
# thing wearing the same clothes, and the difference is only visible in
# a count.
#
# Named models are retired on a schedule. When this one is, every call
# below raises, every caller gets its deterministic template, and the
# product keeps answering 200 while quietly getting worse at its job --
# a summariser that stopped summarising and a console that still said
# "ready", because readiness meant "the client object was constructed",
# which stays true forever after the model behind it is gone.
FAILURES_BEFORE_DEGRADED = 3

_health_lock = threading.Lock()
_consecutive_failures = 0
_last_error: Optional[str] = None
_last_success_at: Optional[datetime] = None


def _note_success() -> None:
    global _consecutive_failures, _last_error, _last_success_at
    with _health_lock:
        _consecutive_failures = 0
        _last_error = None
        _last_success_at = datetime.now(timezone.utc)


def _note_failure(exc: BaseException) -> None:
    global _consecutive_failures, _last_error
    with _health_lock:
        _consecutive_failures += 1
        _last_error = f"{type(exc).__name__}: {exc}"[:300]
        count = _consecutive_failures
    if count == FAILURES_BEFORE_DEGRADED:
        logger.error(
            "Generation has failed %d times in a row against model %r. "
            "Every AI-authored message is now a deterministic template. "
            "If the model has been retired, set CYBERLOGIX_GEMINI_MODEL "
            "to a current one.",
            count, GEMINI_MODEL,
        )


def dispatch_status() -> Dict[str, Any]:
    """What the model layer is actually doing, not merely configured to do.

    `degraded` is the field worth alarming on: it means calls are being
    made and failing, which no amount of "configured: true" reveals.
    """
    with _health_lock:
        failures, error = _consecutive_failures, _last_error
        since = _last_success_at
    return {
        "configured": client is not None,
        "model": GEMINI_MODEL,
        "consecutive_failures": failures,
        "degraded": failures >= FAILURES_BEFORE_DEGRADED,
        "last_error": error,
        "last_success_at": since.isoformat() if since else None,
    }

try:
    client = genai.Client()
    logger.info("Google GenAI client initialized (model=%s).", GEMINI_MODEL)
except Exception as exc:  # noqa: BLE001 - startup must never hard-fail
    client = None
    logger.warning(
        "Google GenAI client unavailable (%s). All generated copy will use "
        "deterministic fallback templates.",
        exc,
    )


def dispatch_ready() -> bool:
    """True when the Gemini client initialized successfully."""
    return client is not None


def safe_generate(
    prompt: str,
    fallback: str,
    purpose: str,
    tenant_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Generate text, degrading to `fallback` instead of raising.

    Returns a (text, source) pair where source is "gemini", "cache" or
    "fallback_template". Passing `tenant_id` meters the call and subjects it
    to that tenant's daily budget; omitting it skips both.
    """
    # Imported here because costs.py depends on the store, which must not be
    # pulled in at gemini import time.
    from costs import allow_ai_call, cache_key, record

    key = cache_key(prompt, purpose)
    cached = STORE.cache_get(key)
    if cached is not None:
        record(tenant_id, "ai_cache_hits")
        logger.info("Cache hit for %s; no model call made.", purpose)
        return cached, "cache"

    allowed, reason = allow_ai_call(tenant_id)
    if not allowed:
        record(tenant_id, "ai_suppressed")
        logger.warning("%s not generated: %s", purpose, reason)
        return fallback, "fallback_template"

    if client is None:
        logger.error(
            "Gemini unavailable for %s; using deterministic template. "
            "Verify the GEMINI_API_KEY environment variable.",
            purpose,
        )
        return fallback, "fallback_template"

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty body.")
        record(tenant_id, "ai_calls")
        STORE.cache_put(key, text)
        _note_success()
        return text, "gemini"
    except Exception as exc:  # noqa: BLE001 - an alert must always go out
        logger.exception("Gemini %s failed (%s); falling back.", purpose, exc)
        _note_failure(exc)
        return fallback, "fallback_template"
