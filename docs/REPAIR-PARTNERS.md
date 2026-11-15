# Repair partners (refrigeration technicians)

When a fridge or freezer is failing, the business needs a technician. The
forecaster and the pattern check already see it coming. This programme
puts the two together: the business can ask for a repair quote in one
tap, one approved repair company near it gets the request, and the repair
company pays us a fee for each lead.

We do not do repairs and we do not vouch for any technician. Every page
that mentions repairs says so in those words, and the business chooses
whether to call them.

It is not the reseller channel ([PARTNERS.md](PARTNERS.md)), whose
partners look after a business's units and see them, and not the
insurance partners ([INSURANCE-PARTNERS.md](INSURANCE-PARTNERS.md)), who
refer businesses to us. A repair partner sees a lead and nothing else.

## What the business sees

A unit card in the console shows **Get a repair quote** when one of two
things is true (`repairs.repair_signal`):

- **Failing.** The pattern check (`patterns.py`) calls its current warm
  spell a failure, with confidence: above the limit, not a defrost on its
  schedule, not a door, and not back down.
- **Drifting warm.** The forecaster (`forecaster.py`) sees a steady warming
  trend (r-squared at least 0.5) heading for the upper limit, with a risk
  of elevated, high or critical, and the shape is not a defrost or a door.

Tapping it opens a confirm screen that sends nothing. It shows the exact
summary that would be sent, a contact (name, phone, email: the signed-in
person and the business's phone, all changeable, and either phone or
email can be left empty), the address of the location (the site's
address when there is one, plus city and ZIP or postcode), the list of
what the repair company gets, and a box to tick: "Send these details to
one approved repair company that covers this address." Only an operator
or owner can send; a viewer sees the offer and no button.

After sending, the screen names the repair company and its phone, and
the card says "Repair quote sent to ... on ..." until the unit may be
sent again.

## What a lead holds, and nothing else

- the business name
- the contact person and the phone and/or email the business chose
- the address of that location
- a plain summary: the kind of unit (fridge or freezer, by its limit, and
  the name the business gave it), what we saw (in words, with the limit
  and the last reading), and since when

Never a sensor id, a reading history, a key, the business's own contact
details on file, or any access to the account. The partner key works on
the partner's own dashboard and nowhere else.

The lead email to the partner is marked sensitive, so its text is blanked
in our mail log once it is sent. Log lines name the lead and the partner
by id and mask the contact (`mask_email`).

## Rules

- **One partner per lead**, chosen fairly among approved, switched-on
  partners whose areas cover the address: whoever has gone longest
  without a lead goes first (never had one goes before anyone), then
  fewest leads, then the oldest partner (`repairs.take_next`). Picked and
  marked under one lock, so two requests at once go to two partners.
- **Areas** are postcode or ZIP prefixes (`021` covers `02110` and
  `02139-4307`; spaces and case do not matter) and city names (case and
  punctuation do not matter). Set by you at approval and changeable later.
- **One lead per unit every 3 days** (`LEAD_GAP_DAYS`). Another unit at
  the same business is its own lead.
- **Nobody covering the address** means nothing is sent and nothing is
  charged; the business is told so.
- **The application form** is rate limited: 3 a caller an hour, 30 in all
  an hour. Applying twice from one address files one application.

## From application to first payment

1. **They apply** at `/repair-partners` (public, in the sitemap): company,
   name, work email, phone, where they work, and optionally a website and
   a note about licences and insurance.
2. **You are emailed** at `CYBERLOGIX_OPERATOR_EMAIL`, with the ordinary
   mail sender. Unset, it still waits in the console.
3. **You decide** in the operator console, `/book`, **Partners** tab,
   under Repair partners: pick the application, type the ZIP or postcode
   prefixes and the cities they cover (at least one), optionally a fee
   for them, and **Approve with these areas**, or **Decline it** (a short
   polite note goes to them).
4. **On approval** they get a dashboard key, shown to you once and mailed
   to them once (`clx_rep_...`; only its fingerprint is kept). **New
   dashboard key** replaces it; **Switch off** stops their leads and their
   dashboard, and what they owe stays recorded.
5. **Leads arrive** by email and on their dashboard at
   `/repair-partners/dashboard`: each lead's four things, its fee and its
   state, what they have been charged, what they have paid and what they
   owe.

## Fees, bad leads and billing

- **The fee.** Each delivered lead is recorded as owed at the partner's
  fee: `CYBERLOGIX_REPAIR_LEAD_FEE_USD` ($35 by default), kept with the
  partner when approved, so changing the setting never changes an
  existing partner's fee.
- **A bad lead.** Within 7 days of a lead (`BAD_LEAD_DAYS`) the partner
  can mark it bad, with a reason, on their dashboard. You are emailed, and
  it waits under "Leads marked bad". You **Refuse it: no charge** (we agree
  it was bad; it is not charged) or **Keep the charge**. Until you decide,
  it shows as "in question" and is not in what they owe.
- **Money**, summed in Decimal to the cent (`repairs.ledger`): charged is
  every lead owed or kept; owed is charged less payments recorded.
- **Billing is by hand.** **Email them a statement** sends every lead,
  its state, the payments received and the balance, with your bank
  details from `CYBERLOGIX_REMIT_TO`. When their transfer arrives, record
  it under "Record a payment a repair partner made to us". Nothing here
  takes or moves money.

## When a business deletes itself

The lead (with the contact details) names the business and is erased with
it. The fee for it is a separate row that names nobody, so what the
partner owes stays right; their dashboard shows that lead as "the
business has since closed its account".

## Settings

| Setting | Default | What it does |
|---|---|---|
| `CYBERLOGIX_REPAIR_LEAD_FEE_USD` | `35` | What a repair partner pays per lead (0 to 1000), kept with each partner at approval |
| `CYBERLOGIX_OPERATOR_EMAIL` | — | Where applications and bad-lead notices go |

## The agreements

The privacy statement (agreements 2.7) says, in one sentence, that if a
business asks for a repair quote, the one repair company it is sent to
gets the business name, the contact and address it chose and a short note
about the problem, and nothing else.
