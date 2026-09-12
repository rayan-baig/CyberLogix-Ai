"""A thousand random lifetimes, checked against the rules that must hold.

Hand-auditing found real bugs, and then stopped finding them: ten passes
over escalation, webhooks, cross-tenant reach, durability, benchmark
privacy and 990 hostile payloads turned up four defects and six clean
sweeps. Reading harder was reaching its limit.

What a person is bad at is the *order* things happen in. Sign, bill, add
six sensors, void the invoice, lapse, upgrade, bill again, part-pay, buy
an add-on, renew — nobody writes that test by hand, and it is exactly
where billing bugs live, because every one found so far was an
interaction rather than a line.

So this drives the real application through random sequences of the
things a customer and an operator actually do, and after every single
step asserts the rules that must never break. Each scenario is seeded, so
a failure prints the seed that reproduces it exactly.

Run more of them with CYBERLOGIX_FUZZ_SCENARIOS=1000. The suite keeps a
small deterministic sample so it stays fast; the big run is for when
something has changed underneath.
"""

from __future__ import annotations

import os
import random
from datetime import timedelta

import pytest

import costs
import signup
from contracts import run_billing, run_dunning
from store import PLAN_TIERS, STORE, add_months, utc_now

SCENARIOS = int(os.environ.get("CYBERLOGIX_FUZZ_SCENARIOS", "40"))
STEPS = int(os.environ.get("CYBERLOGIX_FUZZ_STEPS", "22"))

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}
PROVISIONING = {"X-CyberLogix-Provisioning": "test-provisioning-key"}
VERTICALS = ["restaurant", "pharmacy", "cryostorage", "logistics",
             "country_club", "cybersecurity"]
ADD_ONS = ["assurance", "vault", "benchmarks", "equipment_intelligence"]


class World:
    """One tenant's whole life, and what we know to be true about it."""

    def __init__(self, api, rng):
        self.api = api
        self.rng = rng
        self.vertical = rng.choice(VERTICALS)
        self.sensors: list[str] = []
        self.next_sensor = 0
        self.log: list[str] = []
        # Per subscription: a renewal starts a new one at zero, which is
        # not the counter going backwards.
        self.max_periods_billed: dict[str, int] = {}

        signup.reset_rate_limits()
        body = api.post("/api/signup", json={
            "company_name": "Fuzz Co",
            "full_name": "Dana Reyes",
            "email": "dana@fuzz.example",
            "password": "correct-horse-battery",
            "contact_phone": "+15550100",
            "industry_vertical": self.vertical,
        }).json()
        self.key = {"X-CyberLogix-Key": body["api_key"]}
        self.bearer = {"Authorization": f"Bearer {body['token']}"}
        self.both = {**self.key, **self.bearer}
        self.tenant_id = body["tenant"]["tenant_id"]
        self.log.append(f"signup({self.vertical})")

    # ---- the things anybody can do -------------------------------------

    def add_sensor(self):
        name = f"S-{self.next_sensor}"
        self.next_sensor += 1
        resp = self.api.post("/api/licenses/me/sensors", headers=self.key, json={
            "sensor_id": name, "industry_vertical": self.vertical,
            "location_name": "Site"})
        if resp.status_code == 201:
            self.sensors.append(name)
        self.log.append(f"add_sensor -> {resp.status_code}")

    def remove_sensor(self):
        if not self.sensors:
            return
        name = self.rng.choice(self.sensors)
        resp = self.api.delete(f"/api/licenses/me/sensors/{name}",
                               headers=self.both)
        if resp.status_code in (200, 204):
            self.sensors.remove(name)
        self.log.append(f"remove_sensor -> {resp.status_code}")

    def upgrade(self):
        target = self.rng.choice(["growth", "enterprise"])
        resp = self.api.post("/api/licenses/me/plan", headers=self.both,
                             json={"plan": target})
        self.log.append(f"upgrade({target}) -> {resp.status_code}")

    def sign(self):
        resp = self.api.post("/api/contracts", headers=self.both, json={
            "term_years": self.rng.choice([1, 3, 5]),
            "escalator_percent": self.rng.choice([0.0, 5.0, 7.5]),
            "annual_prepay": self.rng.random() < 0.3,
            "add_ons": self.rng.sample(ADD_ONS, self.rng.randint(0, 2)),
        })
        self.log.append(f"sign -> {resp.status_code}")

    def set_add_ons(self):
        resp = self.api.post("/api/contracts/add-ons", headers=self.both, json={
            "add_ons": self.rng.sample(ADD_ONS, self.rng.randint(0, 4))})
        self.log.append(f"add_ons -> {resp.status_code}")

    def renew(self):
        resp = self.api.post("/api/contracts/renew", headers=self.both,
                             json={"term_years": self.rng.choice([1, 3])})
        self.log.append(f"renew -> {resp.status_code}")

    def cancel(self):
        resp = self.api.post("/api/contracts/cancel", headers=self.both,
                             json={"reason": "fuzz"})
        self.log.append(f"cancel -> {resp.status_code}")

    def bill(self):
        issued = run_billing()["invoices_issued"]
        self.log.append(f"bill -> {issued}")

    def chase(self):
        out = run_dunning()
        self.log.append(f"dunning -> {out['notices_count']}")

    def pay(self):
        open_invoices = [i for i in STORE.invoices_for(self.tenant_id) if i.open]
        if not open_invoices:
            return
        invoice = self.rng.choice(open_invoices)
        part = self.rng.random() < 0.5
        body = {"reference": "WIRE"}
        if part and invoice.balance_usd > 2:
            body["amount_usd"] = round(invoice.balance_usd / 2, 2)
        resp = self.api.post(
            f"/api/invoices/{invoice.invoice_id}/paid",
            params={"tenant_id": self.tenant_id}, headers=ADMIN, json=body)
        self.log.append(f"pay(part={part}) -> {resp.status_code}")

    def void(self):
        live = [i for i in STORE.invoices_for(self.tenant_id)
                if i.state in ("issued", "part_paid")]
        if not live:
            return
        invoice = self.rng.choice(live)
        resp = self.api.post(
            f"/api/invoices/{invoice.invoice_id}/void",
            params={"tenant_id": self.tenant_id}, headers=ADMIN, json={})
        self.log.append(f"void -> {resp.status_code}")

    def travel(self):
        """Move the contract's start back, which is how time passes here."""
        sub = STORE.active_subscription(self.tenant_id)
        if sub is None:
            return
        months = self.rng.choice([1, 1, 2, 13])
        sub.started_at = add_months(sub.started_at, -months)
        STORE.save_subscription(sub)
        self.log.append(f"travel(-{months}mo)")

    def age_invoices(self):
        for invoice in STORE.invoices_for(self.tenant_id):
            if invoice.open and invoice.due_at:
                invoice.due_at -= timedelta(days=self.rng.choice([5, 20, 40]))
                STORE._db.put("invoice", invoice.invoice_id, invoice.to_row())
        self.log.append("age_invoices")

    def lapse(self):
        tenant = STORE.get_tenant(self.tenant_id)
        tenant.expires_at = utc_now() + timedelta(
            days=self.rng.choice([-40, -5, -1, 3]))
        STORE._db.put("tenant", self.tenant_id, tenant.to_row())
        self.log.append("lapse")

    def pulse(self):
        if not self.sensors:
            return
        resp = self.api.post("/api/sensor-pulse", headers=self.key, json={
            "sensor_id": self.rng.choice(self.sensors),
            "temperature_fahrenheit": self.rng.choice([20.0, 61.0, 250.0])})
        self.log.append(f"pulse -> {resp.status_code}")

    def restart(self):
        """Everything must survive coming back from disk."""
        STORE.__init__(STORE._db)
        STORE.load()
        self.log.append("restart")

    OPS = ["add_sensor", "add_sensor", "add_sensor", "remove_sensor",
           "upgrade", "sign", "set_add_ons", "renew", "cancel",
           "bill", "bill", "chase", "pay", "void", "travel",
           "age_invoices", "lapse", "pulse", "restart"]

    def step(self):
        getattr(self, self.rng.choice(self.OPS))()


# ---- the rules that must never break -----------------------------------


def check_invariants(world):
    """Everything that has to be true, whatever just happened."""
    tenant = STORE.get_tenant(world.tenant_id)
    invoices = STORE.invoices_for(world.tenant_id)
    sub = STORE.active_subscription(world.tenant_id)

    for invoice in invoices:
        lines = round(sum(line["amount_usd"] for line in invoice.lines), 2)
        assert lines == pytest.approx(invoice.subtotal_usd, abs=0.02), (
            f"{invoice.number}: lines sum to {lines}, subtotal says "
            f"{invoice.subtotal_usd}"
        )
        assert invoice.total_usd == pytest.approx(invoice.subtotal_usd, abs=0.02)
        assert invoice.amount_paid_usd >= 0
        assert invoice.balance_usd == pytest.approx(
            max(0.0, invoice.total_usd - invoice.amount_paid_usd), abs=0.02)
        assert invoice.state in ("issued", "part_paid", "paid", "void")
        if invoice.state == "paid":
            assert invoice.balance_usd == pytest.approx(0.0, abs=0.02)
        # An arrears line is a *part* period. It can never exceed what a
        # whole one would cost for the same units.
        for line in invoice.lines:
            if line["kind"] == "arrears":
                ceiling = line["quantity"] * line["unit_price_usd"] * 1.001
                assert line["amount_usd"] <= ceiling, (
                    f"{invoice.number}: arrears {line['amount_usd']} exceeds a "
                    f"full period at {line['unit_price_usd']} x "
                    f"{line['quantity']}"
                )

    numbers = [i.number for i in invoices]
    assert len(numbers) == len(set(numbers)), f"duplicate invoice number: {numbers}"

    # The rule the whole billing module exists to keep: one live invoice
    # per period of a subscription.
    seen = {}
    for invoice in invoices:
        if invoice.state == "void" or invoice.billing_period is None:
            continue
        slot = (invoice.source, invoice.billing_period)
        assert slot not in seen, (
            f"period {invoice.billing_period} of {invoice.source} billed "
            f"twice: {seen.get(slot)} and {invoice.number}"
        )
        seen[slot] = invoice.number

    if sub is not None:
        seen_before = world.max_periods_billed.get(sub.subscription_id, 0)
        assert sub.periods_billed >= seen_before, (
            f"{sub.subscription_id}: periods_billed went "
            f"{seen_before} -> {sub.periods_billed}"
        )
        world.max_periods_billed[sub.subscription_id] = sub.periods_billed
        assert set(sub.add_ons) <= set(ADD_ONS)
        assert sub.term_years >= 1
        assert all(p < sub.periods_billed for p in sub.rebill_periods), (
            "a period is queued for re-billing that was never billed"
        )

    cap = PLAN_TIERS[tenant.plan]["max_sensors"]
    live = STORE.sensors_for(world.tenant_id)
    assert len(live) <= cap, (
        f"{len(live)} sensors on a plan that allows {cap}"
    )

    # A trial can never be reached by self-service, so a tenant that has
    # been paid-up cannot be sitting on one.
    assert tenant.plan in PLAN_TIERS

    # The promise in section 5 of the agreement: alerting is never
    # withheld for money. In grace it must still ingest.
    from auth import licence_state

    if licence_state(tenant) == "grace" and live:
        resp = world.api.post("/api/sensor-pulse", headers=world.key, json={
            "sensor_id": live[0].sensor_id, "temperature_fahrenheit": 20.0})
        assert resp.status_code == 200, (
            f"an estate in grace was refused ingest: {resp.status_code}"
        )

    # And the ledger is never writable by the party it bills.
    if invoices:
        target = invoices[0]
        for label, resp in (
            ("settle", world.api.post(
                f"/api/invoices/{target.invoice_id}/paid",
                params={"tenant_id": world.tenant_id}, headers=world.both,
                json={"reference": "nice try"})),
            ("void", world.api.post(
                f"/api/invoices/{target.invoice_id}/void",
                params={"tenant_id": world.tenant_id}, headers=world.both,
                json={})),
        ):
            assert resp.status_code in (401, 403, 503), (
                f"the customer could {label} their own invoice: "
                f"{resp.status_code}"
            )

    # Spend ceilings must always be the ones for the plan actually held.
    caps = costs.caps_for(world.tenant_id)
    if tenant.plan == "trial":
        assert caps["sms"] == costs.TRIAL_MAX_SMS_PER_DAY
    else:
        assert caps["sms"] == costs.MAX_SMS_PER_DAY


@pytest.mark.parametrize("seed", range(SCENARIOS))
def test_a_random_lifetime_keeps_every_rule(api, seed):
    """One seeded lifetime, checked after every step.

    On failure the operation log prints, so the sequence that broke it can
    be read straight off and replayed with the same seed.
    """
    rng = random.Random(seed)
    world = World(api, rng)
    check_invariants(world)

    for index in range(STEPS):
        world.step()
        try:
            check_invariants(world)
        except AssertionError as failure:
            raise AssertionError(
                f"seed={seed} broke after step {index + 1}\n"
                + "\n".join(f"  {n:>2}. {line}"
                            for n, line in enumerate(world.log, 1))
                + f"\n\n{failure}"
            ) from failure
