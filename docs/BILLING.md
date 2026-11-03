# Pricing, billing and the books

The rate card, plans and volume pricing; invoices, contracts that bill
themselves and collections; taking payment; the books and the margin
report; and the reseller channel.

## Pricing

Priced per unit, per month, by vertical — a rack is not a reefer truck and
is not billed like one. The invoice is derived from the sensors actually
registered, so a customer with three racks and two hangar bays pays
3 × $899 + 2 × $1,999 = $6,695 a month.

| Sector | Price | Protects against |
|---|---|---|
| Luxury Superyachts | **$4,999** / vessel / month | Engine-room thermal fires; $150k charter guest freezers at sea |
| IVF Clinics & Cryostorage | **$2,999** / tank / month | Embryos, eggs and cell lines that cannot be replaced at any price |
| Fine Wine & Art Storage | **$2,499** / cellar / month | Provenance, which is most of what the collection is worth |
| Private Aviation Hangars | **$1,999** / bay / month | Moisture corrosion of multi-million-dollar avionics |
| Medical Labs & Blood Banks | **$1,499** / vault / month | OSHA/FDA chain-of-custody; automated audit reports |
| High-End Country Clubs | **$1,499** / kitchen / month | Holiday dining inventory lost to compressor failure |
| Retail & Hospital Pharmacies | **$1,299** / pharmacy / month | The continuous-monitoring requirement for vaccine storage |
| Licensed Cannabis Cultivation | **$1,199** / room / month | Environmental control a licence renewal is judged against |
| Franchise Restaurants | **$999** / location / month | $15,000 walk-in spoilage; health department logs |
| CyberTech Data Centers | **$899** / rack / month | $50,000+ server meltdowns and SLA contract breaches |
| Solar Infrastructure | **$899** / enclosure / month | Battery-bank thermal runaway and fire liability |
| Cold-Chain Logistics | **$749** / reefer truck / month | Dock cargo rejection disputes; tamper-proof handover passes |

Rates are set for the pinnacle of each market rather than its volume. An
owner who spends millions a year on a vessel reads a $399 service as a toy,
and a price below the value at risk attracts exactly the buyer who haggles.

The rate card is public at `GET /api/billing/pricing` and on the sign-in
screen. `GET /api/billing` itemises a tenant's own estate; registering or
decommissioning a sensor reports what it adds to or removes from the bill.

### Plans are about contract, not price

| Plan | Unit ceiling | Term | Charged |
|---|---|---|---|
| `trial` | 5 | 14 days | no |
| `growth` | 50 | 365 days | yes |
| `enterprise` | 1000 | 365 days | yes |

A trial estate is priced but not charged, so a prospect can see what their
real fleet would cost. Downgrades that would strand units are refused with
`409`; decommission sensors first.

### Enterprise volume brackets

A chain is billed by enrolled branch count rather than per unit, and the
contract covers every sensor inside those branches:

| Units | Volume discount | Restaurant @ $999 | Vessel @ $4,999 |
|---|---|---|---|
| 1–9 | list | $999 | $4,999 |
| 10–19 | −2.5% | $974 | $4,874 |
| 20–29 | −5% | $949 | $4,749 |
| 30–39 | −7.5% | $924 | $4,624 |
| 40+ | −10% | $899 | $4,499 |

One ladder serves every vertical: the discount is a percentage of that
vertical's own rate, not a fixed dollar step. It caps at 10% deliberately —
at the top of a market a deep discount reads as eagerness, and every point of
it comes off the largest accounts.

There is **no ceiling on a contract**. A cap is a hard stop on revenue from
exactly the accounts worth the most; at these rates a chain of eleven vessels
would have hit a $50,000 cap and every vessel after that would be free.

**No estate is charged more than a larger estate would pay.** The discount
deepens a whole band at a time, so the ladder alone would make 40 units
cheaper than 39; the smaller estate gets the lower figure instead. A test
walks every vertical from 1 to 200 units asserting the bill never falls.

`GET /api/v1/enterprise-billing/tiers` publishes the bands, and every quote
carries a `next_tier` block naming where the next discount lands.

`POST /api/v1/enterprise-billing/provision-cluster` opens a contract;
`/quote` prices both models side by side before anyone signs, since volume
billing wins on multi-sensor sites and loses badly on single ones. An active
contract supersedes the rate card, and the rate-card figure is kept alongside
for comparison rather than charged.



### Add-ons, and why none of them is a share of savings

| Add-on | Basis | Price |
|---|---|---|
| Certified Compliance Vault | per estate | $499 / month |
| Sector Benchmarks | per estate | $299 / month |
| Equipment Intelligence | per estate | $399 / month |

Every one is a fixed fee. A percentage of what a customer saved sounds
aligned and is not: a quiet year pays nothing for the same standing
obligation, and on a marginal claim it puts the vendor on the wrong side of
the customer. A test asserts no add-on can be priced any other way.

### The terms that decide the other half

The rate card is half of what an estate is worth.

- **$1,500 per site at commissioning**, once. It funds acquisition on the
  day of signature rather than eleven months later, and a customer who paid
  to be installed does not churn casually.
- **10% for annual prepay.** Worth more than it costs: it removes the
  collections problem and fixes the customer for twelve months.
- **A 5% annual escalator** on multi-year contracts. Uncontroversial at
  signature and compounding — three years is 5.1% more contract value for
  no additional delivery.

`GET /api/billing/deal?years=3&annual_prepay=true&include_add_ons=assurance,vault`
returns the whole thing in one figure, so nobody discovers a setup fee or an
escalator afterwards. That is the only way those terms survive a renewal
conversation.

### Return on the subscription

`GET /api/billing/roi?days=30` weighs loss avoided against what the
subscription cost over the period.

Two rules keep that number honest. Only an incident **a person answered**
counts — an alert nobody acted on is a warning that went unheeded, not a
save. And only verticals with a **supplied** loss figure contribute: wine
and art storage ($250,000), superyachts ($150,000), cannabis cultivation
($60,000), data centres ($50,000), pharmacies ($40,000) and restaurants
($15,000). Incidents in the other verticals are listed and counted
separately as `unquantified_saves`, never folded into the total with an
invented number. Add a figure for one of those (`loss_avoided_usd` in
`pricing.py`) and its incidents count too.

## Invoicing

`POST /api/invoices` issues a numbered, dated invoice and **freezes it**.
The figures are snapshotted at issue rather than recomputed on read: an
invoice whose total moves after it was sent is a dispute the customer is
right to raise.

Numbers are sequential and gapless within a year, derived from what has
already been issued rather than a counter, so a restart cannot reissue one.
Voiding does not free the number — a gap in the sequence is the first thing
an auditor asks about. A short payment is recorded as a short payment
rather than quietly closing the invoice.

The document carries the issuer's legal name, address, tax ID and
remittance details from configuration, and says so plainly when they are
unset rather than printing a line reading "Tax ID:" with nothing after it.

In the console an invoice opens as a document and prints on one page:
issuer, the business it was billed to, the period it covers (from the
contract's billing period, or the days from issue for a one-off), every
line, subtotal, total and any amount received, the terms, and a footer
naming MTRX Horizyn. The print stylesheet hides everything else.

Payment collection is left to a processor. `POST /api/invoices/{id}/paid`
is where a webhook would land, and the lifecycle is complete without one —
which is also how a bank transfer, how most contracts at these sizes are
actually settled, gets recorded.

## Contracts that bill themselves

Everything above this could *quote*: a three-year term, a five percent
annual escalator, a $1,500 commissioning fee, ten percent off for paying
a year up front. Nothing carried any of it into an invoice. Billing was a
button somebody had to remember to press, at the month-one rate, forever.
On a $4,000-a-month estate signed to three escalating years the gap
between the deal as quoted and the deal as billed is $7,320.

`POST /api/contracts` makes the signed deal durable — term, escalator,
prepay, add-ons — and from then on invoices issue themselves. The
scheduler runs the billing pass hourly; `POST /api/contracts/billing-run`
is the same code on demand for a month-end close. Running it twice issues
nothing the second time: the claim on a period is atomic, so a scheduler
tick landing on a manual catch-up cannot bill the same month twice.

Three things it gets right that are easy to get wrong:

- **The escalator reaches the invoice.** Year three of a five percent term
  bills at 1.1025 times the rate card, and a renewal starts from where the
  last term finished rather than back at the year-one number.
- **Mid-period growth is charged.** Billing runs in advance, so a customer
  who signs for five racks and rolls out twenty more the following week
  was monitored on twenty-five and billed for five. Each invoice now
  carries the part-period owed for anything registered during the previous
  one, priced from the day it was registered.
- **A voided invoice comes back.** `periods_billed` is a high-water mark,
  so voiding one used to give that month away for good. The period goes on
  a re-bill list instead — a list rather than a rewind, because the voided
  invoice is not always the newest one.

### Collections

Reminders are staged by how late the invoice actually is, not by a counter
that gets walked: an invoice ninety days overdue that nobody has chased
gets the notice that fits ninety days, once, rather than four milder ones
in the same afternoon. A late charge is issued as its own numbered
document — an issued invoice whose total moves is a dispute. Past
`DELINQUENT_AFTER_DAYS` the account is delinquent.

Every notice is sent. For a long time none of them were: the ladder ran,
the stages were claimed, the text was appended to a list and handed back
to whoever called the endpoint, and the customer was never told. A
collections process the debtor cannot see is not a collections process.

The invoice itself now goes out on the day it is issued, too. Before
that, the first a customer heard about one was a chase for missing a
demand nobody had sent them — net 30 starts when accounts payable sees
the document, not when the ledger decides it exists. Recording a payment
sends a receipt, which removes an entire category of email and is how a
customer learns the chasing has stopped.

**What delinquency does not do is stop the monitoring.** A freezer full of
embryos does not stop being somebody's freezer because their finance
department is slow. Alerting, escalation and ingest run for a delinquent
account exactly as for a paid one; what is withheld is reporting —
benchmarks, attestations, exports.

### When a licence runs out

Expiry is a grace window, not a cliff. For `CYBERLOGIX_LICENCE_GRACE_DAYS`
the estate is watched exactly as before and only its reporting is
withheld; past that, ingest stops and the refusal names the route that
restores it.

That is for an account with no contract: a trial nobody bought, or a
plan nobody signed for. **Under a signed contract that is still running,
the licence does not lapse at all.** A month is billed in advance and
paid net 30, and the paid-up time only runs on from a paid invoice, so it
ends before the next bill is even due. Read as a lapse, a slow finance
department stopped a contract customer's freezer alarms two weeks later
— which the agreement promises never happens. While a contract runs,
an unpaid bill is collections' business, and the most it withholds is
reporting, at 45 days. A cancelled contract, or one past its term
without renewal, lapses like any other licence.

What the console says along the way: nothing while a bill is not yet
due; from the day it falls due, a warning with the amount, how many days
are left before reports pause, a "Pay now" link carrying that invoice's
id, and "Alerting is still running"; at 45 days, that reporting is
withheld until it clears. The banner goes when the bill is paid.
`tests/test_payment_lifecycle.py` walks one business through trial,
paid, late and paid again by card against a local fake Stripe, with
replayed, stale and out-of-order webhooks, checking all of that at every
step and that a warm freezer is texted and phoned about throughout.

Every route that is the way *back* stays open at every point — signing in,
changing plan, signing a contract, reading and settling an invoice,
accepting the terms, and the console page that carries all of them.
Refusing those because an account has lapsed is a deadlock: the customer
cannot pay because they have not paid. Suspension is the exception, in
both directions, because a lapse is something that happened to a customer
and a suspension is something somebody decided about them.

### The book

Nothing gave the person who owns the company a view of their own
customers. `/book` is that view, and `GET /api/contracts/attention` is the
data behind it: every account that needs a person, ranked by urgency and
then by what is at stake, with the contact details and the one action.
Above it, the totals — paying accounts, MRR, ARR, cash outstanding, annual
revenue at risk, identified expansion.

Nothing on it is a forecast. Every figure is the rate card applied to
units registered right now, or cash on an invoice already issued.

It spans every account, so it wants `CYBERLOGIX_ADMIN_KEY` rather than any
one customer's credential. The page holds no secret: it renders nothing
until the key is typed in, keeps it in `sessionStorage` so it does not
outlive the tab, and sends it as a header rather than a query string.

## Taking payment

An invoice went out and somebody had to notice the money and type it in.
`payments.py` closes that loop: a Stripe webhook at
`/api/payments/stripe`, written without the Stripe SDK because the
signature is thirty lines of HMAC and a dependency that must be
installed before the tests run is a dependency that makes them optional.

Three things carry it.

**The signature is the authentication.** The endpoint takes no
credential of ours, because Stripe holds none — so without the check the
URL is a button for marking every invoice in the system paid, which is a
better attack than breaking in because it looks like being paid. It is
verified over the exact bytes received (re-serialising the JSON breaks
it, which is how this check usually gets "fixed" into uselessness) and
with a timestamp tolerance, because a valid signature replayed next week
is still a valid signature. Unset secret means the endpoint refuses
everything rather than trusting the body.

**Retries are free.** Stripe delivers at least once and retries any
non-2xx for days. One card payment arrives as three events (the
checkout, the payment intent and the charge), and all three name the same
payment intent, so that is the payment reference and the ledger's own
duplicate check settles a storm of retries, in any order, exactly once —
and an event we do not handle gets a polite 200 rather than a refusal
Stripe will keep re-sending.

Only the checkout session carries a payment link's invoice id. The
payment intent and the charge do not, so when one of them is already on
an invoice it is recognised as that payment rather than listed as money
nobody can place; and when one arrives first and is listed, the checkout
that follows matches it and takes it off the list.

**Unmatched money is visible.** A payment that names no invoice, names
one that does not exist, arrives in another currency, or belongs to a
voided invoice is recorded rather than dropped, and never guessed at.
Guessing is how a customer gets credited for a payment they did not make
and chased for one they did. Every row on `/api/payments/unmatched` is
somebody who has paid and is still being chased, so it goes in the daily
digest too.

For any of that to match, the payment has to say which invoice it is
for: `CYBERLOGIX_PAY_URL` may contain `{invoice}`, which becomes the
invoice id — for a Stripe payment link, `?client_reference_id={invoice}`.

## The books

Tax is not a cost this codebase can cut. It is a share of profit, and
the levers on it — entity structure, timing, deductions — depend on
facts that live outside this repository and belong to an accountant.

What is in scope is making that person cheap and making sure nothing is
left on the table. `/api/books` produces the sales ledger, the cash book
and both as CSV, because an accountant handed a reconciled ledger costs
less than one handed a database, and a deduction nobody evidenced is a
deduction nobody claims.

Expenses go in at `POST /api/books/expenses` and come out at
`/api/books/expenses.csv`. That endpoint is the only genuine lever on a
tax bill that is actually in your hands: tax is charged on profit,
profit is revenue minus what you can evidence, and an expense nobody
records is a deduction nobody claims. Entries without a receipt
reference are flagged rather than refused — still a real expense, just
one you may not get to keep if anybody asks.

It reports the period on **both bases and picks neither**. An invoice
issued on 28 December and paid on 4 January is revenue in one tax year
under accrual and the next under cash; which applies is a question about
the entity, not about the data. The cash book is only possible because
each payment is now kept with its own date and reference — while an
invoice had a single `payment_reference` that every payment overwrote,
no cash-basis figure could be derived from it at all.

The expense side says plainly that it is partial. The application knows
what it spends on models, telephony and infrastructure; it has no idea
about hardware, professional fees, software or travel, which are
probably the larger half. Presenting the metered spend as the whole
expense side would cost more in unclaimed deductions than the export
saves in effort.

Nothing here computes anybody's tax, and nothing here is a return.

## Where the money actually goes

`/api/margin` ranks the cost lines against the rate card. The order is
not the one anybody guesses, per restaurant location per year:

| line | cost | of revenue |
|---|---|---|
| Annual prepay discount (10%) | $1,198.80 | 10.00% |
| Bad debt (3%) | $359.64 | 3.00% |
| Card fees (2.9% + 30¢) | $351.25 | 2.93% |
| Metered usage (AI, SMS, voice) | $5.60 | 0.05% |

The discount we *choose* to give away costs more than every real cost
combined, and no statement shows it, because nobody invoices you for
revenue you never charged. The metered spend this codebase has an entire
module of controls for is a rounding error.

### Measured, not assumed

Bad debt and the card share started as constants in a settings file.
That is defensible on day one and indefensible once there is a year of
invoices in the database — and worse, a guess is the one number that can
be improved by editing it, which improves nothing except the dashboard.

Both are now computed from the ledger once there are twenty invoices old
enough to judge (past terms plus the delinquency window, so last week's
billing is not counted as a loss). Under that floor the configured
assumption is used and the report says which and why: three customers
where one paid late is not a 33% bad-debt rate, it is three customers.

**Measuring is allowed to make the number worse, and on the first real
ledger it did.** The assumption was 3%; the ledger said 6.67%. Had the
constant been lowered to hit a target, the dashboard would have read 96%
while the business made 91.7%.

`/api/margin` reports the pre-tax margin against
`CYBERLOGIX_TARGET_MARGIN_PERCENT`, names the single largest line
standing in the way rather than listing four, says what bad-debt rate
would meet the target with everything else held, and — because nobody
collects from a percentage — names the accounts, what they owe, how late
they are and how to reach them.

`margin.prepay_verdict()` asks whether the discount pays for itself. A
discount is a purchase — certainty and cash, bought with margin — worth
the bad debt it avoids, the card fees it avoids, and whatever holding
the money early is worth. At the defaults that is 5.9%, so ten is four
points of gift. The financing term is zero unless somebody sets it,
because a company with these margins and no capital needs is not short
of cash, and assuming otherwise is how a discount justifies itself.

Nothing here changes a price. The discount became a setting with its
current value as the default: a smaller one is only a saving if the
customer still prepays, which is a commercial call rather than a code
change. And nothing here models tax, which is larger than every
operating cost put together and belongs to an accountant.

The one cut actually made: invoices now lead with bank transfer and
offer card second. Most finance departments pay by whichever method the
invoice leads with, and a card processor takes 2.9% of everything it
touches — $348 a year per location for a convenience nobody asked for on
contracts that settle by wire anyway.

## The reseller channel

A refrigeration servicer with two hundred restaurant clients already visits
every site and is already called at 2am when a walk-in fails. Selling to
them once puts the product in two hundred kitchens.

Partners are minted by the platform operator (`X-CyberLogix-Admin`, gated
on `CYBERLOGIX_ADMIN_KEY`; unset means those endpoints are **closed**, not
open) and earn a share of what their accounts bill — paid on collected
revenue, so they earn from accounts that stay.

The portal at `/partners` shows their book, what it earns them, which
accounts need an engineer today, and one account in operational detail. A
partner is a principal with narrower rights than a tenant owner: scoped to
their own accounts, and shown what an engineer needs without the customer's
roster or audit trail. Setting one up, step by step, is in
[PARTNERS.md](PARTNERS.md).
