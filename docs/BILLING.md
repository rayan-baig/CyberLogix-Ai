# Pricing, billing and the books

The rate card, plans, paying yearly and chain discounts; invoices,
contracts that bill themselves and collections; taking payment; the books
and the margin report; and the reseller channel.

## Pricing

Restaurants and pharmacies have a published price, per location. Every
other trade is priced by quote.

| Trade | Price | What it covers |
|---|---|---|
| Starter (a small cafe or shop) | **$59** a month, flat | 1 location, up to 3 fridges or freezers |
| Franchise Restaurants | **$149** / location / month | up to 10 units at that location |
| Retail & Hospital Pharmacies | **$199** / pharmacy / month | up to 10 units at that pharmacy |
| A unit past 10 at one location | **+$15** / unit / month | restaurants and pharmacies |
| Kitchen Safety Bundle | **+$29** / location / month | the daily OmniArmor report, and OmniArmor Pro for that location |
| Every other trade | **Custom pricing — ask for a quote** | sized to what is run |
| Setup | **No setup fee** | on any plan |
| Paying yearly | **10 × the monthly price** for 12 months | everything recurring: "2 months free" |
| Chains of 5–19 restaurants or pharmacies | **10% off** each location | location prices only |
| Chains of 20+ | **20% off** each location | location prices only |

The prices are `PRICE_BOOK`, `STARTER_MONTHLY_USD`,
`UNITS_PER_LOCATION`, `EXTRA_UNIT_MONTHLY_USD`, `ADD_ONS`,
`YEARLY_MONTHS_CHARGED` / `YEARLY_MONTHS_COVERED` and
`CHAIN_DISCOUNT_TIERS` in `pricing.py`, and every page reads them from
there: the pricing page and its calculator, the front page, the help
page, the industry pages, the sales sheets and sign-up.

### Paying yearly: pay for 10 months, get 12

A year paid up front costs ten times the monthly price and covers twelve
months (`pricing.yearly_price`), shown everywhere as "2 months free"
(about 16.7% off). It covers everything recurring on the bill: Starter
($590 a year), each location ($1,490 for a restaurant, $1,990 for a
pharmacy), extra units ($150 a year each), the Kitchen Safety Bundle and
the other extras. It is worked out after any chain discount.

How it bills (`contracts.py`):

- **One invoice for the year.** Twelve months of every recurring line,
  then one line taking two of them off: "Paid yearly: 12 months for the
  price of 10 (2 months free)". Nothing more until the year renews.
- **Added part-way through the year.** The billing pass (hourly) compares
  the estate's monthly price with what the year's invoice paid for
  (`Subscription.year_covered`). When it has grown, it issues one invoice
  for the difference at the yearly price for the days left of the year:
  a restaurant added with 183 of 365 days left is $149 × 10 × 183/365.
  Shrinking refunds nothing, and the higher figure is kept, so taking a
  unit off and putting it back is not charged twice. The part-period
  catch-up that monthly plans carry on their next invoice is not used on
  a yearly plan, so nothing is charged twice. Days spent paused count on
  neither side of the fraction: a pause makes the year longer, not
  longer to watch. If the year's invoice is voided and issued again, it
  is priced on the estate as it is then, less what mid-year invoices
  already charge ("Added part-way through the year: already billed on its
  own invoice"), so it comes to what the voided one did.
- **Renewal reminders** 30 and 7 days before the year renews
  (`YEARLY_RENEWAL_REMINDER_DAYS`), through the ordinary mail queue, each
  once: the key is the contract, the year and the reminder. A plan first
  seen five days out gets the 7-day one only. A plan that will not renew
  gets none.
- **Switching monthly ↔ yearly at renewal.**
  `POST /api/contracts/billing-interval {"billing_interval": "yearly"}`
  (owner) schedules it; it starts when what has been paid for runs out,
  the end of the paid month or year. The old contract hands over to a new
  one starting exactly there, billed the new way, at the rate the old one
  would have charged next. Asking for the interval the contract already
  has cancels a pending switch. `POST /api/contracts/renew` also takes
  `billing_interval`.

This replaced a 10% annual prepayment discount, which was a setting
(`CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT`, now ignored). One rule, so
the pages, the invoices and the margin report cannot quote different
yearly figures. A contract that took the old prepay is a yearly contract
and renews at ten months for twelve, which costs it less.

### Cancelling

`pricing.CANCELLATION_RULE`, shown at checkout (the contract form in the
console, `/api/contracts`, the sign response), on the pricing page, the
help page, sign-up and in the agreement:

> Cancel any time. Nothing more is billed after you cancel, and the
> service carries on to the end of the month or year you have already
> paid for. What you have already paid is not refunded, in part or in
> full.

`POST /api/contracts/cancel` returns `refund_usd: 0` and
`service_until`, the end of the last period invoiced. Nothing is billed
after it, not even a mid-year addition.

### Pause instead of cancel, and the offer to stay

The console's Money tab has a **Pause or cancel** card (`retention.py`,
`/api/leaving`). For an owner of a business with a contract it shows, in
this order: pause for 1, 2 or 3 months, each with the date it ends; the
one-time offer to stay; and **Cancel my plan**, which is the same
`POST /api/contracts/cancel` as always, one press, never behind another
screen. A trial has nothing to pause or cancel. A business with no
contract can still pause (alerts stop); it has nothing to cancel.

**A pause** (`POST /api/leaving/pause`, owner only) starts at once and
ends on its own on the date shown:

- **No bills.** Billing here is our own invoices, so a pause moves the
  contract's next billing date, and every one after it, later by exactly
  the length of the pause (`Subscription.shifts`). No period starts while
  paused, the days already paid for are still there afterwards, and the
  term ends that much later. The billing run also skips a paused
  business, so not even a re-issued month goes out during a pause.
- **No alerts.** Readings are still taken and kept, but an out-of-range
  reading opens no incident and texts, calls and emails nobody, the sweep
  calls nobody and texts nobody about quiet sensors, and the weekly
  reassurance text is not sent. The console says so at the top in red:
  "Paused until ... No bills and no alerts until then".
- **Nothing is deleted.**
- **A reminder** is emailed to the business three days before it ends
  (the hourly pass, `retention.run_pauses`).
- **Coming back early** (`POST /api/leaving/resume`) turns alerts on at
  once and moves the next bill only by the time actually spent paused.
  Cancelling while paused does the same before cancelling, so the service
  runs to the end of what was paid for plus only the time used.

**The offer to stay** (`POST /api/leaving/stay`, owner only):
`CYBERLOGIX_SAVE_OFFER_PERCENT` (25) off the next
`CYBERLOGIX_SAVE_OFFER_MONTHS` (2) months of the contract, as a line of
its own on each of those invoices ("Thank you for staying: 25% off (month
1 of 2)"), worked out from the recurring lines after any chain discount.
On a yearly plan it is that percentage of two of the twelve months, on
the next yearly invoice. Once per business, ever, including after
cancelling and signing again. A voided invoice that carried it is
re-issued with the same line, never a third month. These are our own
invoice lines, not a card processor's coupon, so the settings are a
percentage and a number of months.

**The numbers.** Each pause, save and cancel is also written down as an
event that names no business (`retention_event`), and the operator
console's Money tab shows them: paused now, and pauses, saves and cancels
in the last 30 days and ever (`GET /api/leaving/numbers`). The operator's
morning email has the same line whenever any of them happened.

### Founding member codes

The company site's early-access list promises founding members a launch
discount by a code. Other products take it as a card processor's
promotion code at checkout; CyberLogix bills from its own invoices with
payment links, so there is no checkout for such a code to attach to.
Here the code is one of our own invoice lines (`founders.py`), made the
same way as the offer to stay.

- **The code** is `CYBERLOGIX_FOUNDER_CODE`, compared ignoring case and
  spaces. Empty means off: the sign-up page and the console say nothing
  about it, and it is not taken.
- **Entered** in the code box at sign-up (the box that also takes a
  friend's or a partner's code), or later on the console's Money tab, on
  the Bills card (`POST /api/founder-code`, owner only), but only before
  the business's first paid invoice. Entered after an unpaid first
  invoice, it starts with the next one.
- **Monthly:** `CYBERLOGIX_FOUNDER_PERCENT` (20) off each of the first
  `CYBERLOGIX_FOUNDER_MONTHS` (3) invoices, as a line of its own:
  "Founding member: 20% off your first 3 months (month 1 of 3)".
- **Yearly:** the percentage of that many months' worth, taken once, on
  the first yearly invoice: "Paying yearly: 20% off 3 months' worth, taken
  once on the first yearly bill". On $149 a month that is $89.40 off, and
  the two free months of a yearly plan are still there as before.
- **The base** is the recurring lines after any chain discount, the same
  as the offer to stay, so the chain discount applies first and the
  founder line after it.
- **Once per business, ever.** A voided invoice that carried it is
  re-issued with the same line, never an extra month.
- **Never with the offer to stay on one invoice.** When both would
  apply, the bigger line goes on, and the other is not used up by that
  invoice: it carries on to the next.
- **`CYBERLOGIX_FOUNDER_UNTIL`** (YYYY-MM-DD) is the last day the code
  can be entered. A business that entered it in time keeps the discount
  after that date, and so does one that entered it before the code was
  switched off.
- **The numbers.** Each code entered is counted with no business named
  (`retention_event`, event `founder`), and the Money tab of `/book`
  shows founder codes used in the last 30 days and ever
  (`GET /api/leaving/numbers`).

### Chain discounts

Restaurants and pharmacies take a discount on each location's price by
how many locations the business has, counted together:

| Locations | Each location | Restaurant | Pharmacy |
|---|---|---|---|
| 1–4 | list price | $149 | $199 |
| 5–19 | 10% off | $134.10 | $179.10 |
| 20+ | 20% off | $119.20 | $159.20 |

Twenty restaurants: $149 → $119.20 each, $2,384 a month, or $23,840 a
year paid yearly (`pricing.chain_example()`; the pricing page prints it
from there).

- **Self-serve.** Worked out from the locations on the account every time
  an invoice is priced, so it applies from the next invoice with nothing
  to sign. The invoice shows it on its own line: "Chain discount (20
  locations): −20%".
- **Location prices only.** Not Starter (one location), not the $15
  extra-unit fee, not the extras. "The price of each location drops" is
  the whole rule, and it is the one a customer can check: 20 × $149 × 20%.
- **No chain pays more than a bigger one.** Twenty locations at 20% off
  cost less than eighteen or nineteen at 10% off, so those pay the
  twenty-location price, and the line says so.
- **Stacks with yearly.** Yearly is ten months of the discounted price.
- A location added part-way through a month is caught up at the
  discounted per-location rate.

### One volume discount

There used to be two: the enterprise ladder (2.5% off for every ten
units, up to 10%), which priced operator-made volume contracts, and
nothing at all for a self-serve chain. Now the chain tiers are the only
one. Volume contracts (`/api/v1/enterprise-billing`) are priced with the
same tiers, and a business on a contract is billed from the contract
alone (`pricing.build_subscription` returns the contract line and adds
no chain line), so nobody can get both. Every contract price that
changed went down.

### The eleventh unit at a location

A location's price covers up to ten fridges and freezers there. Each unit
past ten **at the same location** is a flat **$15 a month**, on its own
invoice line ("2 extra units past 10 at a location, $15 each"), so the
location line keeps saying what it always said. $15 is a tenth of $149
rounded up: the eleventh unit costs about what each of the first ten
does, rather than a whole second location for one more fridge. Eleven
units at one shop and nine at another is one extra unit, not ten. A unit
not filed under any location, where locations are set up, is counted at
none of them (the customer gets the benefit of the doubt, as for the
location count). An extra unit added part-way through a period starts on
the next invoice; there is no part-period catch-up for it. The chain
quote (`/api/v1/enterprise-billing/quote`) adds the same charge to the
rate-card side when `units_per_branch` is over ten. A volume contract
covers every sensor in an enrolled branch, so it has no extra units.

### The trades priced by quote

Data centres, aviation hangars, superyachts, solar and storage, medical
labs, cryostorage, country clubs, cold-chain logistics, wine and art, and
cannabis cultivation are `custom_quote` in `PRICE_BOOK`. Their per-unit
rates stay in the code, because a business already on one of them is
invoiced from that figure, and keeps being. No public page, sales sheet
or unauthenticated catalogue (`GET /api/industries`,
`GET /api/billing/pricing`) shows the figure: they say "Custom pricing —
ask for a quote" and offer **Talk to us**, a mail to
`CYBERLOGIX_SUPPORT_EMAIL` (or `MAIL_REPLY_TO`) with the address also
printed as text that can be selected and copied. With no address set,
the link goes to the help page. The calculator says "Ask for a quote"
instead of a total when one of these trades is picked.

### Businesses already on the old prices

Every price went down on 2026-11-14 (`PRICES_CHANGED_AT`). An invoice is
priced from `pricing.py` on the day it is issued, so a business already
on the old prices **pays the new, lower price from its next invoice**:
nothing to migrate, and every invoice already issued stays exactly as it
was sent (invoices are frozen at issue). Starter $79 becomes $59, a
restaurant location $249 becomes $149, a pharmacy $199 a pharmacy instead
of $1,299 a fridge, and no setup fee is charged on a contract's first
bill, even one signed before the change. A contract's escalator applies
to the new rate.

The one way the new card could cost somebody more is a restaurant with
more than ten units at one shop, which paid a flat $249 for the shop. For
a business that signed up before the change, the extra-unit charge at a
location is held so that the location never costs more than its old
$249 (`OLD_LOCATION_PRICE_USD`); the invoice line says so. New businesses
pay the card.

### Invoices

The invoice is derived from the sensors actually registered.
`GET /api/billing` itemises a tenant's own estate; registering or
decommissioning a sensor reports what it adds to or removes from the bill.
A business on a trade priced by quote is billed per unit (a rack, a
vessel, a tank) at its rate in `PRICE_BOOK`.

### Plans are about contract, not price

| Plan | Unit ceiling | Locations | Term | Charged |
|---|---|---|---|---|
| `trial` | 5 | any | 14 days (30 with a referral code) | no |
| `starter` | 3 | 1 | 365 days | yes, a flat $59 a month |
| `growth` | 50 | any | 365 days | yes, the rate card |
| `enterprise` | 1000 | any | 365 days | yes, the rate card |

A trial estate is priced but not charged, so a prospect can see what their
real fleet would cost. Downgrades that would strand units are refused with
`409`; decommission sensors first.

### Starter, for a small cafe or shop

One location and up to three fridges or freezers for **$59 a month**, flat.
The price is `STARTER_MONTHLY_USD` in `pricing.py`; the three units and the
one location are `PLAN_TIERS["starter"]` in `domain.py`. Every page that
names it (the pricing page and its calculator, the plan list on the front
page, sign-up, the help page, the restaurant sales sheet, the console's
What you pay card) reads those values.

- **What is on it:** text alerts to the whole on-call list, the site-wide
  alert when several units warm at once, the inspector's log, the monthly
  report, tamper-evident readings and claim packets. Safety is never an
  upsell.
- **What moving up adds:** phone calls when a text goes unanswered, the
  forecast, the per-estate extras (the vault, benchmarks, equipment
  intelligence), and room for more units and locations. Starter takes one
  extra, the Kitchen Safety Bundle.
- **Which trades:** the restaurant limits (`STARTER_VERTICALS`). A vaccine
  fridge or a rack is a different job at a different price.
- **No setup fee**, like every plan.
- **The way up, with no surprise charge.** The fourth unit, a second site,
  or a unit on another trade's limits is refused with `409` and a sentence
  that names Growth, its price for a restaurant, the button or call that
  moves, and says nothing has been charged. Moving onto Starter with more
  than it holds is refused the same way. Growth cannot drop itself back to
  Starter (an operator can).

**On the invoice.** One line, "Starter plan: 1 location, 2 of 3 units",
at $59. Units added during a period on Starter owe nothing extra. A
business that moves to Growth part-way through a period owes, on the next
invoice, the rate card for the units it had at the moment it moved, less
the $59 already billed for those days, for the rest of that period
("Moved from Starter to Growth on 2026-11-20: the rate card less Starter,
part period to 2026-12-01"). From then on it is billed at the rate card
in advance like everyone else, and a shop added after the move is caught
up as ordinary mid-period arrears. Each plan change is recorded with its
time (`plan_change` rows), which is what the bill reads.

### Enterprise volume contracts

The operator can put a chain on a contract billed by enrolled branch
count, which covers every sensor inside those branches. It is priced with
the chain discount tiers above, on that vertical's own rate:

| Branches | Discount | Restaurant @ $149 |
|---|---|---|
| 1–4 | list | $149 |
| 5–19 | −10% | $134.10 |
| 20+ | −20% | $119.20 |

There is **no ceiling on a contract**. A cap is a hard stop on revenue from
exactly the accounts worth the most; at these rates a chain of eleven vessels
would have hit a $50,000 cap and every vessel after that would be free.

**No estate is charged more than a larger estate would pay.** The discount
steps a whole tier at a time, so the tiers alone would make 20 branches
cheaper than 19; the smaller estate gets the lower figure instead. A test
walks every vertical from 1 to 200 units asserting the bill never falls.

`GET /api/v1/enterprise-billing/tiers` publishes the tiers, and every quote
carries a `next_tier` block naming where the next discount lands.

`POST /api/v1/enterprise-billing/provision-cluster` opens a contract;
`/quote` prices both models side by side before anyone signs, both with the
chain discount, so they differ only in what a branch covers. An active
contract supersedes the rate card, and the rate-card figure is kept alongside
for comparison rather than charged.



### Add-ons, and why none of them is a share of savings

| Add-on | Basis | Price |
|---|---|---|
| Certified Compliance Vault | per estate | $499 / month |
| Sector Benchmarks | per estate | $299 / month |
| Equipment Intelligence | per estate | $399 / month |
| Kitchen Safety Bundle | per location | $29 / month, includes OmniArmor Pro for that location |

The **Kitchen Safety Bundle** is the CyberLogix half of the bundle with
OmniArmor ([OMNIARMOR-CONNECTION.md](OMNIARMOR-CONNECTION.md)). It is
billed while the OmniArmor connection is switched on, not picked from a
list, so nobody pays for a connection they removed: connecting adds it to
the next invoice at $29 for each location (the sites set up, or one),
disconnecting takes it off. It **includes OmniArmor Pro for that
location**: OmniArmor waives its own Pro fee for a workspace whose daily
reports arrive signed by this server (`KITCHEN_BUNDLE_SECRET`,
[OMNIARMOR-CONNECTION.md](OMNIARMOR-CONNECTION.md#the-signature)). It is
free on a trial, so a kitchen can try it first, and it is the one extra
Starter takes. The figure the MTRX Horizyn bundle page shows is
`pricing.bundle_price_label()`: "+$29 a month per location", and
`bundle_includes_label()` is "Includes OmniArmor Pro for that location".

Every one is a fixed fee. A percentage of what a customer saved sounds
aligned and is not: a quiet year pays nothing for the same standing
obligation, and on a marginal claim it puts the vendor on the wrong side of
the customer. A test asserts no add-on can be priced any other way.

### The terms that decide the other half

The rate card is half of what an estate is worth.

- **No setup fee.** It was $1,500 a site at commissioning; it is now $0
  on every plan, so the first bill is the first month and nothing else.
  `SETUP_FEE_PER_SITE_USD` is still the one setting the deal, the invoice
  and a contract's first bill read, so all three say $0 together.
- **Paying yearly: 2 months free.** Ten months' price for twelve. It
  removes the collections problem and fixes the customer for a year;
  whether it is worth what it costs is `margin.prepay_verdict()`'s
  question (below).
- **A 5% annual escalator** on multi-year contracts. Uncontroversial at
  signature and compounding — three years is 5.1% more contract value for
  no additional delivery.

`GET /api/billing/deal?years=3&annual_prepay=true&include_add_ons=assurance,vault`
returns the whole thing in one figure (its setup block reads $0, "No setup
fee."; `annual_prepay=true` is the yearly plan, ten months for twelve), so
nobody discovers an escalator afterwards. That is the only way
that term survives a renewal conversation.

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

## Account credit

Money we owe a business comes off its next invoice as a line of its own,
"Credit: ..." with a minus amount, after any yearly or chain discount
(`credits.py`). Each credit is granted once per reason (its id is the
reason, so a retried payment cannot grant it twice), never takes an
invoice below zero, and carries what is left to the next invoice. An
invoice the credit pays in full is recorded as paid by the credit, so
nobody is chased for $0. Voiding an invoice gives back the credit it
used, and the invoice that replaces it takes it again. Late charges take
no credit. Both the billing run and a hand-issued invoice go through the
same function, so neither can forget.

## Bring a friend

Each business has a referral code, which its owner sees under Money,
Bring a friend (`GET /api/referrals/me`, owners only), with a link to the
sign-up page that carries it (`/signup?ref=FRIEND-XXXX-XXXX`).

- **The friend** gets a 30-day trial instead of 14 (`REFERRAL_TRIAL_DAYS`).
- **The referrer** gets one month free on one location when the friend
  pays its first invoice in full: a credit of the referrer's own location
  price (Starter: $59; a restaurant on the rate card: $149; a pharmacy:
  $199; an estate billed per unit: its cheapest unit), on its next
  invoice.
- **Not yourself.** A code is refused at sign-up for the email or phone of
  the business that owns it, and the reward is refused if the two
  businesses share a person (one sign-in can open both). Adding a business
  from your own sign-in takes no code.
- **Once per business.** However often it pays, and however often the
  same payment is delivered.
- **Only real money.** Nothing for a part payment, a $0 invoice paid by
  credit, or a business that cancelled before paying. If the friend's
  payment is refunded, the operator records it on `/book`, Cash ("A
  referred business was refunded", `POST /api/referrals/{tenant_id}/refunded`)
  and what is left of the reward is withdrawn.
- **A ceiling** of 12 rewards per referrer in any 365 days
  (`REFERRAL_REWARDS_PER_YEAR`).
- **Partners do not stack.** A business a reseller partner manages when it
  pays its first invoice earns the partner its commission and earns nobody
  a referral credit: one business, one reward, and the partner, who looks
  after it, is the one paid. A partner-managed business can still refer
  others; its credit lowers what it pays, and commission is on cash
  received, so the two never land on the same money.

The console lists who joined by business name only, never the person, and
what was earned. The monthly report says what was earned that month and
what is waiting to come off the next invoice.

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
annual escalator, a one-time setup fee (there is none now), a discount
for paying a year up front. Nothing carried any of it into an invoice. Billing was a
button somebody had to remember to press, at the month-one rate, forever.
On a $4,000-a-month estate signed to three escalating years the gap
between the deal as quoted and the deal as billed is $7,320.

`POST /api/contracts` makes the signed deal durable — term, escalator,
monthly or yearly, add-ons — and from then on invoices issue themselves. The
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
| Yearly plan: 2 months free | $298.00 | 16.67% |
| Card fees (2.9% + 30¢) | $55.45 | 3.10% |
| Bad debt (3%) | $53.64 | 3.00% |
| Metered usage (AI, SMS, voice) | $5.64 | 0.32% |

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
the money early is worth. At the defaults that is 5.9%, so two months
free (16.67%) is about eleven points of gift: a choice made for the
customer's sake, and published next to its cost. The financing term is zero unless somebody sets it,
because a company with these margins and no capital needs is not short
of cash, and assuming otherwise is how a discount justifies itself.

Nothing here changes a price. The yearly offer is one rule in
`pricing.py`: a smaller discount is only a saving if the customer still
pays yearly, which is a commercial call rather than a code change. And nothing here models tax, which is larger than every
operating cost put together and belongs to an accountant.

The one cut actually made: invoices now lead with bank transfer and
offer card second. Most finance departments pay by whichever method the
invoice leads with, and a card processor takes 2.9% of everything it
touches — about $52 a year per location for a convenience nobody asked for on
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
[PARTNERS.md](PARTNERS.md). A partner's commission and a referral credit
never apply to the same business (see Bring a friend, above).
