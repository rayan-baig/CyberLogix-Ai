# Pilot on CyberLogix

Pilot is the AI assistant in every MTRX Horizyn app: the round robot in the corner of the page.
It runs on **the person's own AI key**, from their browser, with the provider they choose
(Anthropic, OpenAI, Google Gemini, or any OpenAI-compatible API). CyberLogix never sees the key,
the conversation or the answers, and pays nothing for the AI.

The shared core is **Pilot core 1.4.2**, the same files as on the MTRX Horizyn site, copied
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
app's `avoid` list adds the console's tabs (`.finder`, `.f-tab`, until 3.29.0), the pinned "Needs you now"
bar and its buttons, the "It's fixed" dialog and the front page's first-screen facts, and its
`keyNote` says in one sentence what an AI key is and where to get one.

**Always wholly on the screen.** When every corner is busy, the core "tucks" the robot away. Up
to core 1.1.1 that slid it past the screen's edge, so only a slice showed (355–411px on a 375px
phone), and 3.23.0 worked round it in this app's stylesheet. Core 1.2.0 has the setting, and the
config turns it on (`keepInViewport: true`): tucked away, the robot shrinks into its corner,
wholly on the screen (`.pilot-keep-in` in `static/pilot/pilot.css`, about half size 8px from the edges
on a desktop and 82% 3px from the edges on a phone since core 1.3.0), and the core's placement counts that small
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

**A visitor's welcome on the public pages (3.25.0).** Pilot can see nobody's fridges on `/`,
`/pricing`, `/help`, `/help/sensors`, `/for/…` and `/demo`, so there the five pills are what a
visitor deciding asks (`VISITOR_PILLS`): **Will it work for my café?**, **What does it cost?**,
**See it live**, **What sensors do I need?** (all sent at once) and **Add my AI key**. The
console and `/today` keep the five above (`OWN_PILLS`). The front page's own suggestions under
them repeat none ("Will a door left open wake us?", "Wi-Fi or power goes off?", "Do you phone at
night?"). The `avoid` list also has a fridge's status label wherever it is shown ("Too warm",
"No signal", "Fine": `.sensor-top`, `.incident-top`, `/today`'s `.thing-word`, the demo's
`.demo-word`, and any `.badge`), so the robot never hides whether a fridge is OK.

**Core 1.3.0 (3.25.0).** The thirteen files from the MTRX Horizyn site, byte for byte. The first
look of a visit glows wherever placement puts the robot instead of showing it whole on top of the
page's words; the tucked-away robot is never smaller than a 44px tap target (it was about 24px on
a phone); and since the welcome has this app's own key pill, there is no second key card and the
key wording says "AI key". The config's `keyNoteShort` is the welcome's one line ("AI keys are
for grown-ups (18+)."); the full `keyNote` is its tooltip and stays beside the key settings.

**Three visitor pills need no key (3.26.0).** On the public pages **See it live**, **What does
it cost?** and **What sensors do I need?** went to their page, key or no key. Core 1.3.0 had no
link pill, so the config caught the press itself. Core 1.4.0 has one, and 3.27.0 removed the
catcher (below).

**Never on the logo (3.26.0).** On a phone the robot sat top-left, on the logo, on a new
business's first screen, and jumped there after a long scroll on `/help/sensors`: core 1.3.0's
last resort, when every corner is busy, counted only square pixels, and a small logo was "less"
than the edge of a fold or the Getting started card. 3.26.0 listed every page's header bar in
`avoid` and only the Getting started card's buttons, not the whole card. Core 1.4.0 fixes the
cause, and 3.27.0 took the header bars off the list again (below).

**Core 1.4.0 (3.27.0): useful before a key.** The thirteen files from the MTRX Horizyn site,
byte for byte. A welcome pill or a page suggestion can now be a `link` (a page of this site:
Pilot closes and goes there) or an `answer` (Pilot shows it at once as its own message: no key,
and nothing is sent anywhere). Under an answer, while there is no key, Pilot offers it: "Want
more? Add your AI key to ask anything." (`answerMore`).

| Pill | Where | What it does now |
|---|---|---|
| Which fridges need me? | console, `/today` | answer: every unit out of range, alerting, gone quiet or waiting for its first reading, worst first, in the same words as its card (`CLXWords.condition`); "Nobody has answered" if an alert is open |
| What does this alert mean? | console, `/today` | answer: the alert at the top of "Needs you now", from its own record: how warm, its limit, when it went out, the worst so far, the wait it passed, who has it ("someone on your team", never a name), what to check |
| Is the food still OK? | console, `/today` | answer: how long it was out and the worst reading, the rule of thumb, and always its not-a-ruling note |
| How do I connect a sensor? | console, `/today` | link to `/console#connect`: the console opens "Connect your sensor" (the wizard) once signed in |
| Will it work for my café? | public pages | answer: a few lines with the real limits, waits, text and call timings, Starter's price and the trial, with links to the sensor guides, prices, the demo and sign-up |
| What does it cost? | public pages | link to `/pricing` |
| See it live | public pages | link to `/demo` |
| What sensors do I need? | public pages | link to `/help/sensors` |
| Add my AI key | everywhere | key settings (the only welcome pill that needs the AI) |

The answers on the console and `/today` are read from what the page already loaded for the
person: the overview its cards and "Needs you now" are drawn from, handed over by the page's own
bridge (`CyberLogixConsole.overview()` in `static/console.js`, `CyberLogixToday.overview()` in
`static/simple.js`). Nothing is fetched to answer and nothing leaves the browser. A unit's name is
kept as plain words (no links or bold of its own). Signed out, or before the page has loaded,
they say where to look instead. They carry no person's name, number or email. Since core 1.4.1
(3.29.0) they never leave the browser at all, not even once a key is added: the AI is sent the
stand-in "(Pilot showed a built-in answer)" in their place.

Page suggestions that are facts are answers too, from the figures on Pilot's script tag
(`main.pilot_facts`): "Will a door left open wake us?", "Wi-Fi or power goes off?", "Do you
phone at night?" / "Do you phone us?", "What's in the trial?", "A fridge is warm: what now?",
"A log for the inspector", "Set up step by step", and the demo's "What am I seeing?" and "What
happens next?", and the pricing page's "Which plan for a café?" (3.28.0). "Start a free trial"
goes to `/signup`, "I forgot my password" to `/reset`. The open questions ("Easiest way to set
up", "How would this work for me?", "When do you alert?", "Make a proof report") stay AI prompts.

**"It's fixed now" points at the real button (3.28.0).** It is an answer, not a request to the
AI: it names the open alert and says where its **It's fixed** button is, which checks the fridge
is back in range and asks whether the food was kept. Pilot never closes an alert itself from this
suggestion. Signed in, "Log for the inspector" is a link straight to the log (3.30.0):
`/console?view=full#inspector-log?open=1` from `/today`, only the `#` on the console, and the
console opens the last seven days' log. A visitor's "A log for the inspector" still says where it
is, with "Sign in".

**Fridge rows and temperatures are on the avoid list (3.28.0).** `.thing-temp`, `.thing-name`,
`.demo-unit-temp`, `.demo-unit-top`, `#needs` and `.need`: at 375px the robot waved over a
62.5° reading on /today's last row and over the demo Freezer card's edge.

**The header bars are off the avoid list (3.27.0).** Core 1.4.0 tries a phone's bottom corners
first, counts a header that stays put as busy, and weighs a link or button it would cover, so
the robot never parks over the logo or Sign in without this app's help. `.top`, `.demo-top` and
the console's `.nav` are no longer in `avoid`. Checked in a real browser at 320×640, 375×667,
375×812 and 1440×900 on `/`, `/pricing`, `/help`, `/help/sensors`, `/for`, `/for/restaurant`,
`/demo`, the console's sign-in, a new business's console, a console with an open alert and
`/today`, scrolled from top to bottom: never in a phone's top corner, and never over the logo,
Sign in or a header link. One spot was left to the core: on a 320px phone, a console with an
open alert scrolled so that the pinned "Needs you now" bar fills the top and the search box and
tab row fill the bottom, every corner is busy and the robot tucked bottom-left over the end of
the search box until the next scroll. Core 1.4.1 fixes it (below).

**Core 1.4.1 (3.29.0): hide rather than cover.** The thirteen files from the MTRX Horizyn
site (commit f221ffc), byte for byte, with its new pictures. What changes here:

- **On a phone the robot fades out instead of covering something.** When even the least-bad
  corner would put it over a link, a button, a box, a header or bar that stays put, or anything in
  this app's `avoid` list (a temperature, a status label, "Needs you now"), it fades out, taps go
  through, and it comes back once there is room (after a scroll, or when typing ends). The 320px
  console with an open alert now hides it there instead of tucking it over the search box.
  `hideWhenCrowded` is left at its default (on).
- **Once the page is scrolled, one bottom corner.** The phone robot stays tucked in one bottom
  corner while words pass under it, instead of changing corner every few hundred pixels.
- **The welcome stays.** Under an answer of this app's own (Which fridges need me?, Will it work
  for my café?, a fact suggestion), the welcome's pills come back as a small row, so a second
  question needs no Clear chat. "Want more? Add your AI key to ask anything." shows once per chat,
  under the first such answer.
- **The one-line key note stays for the whole chat.** `keyNoteShort` ("AI keys are for grown-ups
  (18+).") now shows under every chat, not only the welcome, with **More** to open the full note.
- **A crisp robot on phones.** The script tag names the robot and its resting layers in the sizes
  they are drawn at on a phone (`data-robot-set`, `data-rest-body-set`, `data-rest-head-set`,
  `data-rest-eyes-set`, built in `main.pilot_tag` with every address fingerprinted), so a phone
  shows a 112px, 168px or 176px file instead of shrinking the big one.
- **Answers stay on the device** (above).

**The console's search box and tabs are off the `avoid` list (3.29.0).** `.finder` and `.f-tab`
were this app's stopgap for the robot landing on them; core 1.4.1 hides it rather than cover a box
or a button, so they go. Measured on the 320px console with an open alert, scrolled 500px (the
"Needs you now" bar pinned across the top, the search box crossing the bottom): hidden there, and
the same corner or hiding at every other scroll position, with or without the two entries. The rest
of the list stays: the temperatures, status labels and "Needs you now" are words, which the core
lets a tucked robot sit over, and listing them is what makes it hide instead; the other buttons
(the alert's, the Getting started card's, the front page's) are measured exactly from the list,
where the core's own look samples a few points, and were not shown to be covered without them.
Checked in a real browser at 320×640, 375×667, 375×812 and 1440×900 on `/`, `/pricing`, `/help`,
`/help/sensors`, `/for`, `/for/restaurant`, `/demo`, the console's sign-in, a new business's
console, a console with an open alert and `/today` with and without one, scrolled top to bottom:
never over a temperature, a status label, the logo or a header link, never in a phone's top
corner, hidden rather than over the search box or tabs at 320px. On a phone it is hidden more
often than before, by design: at the top of `/help`, and on parts of `/pricing` and
`/help/sensors`, every corner has a link, so it waits for a scroll.

**Core 1.4.2 (3.31.0): typed questions without a key, and always findable.** The thirteen files
from the MTRX Horizyn site (commit a1c9253), byte for byte; the pictures are unchanged. What
changes here:

- **A typed question that fits one of this app's own answers gets it, with no key.** "Is my
  walk-in fridge OK?" on the console or `/today` gets "Which fridges need me?", "How much does it
  cost?" on a public page gets the link to the prices, "Should I throw the food away?" gets "Is
  the food still OK?", under the line "Here's what I can tell you without an AI key:". It is
  worked out in the browser and nothing is sent. Every pill and suggestion that answers or links
  with no key carries its own `match` words (the `MATCH` list in `static/pilot-cyberlogix.js`:
  "fridge", "warm", "ok" on "Which fridges need me?"; "cost", "price", "much" on "What does it
  cost?"; "cafe", "restaurant", "bakery" on "Will it work for my café?"; and so on). The core is
  careful: two typed words must be the item's own, and no other item on the page may come as
  close, so a question that fits nothing ("Write me a poem about fridges", "What's the weather
  like?") still opens key settings with the words kept in the box. `matchTyped` is left on.
- **"does" and "this".** The core takes a plural "s" off a word before it checks its list of
  words that don't count, so "does" and "this" count (as "doe" and "thi"), and both are in "What
  does this alert mean?". With no key, "Does this work with my sensor?" or "What does this button
  do?" got the alert's answer on the console. They are listed on "Which fridges need me?" (and
  "this" on "It's fixed now") too, so they can never pick the alert's answer on their own.
  Reported for the core.
- **"Sum up my fridges" in place of "Summarise this".** On the console and `/today` the
  suggestion after the page's own is this app's summary (`screenChip`): how many units, how many
  are fine or need you, the open alerts and whether anyone has answered, and any food nobody has
  answered for, from what the page already loaded, with no key and nothing sent. (The core's
  "Summarise this" sends the screen to the AI.) On the public pages the core's own is left, and
  it now shows only once a key is set.
- **The handle.** When the robot is hidden at rest (every corner would cover something), after
  about 2 s with the page still a slice of the robot peeks in at the screen's left or right edge:
  a 44px button that opens Pilot, only where it covers no link, button, box, pinned bar or
  anything in `avoid` (the temperatures and status labels included). Left on (`handle` not set).
  The logo is not on the `avoid` list: at 320px and 375×667 on `/today` with an alert, the alert
  and "Needs you now" cards (both listed) fill both edges of the first screen, and the only free
  spot is the left edge beside the logo, where the handle peeks in over the logo mark's edge
  (listed, there was no robot and no handle there until a scroll).
- **On a computer** a link or button within 8 px of the robot counts as covered, a top corner
  touching the header's links is a last resort, and with every corner touching one the robot hides
  and the handle shows.
- **Under one of this app's answers** the page's own suggestions come first, then the welcome's
  pills, each once.
- Nothing to remove here: this app never raised the robot's corners (core 1.4.2's other fix) or
  moved it with `--pilot-keep-d`, and sets no `hideWhenCrowded`.

Checked in a real browser at 320×640, 375×667, 375×812, 390×844 and 1440×900 on `/`, `/pricing`,
`/help`, `/help/sensors`, `/for`, `/for/restaurant`, `/demo`, the console's sign-in, a new
business's console, a console with an open alert and `/today` with and without one, scrolled top
to bottom: the robot or the handle on the first screen of every page (the handle about 2.2 s after
load where every corner is busy, such as the top of `/help` on a phone or `/today` with an alert at
320px), neither over a button, a box, a temperature or a status label, and
Pilot still absent from `/signup`, `/legal`, a proof page, an error page and the
operator's `/book`. The only touches measured are 3 px of the tucked robot's box over the edge of a
link card (`/for` at 320 to 390px, where every card is a link): the core's side room is 30 px and
the tucked robot 46 px wide, 3 px in, the same on 3.30.0 (reported for the core). Typed questions
that fit an answer were answered with no key and no request carrying them; others opened key
settings with the words kept.

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
| `static/pilot/*` | Pilot core 1.4.2, unchanged (thirteen files, the robot pictures and the three layers in every size, and `poses/`) |
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
tools: confirm first, nothing private, and the refusals; the answers with no key, typed questions
matched to them, and "Sum up my fridges").
