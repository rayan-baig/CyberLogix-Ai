# Changelog

What changed in CyberLogix AI, newest first, in plain words. The number at
the top must match the `VERSION` file; a test checks that it does.

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
