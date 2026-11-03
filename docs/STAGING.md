# A staging copy

A second, separate copy of CyberLogix on Render, at
`staging.cyberlogix.example.com` (where `example.com` stands for MTRX
Horizyn's domain). You try a change there first, with fake data and test
credentials, and only then put it on the live copy.

The rule for everything below: **staging shares nothing with live.** Not
the database, not the disk, not a key, not a phone number, not a mail
account. If staging could reach a real person, sooner or later it will.

## Why it is not in render.yaml

`render.yaml` is the live copy's Blueprint. A staging service added to it
would be created and changed every time the live Blueprint is synced, and
one careless edit to the shared file could touch the live service. So
staging is set up once, by hand, in Render's dashboard, using the
settings below. It costs about the same as the live copy (a starter
instance and a small disk).

## 1. The service

Render → New → Web Service → this repository.

| Setting | Value |
|---|---|
| Name | `cyberlogix-staging` |
| Runtime | Docker (it uses the same `Dockerfile`) |
| Branch | `staging` (make it once from the live branch) |
| Instance type | Starter, not Free (Free has no disk and sleeps) |
| Instances | 1 |
| Health check path | `/api/health` |
| Auto-deploy | On: a push to `staging` deploys it |
| Disk | Its own, new disk: name `cyberlogix-staging-data`, mount path `/app/data`, 1 GB |

Never attach the live disk, and never copy the live database onto this
one. The live database holds real customers' names, phone numbers and
email addresses.

## 2. The address

1. At whoever runs the domain's DNS, add a `CNAME` named
   `staging.cyberlogix` pointing at the staging service's
   `onrender.com` address.
2. Render: the staging service → Settings → Custom Domains → add
   `staging.cyberlogix.example.com`, and wait for the certificate.

## 3. The settings

Set these on the staging service (Environment). Generate new secrets;
never paste in the live ones.

| Setting | Staging value | Why |
|---|---|---|
| `CYBERLOGIX_DB_PATH` | `/app/data/cyberlogix.db` | On staging's own disk |
| `CYBERLOGIX_BACKUP_DIR` | `/app/data/backups` | The same |
| `PUBLIC_BASE_URL` | `https://staging.cyberlogix.example.com` | Links, "press 1" and replies come back here, not to live |
| `CYBERLOGIX_NOINDEX` | `1` | Search engines leave it out, so nobody signs up on staging by mistake |
| `CYBERLOGIX_ADMIN_KEY` | a new one | Generated, as in DEPLOY.md section 3 |
| `CYBERLOGIX_MAIL_SECRET` | a new one | The same |
| `CYBERLOGIX_TRUST_PROXY_HEADER` | `1` | Render's proxy, as on live |
| `CYBERLOGIX_SWEEP_SECONDS` | `60` | As on live, so alerts behave the same |
| `CYBERLOGIX_HEARTBEAT_URL` | empty, or a separate check | **Never live's.** Staging's pings would hide a dead live server |
| `STATS_KEY` | empty | MTRX Horizyn's console must not count staging's test users |
| `CYBERLOGIX_PROVISIONING_KEY` | empty, or a new one | Only if you test creating paid accounts |
| `CYBERLOGIX_OPERATOR_EMAIL` | empty, or your own | The daily digest |
| `CYBERLOGIX_ALLOWED_ORIGINS` | empty | As on live |

Leave the language-model key unset unless you are testing the wording:
alerts then use fixed templates, which is what most tests want anyway.

### Texts and calls: never to real people

The safe default is to **leave `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
and `TWILIO_FROM_NUMBER` empty.** Staging then runs in dry run: every
text and call is written and recorded on the incident, and nothing is
sent. That is a built-in sink. You can read exactly what would
have gone out on the incident in the console.

To test against Twilio itself without texting anyone, use Twilio's **test
credentials** (the test account SID and test auth token from the Twilio
console). Twilio accepts the request and delivers nothing. With them,
`TWILIO_FROM_NUMBER` must be Twilio's own test number, `+15005550006`.

Never put the live account's token or the live number on staging, and
never point the live number's "A message comes in" webhook at staging. A
reply from a real person to a real alert must reach live.

To test "reply 1" for real, buy a second, cheap Twilio number for staging
only, point its "A message comes in" webhook at
`https://staging.cyberlogix.example.com/api/voice/sms-reply`, and put
only your own phone on staging's on-call lists.

### Mail: never to real people

**Leave `SMTP_HOST` empty.** Mail is then queued, never sent, and
dropped after 72 hours; the queue is at `/book → Health`. To read the
mail as it would arrive, use a test inbox service that accepts SMTP and
delivers nothing, and give staging its SMTP settings. Never the live mail
account. Set `MAIL_FROM` to a staging address, such as
`no-reply@staging.cyberlogix.example.com`.

### Payments: Stripe in test mode

Switch the Stripe dashboard to **test mode** and, there:

1. Developers → Webhooks → add endpoint
   `https://staging.cyberlogix.example.com/api/payments/stripe`,
   subscribed to `checkout.session.completed`. Put its signing secret
   (the test one) in staging's `STRIPE_WEBHOOK_SECRET`.
2. Make a test-mode payment link that passes the invoice number as
   `?client_reference_id={invoice}`, and put it in `CYBERLOGIX_PAY_URL`.

Pay with Stripe's test card `4242 4242 4242 4242`, any future date and
any CVC. Nothing is charged. Never put a live-mode secret or link on
staging.

## 4. Something to look at

Staging starts empty. To fill it with a made-up business, open the
staging service's Shell in Render and seed a **new** database file next
to the empty one:

```
python demo_account.py --db /app/data/demo.db \
  --url https://staging.cyberlogix.example.com
```

Then set `CYBERLOGIX_DB_PATH=/app/data/demo.db` and redeploy. It prints
the owner's and an operator's sign-in. It refuses a database that holds
anything, mails nobody, and keeps its backups in
`/app/data/demo-backups`. Its phone numbers are made up, so leave Twilio
empty while it is in use.

## 5. From staging to live

1. Push the change to the `staging` branch. Staging deploys it.
2. Check it there:
   - `curl -s https://staging.cyberlogix.example.com/api/health` shows the
     new version from `VERSION`;
   - `/book → Health` shows nothing new that is broken;
   - walk the change by hand, and for anything that alerts, take a test
     sensor out of range and read the incident: what would have been
     texted, and to whom.
3. Merge the same commit into the live branch (the one `render.yaml`
   names). Do not re-type the change: the commit that was tested is the
   commit that ships.
4. Deploy live (it is on manual deploy in `render.yaml`: the service →
   Manual Deploy → Deploy latest commit), then check
   `https://cyberlogix.example.com/api/health` shows the same version.

Settings do not travel with a commit. If the change needs a new setting,
add it to live by hand before step 4, with live's own value.

## 6. Keeping them apart, a checklist

- [ ] Staging has its own disk; the live disk is attached to live only.
- [ ] `PUBLIC_BASE_URL` on staging is the staging address.
- [ ] `CYBERLOGIX_NOINDEX=1` on staging, and unset on live.
- [ ] Twilio on staging is empty, test credentials, or a staging-only
      number. The live number's webhooks point at live.
- [ ] SMTP on staging is empty or a test inbox.
- [ ] Stripe on staging is test mode only.
- [ ] Every secret on staging was generated for staging.
- [ ] The heartbeat on staging is empty or its own.
- [ ] No copy of the live database has ever been put on staging.
