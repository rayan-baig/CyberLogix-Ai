# Changelog

What changed in CyberLogix AI, newest first, in plain words. The number at
the top must match the `VERSION` file; a test checks that it does.


## 3.13.0 — 2026-11-14

Yearly plans at two months free, and chain discounts anyone can see.

- **Pay yearly: 12 months for the price of 10.** A year paid up front is
  ten times the monthly price ("2 months free", about 16.7% off), on
  everything recurring: Starter ($590 a year), each restaurant ($1,490),
  each pharmacy ($1,990), extra units ($150 a year each), the Kitchen
  Safety Bundle and the other extras. One invoice for the year, with the
  two free months on their own line, then nothing until it renews.
- **It replaces the 10% annual prepayment discount.** That was a setting
  (`CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT`, now ignored) quoted as
  "10% off that year". There is one yearly offer now, everywhere. A
  contract that took the old prepay is a yearly contract and renews at
  ten months for twelve, which costs it less.
- **Added part-way through a paid year?** A location or units added
  mid-year are billed at once, for the rest of that year at the yearly
  price, on one small invoice. Taking something off refunds nothing, and
  putting it back is not charged twice.
- **Renewal reminders** 30 and 7 days before a yearly plan renews, each
  sent once, with what the next year will cost.
- **Switch between monthly and yearly at renewal.** From the contract
  card in the console (or `POST /api/contracts/billing-interval`); the
  switch starts when what has been paid for runs out. A renewal can also
  switch.
- **Cancelling, plainly:** nothing more is billed, the service carries on
  to the end of the month or year already paid for, and what was paid is
  not refunded. Said at checkout (the contract form), on the pricing page,
  the help page, sign-up and in the agreement; the cancel response gives
  the date the service runs to.
- **Chain discounts, self-serve:** 10% off each restaurant or pharmacy
  location from 5 locations, 20% off from 20, counted together. On its
  own invoice line ("Chain discount (20 locations): −20%"), from the next
  invoice, with nothing to sign. Location prices only: not Starter, not
  the $15 extra-unit fee, not the extras. No chain pays more than a
  bigger one, so 18 or 19 locations pay the 20-location price. Yearly is
  worked out on the discounted price: 20 restaurants are $119.20 each,
  $2,384 a month or $23,840 a year.
- **One volume discount.** The enterprise ladder (2.5% off for every ten
  units, up to 10%) is gone; volume contracts use the chain tiers, and a
  business on a contract is billed from the contract alone, so nobody
  gets two discounts. Every contract price that changed went down.
- **The pricing page** has a monthly/yearly switch on the calculator, the
  chain discount on the calculator's lines, a "Paid yearly" column, a
  chain table for restaurants and pharmacies, the worked example (worked
  out from `pricing.py`, not typed) and a yearly panel with the
  cancellation rule. The help page has a new question, "Can I pay yearly,
  and what if I cancel?", and the chain discounts under "What does it
  cost?". The sales sheets, sign-up, the console's bill and contract
  cards, the price list API and the margin report say the same.
- **Agreements 2.5.** The subscription agreement now says how paying
  yearly works and what cancelling does. No prices are quoted in it.

## 3.12.2 — 2026-11-14

- **The real address, cyberlogix.mtrxhorizyn.com.** DEPLOY.md, the
  README, the staging, operations, alerts, partners, OmniArmor and sensor
  guides, `.env.example` and the comments in `render.yaml` now name it
  instead of a placeholder, with `help@cyberlogix.mtrxhorizyn.com` for
  customers and `no-reply@cyberlogix.mtrxhorizyn.com` for sending. The app
  still reads every address from its setting (`PUBLIC_BASE_URL`,
  `MAIL_FROM`, `CYBERLOGIX_SUPPORT_EMAIL`), and the in-app sensor guide
  still uses the address it is served from. `CYBERLOGIX_ALLOWED_ORIGINS`
  stays empty: MTRX Horizyn's console asks from its server, not a browser.

## 3.12.1 — 2026-11-14

- **The rate card is only for signed-in businesses.** The two price
  lookups behind the console's chain calculator now need a business key.
  The custom trades show "ask for a quote" on every public page, and these
  addresses no longer give their rates to anyone who asks.
## 3.12.0 — 2026-11-14

New, lower prices; no setup fee; a quote for every trade but two; and
signed Kitchen Safety Bundle reports, so the bundle can include
OmniArmor Pro.

- **Starter, $59 a month** (was $79). Still one location and up to 3
  units, with everything else as before.
- **Restaurants, $149 a month per location** (was $249), for up to 10
  units at that location. **Each unit past 10 at the same location is
  $15 a month**, on its own invoice line ("2 extra units past 10 at a
  location, $15 each"): about what each of the first ten costs, rather
  than a second location for one more fridge. The allowance is per
  location, so 11 units at one shop and 9 at another is one extra unit.
- **Pharmacies, $199 a month per pharmacy** (was $1,299), billed per
  pharmacy with the same 10-unit allowance. It used to count every
  fridge as a "pharmacy", the bug restaurants had before.
- **No setup fee**, on any plan (was $1,500 a site). Gone from invoices
  and a contract's first bill, the deal, the pricing page, the help
  page, the sales sheets and the docs.
- **Every other trade is "Custom pricing — ask for a quote"** on the
  pricing page and its calculator, the front page's sector cards, the
  industry pages, the sales sheets, the help page and the public
  catalogues, with a **Talk to us** link (a mail to the support address,
  also printed as text you can copy). Their rates stay in `pricing.py`,
  so businesses already on them are invoiced exactly as before. The
  calculator says "Ask for a quote" instead of adding up a trade it has
  no price for, and asks how many units each location has so it can add
  the $15 ones.
- **Businesses already on the old prices pay the new ones from their
  next invoice.** Invoices already sent are not changed. A restaurant
  that signed up before the change and has more than 10 units at a shop
  never pays more than its old $249 for that shop.
- **The Kitchen Safety Bundle stays +$29 a month per location, and now
  includes OmniArmor Pro for that location.** OmniArmor waives its Pro
  fee for a workspace whose daily reports arrive signed by a CyberLogix
  server: every report now carries `X-CyberLogix-Signature:
  t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>` over the
  exact bytes sent, keyed with the new `KITCHEN_BUNDLE_SECRET` setting
  (the same value on both apps). Unset, no header is sent and reports
  work as before.
- The agreements quote no price or setup fee ("Prices are on your
  order"), so their text and version (2.4) are unchanged.

## 3.11.0 — 2026-11-13

Three ways to earn: a $79 Starter plan, "bring a friend" referrals, and
the CyberLogix side of the Kitchen Safety Bundle with OmniArmor.

- **Starter, $79 a month.** One location and up to 3 fridges or freezers,
  for a small cafe or shop's first yes. Texts, the site-wide alert, the
  inspector's log and the monthly report are on it; phone calls and the
  forecast come with Growth. A real plan beside the others
  (`PLAN_TIERS["starter"]`), priced in `pricing.py`, with no setup fee.
  The fourth unit, a second location, or a unit on another trade's limits
  is refused with a sentence that names Growth and says nothing has been
  charged. Invoices bill one flat line; a move to Growth part-way through
  a period bills, on the next invoice, the rate card less Starter for the
  days after the move, and nothing for the days before. The pricing page,
  its calculator (still tested against `pricing.quote_monthly`), the front
  page's plans, sign-up, the help page, the restaurant sales sheet and the
  console's What you pay card all show it from the code.
- **Bring a friend.** Each business has a referral code and link (Money,
  Bring a friend, owners only). A business that signs up with it gets a
  30-day trial. When it pays its first invoice in full, the referrer gets
  one month free on one location, as a credit on its next invoice. Not for
  yourself or a business that shares a person with yours, once per
  business, nothing for a part payment or a business that cancelled first,
  withdrawn if the payment is refunded (the operator records that on
  /book), and at most 12 a year. A business a reseller partner manages
  earns its partner the commission instead of anyone a referral credit.
  The console shows who joined by business name only, and what was
  earned; the monthly report says so too.
- **Account credit,** which did not exist: a credit comes off the next
  invoice as its own line, never below zero, carries what is left, pays a
  $0 invoice outright so nobody is chased for it, and is given back if
  the invoice it was used on is voided.
- **The Kitchen Safety Bundle, +$29 a month per location,** free on the
  trial. The owner pastes OmniArmor's address and connection key (Setup,
  OmniArmor connection); the key is stored encrypted with AES-GCM under
  the new CYBERLOGIX_CREDENTIAL_KEY setting and never shown again. After
  each site's day ends, one combined report goes to OmniArmor exactly as
  its contract describes: units in range all day, each breach with the
  role that acknowledged it and what was done as fixed codes (never a
  name), the warmest reading, and a link to that day's inspector log.
  429 and 5xx are retried with backoff, respecting Retry-After; each day
  is claimed before it is sent, so it goes once, and again only as a
  correction when what it says has changed. "Last sent 2026-11-12 · OK"
  on the card, a "Send a test report" button, and one email to the owners
  when two days have not got through. The tests send every report through
  OmniArmor's own validation, ported. docs/OMNIARMOR-CONNECTION.md has it
  all.

## 3.10.0 — 2026-11-13

A site-wide alert when many units warm up at once, and a section on the
landing page about fridge controls being taken over.

- **The site-wide alert.** When 3 or more units at one site (or half of
  a site's units, never fewer than 2; 2 at night) start warming within
  20 minutes of each other, or 4 units across 2 or more of a business's
  sites (3 at night), the sweep raises one urgent alert on top of each
  unit's own: "Possible site-wide problem at Main Street: 5 of 7 units
  warming at once. Power cut, open door or tampered controls? Check now."
  It goes straight away to the whole on-call list for those sites and to
  the business's own number, by text, email and, on plans with calls, a
  call to every one of them with no ten-minute wait. Quiet hours do not
  hold it back. It uses the same send path, so spend caps, re-sends and
  outage handling apply, and it is claimed once, so two sweeps at once
  send it once.
- **It is not fooled by a defrost or an outage.** A unit in its own
  learned defrost, or whose defrost is due, is not counted; a defrost
  switched on hours early is. Sensors that have gone silent never count,
  and when half of a site's sensors go silent together it is treated as
  an outage and left to the quiet-sensor text. Another business's units
  never count.
- **"1" acknowledges it,** from anyone it reached including the owner,
  and so do the keypress and the Acknowledge button. Acknowledging it
  also acknowledges the open alerts of the units in it, so one answer
  stops every call for that site.
- **Shown as its own thing:** a red banner above everything in the
  console listing the units, a "Site-wide" card in the incident feed, its
  own section in the inspector's log, and its own lines in the monthly
  summary. It is never counted as an extra excursion.
- **Every figure is a setting** (`CYBERLOGIX_GROUP_ALERT_*`), explained in
  docs/ALERTS.md, and the help page has a new question about it.
- **The landing page** has a section, "Your fridges can be hacked. Your
  alarm shouldn't be.": in October 2026 attackers took over the fridge
  controls at hundreds of supermarkets in France; CyberLogix watches with
  its own sensors and only ever reads, so it still sees the warming and
  cannot be used to change a fridge. It says plainly that it cannot stop
  an attack: it finds out in minutes and calls your team. The restaurant
  and pharmacy pages carry one line saying the same.

## 3.9.0 — 2026-11-03

Server errors reported to one inbox, privacy-friendly usage counts, one
look for every email, and the plan and support address on the About card.

- **A server error is emailed,** if CYBERLOGIX_ERRORS_EMAIL is set: the
  time, the version, the method and route, the error, the reference the
  error page showed, and the stack trace. Never the request body,
  cookies, headers, the caller's IP, or anybody's email address or phone
  number. One email per distinct error an hour and at most 20 a day,
  counted in the database. The fault list is unchanged, and now also
  takes keys and tokens out of the error messages it keeps; before, an
  error message that quoted a key kept it.
- **Daily usage totals** for the operator: sign-ups, sensors added, test
  alerts, alerts acknowledged (by button, text reply and keypress),
  inspector's logs and help page views. Numbers per day, nothing about
  who, deleted after 400 days. In /book under Health, at
  /api/admin/usage, and in /api/stats for MTRX Horizyn's console. The
  privacy statement says so (version 2.4).
- **Every email has a plain-text part and an HTML version** in one
  600px column, made from the same text, with the "CyberLogix is a
  product of MTRX Horizyn" footer once. Before, only the monthly summary
  had an HTML part, and it left out the alerts its text listed.
  Licence reminders and the operator's digest now say where to change
  them; marketing mail keeps its unsubscribe link; alerts, bills and
  password resets say they cannot be switched off.
- **The About and help card shows your plan** and, when the deployment
  sets one, the address to write to for help, beside the version and
  the link to /help.

## 3.8.0 — 2026-11-03

A sheet to print for each industry, an API reference that needs no CDN,
partners set up end to end, and the runbook brought up to date.

- **A one-page sheet for every industry,** at /for/<industry>/sheet and
  linked from each industry page: the problem, the fix, where the alarm
  is set, what the text, email and call say, the prices and the free
  trial. Every figure on it comes from the code that charges and alerts,
  and it prints on one side of A4 or Letter.
- **The pricing page names the one-time setup charge** on a contract's
  first bill, and that terms longer than a year rise each year. Both were
  on the bills and on no page a buyer reads first.
- **An API reference at /developers,** linked from the help page: every
  endpoint a customer can call, the key it needs, its parameters and an
  example body, built from the routes on the server. It loads nothing
  from elsewhere and runs no script, so it works where /docs and /redoc
  are blank. Routes only we can call are not listed, and no key is shown.
- **Partners can be set up from start to finish.** The operator can find
  a business's account ID by its name or email, which attaching it to a
  partner needs and nothing showed before. A business can see the name
  of the partner that looks after it. docs/PARTNERS.md is the whole job.
- **A staging copy that cannot reach real people:** docs/STAGING.md, and
  CYBERLOGIX_NOINDEX, which keeps a copy that is not the live one out of
  search results.
- **DEPLOY.md** now covers the Twilio reply webhook, the support address,
  growing the disk to 5 GB, the restore steps, the monthly summary, the
  demo account, rotations and quiet hours, and checking the version.

## 3.7.0 — 2026-11-03

Paying late never silences an alarm; a monthly summary for owners; five
businesses on one sign-in; how to set up every kind of sensor; a demo
account.

- **A late bill can no longer stop the monitoring.** Under a signed
  contract the licence ran out before the next bill was even due, and
  two weeks later readings were refused and nobody was phoned about a
  warm freezer. While a contract runs, the most an unpaid bill does is
  pause reports, at 45 days, as the agreement says.
- **The console says when a bill is late,** from the day it falls due,
  with a Pay now link for that bill and how long before reports pause.
  It no longer counts a contract customer down to an end date that
  stops nothing.
- **A card payment is listed once.** Paying through the pay link also
  put the same money on the unmatched-payments list, waiting to be
  credited a second time by hand.
- **A monthly compliance summary** goes to each business's owners in the
  first week of the month: units, readings kept, every time something
  went out of range with who answered and how fast, what is still open,
  and a link to the inspector's log for the month. Once per business,
  through restarts; with an unsubscribe link.
- **Signing in opens the business you had open last,** if you have
  several. The link in the monthly summary opens the right business.
- **Setting up sensors, step by step,** at /help/sensors and in
  docs/SENSOR-SETUP.md: your own device, devices that put the key in the
  body, Monnit, SensorPush, a vendor's cloud, and anything that sends
  JSON. Every address, header and field in them is checked against the
  code.
- **A sensor key works where its notice says.** The send-anything
  address now reads X-CyberLogix-Sensor-Key; a device set up as told was
  refused.
- **A new rotation puts the first person on call straight away.** Saved
  before the day's handover time, the last person in the list was on
  call until it.
- **A demo account:** `python demo_account.py --db PATH` seeds a made-up
  restaurant group into a new database, prints how to sign in, refuses a
  database with anything in it, and keeps its backups out of the live
  backup folder.

## 3.6.1 — 2026-11-03

- **Server logs keep people's details out.** Log lines about texts and
  calls show only the last four digits of a phone number, and a provider's
  error text has numbers and addresses blanked. A new trial and a failed
  sign-in log the email address shortened, the way mail log lines already
  did.

## 3.6.0 — 2026-11-03

Who is on call, on a screen; and four fixes from a careful read of the
last two days' changes.

- **On call now.** A card on the People tab, under Who gets called: who
  is on call now and until when, who is next, the next turns in the
  rotation's own time, who is in no rotation and so always on call, and
  each person's quiet hours. Owners and operators build, change and stop
  a rotation there and set quiet hours; a viewer sees it all and can
  change nothing. An empty rotation, or a muted person with turns, is
  named as a gap on the card.
- **A long-named unit's alert costs one text, not two.** A name cut
  short to fit was marked with "~", which a text message sends as two
  characters, so a full-length alert was billed as two. The text is now
  measured the way the network counts it.
- **Removing a location removes its rotation.** Its people were released
  to the whole estate but the rotation still held them to their turns,
  and it could not be stopped or its people put in another one.
- **A sweep that fails waits for the next one.** A pass that raised was
  followed at once by another, back to back, flat out (for example with
  the disk full); it now waits its minute like any other.
- **Phone numbers and email addresses stay out of the fault list.** A
  refused text or email names the number or address, and the fault list
  is kept until somebody clears it, past a business deleting everything.

## 3.5.0 — 2026-11-03

Alerts on time under load, through outages and after a restore.

- **Load tested.** 250 to 5,000 sensors reporting every minute, with
  console users refreshing: no failed or lost readings, and a bad
  reading's text goes out in milliseconds. What we measured, and when to
  move off SQLite, is in docs/LOAD-TEST.md.
- **A restart no longer needs four times the memory.** Readings load one
  at a time, and each takes about a quarter of the room it did, so a
  server that ran all day can start again on the same machine.
- **Backups cannot fill the disk.** A snapshot that would leave the live
  database without room is refused and reported, and snapshots can be
  downloaded, so copying them off the machine is one command.
- **A restore sends nothing twice.** Calls, texts, digests and reports
  already sent before a restore are not sent again, and sensors are not
  reported as gone quiet because the snapshot is old.
- **A call that fails is tried again**, 5 and 15 minutes later, while
  Twilio is down. The alert email waits out a mail outage and goes when
  the mail host is back. If the texts fail, the owner's email says so,
  and the owner is told once if no text ever got through.
- **Rotations and quiet hours.** Take turns on call, handing over at a
  local time, through clock changes. Quiet hours hold back texts to a
  person only while somebody else is awake to get them; a breach always
  reaches somebody.
- **Sweeps run on the minute and one at a time,** and the first one after
  a restart is within seconds.

## 3.4.0 — 2026-11-03

- **Reply 1 to say you have it.** Answering an alert text with "1" or
  "ACK" stops the calls. It only works from a number the alert went to,
  and only for that business's open alert.
- **Alerts say what matters first.** Which unit, where, how far out of
  range, what to check and how to answer, in one text message. The same
  alert goes to the business's email, and the phone call says the same
  things and "press 1".
- **A log for the inspector, in one click.** Pick the dates; every unit's
  temperatures, each time one went out of range and who answered it,
  ready to print or save as PDF.
- **Invoices print on one page,** with the period they cover.
- **Withdraw a join code** you gave to the wrong person, before it is used.
- **Safer pages.** No page runs script or styling written into it, so text
  that reaches a page cannot be made to run.
- The public demo has the help page too.

## 3.3.0 — 2026-11-02

Ready for the first customers.

- **Help when you need it.** A help page answers the questions customers
  actually ask: the trial, prices, how alerts escalate, the test alert,
  and how to download or delete your data. It is linked from the console
  and the front page.
- **A sensor gone quiet comes with steps.** When a sensor shows as
  offline, the console and the simple view link to a short list of
  things to check, in order.
- **A price calculator.** The new pricing page works out a monthly price
  from your sectors and how many units you have, using the same rate card
  the invoices use.
- **A first day that guides itself.** A checklist that ticks itself, and
  a test alert that texts your team without opening a problem.
- **Friendly error pages.** A wrong link shows a page that looks like the
  rest of the site, and a fault never shows its inner workings.
- **Found, and shared, properly.** Icons, link previews, a sitemap of the
  public pages, and private pages kept out of search results.
- **The version is shown** in the console and in the health check.
- The company name is MTRX Horizyn everywhere a person reads it, and the
  service runs on its own subdomain of the company's domain.

## 3.2.0 — 2026-11-02

Alerts you can rely on at 3am.

- A sensor that stops reporting gets the team a text, once.
- An alert text that fails is sent again; a roster change that would leave
  nobody to alert is warned about first.
- A freezer's own defrost cycle no longer wakes anybody.
- An outside scheduler can drive the sweep, and is itself watched.
- Slack, Teams and PagerDuty messages retry once and never hold up a call.
- Readings, forecasts and sector documents use the account's own unit.
- Billing fixes: a part payment, a late fee and a paid period now each
  count for exactly what they should.
- Customer guides in plain words: your data, and the tamper-evident vault.
- Tests on every sector, on two Python versions, with type checks on the
  money code.

## 3.1.0 — 2026-11-01

Safer, and yours to take away.

- One sign-in can run several businesses, with a switcher between them.
- Owners can download everything, or delete everything, themselves.
- Security reviews closed gaps between businesses, in sign-in, in billing
  and in webhook addresses. Keys are stored only as fingerprints, and an
  owner can replace a leaked one.
- Spending caps hold under load, and premium-rate numbers are refused.
- Request sizes are capped, and every package is pinned.
- The tests run on every push.

## 3.0.0 — 2026-10-25

The first release under the CyberLogix AI name: sensors and readings,
breach alerts by text and call, the operations console, its own limits
for each industry, billing and invoices, the tamper-evident record, and
the public front page.
