# Proof of monitoring

Show your insurer proof that your fridges are watched 24/7.

Each month you can make a one-page report for any of your locations. You
print it, or save it as a PDF, and hand it to your insurer or broker. It
carries a short code and a link, so they can check it is genuine.

We are a monitoring service, not an insurer. Whether a report makes a
difference to your policy is up to your insurer.

## What the report says

For one location (or every location) and one month:

* **The sensors watched**, and how much of the time each one was online.
  A sensor counts as online when it has sent a reading in the last 30
  minutes, the same rule the console uses.
* **Every temperature alert**, how far it went, and how long it took
  somebody to acknowledge it. Who acknowledged it is given as a role
  ("Owner", "Manager", "On-call person"), never a name.
* **The longest gap in the data**: the longest time any sensor went
  without sending a reading, if it was 10 minutes or more.
* **Who gets alerted**, by role only: each person on the on-call list,
  whether they get texts, calls or both, and whether they cover this
  location or every location. No names, phone numbers or emails.

A unit called after a person ("Dana's fridge") is printed as "Unit 1"
instead, for the same reason.

## Making one

Only an owner can make a report.

1. Open the console, the **Proof** tab, the **Proof of monitoring** card.
2. Choose the location and the month. The current month gives the month so
   far.
3. Press **Make the report**, then **Open**. The report opens on its own
   page; press **Print or save as PDF**.

From the API: `POST /api/proof-reports` with `{"month": "2026-10",
"site_id": "SITE-000001"}` (leave out `site_id` for every location).
`GET /api/proof-reports` lists yours.

## Who can see it

Nobody, until you hand somebody the link. Not the people who work for us,
not a partner, not another business. The link is the only way in.

Anyone who opens the link sees a small page with:

* your business name,
* the month,
* the headline numbers (sensors watched, time online, alerts, how many were
  acknowledged and how fast, the longest gap),
* the report's fingerprint.

It never shows temperatures, unit or location names, people or phone
numbers.

## How someone checks it

They open the link printed at the foot of the report, or go to `/verify`
and type the code. The page says one of:

* **Genuine**: the report is unchanged since it was made. The fingerprint
  shown must match the one printed at the foot of the paper copy. If it
  does, the numbers on paper are the real ones.
* **Changed**: the stored report was altered after it was made. The page
  shows no numbers. Make a fresh report.
* **Switched off**: you switched the link off.
* **Not found**: no report has that code. Check it was typed exactly.

The check is also an API: `GET /api/proof-reports/verify/{code}`.

## Switching a link off

On the card, press **Switch off** next to the report, or
`POST /api/proof-reports/{id}/revoke`. From then on the link says it was
switched off, and shows nothing else. This cannot be undone: to share
again, make a new report, which gets a new code.

## Why it can be trusted

* **The code cannot be guessed.** It is 80 random bits, written as 16
  letters and digits.
* **Edits show.** When a report is made, its contents are frozen and
  hashed (SHA-256), and the hash is sealed with a key only the server
  holds (HMAC-SHA256), tied to the report's id and code. Change one
  figure, or move a seal onto a made-up code, and the check says
  "Changed".
* **Time online does not depend on old readings being kept.** We keep
  only the newest readings for each sensor. So every silence of 10 minutes
  or more is written down when it ends, and the report counts those.

## What it cannot show

* That a sensor was where it says it was, or that the fridge was full.
* Time before we started writing down gaps (when this version went live).
  If part of the month came before that, the report says so and leaves
  that part out of the time online.
* A report made while your account is far behind on payment: reports pause
  then, like other paperwork. Alerts never pause.

## Deleting your account

Deleting your business deletes its reports too. Their links then say
"Not found".
