"""Infinity, and what it does to a record that cannot hold it.

JSON has no literal for infinity, but every parser invents one: `1e400`
reads back as `inf`, and Python's own json accepts a bare `NaN`.
Pydantic's bounds do not stop either — `inf >= 0` is True, so a field
declared `ge=0` lets infinity straight through.

Measured against a running server before the fix:

  * `{"amount_usd": 1e400}` on a payment set an invoice's paid amount to
    infinity. The database refused the write, so disk stayed clean and
    memory did not — and every subsequent read of that invoice, *and of
    the customer's entire invoice list*, answered 500 until the process
    was restarted.
  * `{"danger_above": 1e400}` on a sensor's limits did the same to the
    sensor, and that one is worse: `/api/sensor-pulse` started answering
    500 for it. The device keeps reporting, nothing is recorded, no
    breach can be detected, and the freezer is silently unmonitored. An
    operator role is enough to do it — no platform credential needed.

The last test in this file is the one that matters. It walks every
Pydantic model the application defines, finds every float field, and
asserts each refuses a non-finite value. A new model with a plain
`float` on it fails the suite, which is the only way a rule like this
survives the next feature.
"""


import pytest

from store import STORE


def _raw(api, method, path, body, headers=None):
    """Send a body json.dumps would refuse, the way a real client can."""
    return api.request(
        method, path, headers={"Content-Type": "application/json", **(headers or {})},
        content=body,
    )


@pytest.fixture()
def estate(api, tenant_factory, sensor_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "RACK-01", "cybersecurity")
    return headers, owner, tenant


# ---- the two that were live ---------------------------------------------


@pytest.mark.parametrize("literal", [b"1e400", b"-1e400", b"NaN"])
def test_an_unstorable_payment_is_refused_at_the_door(
    api, admin_headers, estate, settle, literal
):
    from contracts import run_billing

    headers, owner, tenant = estate
    api.post("/api/contracts", headers={**headers, **owner},
             json={"term_years": 1, "escalator_percent": 5.0})
    run_billing()
    invoice = STORE.invoices_for(tenant["tenant_id"])[0]

    resp = _raw(
        api, "POST", f"/api/invoices/{invoice.invoice_id}/paid"
        f"?tenant_id={tenant['tenant_id']}",
        b'{"reference":"INF","amount_usd":' + literal + b"}",
        admin_headers,
    )

    assert resp.status_code == 422, resp.text
    # And the invoice is still readable, which is the part that broke.
    assert api.get(f"/api/invoices/{invoice.invoice_id}",
                   headers=headers).status_code == 200
    assert api.get("/api/invoices", headers=headers).status_code == 200
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 0.0


@pytest.mark.parametrize("literal", [b"1e400", b"-1e400", b"NaN"])
def test_an_unstorable_threshold_cannot_stop_a_sensor_reporting(
    api, estate, literal
):
    """The worst of the two: monitoring stops and nothing says so."""
    headers, owner, tenant = estate

    resp = _raw(
        api, "POST", "/api/licenses/me/sensors/RACK-01/thresholds",
        b'{"danger_above":' + literal + b"}", {**headers, **owner},
    )

    assert resp.status_code == 422, resp.text
    # The freezer is still being watched, which is the whole product.
    pulse = api.post("/api/sensor-pulse", headers=headers,
                     json={"sensor_id": "RACK-01", "temperature_fahrenheit": 40.0})
    assert pulse.status_code == 200, pulse.text
    assert api.get("/api/licenses/me/sensors", headers=headers).status_code == 200


def test_a_refused_write_leaves_memory_agreeing_with_disk(api, estate):
    """The second lock on the same door.

    The boundary check is the real fix. This is what stops the *next*
    unstorable value — a full disk, something nobody anticipated — from
    corrupting the working set: the row is written before it is
    committed to the live object.
    """
    headers, owner, tenant = estate
    sensor = STORE.sensors_for(tenant["tenant_id"])[0]

    def _refuse(kind, rec_id, data):
        raise ValueError("simulated: the database will not take this")

    original = STORE._db.put
    STORE._db.put = _refuse
    try:
        with pytest.raises(ValueError):
            STORE.set_sensor_overrides(sensor, 10.0, None)
    finally:
        STORE._db.put = original

    assert sensor.override_above is None, (
        "the sensor kept a value that was never persisted"
    )
    assert api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01",
                          "temperature_fahrenheit": 40.0}).status_code == 200


# ---- the guard that outlives this commit --------------------------------


def _models():
    """Every Pydantic model the application defines, with its module."""
    import importlib
    import pkgutil
    from pathlib import Path

    from pydantic import BaseModel

    root = Path(__file__).resolve().parent.parent
    found = {}
    for info in pkgutil.iter_modules([str(root)]):
        if info.name in ("seed_demo", "conftest"):
            continue
        try:
            module = importlib.import_module(info.name)
        except Exception:  # pragma: no cover - a module that will not import
            continue        # is a different test's problem
        for name in dir(module):
            attribute = getattr(module, name)
            if (
                isinstance(attribute, type)
                and issubclass(attribute, BaseModel)
                and attribute is not BaseModel
                and attribute.__module__ == info.name
            ):
                found[f"{info.name}.{name}"] = attribute
    return found


def _float_fields(model):
    """Field names on this model that accept a float."""
    names = []
    for name, field in model.model_fields.items():
        annotation = repr(field.annotation)
        if "float" in annotation:
            names.append(name)
    return names


def test_the_application_defines_models_to_walk():
    """A guard on the guard: a broken crawler that finds nothing passes
    every assertion below it."""
    models = _models()
    assert len(models) > 20, f"only found {len(models)} models"
    with_floats = [n for n, m in models.items() if _float_fields(m)]
    assert len(with_floats) >= 5, f"only found floats on {with_floats}"


def test_no_model_anywhere_accepts_infinity():
    """The rule, enforced across the whole application.

    A new model with a plain `float` on it fails here. That is the only
    way this survives the next feature — the two bugs above were both
    written by somebody who simply did not think about infinity, which
    is everybody, always.
    """
    offenders = []
    for label, model in sorted(_models().items()):
        for name in _float_fields(model):
            for hostile in (float("inf"), float("-inf"), float("nan")):
                try:
                    model.model_validate({name: hostile})
                except Exception as exc:
                    # Refused for any reason — the type, a bound, or the
                    # finite check — is a pass. What matters is that it
                    # did not get through.
                    if "finite" in str(exc) or "valid" in str(exc).lower():
                        continue
                    continue
                offenders.append(f"{label}.{name} accepted {hostile}")

    assert not offenders, (
        "these fields accept a value that cannot be stored, and storing "
        "one corrupts the record it is written into. Use `Finite` from "
        "models.py instead of `float`:\n  " + "\n  ".join(offenders)
    )
