# Mail and digests

The outbound mail transport and its guards, the emails that ask a trial
for the order, and the daily and weekly digests.

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
