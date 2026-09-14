"""One estate, one scale, across every document that leaves the building.

Readings are stored in Fahrenheit and converted for display. The console
did that. The 3am text did that. The compliance export did not — so a
cellar in Bordeaux set its account to Celsius, saw Celsius everywhere it
looked, and then handed an inspector a document headed `Min °F` reading
78.0 for a cellar its own records said had reached 25.56.

Two documents about the same event that disagree is the exact thing a
compliance record exists to prevent. And "78" in a wine cellar is a
catastrophe or an ordinary afternoon depending only on which scale the
reader assumes — the export never said.

Same class as the sector picker: the application knew the answer,
used it everywhere the customer could see, and dropped it in the one
artifact that goes to somebody else.
"""

import csv
import io

import pytest

from store import STORE


@pytest.fixture()
def cellar(api, tenant_factory, sensor_factory):
    """A customer who works in Celsius and has said so."""
    headers, tenant = tenant_factory(
        plan="enterprise", company_name="Bordeaux Cellars"
    )
    assert api.post(
        "/api/licenses/me/temperature-unit", headers=headers,
        json={"temperature_unit": "C"},
    ).status_code == 200
    sensor_factory(headers, "CELLAR-1", "wine_and_art")
    # 78F is 25.56C, and well past this sector's limit either way.
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "CELLAR-1", "temperature_fahrenheit": 78.0})
    return headers, tenant


def _csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


# ---- the artifact that goes to a third party ---------------------------


def test_the_compliance_export_is_in_the_customers_own_unit(api, cellar):
    headers, _ = cellar

    text = api.get("/api/autopilot/compliance.csv", headers=headers).text
    row = _csv_rows(text)[0]

    assert "Min °C" in text and "Min °F" not in text
    assert float(row["Min °C"]) == pytest.approx(25.56, abs=0.01)


def test_the_compliance_report_says_which_scale_it_is_in(api, cellar):
    """Without it the temperatures are numbers with no scale, and a
    document that cannot say whether 78 is a disaster is not evidence."""
    headers, _ = cellar

    report = api.get("/api/autopilot/compliance", headers=headers).json()

    assert report["temperature_unit"] == "C"
    assert report["per_sensor"][0]["temperature_unit"] == "C"
    assert report["per_sensor"][0]["min_temperature"] == pytest.approx(
        25.56, abs=0.01
    )


def test_a_fahrenheit_customer_is_unaffected(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="enterprise")
    sensor_factory(headers, "RACK-01", "cybersecurity")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "RACK-01", "temperature_fahrenheit": 90.0})

    text = api.get("/api/autopilot/compliance.csv", headers=headers).text
    row = _csv_rows(text)[0]

    assert "Min °F" in text
    assert float(row["Min °F"]) == pytest.approx(90.0, abs=0.01)


# ---- the invariant, rather than the instance ---------------------------


def test_every_surface_quotes_the_same_number(api, cellar):
    """The console, the alarm and the inspector's document, side by side.

    This is the assertion that would have caught it. Each of the three
    was individually defensible; only comparing them shows that one of
    them is telling a different story about the same freezer.
    """
    headers, tenant = cellar

    console = api.get("/api/console/overview", headers=headers).json()
    report = api.get("/api/autopilot/compliance", headers=headers).json()
    incident = api.get("/api/voice/incidents", headers=headers).json()[
        "incidents"][0]

    assert console["temperature_unit"] == "C"
    assert report["temperature_unit"] == "C"
    # The text a human is woken by, in the same scale as the document
    # they will be shown afterwards.
    assert "°C" in incident["breach_details"]
    assert "25.56" in incident["breach_details"]
    assert report["per_sensor"][0]["min_temperature"] == pytest.approx(
        25.56, abs=0.01
    )


def test_switching_unit_moves_every_document_together(api, cellar):
    """A customer who changes their mind must not end up with a filing
    cabinet half in one scale and half in the other."""
    headers, _ = cellar

    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "F"})

    text = api.get("/api/autopilot/compliance.csv", headers=headers).text
    report = api.get("/api/autopilot/compliance", headers=headers).json()

    assert "Min °F" in text
    assert report["temperature_unit"] == "F"
    assert report["per_sensor"][0]["min_temperature"] == pytest.approx(
        78.0, abs=0.01
    )


# ---- the vault, which was already right ---------------------------------


def test_the_attestation_is_in_the_customers_unit_and_declares_it(api, cellar):
    """Checked rather than assumed, and it turned out to be fine.

    I expected this to be the same bug and it is not: the attestation
    converts to the account's unit and its safe band carries an explicit
    `unit`, so the document states its own scale. Worth a test anyway —
    it is the artifact an insurer reads, and it is the one place where
    getting the scale wrong would be argued about in a claim.
    """
    headers, _ = cellar

    attestation = api.get("/api/vault/attestation", headers=headers).json()
    entry = attestation["entries"][0]

    assert entry["safe_band"]["unit"] == "C"
    assert entry["coldest"] == pytest.approx(25.56, abs=0.01)


def test_the_hash_chain_stays_in_fahrenheit_and_says_so(api, cellar):
    """Do not "fix" the chain to match the presentation.

    The link's field is literally named `temperature_fahrenheit`, so it
    is self-describing, and the hash covers that value. Converting it
    would change what is hashed and break every chain already exported —
    including ones customers have handed to insurers, which is the single
    thing the feature promises cannot happen.

    The canonical record is Fahrenheit and names itself. Presentation is
    every other endpoint's job.
    """
    from vault import build_chain

    headers, tenant = cellar
    sensor = STORE.sensors_for(tenant["tenant_id"])[0]
    chain = build_chain(STORE.readings_for(sensor.sensor_id))

    assert chain, "no readings chained"
    assert "temperature_fahrenheit" in chain[0]
    assert chain[0]["temperature_fahrenheit"] == pytest.approx(78.0, abs=0.01)


def test_the_per_sensor_attestation_verifies_for_this_customer(api, cellar):
    """A Celsius account's evidence must still check out.

    The verifier re-derives the chain from the stored Fahrenheit
    readings, which is why converting the chain for display would break
    it. Covered end to end elsewhere; this asserts it specifically for
    an account whose presentation unit is not the stored one.
    """
    headers, _ = cellar

    # Issuing the attestation is what anchors the head; verification
    # compares against that anchor, so there has to be one.
    assert api.get("/api/vault/attestation/CELLAR-1",
                   headers=headers).status_code == 200

    check = api.get("/api/vault/verify/CELLAR-1", headers=headers)

    assert check.status_code == 200, check.text
    body = check.json()
    assert body["verifiable"] is True
    assert body["intact"] is True
    assert body["rederived_head"] == body["attested_head"]
