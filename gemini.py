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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _CallTimedOut
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from google import genai

from store import STORE

logger = logging.getLogger("cyberlogix.gemini")

# Which model writes the copy. No default: a model's name is a setting,
# never something written into the code, and named models are retired on
# a schedule. Unset, every line is its template, the same as having no key.
GEMINI_MODEL = os.environ.get("CYBERLOGIX_GEMINI_MODEL", "").strip()


def _seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.error("%s is not a number; using %ss.", name, default)
        return default
    return value if value > 0 else default


# The alert text waits for the model's one line about what to check. The
# SDK's own HTTP client waits forever by default (`timeout=None`), so a
# model service that accepted the connection and then said nothing held
# the breach text back for as long as it liked: the reading's request
# never answered and nobody was texted. Now the line has this long, and
# then the alert goes out with the template line instead.
#
# Two limits, because the SDK's timeout is per socket read and a reply
# that trickles in a byte at a time never trips it: the HTTP client gets
# the timeout, and the caller stops waiting at the same time whatever the
# socket is doing.
AI_TIMEOUT_SECONDS = _seconds("CYBERLOGIX_AI_TIMEOUT_SECONDS", 6.0)


def http_options() -> Dict[str, Any]:
    """What the SDK client is built with: a timeout, in milliseconds."""
    return {"timeout": int(AI_TIMEOUT_SECONDS * 1000)}


# Calls run here so the caller can stop waiting. A call that hangs past
# its deadline keeps its worker until the socket gives up, so there are a
# few; when all are stuck, a new call waits in line and still times out
# on schedule.
_calls = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-call")

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
        "configured": client is not None and bool(GEMINI_MODEL),
        "model": GEMINI_MODEL,
        "consecutive_failures": failures,
        "degraded": failures >= FAILURES_BEFORE_DEGRADED,
        "last_error": error,
        "last_success_at": since.isoformat() if since else None,
    }

try:
    client = genai.Client(http_options=http_options())  # type: ignore[arg-type]
    logger.info("Google GenAI client initialized (model=%s).", GEMINI_MODEL)
except Exception as exc:  # noqa: BLE001 - startup must never hard-fail
    client = None
    logger.warning(
        "Google GenAI client unavailable (%s). All generated copy will use "
        "deterministic fallback templates.",
        exc,
    )


def dispatch_ready() -> bool:
    """True when the Gemini client initialized and a model is named."""
    return client is not None and bool(GEMINI_MODEL)


def _client_for_key(api_key: str):
    """A client on a business's own key, with the same timeout as ours."""
    return genai.Client(api_key=api_key, http_options=http_options())  # type: ignore[arg-type]


def _generate_on_own_key(prompt: str, fallback: str, purpose: str, tenant_id: str,
                         own_key: str, use_cache: bool) -> Tuple[str, str]:
    """One alert line on the business's own key (alertkey.py).

    The same deadline as every other alert line, and the template when it
    fails: never the service's own key instead, and never a wait. Not
    counted against the service's AI budget, because the business's
    provider bills it. Cached under the business alone, so a line its key
    paid for is never handed to another business.
    """
    import alertkey
    from costs import cache_key, record

    key = cache_key(prompt, f"{purpose}\x00own-key\x00{tenant_id}")
    if use_cache:
        cached = STORE.cache_get(key)
        if cached is not None:
            record(tenant_id, "ai_cache_hits")
            return cached, "cache"
    if not GEMINI_MODEL:
        alertkey.note(tenant_id, False, "no model is set on this service")
        return fallback, "fallback_template"
    try:
        call = _calls.submit(
            lambda: _client_for_key(own_key).models.generate_content(
                model=GEMINI_MODEL, contents=prompt))
        try:
            response = call.result(timeout=AI_TIMEOUT_SECONDS)
        except _CallTimedOut:
            call.cancel()
            raise TimeoutError(
                f"no answer within {AI_TIMEOUT_SECONDS:g}s") from None
        text = (response.text or "").strip()
        if not text:
            raise ValueError("an empty answer")
        if use_cache:
            STORE.cache_put(key, text)
        alertkey.note(tenant_id, True)
        return text, "gemini"
    except Exception as exc:  # noqa: BLE001 - an alert must always go out
        # The kind of failure and the provider's words with the key taken
        # out. No traceback: it would carry the same words again.
        said = alertkey.scrub(str(exc), own_key)
        logger.warning("%s on the business's own AI key failed for %s (%s: %s); "
                       "using the template line.", purpose, tenant_id,
                       type(exc).__name__, said)
        alertkey.note(tenant_id, False, type(exc).__name__)
        return fallback, "fallback_template"


def safe_generate(
    prompt: str,
    fallback: str,
    purpose: str,
    tenant_id: Optional[str] = None,
    business_key: bool = False,
    use_cache: bool = True,
) -> Tuple[str, str]:
    """Generate text, degrading to `fallback` instead of raising.

    Returns a (text, source) pair where source is "gemini", "cache" or
    "fallback_template". Passing `tenant_id` meters the call and subjects it
    to that tenant's daily budget; omitting it skips both.

    `business_key` is for the alert lines only: when the business has
    saved its own AI key (alertkey.py), the line is written on that key
    instead of the service's, with the same deadline and fallback.
    """
    # Imported here because costs.py depends on the store, which must not be
    # pulled in at gemini import time.
    from costs import allow_ai_call, cache_key, record, reserve

    if business_key and tenant_id:
        import alertkey

        own_key = alertkey.key_for(tenant_id)
        if own_key:
            return _generate_on_own_key(prompt, fallback, purpose, tenant_id,
                                        own_key, use_cache)

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

    if client is None or not GEMINI_MODEL:
        logger.error(
            "Gemini unavailable for %s; using deterministic template. "
            "Verify GEMINI_API_KEY and CYBERLOGIX_GEMINI_MODEL.",
            purpose,
        )
        return fallback, "fallback_template"

    # The place under today's cap is taken together with the check (see
    # costs.reserve). It is kept even if the call then fails: a call that
    # was made was paid for.
    allowed, reason = reserve(tenant_id, "ai")
    if not allowed:
        record(tenant_id, "ai_suppressed")
        return fallback, "fallback_template"

    try:
        call = _calls.submit(
            client.models.generate_content, model=GEMINI_MODEL, contents=prompt,
        )
        try:
            response = call.result(timeout=AI_TIMEOUT_SECONDS)
        except _CallTimedOut:
            call.cancel()
            raise TimeoutError(
                f"no answer within {AI_TIMEOUT_SECONDS:g}s") from None
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty body.")
        STORE.cache_put(key, text)
        _note_success()
        return text, "gemini"
    except Exception as exc:  # noqa: BLE001 - an alert must always go out
        logger.exception("Gemini %s failed (%s); falling back.", purpose, exc)
        _note_failure(exc)
        return fallback, "fallback_template"
