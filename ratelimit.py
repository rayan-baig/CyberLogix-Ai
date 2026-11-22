"""A sliding-window limit for the public doors that are not sign-up.

Sign-up keeps its own (signup.py). This is the same idea made reusable:
a ceiling per caller and a ceiling in total, counted and recorded under
one lock, so a burst of requests cannot all read the same count and all
decide they were under it. The total ceiling holds even when the caller
cannot be told apart (no trusted proxy header), which is why there is
one.

Used by the partner application form and the public proof report check
(insurers.py, proofreports.py).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List

from fastapi import HTTPException, Request, status


def wait_words(seconds: int) -> str:
    """How long to wait, the way a person says it (3.26.0).

    "Try again in 2832 seconds" was on the sign-up page. Under a minute it
    is the seconds; after that whole minutes, rounded up, so nobody comes
    back a moment too soon. The exact figure stays in Retry-After.
    """
    s = max(1, int(seconds))
    if s < 60:
        return f"{s} second{'' if s == 1 else 's'}"
    minutes = -(-s // 60)
    if minutes == 1:
        return "about a minute"
    if minutes < 120:
        return f"about {minutes} minutes"
    hours = -(-minutes // 60)
    return f"about {hours} hours"

_ALL: List["Limit"] = []


class Limit:
    """At most `per_caller` per caller and `total` in all, per window."""

    def __init__(self, name: str, per_caller: int, total: int,
                 window_seconds: int, words: str) -> None:
        self.name = name
        self.per_caller = per_caller
        self.total = total
        self.window = window_seconds
        # What the caller was doing, for the refusal: "applications",
        # "checks".
        self.words = words
        self._lock = threading.Lock()
        self._by_caller: Dict[str, Deque[float]] = defaultdict(deque)
        self._all: Deque[float] = deque()
        _ALL.append(self)

    def _prune(self, bucket: Deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= self.window:
            bucket.popleft()

    def check(self, request: Request) -> None:
        """Count this request, or refuse it with a 429 and Retry-After."""
        from signup import caller_key

        key = caller_key(request)
        now = time.monotonic()
        with self._lock:
            mine = self._by_caller[key]
            self._prune(mine, now)
            self._prune(self._all, now)
            full = None
            if len(mine) >= self.per_caller:
                full = mine
            elif len(self._all) >= self.total:
                full = self._all
            if full is not None:
                wait = int(self.window - (now - full[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(f"Too many {self.words} in a short time. Please "
                            f"try again in {wait_words(wait)}."),
                    headers={"Retry-After": str(wait)},
                )
            mine.append(now)
            self._all.append(now)
            # Callers whose window has emptied are forgotten once the table
            # grows, so it cannot grow without bound.
            if len(self._by_caller) > 2000:
                for other in list(self._by_caller):
                    self._prune(self._by_caller[other], now)
                    if not self._by_caller[other]:
                        del self._by_caller[other]

    def reset(self) -> None:
        with self._lock:
            self._by_caller.clear()
            self._all.clear()


def reset_all() -> None:
    """Empty every window. For tests, and for a deliberate operator reset."""
    for limit in _ALL:
        limit.reset()
