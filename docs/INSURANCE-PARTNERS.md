# Insurance partners (brokers and insurers)

Insurers and brokers pay out when a fridge fails and the stock is lost.
A business whose fridges are watched around the clock is a better risk,
and brokers and insurers meet many such businesses. This programme lets
them send businesses to us and earn a share of what those businesses pay.

It is not the reseller channel ([PARTNERS.md](PARTNERS.md)). A reseller
looks after a business's units and sees them. An insurance partner looks
after nothing and sees nothing from inside any business.

We are a monitoring service, not an insurer, and nothing here promises a
business a lower premium. What a business can do is show its insurer a
[proof of monitoring](PROOF-OF-MONITORING.md) report.

## From application to first payout

1. **They apply** at `/insurance-partners` (public, linked from the front
   page). The form asks for broker or insurer, company, name, work email,
   and optionally a phone number, website and a note. It is rate limited:
   3 a caller an hour, 30 in all an hour. Applying twice from one address
   gives the same answer and files one application.
2. **You are emailed** at `CYBERLOGIX_OPERATOR_EMAIL` with what they said.
   Unset, nothing is mailed and the application still waits in the console.
3. **You decide** in the operator console, `/book`, the **Partners** tab:
   **Approve** or **Decline**. A declined firm gets a short, polite note
   and may apply again later.
4. **On approval** the partner gets a partner code (`PARTNER-XXXX-XXXX`), a
   sign-up link (`/signup?ref=PARTNER-...`) and a dashboard key. The key is
   shown to you once and mailed to them once; only its fingerprint is
   kept. If mail is not set up, give it to them yourself.
5. **They refer businesses.** A business that signs up with the code gets
   the same welcome as a friend's referral: the longer free trial
   (`REFERRAL_TRIAL_DAYS`, 30 days). A partner cannot use their own code
   (the sign-up email must not be theirs).
6. **They earn.** Each time the business pays us, the partner's share is
   recorded as owed (below).
7. **You pay them**, by bank transfer or however you agreed, outside the
   app. Then record it on the Partners tab (**Record a payment made to a
   partner**: amount, date, reference). Nothing in the app moves money.

## How the share is worked out

* **Default: 10% of what the business pays us, for its first 12 months**,
  counted from its first payment. Both are settings:
  `CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT` and
  `CYBERLOGIX_INSURANCE_PARTNER_SHARE_MONTHS`.
* The percentage and months are kept with each partner when they are
  approved, so changing the settings later changes the deal only for
  partners approved after that. You can set a different share for one
  partner when approving (`share_percent`, `share_months`).
* It is on money that arrived, invoice by invoice, rounded to the cent
  (half up). A part payment earns its part when it arrives. Money paid
  over an invoice's total earns nothing. Payments after the months run out
  earn nothing.
* A late charge earns nothing (it is about lateness, not service).
* A business a reseller looks after earns its reseller a commission
  instead: one business, one share.
* **A refund takes back the share of what was refunded.** Record the
  refund the way you already do for a friend's referral (Unmatched cash
  tab, "A referred business was refunded", or
  `POST /api/referrals/{tenant_id}/refunded`), with the invoice number
  (`invoice_number`) and, for a part refund, the amount (`amount_usd`).
  A part refund takes back only the share of that amount: $20 refunded of
  a $59 payment takes back $2.00 of its $5.90. A full refund of a payment
  takes back that payment's whole share. Left without an invoice number,
  every payment so far counts as refunded in full. The business's later
  payments keep earning as usual, inside the share period. What was
  already paid out stays paid, and shows in the partner's balance.
* **A partner switched off keeps earning on businesses it already
  referred**, for the months its share runs. Switching off closes the
  dashboard and stops the code signing anybody new up; it does not cancel
  what the partner was promised for businesses that already joined. To
  end that too, agree it with the partner and record it by hand.
* **Earned − paid = owed**, each summed from the stored rows.
* What is owed outlives a business deleting itself; its name goes with it.

Worked example: a restaurant on Starter pays $59 a month. Its partner earns
$5.90 each month for 12 months, $70.80 in all. If it pays a year up front
($590), the partner earns $59.00 on that payment.

## What a partner sees

Their dashboard at `/insurance-partners/dashboard`, opened with their key:

* their code and sign-up link, and their terms,
* each business that joined with their code: **business name and plan
  only**, when it joined, until when it earns, and what it has earned,
* earned, paid and still owed, and each payment made to them.

What a partner never sees: readings, alerts, units, locations, people,
phone numbers, emails, bills or anything else from inside a business. The
partner key is accepted by `GET /api/insurance-partners/me` and nothing
else. The only way a partner sees anything about a business's fridges is a
proof of monitoring report link the business chose to give them, which is
open to anybody with that link and shows only the headline numbers.

The privacy statement says this in one sentence (agreements version 2.6).

## The operator's controls

All behind the platform key (`X-Kryozyn-Admin`):

| | |
|---|---|
| `GET /api/insurance-partners` | Applications, partners, what each is owed, the settings |
| `POST /api/insurance-partners/{id}/approve` | Code, link and key (shown once, mailed once) |
| `POST /api/insurance-partners/{id}/decline` | Declines, with a short note to them |
| `POST /api/insurance-partners/{id}/status` | `{"active": false}` switches a partner off: no dashboard, and their code signs nobody up. What they earned stays recorded |
| `POST /api/insurance-partners/{id}/rotate-key` | A new key, mailed; the old one stops at once |
| `POST /api/insurance-partners/{id}/payouts` | Records a payment you made: `amount_usd`, `paid_on`, `reference`, `note` |

Public: `POST /api/insurance-partners/apply`. Partner:
`GET /api/insurance-partners/me` with `X-Kryozyn-Insurance-Partner`.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT` | `10` | Share of what a referred business pays (0 to 50) |
| `CYBERLOGIX_INSURANCE_PARTNER_SHARE_MONTHS` | `12` | For how many months from its first payment (1 to 60) |
| `CYBERLOGIX_OPERATOR_EMAIL` | — | Where applications are mailed (also the daily digest) |
| `CYBERLOGIX_PROOF_REPORT_KEY` | — | Seals proof of monitoring reports ([PROOF-OF-MONITORING.md](PROOF-OF-MONITORING.md)) |
