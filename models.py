"""Shared request-model pieces.

One module so that a rule which has to hold on every model lives in one
place rather than being remembered on each.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BeforeValidator
from typing_extensions import Annotated


def _finite(value: Any) -> Any:
    """Refuse infinity and NaN before they reach anything that stores them.

    JSON has no literal for either, but every parser invents one:
    `1e400` reads back as `inf`, and Python's json accepts a bare `NaN`.
    Pydantic's own bounds do not stop them — `inf >= 0` is True, so a
    field declared `ge=0` lets infinity straight through.

    What that cost, measured on a running server:

    * `{"amount_usd": 1e400}` on a payment set an invoice's paid amount
      to infinity. The database refused to write it, so disk stayed
      clean and the in-memory invoice did not — and from then on reading
      that invoice, *or the customer's whole invoice list*, answered 500
      until somebody restarted the process.
    * `{"danger_above": 1e400}` on a sensor's limits did the same to the
      sensor, and that one is worse: `/api/sensor-pulse` began answering
      500 for it. The device keeps reporting, nothing is recorded, no
      breach can be detected, and the freezer is silently unmonitored.
      An operator role is enough to do it.

    So it is refused at the door, as a 422, on every float the API
    accepts.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            "must be a finite number: infinity and NaN cannot be stored, "
            "and a value that cannot be stored corrupts the record it "
            "was written into"
        )
    if isinstance(value, str):
        # Pydantic will coerce "inf" and "nan" to floats further down.
        if value.strip().lower().lstrip("+-") in ("inf", "infinity", "nan"):
            raise ValueError("must be a finite number")
    return value


# Use in place of `float` on any model field the API accepts.
Finite = Annotated[float, BeforeValidator(_finite)]
