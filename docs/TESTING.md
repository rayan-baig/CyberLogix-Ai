# Testing

What the test suite covers and how to run it, the type check on the money
code, the fuzzer, the browser smoke test, and mutation testing.

## Tests

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests/ -q
```

About 2,010 tests across 124 files. Gemini, Twilio and SMTP are all stubbed and the
database is in-memory, so the suite runs without credentials, makes no network
calls and touches no file on disk. The mail fixture replaces only the function
that opens the socket, so a test asserting that a chase went out is asserting
on the message that would actually have left the building.

CI runs the suite on Python 3.11, which the image ships, and again on 3.13.
Password hashing runs at a fraction of its real cost under the suite (see
`tests/conftest.py`); a test holds the production setting where it is.

## Types in the money code

The seven modules that price, invoice, take payment and keep the books are
type-checked in CI. Only those: `mypy.ini` lists them, so the check stays
small enough that every finding gets read.

```bash
.venv/bin/pip install mypy==2.4.1
.venv/bin/python -m mypy
```

## The fuzzer

`tests/test_invariants_fuzz.py` drives the real application through seeded
random sequences of the nineteen things a customer and an operator actually
do — sign, bill, add sensors, void, lapse, upgrade, part-pay, buy add-ons,
renew, cancel, restart from disk — and after **every step** asserts the rules
that must never break:

- an invoice's lines sum to its total, and its balance is what is left;
- no period of a contract is ever billed twice;
- `periods_billed` never goes backwards;
- sensors never exceed the plan's seats;
- an arrears line never exceeds a whole period for the same units;
- an estate inside its grace window still accepts readings;
- no tenant credential can settle or void an invoice.

The suite runs forty scenarios so it stays quick. Run the full sweep when
something underneath has changed:

```bash
CYBERLOGIX_FUZZ_SCENARIOS=1000 .venv/bin/python -m pytest \
  tests/test_invariants_fuzz.py -q
```

A failure prints the seed and the whole operation log, so it replays exactly.
Two money bugs were found this way that no hand-written test reached — a
voided month disappearing when the contract was renewed, and a failed re-bill
rewinding a counter it had never advanced, which left the same month queued
from both ends.

## The console in a real browser

`tools/console_smoke.js` drives the console in headless Chromium at phone
(390×844) and desktop size: signs a business up through the form, walks every
tab empty and again with a breach, opens the sensor drawer, and checks that
nothing shows `undefined`/`NaN`/`null`, the page never scrolls sideways, the
poll backs off while the server is down, wall mode shows the whole estate and
none of the account's private details, and an installed app with no network
gets the offline page. It starts its own server on a throwaway database:

```bash
npm i playwright && npx playwright install chromium   # once
PYTHON=.venv/bin/python node tools/console_smoke.js
# or against a running server, with a screenshot of every step:
BASE_URL=http://127.0.0.1:8080 SMOKE_SHOTS=/tmp/shots node tools/console_smoke.js
```

It also walks a new business's first day: the landing page states who it is
for, the price and the trial in the first screen (phone and laptop), sign-up
lands in the console, every empty card offers its next step, the checklist
ticks itself and goes, the test alert opens no incident, and a simulated unit
is stamped as one and stays out of the real fleet.

`tools/first_day_walk.js` times the path from the landing page to a first
sensor reading and counts the clicks and fields on the way:

```bash
PYTHON=.venv/bin/python node tools/first_day_walk.js          # phone
WIDE=1 PYTHON=.venv/bin/python node tools/first_day_walk.js   # desktop
```

`tests/test_every_route_is_tested.py` fails a whole-suite run if any route
the app serves is not reached by at least one test.

`tests/test_pricing_page.py` runs `static/pricing.js` under Node (skipped
if Node is missing) and checks the calculator's sum against
`pricing.quote_monthly`, which is what the invoice charges.
`tests/test_help_page.py` checks every figure on the help page against
the constant it comes from, and `tests/test_version.py` checks that
`VERSION` has an entry at the top of `CHANGELOG.md`.

## Mutation testing

Every fix in this repository was re-broken on purpose to confirm a test
catches it. Where a mutation survived, that was treated as a gap in the tests
rather than a false alarm — several of the sharper tests here exist because
the obvious one passed against reintroduced bugs.
