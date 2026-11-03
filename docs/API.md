# Access, sign-in and the API

Who can call the API and with what credential, roles and the audit trail,
getting back into an account, provisioning paid accounts, the browser
security headers, and what the API refuses at the door. The interactive
reference for every route is served at `/docs` on a running server; the
[README quick start](../README.md#quick-start) is the shortest walk
through it.

## Authentication

Three credentials reach the API:

* a **per-sensor ingest key** in `X-CyberLogix-Sensor-Key` (or as
  `api_key_token` for third-party hardware) belongs to one asset and does
  one thing: report readings, for that asset. Issued once when the sensor
  is registered, never echoed again, rotatable per sensor, and dies with
  the sensor. **This is what belongs on the hardware.**
* a **tenant API key** in `X-CyberLogix-Key` identifies a machine — a
  provisioning script, the autopilot scheduler — and carries no human
  identity. Returned exactly once, at onboarding, and never echoed again;
* a **bearer session token** in `Authorization` identifies a signed-in person,
  and is what the console uses.

The tenant key is a master key: it registers assets, retunes alarm
thresholds and speaks for the whole estate. It used to be the only thing a
sensor could carry, which put all of that inside a box bolted to the wall
of a walk-in freezer — reachable by anyone with a screwdriver and a serial
cable, in a room the public can often walk into. A sensor key is worth one
freezer's readings, and a leaked one is re-keyed with a single call
instead of re-keying an estate.

The master key itself can be replaced too: `POST /api/licenses/me/rotate-key`
(the console's *Replace master key*, under the team list). An owner's
signed-in session only, never the key, so whoever leaked it cannot rotate it
and lock the business out. The old key stops working at once; sensors on
their own keys are not affected.

Two routes refuse a machine credential outright — `POST
/api/licenses/me/suspend` and `POST /api/licenses/me/plan`. Turning off a
company's monitoring, or moving it onto a plan without voice escalation,
is not an action whose audit record should read "API key".

Public endpoints are `/`, `/console`, `/api`, `/api/health`, `/api/industries`,
`/api/licenses/plans`, `/api/licenses/tenants` and `/api/accounts/login`.

Status codes distinguish the failure modes: `401` for a missing or unknown
credential, `402` for a suspended or expired license, `403` for a plan or role
that lacks the permission. A billing lapse is never reported as a bad
credential, and a wrong password and an unknown email return identical
wording so the endpoint cannot be used to enumerate accounts.

## Operators, roles and the audit trail

People sign in as themselves. Roles are `owner` > `operator` > `viewer`; each
implies the ones below it. Only owners invite users, change roles or disable
accounts, and an owner can neither demote nor disable themselves, so a tenant
is never left without one.

A **viewer** changes nothing. An **operator** runs the estate — registering
and decommissioning sensors, setting alarm thresholds, managing sites,
contacts and alert channels, escalating an incident to a phone call. An
**owner** additionally owns the money and the licence: the plan, invoices,
enterprise billing, inviting people, and suspending the service.

Acknowledging and resolving need an operator. Acknowledging does not mean
"I have seen this" — it means somebody who can deal with it has it, and it
stops the product waking anyone else. A viewer cannot change a threshold,
decommission an asset or escalate, so letting them halt the ladder halts it
on behalf of someone unable to act. The counter-argument, that making
acknowledgement harder during an emergency is its own hazard, is answered
by the path that matters at 3am staying open: the person on call presses 1
on the handset, and that callback is authenticated by Twilio's signature
and a per-incident secret, not by a role.

`tests/test_authorization_matrix.py` enforces this structurally: every
state-changing route must carry a role dependency or be named in an
exemption list with the reason, so a new route cannot quietly arrive
without one. A tenant API key is a machine credential with no human
identity, so it passes role checks — a provisioning script registering
fifty sensors is why it exists. Disabling someone revokes their live sessions
immediately rather than waiting for expiry. Passwords are scrypt hashes with
per-user salts and never leave the server.

Bootstrap the first owner with the tenant API key, then invite the rest:

```bash
curl -X POST localhost:8080/api/accounts/bootstrap \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "email": "dana@blueharbor.example",
  "full_name": "Dana Reyes",
  "password": "a-long-passphrase"
}'

curl -X POST localhost:8080/api/accounts/login -H 'Content-Type: application/json' \
  -d '{"email": "dana@blueharbor.example", "password": "a-long-passphrase"}'
```

Someone locked out is recovered by an owner issuing a one-time reset
(`POST /api/accounts/users/{id}/reset`), handing the token over out of band,
and the user redeeming it at `POST /api/accounts/reset`. Only the token's hash
is stored, so a copy of the database cannot replay it; it works once, expires
in 24 hours, and redeeming it revokes that user's existing sessions, since a
reset usually means the account was compromised.

Repeated failed sign-ins for one account are throttled — eight attempts in
five minutes, then `429` with a `Retry-After`. The limit is per account, so
one address being attacked cannot lock the rest of the team out.

Every state change a human causes is written to a durable audit trail with
their name against it, readable at `GET /api/accounts/audit`. Acknowledging an
incident while signed in records *you*, not a generic label; the same action
performed with a machine key is recorded with `actor_role: "machine"`. A
compliance report is only as good as its provenance.

## Provisioning a paid account

**Provisioning a paid account is different.** `POST /api/licenses/tenants`
still exists for it, and anything above a trial needs
`X-CyberLogix-Provisioning` matching `CYBERLOGIX_PROVISIONING_KEY`. With
that variable unset no paid account can be created at all, which is the
right default: a deployment nobody has configured should refuse to hand
out Enterprise licences rather than hand them to everybody.

```bash
curl -X POST localhost:8080/api/licenses/tenants \
  -H "X-CyberLogix-Provisioning: $PROVISIONING_KEY" \
  -H 'Content-Type: application/json' -d '{
  "company_name": "Blue Harbor Yacht Club",
  "contact_name": "Dana Reyes",
  "contact_phone": "+1-555-0100",
  "contact_email": "ops@blueharbor.example",
  "plan": "growth"
}'
```

## Getting back in

A password reset could only be issued by an owner, which is exactly the
person who cannot ask for one when it is their own password that is
gone. A single-owner account whose owner forgot their password was
unreachable forever.

`POST /api/accounts/forgot` takes an address and nothing else, and
`/reset` is where the emailed link lands — its own page rather than a
panel on the console, because the person following it is by definition
the person who cannot get past the console's sign-in form.

Three things it is careful not to become. It never says whether an
address is on file, including when it is rate limited, because a 429 for
one address and a 200 for another answers exactly the question the
response is otherwise refusing. It is limited per address rather than
globally, so one customer asking three times cannot lock out the next.
And the page scrubs the token out of the URL as soon as it reads it: a
one-time credential in the address bar is one screenshot or one Referer
header away from being somebody else's.

## Headers a browser can enforce

Every response carries `nosniff`, `X-Frame-Options: DENY`, a
`Referrer-Policy`, a `Permissions-Policy` and a content security policy
with `frame-ancestors 'none'`, `base-uri 'none'` and `object-src 'none'`.
HSTS is sent only over HTTPS — a browser that honours it on a plain-http
response cannot then reach a development server, and a header that makes
the product unrunnable locally is one somebody deletes rather than fixes.

Two of these are not generic hygiene. `frame-ancestors` stops the console
being loaded invisibly inside somebody else's page, where a customer
thinks they are clicking one thing and is clicking *suspend this licence*
on ours. `Referrer-Policy` is the other half of the password-reset fix:
the reset page scrubs the token out of the address bar, and this stops it
reaching a third party in a `Referer` header before the scrub happens.

Neither `script-src` nor `style-src` allows `'unsafe-inline'`. Every
page's script and stylesheet is a file under `/static` (`console.js`,
`landing.css` and so on), no element carries an `onclick` or a
`style="..."` attribute, and markup a script builds with `innerHTML` uses
classes from `utilities.css` instead of style attributes (a width worked
out at run time, such as a battery meter, is set through `element.style`,
which the policy allows). So text that reaches a page -- a sensor name, a
company name -- cannot run as script or restyle the page even if an escape
is missed somewhere: the browser refuses anything inline. The typeface
origins are named rather than pretended away, because a policy that
blocks the product's own stylesheet protects nothing once somebody
removes it.

The two API reference pages, `/docs` (Swagger UI) and `/redoc`, run
third-party code from `cdn.jsdelivr.net`, so they get a policy of their
own that names that origin. Their setup is a file (`static/api-docs.js`),
so script stays strict there too; style keeps `'unsafe-inline'` on those
two pages only, because ReDoc writes its styles into `<style>` elements at
run time and Swagger UI's bundle is not ours to change.

`tests/test_csp.py` holds every page the app serves to this (the list is
read from the router, so a new page is checked too), and the browser smoke
test fails on any policy refusal it sees.

## Numbers that cannot be stored

JSON has no literal for infinity, but every parser invents one: `1e400`
reads back as `inf`, and Python's own json accepts a bare `NaN`.
Pydantic's bounds do not stop them, because `inf >= 0` is True — a field
declared `ge=0` lets infinity straight through.

Two live consequences, both found by throwing hostile values at a
running server. `{"amount_usd": 1e400}` on a payment set an invoice's
paid amount to infinity; the database refused the write, so disk stayed
clean and memory did not, and every read of that invoice *and of the
customer's whole invoice list* answered 500 until a restart.
`{"danger_above": 1e400}` did the same to a sensor — and that one stops
`/api/sensor-pulse` answering for it, so the device keeps reporting into
nothing and the freezer is silently unmonitored. An operator role is
enough to do it.

`models.Finite` refuses both at the door on every float the API accepts.
Behind it, the two writes that take a caller-supplied number now persist
before committing to the live object, so a value the database refuses —
for any reason, not just this one — cannot leave the working set holding
something that will not store.

The test that matters is neither of those. It walks every Pydantic model
the application defines, finds every float field, and asserts each
refuses a non-finite value. A new model with a plain `float` on it fails
the suite by name. Both of these bugs were written by somebody who did
not think about infinity, which is everybody, always.
