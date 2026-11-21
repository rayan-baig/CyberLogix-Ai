# Pilot on CyberLogix

Pilot is the AI assistant in every MTRX Horizyn app: the round robot in the corner of the page.
It runs on **the person's own AI key**, from their browser, with the provider they choose
(Anthropic, OpenAI, Google Gemini, or any OpenAI-compatible API). CyberLogix never sees the key,
the conversation or the answers, and pays nothing for the AI.

The shared core is **Pilot core 1.2.0**, the same files as on the MTRX Horizyn site, copied
unchanged into `static/pilot/`. How the core works (the robot, the window, keys and providers,
memory, voice, pictures and videos, tabs, safe rendering, what is sent and to whom) is written
once, in the MTRX Horizyn repository: `docs/PILOT.md` there. A newer core is copied into every
app together; `tests/test_pilot.py` pins the files' fingerprints so a change here cannot slip in
by itself.

This page is what CyberLogix adds.

## Where it is

On the public pages (`/`, `/pricing`, `/help`, `/help/sensors`, `/for` and every `/for/…` page,
`/demo`) and on the console (`/console`, including its sign-in) and the Simple view (`/today`).

Not on a `/verify` proof page, an error page (404 or 500), the operator's `/book`, a printed
industry sheet, a reset or proof link, sign-up, the agreements, or the partner pages. A page has
Pilot only if its file carries `<!--pilot-->` before `</body>`; the server swaps that for the one
script tag (`main.pilot_tag`), with every address fingerprinted like the page's other files.

The tag also carries the figures Pilot may quote, read from the code that charges and alarms
(`main.pilot_facts`): "From $59", the trial's days, units and 5 calls, the 41°F fridge and 10°F
freezer limits, how long a unit must stay out of range before anybody is texted (20 minutes
for a food fridge, 30 for a freezer, 10 for a vaccine fridge) and the 15°F that alerts at once,
the 30 minutes before a sensor counts as offline, the 10 minutes before calls, and the 60-second
text promise.

Core 1.1.1 (and so 1.2.0) looks again for a free corner as the page scrolls, never covers a box being typed
in, and, once a chat has started, puts the page's suggestions above the conversation. This
app's `avoid` list adds the console's tabs (`.finder`, `.f-tab`), the pinned "Needs you now"
bar and its buttons, the "It's fixed" dialog and the front page's first-screen facts, and its
`keyNote` says in one sentence what an AI key is and where to get one.

**Always wholly on the screen.** When every corner is busy, the core "tucks" the robot away. Up
to core 1.1.1 that slid it past the screen's edge, so only a slice showed (355–411px on a 375px
phone), and 3.23.0 worked round it in this app's stylesheet. Core 1.2.0 has the setting, and the
config turns it on (`keepInViewport: true`): tucked away, the robot shrinks into its corner,
wholly on the screen (`.pilot-keep-in` in `static/pilot/pilot.css`, half size 8px from the edges
on a desktop, 42% size 3px from the edges on a phone), and the core's placement counts that small
box when it looks for a corner where it covers nothing. 3.24.0 removed the app's own copy of the
fix from `static/theme.css`, with the extra room it gave the Getting started card on a phone:
the core's smaller tucked robot sits in the side room the core keeps on a phone, clear of the
card's words and buttons (checked at 320 and 375px). The config's `avoid` list also keeps it off
that card (`#first-day`) and its buttons.

**CyberLogix's own welcome (3.24.0, core 1.2.0).** The window's header says "Your fridge-alarm
helper", and its five pills are what a café asks most:

| Pill | What it does |
|---|---|
| Which fridges need me? | sent at once: which units are warm, out of range or quiet, and what to check first |
| What does this alert mean? | sent at once: the latest alert in plain words, where it stands, what to do |
| Is the food still OK? | sent at once: the existing rule of thumb, always with its not-a-ruling note |
| Add a fridge | sent at once: on the console the `add_sensor` tool (it asks first), elsewhere the steps |
| Add my AI key | opens key settings |

Signed in, the console's tools answer them. On a public page (and the console's sign-in) the page
tells the AI that the person is not signed in and it can't see their fridges there, so it says
where to look instead of guessing. The page's own suggestions show under the pills; the signed-in
ones ("Show open alerts", "It's fixed now", "Make a proof report") repeat none of them. The key
settings leave out image and video keys (`hideKeySections`), which CyberLogix has no use for. The
phone's **Menu** on the public pages is a `<details>` drop-down, not a dialog, so it is listed in
`modals` and the robot steps back while it is open; every other pop-up here is a `<dialog>` or
`role="dialog"`, which the core already counts. A message sent before there is a key waits in the
box and goes the moment a key is saved (core 1.2.0).

Core 1.1.0 adds the moving robot. The tag names its files (`data-motion`, `data-actor`,
`data-actor-css`, the three `data-rest-*` layers) and every pose picture in
`static/pilot/poses/` (`data-poses`, from `main.pilot_poses`; a new pose is added by putting its
picture there). They are fetched once the page is idle, never under reduce motion, and a pose
picture only when a move first needs it. The config's `avoid` list keeps the robot off the
console's alert banners, the Simple view's "Needs you now" and its buttons, and the buttons that
answer an alert; on a phone every page keeps its last lines clear of the robot with
`padding-bottom: var(--pilot-gutter)` (`static/theme.css`).

The suggestions on the welcome and above the conversation, and what a task fills in, are
written for a café or small restaurant first.

## The files

| File | What it is |
|---|---|
| `static/pilot/*` | Pilot core 1.2.0, unchanged (thirteen files, the two robot pictures, the three layers and `poses/`) |
| `static/pilot-cyberlogix.js` | the config: name, page names, suggestions per page, the key note, the facts |
| `static/pilot-cyberlogix-pack.js` | the knowledge pack, the "why CyberLogix is better" list and the console tools, fetched only when Pilot is first opened |
| `static/console.js` (`window.CyberLogixConsole`) | what the console lets Pilot use: its own `api()` with the signed-in session, the open tab, and the same "add a fridge or freezer" the form uses |

The robot and the window look exactly as on the MTRX site; the tagline and the welcome's pills
are CyberLogix's own.

## What it knows

The pack is written from the real app and its help: how alerts and escalation work, fridge and
freezer limits, "What you need" (CyberLogix doesn't sell sensors), the setup steps, plans and
prices, the trial, proof of monitoring reports, the Simple view and the Advanced console, what
happens if the power or the internet goes off, the careful "is the food still OK" rule of thumb,
your data, the AI key for alert wording, and Pilot itself. The "why CyberLogix is better" list is
built only from real features, with no rival named and no invented numbers.

## What it can do

Only on the console and the Simple view, for someone signed in, through the console's own API with
their own session: the server checks every call exactly as it checks a button press, so a viewer
can't do what a viewer can't do.

| Tool | Kind |
|---|---|
| `show_units_out_of_range`: which units are warm, out of range, heading that way, or not reporting | reads |
| `show_open_alerts` | reads |
| `explain_alert`: what an alert means and where it stands | reads |
| `is_the_food_ok`: the existing rule of thumb (`/api/disposition`), always with its not-a-ruling note | reads |
| `explain_this_page` | reads |
| `acknowledge_alert` | asks first |
| `mark_alert_fixed` | asks first |
| `add_sensor`: asks "fridge or freezer?"; console only; the sensor key is shown once in the console's key box, never to the AI | asks first |
| `make_proof_report` (owners) | asks first |

Never: change the plan, delete anything, change who gets called, or send texts or calls. There is
no tool for any of them, the core engine refuses tool names that suggest them, and the pack's own
gate refuses every address but the six its tools use, before a request is made.

What the AI sees from a tool is cut down to what the question needs: a unit's name, its reading and
limits, whether it reports, an alert's state. Never a person's name, a phone number, an email, a
key or the business's id. The box a new key is shown in and the AI key card are marked
`data-pilot-private`, so "Analyze & explain" never reads them.

## The AI key for alert wording

Each alert text and call has one line saying what to check first, written by the AI when it can
and by a plain template when it can't. An owner may save the business's own Google AI Studio (Gemini API) key under
Setup, **Your AI key for alert wording** (`alertkey.py`, `GET`/`PUT`/`DELETE /api/ai-key`, and
`POST /api/ai-key/test` to try it). Then that line is written on their key, for their alerts only.

- Encrypted at rest with AES-GCM under a key derived from `CYBERLOGIX_KEY_ENCRYPTION_SECRET`,
  bound to the business. Unset, the card hides the form and says why.
- Shown again only as its last four characters. Never in a log (a failure is logged by its kind,
  with the key scrubbed from anything the provider said), an answer, the download or the audit
  trail.
- It overrides the service's own key for that business. A call on it has the same deadline as
  every alert line (`CYBERLOGIX_AI_TIMEOUT_SECONDS`, 6 s) and, if it fails or is slow, the alert
  goes out with the template line, on time. It never falls back to the service's key.
- Removable (Remove key), and deleted with the business.

## The alarm path

Pilot is files in the browser and nothing else: no route, no server call, nothing on the alarm
path. Alerts go out on time if Pilot, or any AI, is broken. The alarm-path watcher, `/readyz` and
every timeout are as they were.

## The security policy

Pilot added only the provider addresses (the same list as the MTRX site) to `connect-src`,
`blob:` to `media-src`, and `microphone=(self)` to the Permissions-Policy. Script and style stay
this site's own, with nothing inline.

## Tests

`tests/test_pilot.py` (where it is and isn't, the policy, lazy loading, the core unchanged, no
model names, the pack's figures against the code), `tests/test_alert_ai_key.py` (encryption,
last four, never logged, fallback, owners only, hidden without the secret), and in Node
`tests/js/pilot.test.js` (the core's own tests) and `tests/js/cyberlogix-pilot.test.js` (the
tools: confirm first, nothing private, and the refusals).
