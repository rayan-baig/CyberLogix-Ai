# The OmniArmor connection (Kitchen Safety Bundle)

OmniArmor is MTRX Horizyn's food-safety checklist app. It has a
temperature-log check (cold holding at 41°F or below) that somebody fills
in by hand. With the Kitchen Safety Bundle, CyberLogix fills it in: once a
day, after the site's day ends, it sends OmniArmor one combined report for
the business. On a clean day OmniArmor ticks the check with the warmest
reading; on a day something went out of range it leaves a note for a
person to review.

The contract is OmniArmor's `docs/CYBERLOGIX-CONNECTION.md`; the code here
is `omniarmor.py`, and `tests/test_omniarmor_bundle.py` sends every report
through OmniArmor's own validation, ported into
`tests/omniarmor_contract.py`. Change one side, change the other.

Examples use `omniarmor.example.com` for OmniArmor and
`cyberlogix.example.com` for CyberLogix.

## What it costs

**+$29 a month per location**, billed while the connection is on
(`ADD_ONS["kitchen_safety_bundle"]` in `pricing.py`). Free during the
trial, so a kitchen can try it first. It is the one extra the Starter plan
takes. Disconnecting takes it off the next invoice. More in
[BILLING.md](BILLING.md).

## Connecting (the owner)

1. In OmniArmor, the workspace owner opens Settings, Connections and makes
   a **CyberLogix connection key** (`clx_` and 43 more characters). It is
   shown once.
2. In the CyberLogix console, the owner opens Setup, **OmniArmor
   connection**, and fills in:
   - **OmniArmor's address**, `https://omniarmor.example.com` (https only;
     a private or local address is refused);
   - **the connection key**;
   - optionally a **site name** for OmniArmor to show, the **time zone**
     whose midnight ends the day (it defaults to the site's on-call
     rotation, then UTC), and, for a business with several sites, **which
     sites** to include. Every included unit goes into one combined
     report: the contract allows one report per workspace per day.
3. **Send a test report** sends yesterday's real report straight away, so
   the owner sees it arrive. OmniArmor has no test mode; sending a day
   again is safe (it answers "unchanged"), and the daily job will not send
   that day a second time.

Owners only, every route (`GET`, `PUT`, `DELETE /api/connections/omniarmor`,
`POST /api/connections/omniarmor/test`). Operators and viewers are told on
the card that an owner can connect it.

## The key

- Encrypted with AES-GCM before it is stored, under the server's
  `CYBERLOGIX_CREDENTIAL_KEY`, and bound to the business it belongs to, so
  a copy of the database or a backup does not hold it in the clear and a
  sealed key cannot be moved onto another business's row. Without that
  setting nothing can be connected (`503`).
- Never shown again after it is pasted; the console shows its last four
  characters. Never logged, never in a URL, never in an export.
- Paste a new one to replace it. Disconnect deletes it.
- Changing `CYBERLOGIX_CREDENTIAL_KEY` makes every saved key unreadable;
  each business would paste theirs again.

## What a report says

One per business per day, after the site's local day has ended (the hourly
money pass sends it a little after midnight at the site):

| Field | From |
|---|---|
| `date` | the site's local day |
| `site` | the site name set on the connection, or the one site's name |
| `units_total` | every unit included that existed by the end of the day |
| `units_in_range_all_day` | units that reported that day, never read out of range and had no alert open |
| `breaches[]` | each alert that overlapped the day and began on it or a day either side |
| `warmest_reading`, `unit_of_measure` | the warmest in-range reading of the day, in the business's °F or °C |
| `log_url` | that day's inspector log in the console (only when the server's address is https) |

A unit that sent nothing all day is not counted as in range: OmniArmor
then leaves a note rather than ticking the check. A day with no readings
at all from any unit is not sent as clean.

Each breach carries the unit's name, when it went out of range and when it
next read in range (in the site's own time, with its UTC offset; null if
it had not come back), the worst reading, the limit it crossed, and two
fixed codes:

**Who acknowledged it** is a role from OmniArmor's list, never a person:

| In CyberLogix | Sent as |
|---|---|
| an owner of the business | `owner` |
| an operator | `manager` |
| a viewer | `kitchen staff` |
| a text reply "1", a keypress, the API key, anything else | `on-call staff` |
| nobody yet | `null` |

**What was done** is one action code, read from the corrective action
written up against the alert and what happened to the food:

| Recorded | Sent as |
|---|---|
| food discarded or destroyed | `discarded_food` |
| food moved or quarantined | `moved_food` |
| door found open and closed | `closed_door` |
| engineer or technician called | `called_technician` |
| defrosted and recovered | `defrost_cycle` |
| fault corrected, setting adjusted | `adjusted_unit` |
| food probed, unaffected, or used | `checked_food` |
| anything else written down | `monitoring` |
| acknowledged, nothing written yet | `none` |
| nothing at all | `null` |

**No names.** A unit or site name that holds the name of anybody on the
account (people, the on-call list, staff), an email address or a phone
number is sent as "Unit 2", or the site as null. Roles and actions come
from the fixed lists, so names cannot arrive there.

## Reliability

- **Retries.** A `429`, a `5xx` or a network failure is tried again, up to
  four times in a pass, waiting 2, 4 and 8 seconds, or longer when
  `Retry-After` asks (in seconds or as a date). A wait longer than 30
  seconds is not slept through: the day is left for the pass after the
  wait.
- **`401`** stops sending at once: the console says "OmniArmor refused the
  key · paste a new one". Pasting a new key, or pressing Send a test
  report, tries again.
- **`422`** (and `400`, `413`, `415`) is our bug, by the contract's own
  words. It is written down as a fault, which reaches the team by the
  error-report email, and that exact report is not sent again.
- **Never the same day twice.** Each day is claimed in the database under
  the store lock before it is sent, with a ten-minute lease for a pass
  that dies, and the pass itself runs one at a time. A day is sent again
  only if what it says has changed since (an alarm acknowledged or written
  up after midnight): that is a **correction**, which the contract says
  replaces the earlier report. The last two finished days are checked for
  this; older days are not reopened.
- **Catching up.** A server that was down sends the days it missed, up to
  seven, from the day before the connection was made.
- **Every day's result is kept** and shown on the card: sent, corrected,
  or why not. The status line reads "Last sent 2026-11-12 · OK".
- **Two days not delivered** (no success since): the owners get one
  email saying so, with the last answer and what to do. Alarms are not
  affected. A success ends the run of failures; a new run emails again.
- **Reports pause with the rest.** A business more than 45 days behind on
  its bills, or suspended, is not sent reports until it is current, like
  every other report. Its alarms are untouched.

## Settings

| Setting | What it does |
|---|---|
| `CYBERLOGIX_CREDENTIAL_KEY` | Seals saved connection keys. Required to connect. |
| `PUBLIC_BASE_URL` | Where the `log_url` points. Only an https address is sent. |
