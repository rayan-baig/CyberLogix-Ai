"""The customer guides under docs/ say only things that are true.

They are written for people who will not read the code, so nobody reading
them can tell when one goes stale. A route renamed, a default changed, a
hand-check recipe that no longer matches the chain: each of those would
leave a customer following instructions that quietly do not work. These
tests run what the guides tell people to run.
"""

import re
from pathlib import Path

import pytest

import backup
import store
from main import app
from test_every_route_is_tested import _routes as walk_routes

ROOT = Path(__file__).resolve().parent.parent
GUIDES = [ROOT / "docs" / "YOUR-DATA.md", ROOT / "docs" / "THE-VAULT.md"]


def _routes():
    return [(method, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"))
            for method, path in walk_routes(app.routes)]


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_the_readme_links_the_guide(guide):
    assert guide.exists()
    assert f"docs/{guide.name}" in (ROOT / "README.md").read_text()


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_every_route_a_guide_names_exists(guide):
    named = re.findall(r"\b(GET|POST|PUT|PATCH|DELETE) (/api/[^\s`?]+)",
                       guide.read_text())
    assert named, f"{guide.name} names no routes"
    routes = _routes()
    missing = [f"{m} {p}" for m, p in named
               if not any(m == rm and rp.match(p) for rm, rp in routes)]
    assert not missing, f"{guide.name} names routes that do not exist: {missing}"


def test_the_numbers_in_your_data_are_the_defaults():
    text = GUIDES[0].read_text()
    assert f"newest {store.MAX_READINGS_PER_SENSOR} readings" in text
    assert f"last\n**{backup.BACKUP_KEEP}**" in text
    assert f"Within **{backup.BACKUP_KEEP} days**" in text


@pytest.fixture()
def attested(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 29.5, 45.25, 30.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})
    resp = api.get("/api/vault/attestation/FRZ-1?days=30&include_chain=true",
                   headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_hand_check_in_the_vault_guide_reproduces_the_chain_head(attested):
    """The Python recipe is the one an auditor who trusts no server runs."""
    block = re.search(r"```python\n(.*?)```", GUIDES[1].read_text(), re.DOTALL)
    assert block, "THE-VAULT.md lost its hand-check recipe"
    scope = {}
    exec(block.group(1), scope)

    assert scope["chain_head"](attested["chain"]) == attested["chain_head"]
    edited = [dict(r) for r in attested["chain"]]
    edited[1]["temperature_fahrenheit"] = 99.0
    assert scope["chain_head"](edited) != attested["chain_head"]


def test_the_chain_can_be_posted_to_the_public_check_as_the_guide_says(
    api, attested
):
    """The guide says: send the "chain" list as the readings. No account."""
    resp = api.post("/api/vault/verify", json={
        "chain_head": attested["chain_head"], "readings": attested["chain"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["matches"] is True
