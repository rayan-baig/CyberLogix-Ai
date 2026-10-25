"""Sensors you already own, sending what they already send.

The bridge already accepted third-party hardware, and the docstring said
"any off-the-shelf commercial sensor can POST its raw JSON straight to
CyberLogix". That was not true. The endpoint required *our* field names
-- device_sn, reading_value, metric_type -- and no Monnit, SensorPush,
Elitech or Dickson device has ever sent those. A customer had to stand up
a translator in the middle, which is the work the claim promised to
remove.

This is the translator, and it is the point of the product rather than a
convenience. Every hardware vendor in this market sells the monitoring
software that watches their own sensors: buy Monnit, use iMonnit. The
switching cost is the hardware on the wall, and that is the moat. An
estate that can keep its sensors has no moat to cross.

Three ways a payload becomes a reading, in order of how much the
customer has to know:

1. **A preset**, when the shape is one we recognise.
2. **Detection**, when it is not: the keys are searched, case- and
   nesting-insensitive, for something that looks like a serial and
   something that looks like a temperature.
3. **A mapping the customer wrote**, by pasting one real payload from
   their own device and pointing at the fields.

Only the third is guaranteed, which is why it exists. A preset written
against a vendor's documentation is a guess about firmware we have never
seen, and it goes stale the first time they ship a new gateway. So the
presets say what they were checked against, detection explains what it
found and why, and every payload is kept verbatim so the answer to "why
did my sensor not appear" is a fact rather than a theory.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from store import STORE, iso, utc_now

# A payload is kept whole for this long so somebody wiring up a device can
# see exactly what arrived. Bounded: this is raw input from the internet.
MAX_SAMPLES_PER_TENANT = 25
KIND = "byod_sample"


# --- what a reading looks like, whoever sent it ----------------------------

# Keys worth trying, most specific first. Matching is case-insensitive and
# ignores separators, so "Sensor ID", "sensor_id" and "sensorID" are one.
SERIAL_KEYS = (
    "device_sn", "deviceserial", "serialnumber", "serial", "sensorid",
    "deviceid", "device", "sensorname", "macaddress", "mac", "imei", "id",
)
VALUE_KEYS = (
    "reading_value", "temperature", "temperaturec", "temperaturef",
    "tempc", "tempf", "temp", "currentreading", "value", "reading",
    "datavalue", "datavalues", "plotvalue", "plotvalues", "measurement",
)
TIME_KEYS = (
    "observed", "messagedate", "recordedat", "timestamp", "time", "at",
    "datetime", "date", "lastreading",
)
BATTERY_KEYS = ("battery", "batterylevel", "batterypercent", "voltage")
SIGNAL_KEYS = ("signalstrength", "signal", "rssi")

# Presets. Each says what it was actually checked against, because a
# preset is a claim about firmware and an unqualified claim is a trap.
PRESETS: Dict[str, Dict[str, Any]] = {
    "monnit": {
        "name": "Monnit / iMonnit",
        "serial": "SensorID",
        "value": "DataValues",
        "time": "MessageDate",
        "battery": "Battery",
        "signal": "SignalStrength",
        "unit": None,
        # iMonnit states the unit in its own field -- "TemperatureC" --
        # rather than in the value, so it is read from there.
        "unit_field": "DataTypes",
        "checked_against": (
            "field names taken from working open-source iMonnit "
            "integrations, not from a device on a bench"
        ),
    },
    "sensorpush": {
        "name": "SensorPush",
        "serial": "sensor",
        "value": "temperature",
        "time": "observed",
        "battery": None,
        "signal": None,
        "unit": "temperature_f",
        "checked_against": (
            "the published SensorPush API sample model; the G1 gateway "
            "reports Fahrenheit by default"
        ),
    },
    "cyberlogix": {
        "name": "CyberLogix native",
        "serial": "device_sn",
        "value": "reading_value",
        "time": None,
        "battery": None,
        "signal": None,
        "unit": None,
        "checked_against": "this application",
    },
}


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def flatten(payload: Any, prefix: str = "") -> Dict[str, Any]:
    """Every leaf in the payload, by dotted path.

    Nested is the norm rather than the exception: a gateway wraps the
    reading in an envelope, and a list of observations arrives under one
    key. Flattening once means the search below does not have to know the
    shape it is looking at.
    """
    found: Dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(payload, list):
        for index, value in enumerate(payload[:20]):
            found.update(flatten(value, f"{prefix}[{index}]"))
    else:
        found[prefix] = payload
    return found


def _first_match(flat: Dict[str, Any], candidates: Tuple[str, ...]):
    """The best key for a role, and why it was chosen."""
    by_norm = {}
    for path, value in flat.items():
        leaf = _norm(path.split(".")[-1].split("[")[0])
        by_norm.setdefault(leaf, (path, value))
    for candidate in candidates:
        if candidate in by_norm:
            return by_norm[candidate]
    return None, None


def as_number(value: Any) -> Optional[float]:
    """A reading, out of whatever the vendor felt like sending.

    Monnit sends "72.5" as a string, and some gateways send "72.5 F" with
    the unit inside the value. A bare float is the exception.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if not match:
            return None
        number = float(match.group())
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        # The same door as every other ingest path: a non-finite reading
        # scored against a threshold reads as nominal, which is the worst
        # possible failure for a flat battery mid-conversion to cause.
        return None
    return number


def unit_from(value: Any, key: str) -> Optional[str]:
    """Celsius or Fahrenheit, when the payload happens to say.

    Never guessed from the number. "4" is a fridge in Celsius and a
    disaster in Fahrenheit, and a wrong guess here is a silent one.

    Two ways it gets said in practice: spelled out somewhere in the key
    or the value ("celsius", "TemperatureC", "temp_f"), or tacked onto
    the reading itself ("72.5 F"), which gateways really do send.
    """
    for text in (str(value), str(key)):
        blob = text.lower()
        if "celsius" in blob or "fahrenheit" in blob:
            return "temperature_c" if "celsius" in blob else "temperature_f"
        # A trailing c/f on a temperature-ish name: temperatureC, temp_f,
        # tempC. Checked on the normalised form so separators do not
        # matter, and only after "temp" so a field called "sensorC" does
        # not decide the unit of the estate.
        # The leaf, not the whole dotted path: "data[0].temp_c" is a
        # temperature field called temp_c, and normalising the path
        # instead turns it into "data0tempc", which starts with "data".
        flat = _norm(text.split(".")[-1].split("[")[0])
        if flat.startswith("temp") and flat.endswith(("c", "f")):
            return "temperature_c" if flat.endswith("c") else "temperature_f"
        # The unit inside the value: "72.5 F", "-2.5 °C".
        trailing = re.search(r"\d\s*°?\s*([cf])\b", blob)
        if trailing:
            return ("temperature_c" if trailing.group(1) == "c"
                    else "temperature_f")
    return None


def detect(payload: Any) -> Dict[str, Any]:
    """Read a payload without being told what sent it."""
    flat = flatten(payload)

    for key, preset in PRESETS.items():
        wanted = {_norm(preset[role]) for role in ("serial", "value")
                  if preset.get(role)}
        present = {_norm(p.split(".")[-1].split("[")[0]) for p in flat}
        if wanted and wanted <= present:
            return _apply(flat, preset, source=f"preset:{key}")

    return _apply(flat, None, source="detected")


def _apply(flat, preset, source) -> Dict[str, Any]:
    if preset:
        serial_path, serial = _first_match(flat, (_norm(preset["serial"]),))
        value_path, raw = _first_match(flat, (_norm(preset["value"]),))
        time_path, when = (_first_match(flat, (_norm(preset["time"]),))
                           if preset.get("time") else (None, None))
        bat_path, battery = (_first_match(flat, (_norm(preset["battery"]),))
                             if preset.get("battery") else (None, None))
        sig_path, signal = (_first_match(flat, (_norm(preset["signal"]),))
                            if preset.get("signal") else (None, None))
        unit = preset.get("unit")
    else:
        serial_path, serial = _first_match(flat, SERIAL_KEYS)
        value_path, raw = _first_match(flat, VALUE_KEYS)
        time_path, when = _first_match(flat, TIME_KEYS)
        bat_path, battery = _first_match(flat, BATTERY_KEYS)
        sig_path, signal = _first_match(flat, SIGNAL_KEYS)
        unit = None

    number = as_number(raw)
    if unit is None and preset and preset.get("unit_field"):
        _, declared = _first_match(flat, (_norm(preset["unit_field"]),))
        if declared is not None:
            unit = unit_from(declared, str(declared))
    if unit is None and value_path:
        unit = unit_from(raw, value_path)

    missing = []
    if not serial:
        missing.append("a serial number identifying which device sent this")
    if number is None:
        missing.append("a numeric reading")

    return {
        "understood": not missing,
        "source": source,
        "preset": preset["name"] if preset else None,
        "checked_against": preset.get("checked_against") if preset else None,
        "serial": str(serial) if serial is not None else None,
        "value": number,
        "unit": unit,
        "observed_at": str(when) if when is not None else None,
        "battery_percent": as_number(battery),
        "signal_percent": as_number(signal),
        "fields_used": {
            "serial": serial_path, "value": value_path, "observed_at": time_path,
            "battery": bat_path, "signal": sig_path,
        },
        "missing": missing,
        "note": (
            "The unit was not stated in the payload, so it has to be set on "
            "the sensor. 4 degrees is a fridge in Celsius and a disaster in "
            "Fahrenheit, and guessing from the number is how that goes "
            "unnoticed." if unit is None and number is not None else ""
        ),
    }


# --- what your device actually sent ----------------------------------------


def remember(tenant_id: str, payload: Any, verdict: Dict[str, Any],
             outcome: str) -> None:
    """Keep the payload verbatim, so onboarding is not guesswork.

    The single most useful thing when wiring up hardware is being able to
    see what arrived, and it is the thing vendor dashboards are worst at.
    """
    now = utc_now()
    row = {
        "sample_id": f"{tenant_id}:{now.timestamp()}",
        "tenant_id": tenant_id,
        "at": iso(now),
        "outcome": outcome,
        "understood": verdict.get("understood", False),
        "source": verdict.get("source"),
        "serial": verdict.get("serial"),
        "value": verdict.get("value"),
        "unit": verdict.get("unit"),
        "fields_used": verdict.get("fields_used"),
        "missing": verdict.get("missing"),
        "payload": payload,
    }
    with STORE._lock:
        STORE._db.put(KIND, row["sample_id"], row)
        mine = [r for r in STORE._db.all(KIND) if r.get("tenant_id") == tenant_id]
        mine.sort(key=lambda r: r.get("at") or "")
        for stale in mine[:-MAX_SAMPLES_PER_TENANT]:
            STORE._db.delete(KIND, stale["sample_id"])


def samples_for(tenant_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    mine = [r for r in STORE._db.all(KIND) if r.get("tenant_id") == tenant_id]
    mine.sort(key=lambda r: r.get("at") or "", reverse=True)
    return mine[:limit]
