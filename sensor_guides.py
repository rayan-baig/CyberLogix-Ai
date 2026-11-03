"""Step-by-step setup guides for every way a sensor can report.

One guide per way in that the code actually has: our own format, the
webhook form for devices that carry the key in the body, the
send-anything route with its Monnit and SensorPush presets and its field
detection for everything else, and the poller for vendor clouds that will
only be read. Words, not screenshots: a screenshot of a vendor's console
is out of date the week they redesign it, and cannot be checked.

These are checked. Each guide lists the routes, headers, query
parameters and fields it tells somebody to use, and
tests/test_sensor_guides.py holds every one of them to the code: the
route exists with that method, the header and the field are ones the
route reads, the preset field names are the ones byod.py matches on. It
also scans the words of every step for anything that looks like a path
or a header and fails if the guide mentions one it did not declare. The
same text is the /help/sensors page and docs/SENSOR-SETUP.md, which a
test keeps identical to what this module renders.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List

from byod import PRESETS, SERIAL_KEYS, VALUE_KEYS
from hardware_bridge import SUPPORTED_METRICS
from pollers import (
    DEFAULT_INTERVAL_MINUTES,
    FAILURES_BEFORE_BROKEN,
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
)

# What the guides call the deployment's address. The page puts the real
# one in its place; the Markdown keeps a placeholder on a reserved domain.
BASE = "{base}"
DOC_BASE = "https://cyberlogix.example.com"

_MONNIT = PRESETS["monnit"]
_PUSH = PRESETS["sensorpush"]


def _add_unit_step(serial: bool) -> str:
    return (
        "In the console, open Setup and use Add a fridge or freezer: say "
        "where it is and which industry it is in, so it gets the right "
        "limits"
        + (", and type the device's own serial number into Third-party "
           "device serial. That serial is how a reading finds this unit."
           if serial else
           ". Give it a Sensor ID you will recognise, such as WALKIN-1, "
           "or leave it blank and one is made from the location.")
        + " Press Claim a seat."
    )


_KEY_STEP = (
    "Copy the sensor key the console shows next. It is shown once. It "
    "lets a device report readings for this one unit and do nothing else, "
    "so it is the key to put on hardware or in a vendor's settings."
)

_CHECK_SAMPLES = (
    "If nothing appears, open Use a sensor you already own in the "
    "console: every payload that reached /api/v1/bridge/any is listed "
    "there as it arrived (keys removed), with what was read from it and "
    "what was missing. A device that is not listed never reached us."
)


GUIDES: List[Dict[str, Any]] = [
    {
        "id": "native",
        "title": "Any device or script that can send a web request",
        "for": ("Gateways and hubs that let you set the address, headers and "
                "body of what they send; a Raspberry Pi; your own script."),
        "steps": [
            _add_unit_step(serial=False),
            _KEY_STEP,
            f"Set the device to send an HTTPS POST to {BASE}/api/sensor-pulse "
            "every few minutes.",
            "Add two headers: X-CyberLogix-Sensor-Key with the sensor key, "
            "and Content-Type: application/json.",
            "Send a JSON body with sensor_id (the Sensor ID from step 1) and "
            "the temperature as temperature_fahrenheit or "
            "temperature_celsius. You may add humidity_percent, "
            "battery_percent and signal_percent; a battery level lets us "
            "warn you before it goes flat.",
            "Check the answer. 200 means the reading was taken and the unit "
            "shows it in the console. 401 means the key was not "
            "recognised; 403 means the key belongs to a different unit; "
            "404 means no unit has that Sensor ID.",
        ],
        "example": {"sensor_id": "WALKIN-1", "temperature_fahrenheit": 2.5,
                    "battery_percent": 88},
        "routes": [("POST", "/api/sensor-pulse"), ("POST", "/api/licenses/me/sensors")],
        "headers": {"/api/sensor-pulse": ["X-CyberLogix-Sensor-Key"]},
        "query": {},
        "fields": {
            "telemetry.SensorReading": ["sensor_id", "temperature_fahrenheit",
                                        "temperature_celsius", "humidity_percent",
                                        "battery_percent", "signal_percent"],
        },
        "presets": {},
    },
    {
        "id": "webhook",
        "title": "Devices that can only put the key in the body (Elitech, Dickson and others)",
        "for": ("Wi-Fi and cellular loggers whose cloud forwards each reading "
                "to a web address, with a form for the body but no way to "
                "add headers."),
        "steps": [
            _add_unit_step(serial=True),
            _KEY_STEP,
            f"In the device's or vendor's forwarding settings, set the "
            f"address to {BASE}/api/v1/bridge/sensor-webhook-ingest and the "
            "method to POST, sending JSON.",
            "Shape the body with these fields: device_sn (the serial from "
            "step 1), api_key_token (the sensor key), reading_value (the "
            "number) and metric_type, which says what the number is: "
            + ", ".join(SUPPORTED_METRICS) + ". Optionally location_label.",
            "Send temperature as its own message from humidity or battery: "
            "one reading per message.",
            "Check the answer. 200 means it was taken. 202 means the serial "
            "is not on any unit yet: the reading is held under Devices "
            "knocking in the console, where one tap adds it and keeps what "
            "it sent. 401 means the key was not recognised.",
        ],
        "example": {"device_sn": "ELITECH-00:1B:44:11", "api_key_token": "(sensor key)",
                    "reading_value": 3.1, "metric_type": "temperature_c"},
        "routes": [("POST", "/api/v1/bridge/sensor-webhook-ingest")],
        "headers": {},
        "query": {},
        "fields": {
            "hardware_bridge.GenericWebhookPayload": [
                "device_sn", "api_key_token", "reading_value", "metric_type",
                "location_label"],
            "licenses.SensorRegister": ["external_device_sn"],
        },
        "presets": {},
    },
    {
        "id": "monnit",
        "title": "Monnit sensors, through iMonnit",
        "for": "Monnit wireless sensors and gateways, with an iMonnit account.",
        "steps": [
            _add_unit_step(serial=True) + " For Monnit the serial is the "
            "sensor's SensorID, the number iMonnit shows for it.",
            _KEY_STEP,
            "In iMonnit, find where it sends sensor readings on to a web "
            "address (a webhook) and give it this address: "
            f"{BASE}/api/v1/bridge/any?k=(the sensor key).",
            "If iMonnit lets you add a header, send the key in "
            "X-CyberLogix-Sensor-Key instead and leave ?k= off the address: "
            "an address with a key in it ends up in logs.",
            "Nothing needs mapping. We read "
            f"{_MONNIT['serial']} as the serial, {_MONNIT['value']} as the "
            f"reading, {_MONNIT['unit_field']} for Celsius or Fahrenheit, "
            f"{_MONNIT['time']} as when it was taken, and "
            f"{_MONNIT['battery']} and {_MONNIT['signal']} as battery and "
            "signal.",
            "One webhook for several sensors: a sensor key only reports for "
            "its own unit and refuses the rest with 403. Send the business "
            "key in X-CyberLogix-Key instead, and replace it from the "
            "console if it is ever seen by somebody it should not be.",
            _CHECK_SAMPLES,
        ],
        "example": {"SensorID": "123456", "DataValues": "3.4",
                    "DataTypes": "TemperatureC", "MessageDate": "2026-11-03 06:00:00",
                    "Battery": "92", "SignalStrength": "80"},
        "routes": [("POST", "/api/v1/bridge/any")],
        "headers": {"/api/v1/bridge/any": ["X-CyberLogix-Sensor-Key", "X-CyberLogix-Key"]},
        "query": {"/api/v1/bridge/any": ["k"]},
        "fields": {"licenses.SensorRegister": ["external_device_sn"]},
        "presets": {"monnit": ["serial", "value", "unit_field", "time", "battery",
                               "signal"]},
    },
    {
        "id": "sensorpush",
        "title": "SensorPush",
        "for": ("SensorPush sensors with a G1 gateway, which reports to "
                "SensorPush's own cloud and not to any address you choose."),
        "steps": [
            _add_unit_step(serial=True) + " For SensorPush the serial is "
            "the sensor's id as its cloud names it.",
            "The gateway cannot be pointed at us, so the readings have to be "
            "fetched from SensorPush's cloud and passed on. Our fetcher "
            "(Fetch from a sensor cloud, in the next guide) only sends GET "
            "requests with headers you fix in advance. If the account's data "
            "can be read that way, use it. If reading it needs a sign-in "
            "step that hands out a short-lived token, or a POST, the "
            "fetcher cannot do it: run a small relay of your own that reads "
            "SensorPush and sends each reading on, as below.",
            f"The relay sends an HTTPS POST to {BASE}/api/v1/bridge/any "
            "with the sensor key in the X-CyberLogix-Sensor-Key header (or "
            "the business key in X-CyberLogix-Key, for several sensors).",
            f"Each reading as JSON with {_PUSH['serial']} (the sensor's "
            f"id), {_PUSH['value']} and {_PUSH['time']}. A SensorPush "
            "temperature is read as Fahrenheit, which is what it reports "
            "unless the account was changed; if yours is in Celsius, send "
            "sensor_id and temp_c instead, which say so.",
            _CHECK_SAMPLES,
        ],
        "example": {"sensor": "16725.1234567890", "temperature": 37.4,
                    "observed": "2026-11-03T06:00:00Z"},
        "routes": [("POST", "/api/v1/bridge/any")],
        "headers": {"/api/v1/bridge/any": ["X-CyberLogix-Sensor-Key", "X-CyberLogix-Key"]},
        "query": {},
        "fields": {"licenses.SensorRegister": ["external_device_sn"]},
        "presets": {"sensorpush": ["serial", "value", "time"]},
    },
    {
        "id": "vendor-cloud",
        "title": "Any vendor cloud you can read with a web address",
        "for": ("Hardware that only talks to its maker's cloud, when that "
                "cloud gives you an HTTPS address that answers with the "
                "latest readings as JSON."),
        "steps": [
            _add_unit_step(serial=True) + " The serial is whatever the "
            "vendor's data calls the device.",
            "Get the address and the credential from the vendor: an HTTPS "
            "address that answers a plain GET with JSON, and the header it "
            "wants the credential in, such as Authorization.",
            "In the console, open Fetch from a sensor cloud. Give it a name, "
            "the address, and the header name and value. Choose how often to "
            f"fetch: every {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} "
            f"minutes, {DEFAULT_INTERVAL_MINUTES} if you leave it.",
            "Press Try now. It fetches once and says what came back: "
            "readings taken, nothing new, or why nothing could be used.",
            "What comes back is read the same way as the send-anything route "
            "below: one reading, a list, or readings under samples, readings, "
            "data, results or sensors, and each needs a serial that is on one "
            "of your units.",
            "The address must start with https and point at the public "
            "internet. Redirects are not followed: save the final address. "
            f"After {FAILURES_BEFORE_BROKEN} failures in a row it stops "
            "asking and the owner is emailed; Try now starts it again.",
        ],
        "example": {"readings": [{"device_id": "GW-7781", "temp_c": 3.9,
                                  "timestamp": "2026-11-03T06:00:00Z"}]},
        "routes": [("POST", "/api/pollers"), ("POST", "/api/pollers/{source_id}/test")],
        "headers": {},
        "query": {},
        "fields": {"pollers.NewSource": ["name", "url", "headers", "interval_minutes"]},
        "presets": {},
    },
    {
        "id": "anything",
        "title": "Anything else that sends its own JSON",
        "for": ("A device or cloud that sends readings in its own format, "
                "with names we have never seen."),
        "steps": [
            _add_unit_step(serial=True),
            _KEY_STEP,
            f"Point the device at {BASE}/api/v1/bridge/any with POST and "
            "JSON, and the sensor key in the X-CyberLogix-Sensor-Key header. "
            "If it cannot set headers, add ?k=(the sensor key) to the "
            "address instead.",
            "We look for a serial in a field named like one of: "
            + ", ".join(SERIAL_KEYS) + ". Case, spaces, dashes and "
            "underscores do not matter, and the field may be nested.",
            "And for the reading in a field named like one of: "
            + ", ".join(VALUE_KEYS) + ".",
            "Celsius or Fahrenheit has to be said, never guessed: in the "
            "field name (temp_c, temperatureF), in the value (\"3.5 C\"), or "
            "in the word celsius or fahrenheit. If it is not, the reading is "
            "held under Devices knocking and you choose the unit when you "
            "add it.",
            "Before pointing anything at us, paste one real payload into Use "
            "a sensor you already own (it calls /api/v1/bridge/preview). It "
            "says what would be read from it and what is missing, and "
            "records nothing.",
            _CHECK_SAMPLES,
        ],
        "example": {"Device ID": "DICKSON-7A:2C:19", "Temp C": "3.2",
                    "Time": "2026-11-03T06:00:00Z"},
        "routes": [("POST", "/api/v1/bridge/any"), ("POST", "/api/v1/bridge/preview")],
        "headers": {"/api/v1/bridge/any": ["X-CyberLogix-Sensor-Key"]},
        "query": {"/api/v1/bridge/any": ["k"]},
        "fields": {"licenses.SensorRegister": ["external_device_sn"]},
        "presets": {},
    },
]


def guides() -> List[Dict[str, Any]]:
    return GUIDES


def _steps(guide: Dict[str, Any], base: str) -> List[str]:
    return [step.replace(BASE, base) for step in guide["steps"]]


def render_html(base: str) -> str:
    """The guides as the page's body. Everything is escaped."""
    e = html.escape
    contents = "".join(
        f'<a class="btn btn-sm" href="#{e(g["id"])}">{e(g["title"].split(" (")[0])}</a>'
        for g in GUIDES)
    out = [f'<div class="help-links">{contents}</div>']
    for guide in GUIDES:
        steps = "".join(f"<li>{e(step)}</li>" for step in _steps(guide, base))
        example = e(json.dumps(guide["example"], indent=2))
        out.append(
            f'<h2 id="{e(guide["id"])}">{e(guide["title"])}</h2>'
            f'<div class="panel"><p>{e(guide["for"])}</p>'
            f'<ol class="steps">{steps}</ol>'
            f'<p>What one message looks like:</p><pre>{example}</pre></div>')
    return "\n".join(out)


def render_markdown() -> str:
    """docs/SENSOR-SETUP.md, exactly. A test keeps the file equal to this."""
    out = [
        "# Setting up sensors",
        "",
        "Step by step, in words, for every way a sensor can report to",
        "CyberLogix. The same guides are in the app at `/help/sensors`.",
        f"`{DOC_BASE}` stands for your own address. Every route, header and",
        "field named here is checked against the code by",
        "`tests/test_sensor_guides.py`; this file is generated by",
        "`sensor_guides.py`, so change that rather than this.",
        "",
    ]
    for guide in GUIDES:
        out += [f"## {guide['title']}", "", guide["for"], ""]
        out += [f"{n}. {step}" for n, step in enumerate(_steps(guide, DOC_BASE), 1)]
        out += ["", "What one message looks like:", "", "```json",
                json.dumps(guide["example"], indent=2), "```", ""]
    return "\n".join(out)


if __name__ == "__main__":  # python sensor_guides.py > docs/SENSOR-SETUP.md
    print(render_markdown(), end="")
