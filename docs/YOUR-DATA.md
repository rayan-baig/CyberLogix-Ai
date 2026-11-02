# Your data: take it, or delete it

Your business's records are yours. You can download all of them at any
time. You can delete all of them at any time. You do not have to ask us,
and you do not have to give a reason.

This page says how, what we keep afterwards, and why.

## Who can do this

Only an **owner** of the business. An operator or a viewer cannot.

You must be **signed in as yourself**. The business's master key (the
`X-CyberLogix-Key`) does not work here. So a leaked key cannot take a
copy of your business, or wipe it out.

## Download everything

In the app, open the **Setup** tab. Find **Download or delete
everything**. Press **Download everything**.

You get one file. It holds every record we keep for your business:

- the business and its settings
- sensors and sites
- readings and alerts
- the people on your team, and who gets alerted
- the mail we sent you
- your bills and payments
- the "who did what" record

Some things are left out on purpose:

- **Passwords, keys and sign-in codes.** Never, not even for an owner.
- **Passwords for other services** you connected, such as a sensor
  maker's account. These show as `(hidden)`.
- **Alert web addresses** (for Slack, Teams and so on). Only part of each
  one is shown.
- **An email that held a sign-in link.** Its text shows as `(hidden)`.
  That link could open someone else's account.

The file is in JSON. That is a plain text format that spreadsheets and
most software can read.

### One thing to know about readings

We keep only the **newest 500 readings** for each sensor. At one reading
every five minutes, that is under two days. Older ones are deleted.

Readings around an alert are saved with the alert, so those last longer.

If you need a longer history, download it often. Every report says how
far back it can see.

### With the API

```
GET /api/accounts/business/export
Authorization: Bearer <your sign-in token>
```

For example:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -o my-business.json \
  https://YOUR-SERVER/api/accounts/business/export
```

## Delete everything

In the app, open the **Setup** tab. Find **Download or delete
everything**. Fill in the form and press **Delete everything**.

**This cannot be undone.** Download a copy first if you might want it.

It deletes, straight away:

- the business and its settings
- sensors, sites and readings
- alerts and the "who did what" record
- the people on your team, and who gets alerted
- mail, and everything else stored for the business

To stop mistakes, it asks for two things:

1. **The business's name**, typed out.
2. **Your password.**

It also waits until **nothing is owed**. Deleting is never a way to make a
bill go away. Pay what is due, then delete.

If any of these is wrong, **nothing is deleted**, and you are told why:

| What went wrong | Answer |
|---|---|
| The name does not match | `400` |
| The password is wrong | `403` |
| A bill is still unpaid (the amount is shown) | `409` |

### People who work at other businesses too

Someone on your team may also work at another business that uses
CyberLogix. Deleting yours does not lock them out of the other one. They
keep their email and password there.

### With the API

```
POST /api/accounts/business/erase
Authorization: Bearer <your sign-in token>
Content-Type: application/json

{"company_name": "Your Business Name", "password": "your password"}
```

When it works, the answer looks like this:

```json
{
  "deleted": true,
  "records_removed": 1234,
  "kept": "Bills already sent, because tax law requires it."
}
```

## What we keep after you delete, and why

Three things stay. None of them is your readings, your people or your
settings.

1. **The bills we already sent you.** The law makes a seller keep its
   bills for years. Each one shows the company name, what was charged
   and whether it was paid.
2. **Sector averages, if you used Benchmarks.** Your totals were mixed
   into averages for your whole industry. They never named you. Once
   mixed in, they cannot be pulled back out.
3. **A note that a business was deleted, and when.** It does not hold
   your name. It holds a scrambled fingerprint of your account number
   that cannot be turned back into it. It is there for one job: so that
   a backup can never bring your business back (see below).

## Backups

Every day we save a checked copy of the whole database. We keep the last
**14** of them. (The privacy page in the app shows the exact number for
your server.)

So after you delete, an old copy of your data still sits in those
backups. Each day the oldest backup is replaced. Within **14 days**, your
data is gone from all of them.

We never restore a backup to bring back something you deleted. If we
ever have to restore one, for example after a server fails, everything
deleted since that backup was made is **deleted again at once**. That is
what the note in point 3 above is for.

## You can also just ask

Write to us for a copy, a fix, or a deletion. We will do it.

## See also

- The privacy page in the app: `GET /api/legal/privacy`
- [The vault](THE-VAULT.md): how your records are kept tamper-evident
