# Mail and digests

The outbound mail transport and its guards, the look every email
shares, the emails that ask a trial for the order, and the daily and
weekly digests.

## Mail

Adding this made the privacy statement wrong, which is worth saying
plainly because it is the failure the module was built to prevent.
`legal.py` generates the terms from the code they describe, and the
sub-processor table named Twilio and Google while the mail host was
receiving every contact address, every invoice — which is what a
customer owes — and every weekly report, which is how much of their
estate stopped reporting. The table is now checked structurally rather
than by grepping for two names: a row added or removed without the
reviewed list agreeing fails the suite.

Backups had the same shape of problem. "Ask us to delete and we delete
everything" is untrue of any system that can survive losing a disk, so
the statement now says a deletion propagates as the daily snapshots age
out and that we do not restore one to bring back something you asked us
to delete. Both changes are material, so the terms version moved to 1.1
and every acceptance of 1.0 now shows as not current — which is the
mechanism working, not a bug in it.

`mail.py` is the transport the money engine runs on: SMTP over STARTTLS,
credentials from the environment, one message at a time. The interesting
part is not the socket. An unattended billing pass with a mail socket has
three ways to do real damage, and each has a guard:

* **Sending twice.** Every send claims a `dedupe_key` under the store's
  lock, and the claim is a persisted row — so a stage claim rolled back by
  a restart still cannot produce a second demand.
* **Sending nothing and reporting otherwise.** The message is written down
  before it is attempted. A crash between the two leaves a queued row the
  next pass retries; three attempts, then it is given up on, and never
  after `MAIL_MAX_AGE_HOURS`, because firing a month of stale notices the
  afternoon somebody configures SMTP is worse than sending none.
* **Writing to people who said stop.** An unsubscribe suppresses
  commercial mail. A bounce suppresses everything, invoices included: an
  address the host has refused cannot receive one, and continuing to try
  is how a sending domain stops being delivered for everybody.

The unsubscribe link is HMAC-signed so it cannot be pointed at another
customer, and the secret is persisted so a restart does not silently void
every link already sitting in an inbox. It deliberately does not stop
invoices — a footer link is not a way to opt out of being billed, and
pretending otherwise would be worse for the customer than saying so.

Two things were only ever going to surface by reading a message that had
actually crossed a socket, and both did. `List-Unsubscribe` was arriving
RFC 2047-encoded — valid, and useless as a URL, so a mail client showed
no unsubscribe button and the customer's only way out was the footer
link that fewer people find and more people replace with the spam
button. And the invoice carried a timestamp to the second, on a document
whose entire job is to be paid by somebody who wanted the date.

Unconfigured, nothing is lost and nothing is pretended: notices queue,
`/api/mail/status` names the missing settings, and the operator digest
puts it in the subject line.

## One look for every email

Every email has a plain-text part, which is the message, and an HTML part
made from that same text by `mail.compose`: the same words and the same
links, in one column 600px wide, with the footer once at the bottom. A
block laid out with spaces (an invoice's lines, a setup command) keeps
its spacing; everything a customer typed is escaped. The HTML is made
from the text rather than written beside it because two versions drift:
the monthly summary's HTML once left out every alert its text listed.

The footer always ends "CyberLogix is a product of MTRX Horizyn." Above
that it says how to stop or change the message, by kind (`mail.MUST_SEND`
and `mail.MANAGED`):

| Kind | Footer |
|---|---|
| Marketing: the trial emails, the weekly report, the monthly summary | The signed unsubscribe link, and the `List-Unsubscribe` header |
| Licence reminders, the operator's daily digest | A link to where they are managed (the console, `/book`) |
| Must-send: alerts, the "nobody was texted" email, a stopped vendor feed, a roster gap, invoices and reminders to pay, password resets, error reports | That it concerns the account and is sent regardless of marketing preferences. No link, because none of these can be switched off |

`tests/test_every_email.py` sends one of every kind through a small SMTP
server on the loopback address and reads each one back: both parts, no
unfilled placeholder, the footer once, nothing wider than 600px, the
same words and links in both parts, and the right way out. A new kind of
email has to be added to its list, or it fails.

## Asking for the order

`/book` can say *this trial ends Thursday, call them*, which assumes
somebody opens it on Thursday. `conversion.py` is the other half: the
trial talks to the customer itself, at six moments, each sent once.

Welcome, the minute they sign up, carrying the same four setup commands
the API returns — a trial with no sensor in it never converts, and the
usual reason is that nobody got past step one. An activation nudge two
days in with nothing registered. A week left, three days, the last day —
each carrying what the trial has actually caught and the rate card
applied to the units they have running, never a brochure figure. Then
grace, and the day monitoring stops.

A missed window is skipped, never caught up on. Three rungs of a ladder
arriving in the same minute teaches the customer something true and
fatal: nothing here is really watching anything.

## Digests

Whoever runs the company is not always at a screen, which makes "open
the dashboard" a plan that fails on any day they are busy.

`CYBERLOGIX_OPERATOR_EMAIL` gets the book once a day: what was billed,
what arrived, who to call with their phone number, and — the part that
matters most — what is quietly broken. Mail that cannot be sent looks
exactly like mail nobody needed, so an unconfigured transport goes in the
subject line rather than a log nobody opens.

The other direction is a weekly report to each paying estate. A
monitoring product that works is invisible by construction, and at
renewal the customer cannot remember what they are paying for. Four
thousand readings, two excursions caught, and any unit that has stopped
reporting — that last line is worth more than the rest, because a silent
sensor is the one failure a monitoring product cannot alarm on. Units
that have *never* reported are counted separately from units that
reported and then stopped: the first is an installation somebody did not
finish, the second is hardware that has failed, and calling them the same
thing produces a number that is always non-zero and therefore always
ignored.

## The monthly compliance summary

Once a month each business's owners get the month on one page
(`monthly.py`): how many units were watched, how many readings were kept
and what share were in range, every time something went out of range,
who acknowledged it and how quickly (each person's typical and slowest
time, by name, never by address), and what is still open now — alerts
not closed, units that stopped reporting or never started, low
batteries. It links to the inspector's log for that month, which opens
the console on the right business with the dates filled in; the figures
in the email are read from the same log, so the two cannot disagree. If
the readings kept do not cover the whole month, it says so.

- **Owners only.** It names the staff who answered each alert.
  Operators and viewers are not sent it; an owner removed from the
  business is not either.
- **Once per month per business.** Each owner's copy has its own message
  key, claimed before it is sent, and the business's month is marked done
  in the database when the pass has been round its owners. A restart
  sends nothing twice, and a restore from before it went out carries the
  mark forward. An owner added after it went does not get last month's.
- **One pass at a time.** The hourly money pass runs it; an operator can
  too, with `POST /api/digest/monthly` (admin key). A pass that finds one
  running steps aside, as a second fleet sweep does, and it has its own
  lock, so a month of reports never holds up the sweep that phones people.
- **When.** From the first of the month after `CYBERLOGIX_DIGEST_HOUR_UTC`,
  for a week. A server down on the first still sends it; after a week the
  month is left unsent rather than arriving late.
- **Paused with the other reports.** A business more than 45 days behind,
  or past its licence's grace, gets none until it is current, as the
  agreement says of reports. Settled inside the week, it goes. Alerts are
  never touched.
- **Stopping it.** It is mail we choose to send, so it carries the
  signed unsubscribe link and the `List-Unsubscribe` header, honours a
  stop, and has the footer naming MTRX Horizyn. Like every email, it has
  a plain-text part and an HTML part made from it (below).

`GET /api/digest/monthly` (any signed-in role, or the business key;
`?month=YYYY-MM` for another month) shows what this business's summary
says, who it goes to and whether it has gone, without sending anything.
