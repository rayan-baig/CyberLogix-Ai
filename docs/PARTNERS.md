# Partners (resellers)

A partner is a firm that sells and looks after CyberLogix for its own
customers: a refrigeration servicer, an installer, a facilities company.
They already visit the sites and get the call when a freezer fails.

This page is the whole job, start to finish: what a partner gets, how to
set one up, what happens each month, and what the product does not do.
`tests/test_partner_walkthrough.py` walks these steps in this order, and
the browser smoke test (`tools/console_smoke.js`) signs in to the portal
with a real partner key.

In the commands below, `$ADMIN` is the platform operator's key
(`CYBERLOGIX_ADMIN_KEY`), `$PARTNER_KEY` is the partner's key, and
`cyberlogix.example.com` stands for your own address.

## What a partner gets

- **A portal at `/partners`.** They sign in with their partner key.
- **Their book:** each business they look after, its units, whether any
  are offline or have a flat battery, and any open alert.
- **What needs a visit:** the businesses with an open alert, a unit
  offline or a low battery, at the top.
- **One business in detail:** each unit, where it is, when it last
  reported, battery and signal, and the open alerts. Enough to send an
  engineer.
- **A statement:** what each business paid in the last 30 days and the
  partner's share of it.

What a partner does **not** see: the business's on-call list (names and
phone numbers), its people, its audit trail, its invoices, and any
business that is not theirs. A partner cannot change anything in a
business's account.

## How commission works

- The partner earns a percentage of the money their businesses actually
  paid in the last 30 days. Not the price list, and not a share of any
  loss avoided.
- A business on its free trial, or one that has not paid, earns nothing.
- A cancelled (void) invoice earns nothing. Half paid earns half.
- The rate is set when the partner is made: 20% unless you say
  otherwise, and anything from 0% to 50%.

### Partners and "bring a friend"

Businesses have their own referral codes (docs/BILLING.md, Bring a
friend). The rule between the two is one business, one reward:

- If a partner manages a business when it pays its first invoice, the
  partner earns its commission on it as usual and **nobody earns a
  referral credit** for it, even if it signed up with another business's
  code. The referrer's console says the business is looked after by a
  partner.
- A business a partner manages can still hand out its own code and earn
  credits. A credit lowers what that business pays, and commission is a
  share of what it actually paid, so the partner is never paid on money
  the business did not pay.
- Partner keys have no referral code and earn no referral credit.

## Setting up a partner (the operator)

### 1. Make the partner

```
curl -X POST https://cyberlogix.example.com/api/partners \
  -H "X-CyberLogix-Admin: $ADMIN" -H 'Content-Type: application/json' \
  -d '{"company_name": "Coldline Service Co", "contact_name": "Ray Diaz",
       "contact_email": "ray@coldline.example", "commission_percent": 15}'
```

The answer holds `partner_id` and `api_key`. **The key is shown once.**
Give it to the partner by a route you trust (not plain email), and tell
them to keep it where someone who leaves cannot read it. Only its last
few characters are kept on our side.

### 2. The partner's customer signs up

Their customer starts a trial at `/signup`, like anyone. The partner can
help them do it on site. There is no special link or code for partners.

### 3. Find the business

Attaching a business needs its account ID. Search by its name or the
email it signed up with (at least two characters):

```
curl -H "X-CyberLogix-Admin: $ADMIN" \
  'https://cyberlogix.example.com/api/partners/businesses?search=bella+vista'
```

Each match shows `tenant_id`, the name, the plan, how many units it has,
and which partner already looks after it, if any.

### 4. Attach it

```
curl -X POST https://cyberlogix.example.com/api/partners/PTR-000001/accounts \
  -H "X-CyberLogix-Admin: $ADMIN" -H 'Content-Type: application/json' \
  -d '{"tenant_id": "TEN-000007"}'
```

A business can belong to one partner at a time. Attaching it to a second
is refused until it is taken off the first.

### 5. Tell the business

Tell the business that the partner can see its units and alerts. The
business can check who looks after it: `GET /api/licenses/me` names the
partner in `partner_name`.

### 6. The partner signs in

They open `https://cyberlogix.example.com/partners` and paste the key.
The portal keeps itself up to date while it is open.

## Each month

1. **Read the statement.** The partner sees it on the portal; you see
   every partner's total with:

   ```
   curl -H "X-CyberLogix-Admin: $ADMIN" https://cyberlogix.example.com/api/partners
   ```

   The two figures always agree: both are added up from the same
   per-business amounts.
2. **Pay the commission yourself.** Nothing here sends money to a
   partner. Pay it by bank transfer, then record it in `/book → Books`
   as an expense so the books show it.

## When something changes

| What happened | What to do |
|---|---|
| The key leaked or was lost | `POST /api/partners/{partner_id}/rotate-key`. The old key stops at once; the answer shows the new one, once. |
| Pause a partner | `POST /api/partners/{partner_id}/status` with `{"active": false}`. Their portal is refused until you send `{"active": true}`. |
| A business leaves the partner | `DELETE /api/partners/{partner_id}/accounts/{tenant_id}`. It is gone from their book at once and is looked after directly. |
| A business deletes its account | It disappears from the partner's book. Nothing else to do. |
| A partner stops working with us | Pause them, and take their businesses off them one by one. |

Every one of these needs `X-CyberLogix-Admin`. A partner key cannot do
any of them.

## What is not there yet

These are known gaps, not bugs. Each would be a small piece of work if
the channel needs it.

- **A partner cannot sign up a customer or attach one.** The operator
  attaches every business (steps 3 and 4).
- **No partner referral link.** A sign-up does not record which partner
  sent it. (Businesses have referral codes of their own; partners do
  not.)
- **The commission rate cannot be changed** after the partner is made.
  To change it today, a new partner has to be made and the businesses
  moved to it.
- **No payouts.** The statement says what is owed; paying it is by hand.
- **No partner emails.** Nothing is mailed to a partner: no statement, no
  alert. They read the portal.
- **No screen for the operator.** Setting up a partner is the commands
  above; `/book` does not have a partners tab.
- **A partner cannot be deleted**, only paused, so its history stays.
