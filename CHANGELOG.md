# Changelog

What changed in CyberLogix AI, newest first, in plain words. The number at
the top must match the `VERSION` file; a test checks that it does.

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
