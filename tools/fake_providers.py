"""The real application, with Twilio swapped for a recording sink.

For tools/load_test.py and tools/outage_drill.py only; never deployed.
It imports `main` exactly as production's `uvicorn main:app` does and
changes one thing: the Twilio client is a fake that writes every text and
call to a JSON-lines file, with the time it was handed over, instead of
sending it. Everything else -- the store, SQLite, the sweep loop, the
watchdog, the mail transport -- is the code that ships.

Run it the way the image does, one worker:

    PYTHONPATH=. python -m uvicorn fake_providers:app --app-dir tools \
        --host 127.0.0.1 --port 8181 --workers 1

Settings, all optional:

  LOADTEST_SINK            JSON-lines file for texts, calls and sweeps.
  LOADTEST_TWILIO_MODE     A file holding the fake's behaviour, read on
                           every send so a drill can change it live:
                           `ok`, `error` (Twilio answers 500), or
                           `timeout` (no answer within TWILIO_TIMEOUT_SECONDS).
  LOADTEST_GRACE_MINUTES   Shorten the 10-minute voice grace, so a run can
                           watch the sweep place calls without waiting.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# The repository root, so `import main` finds the application wherever
# uvicorn was started from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The fake needs the three Twilio settings present, or every send stops at
# "not_configured" before it reaches the client. Never real values.
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACfake0000000000000000000000000000")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "fake-token-for-the-load-test")
os.environ.setdefault("TWILIO_FROM_NUMBER", "+15550100")

import automation  # noqa: E402
import notifications  # noqa: E402
import scheduler  # noqa: E402
from main import app  # noqa: E402,F401 - what uvicorn serves

__all__ = ["app", "FakeTwilio"]

SINK = os.environ.get("LOADTEST_SINK", "")
MODE_FILE = os.environ.get("LOADTEST_TWILIO_MODE", "")
_write_lock = threading.Lock()
_counter = [0]


def _record(event: dict) -> None:
    if not SINK:
        return
    event["t"] = time.time()
    line = json.dumps(event)
    with _write_lock:
        with open(SINK, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _mode() -> str:
    if not MODE_FILE:
        return "ok"
    try:
        return Path(MODE_FILE).read_text(encoding="utf-8").strip() or "ok"
    except OSError:
        return "ok"


class _Sent:
    def __init__(self, sid: str):
        self.sid = sid
        self.status = "queued"


class _Endpoint:
    def __init__(self, kind: str):
        self.kind = kind

    def create(self, to: str, from_: str, body: str = "", twiml: str = "", **_):
        mode = _mode()
        if mode == "error":
            _record({"event": f"{self.kind}_refused", "to": to})
            raise RuntimeError("HTTP 500 from Twilio (simulated outage)")
        if mode == "timeout":
            _record({"event": f"{self.kind}_timeout", "to": to})
            time.sleep(notifications.TWILIO_TIMEOUT_SECONDS)
            raise TimeoutError("Twilio did not answer (simulated timeout)")
        with _write_lock:
            _counter[0] += 1
            sid = f"FAKE{self.kind[:2].upper()}{_counter[0]:08d}"
        _record({"event": self.kind, "to": to, "body": body or twiml, "sid": sid})
        return _Sent(sid)


class FakeTwilio:
    def __init__(self):
        self.messages = _Endpoint("sms")
        self.calls = _Endpoint("call")


notifications._client = FakeTwilio()

_grace = os.environ.get("LOADTEST_GRACE_MINUTES", "").strip()
if _grace:
    automation.VOICE_ESCALATION_GRACE_MINUTES = float(_grace)

_real_pass = scheduler.run_one_pass


def _timed_pass():
    started = time.time()
    try:
        return _real_pass()
    finally:
        _record({"event": "sweep", "start": started, "end": time.time()})


# The loop looks the name up on each pass, so this times every sweep.
scheduler.run_one_pass = _timed_pass
