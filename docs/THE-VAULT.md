# The vault: records nobody can quietly change

When something goes wrong, someone will ask to see your temperature
records. An inspector. An insurer. A buyer. They have a fair question:
*how do I know nobody changed these afterwards?*

"Our computer says so" is not a good answer. The vault is a better one.
This page says what it is, what it proves, what it does not prove, and
how anyone can check it.

## What it is

Every reading your sensors send is sealed into a chain.

Think of a row of wax seals. Each new seal is pressed over the reading
**and over the seal before it**. So if anyone changes an old reading,
its seal breaks. And because every later seal was pressed over that one,
they all break too. You cannot fix one without redoing all of them.

In technical terms: each reading gets a fingerprint (a SHA-256 hash). The
fingerprint covers the reading and the fingerprint before it. The very
last fingerprint is called the **chain head**. That one short code stands
for the whole run of readings.

This applies to us as much as to you. We cannot quietly rewrite your
history either.

## What an attestation is

An **attestation** is a short statement you can hand to someone. It
says, for a sensor (or for your whole business) and a period of time:

- how many readings there were
- how many went outside the safe range
- the coldest and warmest reading
- the **chain head** for those readings
- when the statement was made

When you make an attestation, we also **write down its chain head** on
our side. That is what makes a later check mean something. A chain can
only show tampering when it is compared with a chain head that was
written down *before* the change.

If your server has a signing key set up, the attestation also carries a
**signature**. See "The signature" below.

Making an attestation needs a paid-up account. Checking one never does:
a record already handed to an insurer stays checkable, whatever happens
to the account.

## What an attestation proves

If the readings you hand over still produce the chain head in the
attestation, then:

- **No reading in that run was changed** after the attestation was made.
  Not the temperature, not the time, not whether it was out of range.
- **No reading was added, removed or moved** in that run.

That is it. It is a narrow promise, and it is a strong one.

## What it does not prove

Please read this part. Claiming more than this would make the whole
thing worthless.

An attestation does **not** prove:

- **That the sensor was in the right place.** A probe sitting on the
  counter instead of in the freezer still makes honest-looking readings.
- **That the sensor was accurate.** If it read two degrees low, the chain
  faithfully records two degrees low.
- **That nothing is missing from before it was recorded.** If a sensor
  was unplugged or offline, there are simply no readings for that time.
  The chain cannot show readings that never arrived. (The app does flag
  sensors that go quiet. That is a separate thing.)
- **That nothing was changed before the attestation was made.** The
  seal protects the record from the moment the chain head was written
  down. Make attestations often, and hand them over early.
- **That the food, medicine or stock was safe.** It is a record of
  temperatures, not a verdict.

It is also **not** a blockchain, a notary, or a legal guarantee. It is a
chain of fingerprints plus a statement. That is what we say it is.

## The signature

If your server has a signing key set up (`CYBERLOGIX_ATTESTATION_KEY`),
each attestation carries a signature. It shows the attestation was made
by this CyberLogix server.

Be clear about one limit. The signature uses a secret key that only the
server holds (HMAC-SHA256). So only the server can check it. Someone you
hand the attestation to cannot check the signature alone. They can ask
whoever runs the server to confirm it.

The chain itself needs no key at all. Anyone can check it. That is the
part that matters most.

If no key is set up, the attestation says so plainly. The chain still
works.

## How to check one

There are three ways. Pick the one that suits you.

### 1. In the app (for you)

Ask the app to compare today's records with every chain head it wrote
down for that sensor:

```
GET /api/vault/verify/{sensor_id}?days=30
Authorization: Bearer <your sign-in token>
```

The answer says `"intact": true` if nothing has changed. If something
has, it says `"intact": false` and gives the date of the first
attestation that no longer matches.

If the answer says some readings were removed **by the storage limit**,
that is normal. We keep only the newest 500 readings for each sensor, so
very old attestations can no longer be checked from our side. That is
not tampering. But it is why you should **keep your own copy** of the
full chain (see the next step).

### 2. Without an account (for anyone)

First, you get the attestation **with the full chain**:

```
GET /api/vault/attestation/{sensor_id}?days=30&include_chain=true
Authorization: Bearer <your sign-in token>
```

Save that file and hand it over. The `chain` list holds every reading.

The person you gave it to can then check it **without an account**, and
without trusting you or us. They send the readings and the chain head to
this address:

```
POST /api/vault/verify
Content-Type: application/json

{
  "chain_head": "<the chain head from the attestation>",
  "readings": [ <the "chain" list from the attestation, oldest first> ]
}
```

The answer says `"matches": true` if the readings produce that chain
head, and `"matches": false` if they do not. This address stores nothing
and reads nothing of yours. It only does the sums.

You can verify up to 20,000 readings in one go.

### 3. By hand (for the very careful)

Someone who trusts no server at all can do the sums themselves. For each
reading, oldest first, join these with `|` between them:

1. the previous fingerprint (for the first reading, 64 zeros)
2. the sensor's name, such as `FRZ-1`
3. the temperature in Fahrenheit, with exactly 4 decimal places
4. the humidity with 4 decimal places, or the word `null` if there is none
5. `1` if the reading was out of range, `0` if not
6. the time, exactly as written in the chain (for example
   `2026-10-12T19:55:50Z`)

Take the SHA-256 of that text. That is this reading's fingerprint. Use
it as the "previous" for the next reading. The last fingerprint must
equal the chain head.

In Python, it is a few lines:

```python
import hashlib

def chain_head(readings):
    previous = "0" * 64
    for r in readings:
        humidity = "null" if r["humidity_percent"] is None else f"{r['humidity_percent']:.4f}"
        text = "|".join([
            previous,
            r["sensor_id"],
            f"{r['temperature_fahrenheit']:.4f}",
            humidity,
            "1" if r["breached"] else "0",
            r["at"],
        ])
        previous = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return previous
```

## A whole-business attestation

You can also make one statement for every sensor at once:

```
GET /api/vault/attestation?days=30
Authorization: Bearer <your sign-in token>
```

It lists each sensor with its own chain head. It also has one
`estate_digest`: a fingerprint over all the sensors' chain heads, so the
whole business has a single code someone can quote.

## A page you can share

An owner can turn on a public page that shows your record to anyone with
the link: an inspector, an insurer, a customer at the door. It is off
until you turn it on. The link holds a long random code, not your
business's name. You can switch it off, or change the code, at any
time. It shows no staff names or contact details.

## Good habits

- **Make attestations often**, for example once a week, and keep them.
- **Save the full chain** (`include_chain=true`) when you make one.
  Our side keeps only the newest readings.
- **Hand them over early.** An attestation someone else already holds is
  the strongest proof there is that the record has not changed since.

## See also

- [Your data](YOUR-DATA.md): how to download everything, or delete it
