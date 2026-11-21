# Changelog

What changed in CyberLogix AI, newest first, in plain words. The number at
the top must match the `VERSION` file; a test checks that it does.


## 3.22.0 — 2026-11-21

What the latest strict review found, fixed. No price or plan term has
changed, and the alarm itself works as before.

- **An open alert comes first.** Signing in while an alert is open, or
  nobody has answered it, goes straight to the Simple view (/today), where
  **I'm on it** and **It's fixed** are the first thing on the screen. The
  console pins a red **Needs you now** bar under the top bar on every tab,
  with the fridge, how warm, for how long and the two buttons, until it is
  dealt with.
- **"It's fixed" only closes what is fixed.** If the fridge is still out of
  range it says so ("It's still 48°F. We'll close it when it's back under
  41°F."), keeps the alert open, stops the calls because you have it, and
  closes it by itself once the fridge is cold. **Close it anyway** is still
  there, with one line saying why: pick moved the food, called for a
  repair or door was open, or write your own. Every alert closed by hand
  records who closed it and that line, and the inspector's log shows both.
  After a close by hand, nobody is alerted about that fridge again until
  its usual wait has passed again, so nobody is texted 17 seconds later.
- **Every page tells the same story.** The fridge's card, Problems, /today
  and the new bar all use one "out since" (when it went out of range, not
  when the alert opened) and one way of saying how long. While we wait to
  see if a fridge stays warm, all of them say **Watching** and when we will
  text ("We'll text at 3:53 PM if it stays warm") instead of "BREACHING" on
  one and "within its limits" on another.
- **Easier setup.** Sign-up shows four plain choices (restaurant or café;
  shop; pharmacy; clinic) and **Something else** for every other trade. The
  add-a-fridge form takes the kind of business from your sign-up instead of
  asking (with a **Change** link), keeps Sensor ID and serial under
  **Advanced**, and no longer opens on data centres. The alert wait and the
  limit are changed on the fridge's own card, not in a browser pop-up.
  Sign-up, the front page and the console all say four steps.
- **Calmer words.** A new sensor says "Waiting for its first reading", not
  BREACHING, NO FORECAST, an orange "tuned" and a red OFFLINE. States are
  in plain sentence case: Fine, Watching, Too warm, No signal. Screens say
  "Rosa is on it", not her email address. The fridge with a problem comes
  first in the list. The texts-off notice is one plain sentence.
- **Shorter on a phone.** The front page is under half its old length on a
  phone: how it works, what you need, the trades and the plans wait behind
  a tap, and the insurer's report is said once, with the rest of the
  record. The empty gap in the sign-up page's left column on a desktop is
  gone.
- **Pilot core 1.1.1**, copied unchanged from the MTRX Horizyn site: it
  looks again for a free corner as the page scrolls and moves out of the
  way of a box being typed in. It keeps clear of the console's tabs, the
  Needs you now bar and the front page's first-screen facts; once a chat
  has started, the café suggestions come first; and the key settings say
  in one sentence what an AI key is and where to get one.


## 3.21.0 — 2026-11-20

What a strict review found, fixed. No price or plan term has changed.

- **A door opened for a minute no longer wakes anybody.** A fridge has to
  STAY out of range before anyone is texted: 20 minutes for a food fridge,
  30 for a freezer, 10 for a vaccine or lab fridge (15 for a vaccine
  freezer). Each unit can have its own wait (**Alert wait** on its card;
  0 texts on the first reading). A reading 15°F or more past the limit
  still alerts at once, and so does a sensor going quiet. The defrost
  rule and the guard against stretched defrosts work with it: a defrost
  that overruns is alerted on at once, not after another wait.
- **Back to normal before anybody answered: no call.** The alert closes on
  its own with a plain note ("Back to normal after 3 minutes, probably the
  door or a delivery"), the people texted get a short all-clear, and the
  call that was due is cancelled. An alert somebody took is still theirs
  to close.
- **The Simple view puts live alerts first.** "Needs you now" at the top,
  with the fridge, the temperature, how long, and **I'm on it** and
  **It's fixed** buttons. It can no longer say "Everything is fine" while
  an alert is open. The explanation is folded underneath.
- **Readings are kept for 13 months.** Every reading for 30 days, then each
  hour's lowest, highest and average reading until it is 13 months old.
  The inspector's log, the compliance report, the proof report and each
  fridge's daily history read all of it, and **Delete everything** deletes
  all of it. (It was the newest 500 readings, under two days.) Two new
  settings for whoever runs the server: `CYBERLOGIX_KEEP_DETAIL_DAYS` and
  `CYBERLOGIX_KEEP_READINGS_MONTHS`. The database is bigger: see
  docs/LOAD-TEST.md.
- **Easier setup.** Sign-up lists each trade once. "Fridge or freezer?" is
  a choice you make, guessed from the name where it says ("walk-in
  freezer", "milk fridge"). The sensor guide starts with the no-coding way
  and a "We'll help you set it up" email link; the technical detail is
  further down.
- **Plainer words.** "Too warm: 43°F (limit 41°F)", "about 1 minute",
  "Your fridge", "Fridges and freezers online"; the in-range percentage
  waits for a day of readings; sign-in says what it is for. Calls are
  promised only on the plans that make them, and the forecast only where
  the plan has it. The plan box says plainly that a 12-month plan, billed
  monthly, can still be cancelled any time. The front page's food and
  medicine list no longer lists wine storage or trucks (they are on /for),
  and the October 2026 attack links to the report.
- **Shorter on a phone.** Long sections of the front page, and most cards
  of a new account's console, wait behind **More**.
- **Pilot core 1.1.0**, copied unchanged from the MTRX Horizyn site: a
  smaller robot on phones, the moving robot, and it keeps clear of alert
  banners and the buttons that answer an alert. Its first suggestions are
  written for a café.


## 3.20.0 — 2026-11-20

Pilot, the MTRX Horizyn assistant, comes to CyberLogix. No price has
changed, and alerts work exactly as before.

- **Pilot, on your own AI key.** The round robot in the corner of the
  front page, pricing, help, the industry pages, the demo, the console and
  the Simple view. Tap it to chat. It runs on your own key from Anthropic,
  OpenAI, Google Gemini or another compatible service, straight from your
  browser: we never see the key, the chat or the answers, and your AI
  provider bills you. It looks and works the same as on the MTRX Horizyn
  site. It isn't on proof pages, error pages or the operator's book.
- **It knows the product.** How alerts and the calls after them work,
  the 41°F fridge and 10°F freezer limits, what sensors you need, the
  set-up steps, the plans from $59, the trial's 5 calls, proof reports,
  the Simple view and the Advanced console, what happens if the power or
  internet goes off, and an honest list of what CyberLogix does well
  (no other company named).
- **It can help in the console.** Signed in, ask "what's warm now?",
  "show open alerts", "what does this alert mean?", "is the food still
  OK?" (the same careful rule of thumb, with its note that it is not a
  ruling) or "explain this page". It can also acknowledge an alert, mark
  one fixed, add a fridge or freezer (it asks which) or make a proof
  report, **only after you press Allow**, and with your own permissions.
  It can't change your plan, delete anything, change who gets called, or
  send texts or calls.
- **Your own AI key for the alert line.** An owner can save the
  business's Google AI Studio (Gemini API) key under Setup, "Your AI key for alert
  wording". The one line of each alert that says what to check first is
  then written on your key, for your alerts only. It is stored encrypted,
  shown only as its last four characters, never written in our logs, and
  can be removed or tried. If it fails or is slow, the alert goes out on
  time with the plain wording. A new setting,
  `CYBERLOGIX_KEY_ENCRYPTION_SECRET`, must be set on the server for this;
  without it the card says why and hides the form.
- **Agreements 2.8.** The privacy statement now says what Pilot sends and
  to whom, and how a saved AI key is kept and used.
- The security policy lets the browser reach only the AI providers Pilot
  offers (the same list as the MTRX Horizyn site), allows a video Pilot
  made to play, and lets the microphone be used for Pilot's voice input
  on this site only.


## 3.19.0 — 2026-11-19

What an honest review of the product found, fixed. No price has changed.

- **A healthy fridge no longer alarms.** A restaurant's walk-in cooler
  was given a freezer's 32°F limit, so a correct 37°F reading opened a
  critical alarm. The add form now asks **Fridge or freezer?**: a food
  fridge alarms over 41°F and a food freezer over 10°F (vaccine and
  specimen units get their own ranges), and that limit is saved on the
  unit. A restaurant unit added without an answer gets a fridge's 41°F.
  The demo uses the same limits as the product, and the restaurant page
  says 41°F for fridges and 10°F for freezers. **Units you already have
  keep the limit they were alarming on** (32°F for a restaurant unit with
  no limit of its own) until you change it; nothing moved by itself.
- **We say plainly that we do not sell sensors.** The pages used to say
  "its own separate sensors" and "a small battery-powered box". The front
  page, the pricing page and the sensor help page now have **What you
  need**: which wireless sensors work (ones that can send to a web
  address, or whose maker's cloud we can read, such as Monnit and
  SensorPush), roughly what they cost, and the set-up in six steps. No
  shop is linked.
- **What happens if the internet or power goes off** is said on the front
  page and the pricing page: after 30 minutes with no readings we text
  everyone on call, and our alerts still go out from our servers.
- **"Protected" only once readings arrive.** Getting started is four
  steps now: add a sensor, add who gets alerts, **first reading
  received**, and a test alert. Nothing is called protected before the
  sensor has reported.
- **Calls are only promised where they happen.** The console's Who gets
  called card (now "Who gets alerts" on Starter), the escalation preview,
  the Simple view and the proof report for your insurer mention phone
  calls only when your plan includes them. **The free trial can now try
  the call:** 5 alert calls in all (about a minute each), then texts only,
  and every screen says how many are left.
- **No plan changes without a yes.** Moving to a paid plan shows the plan,
  the price, that it is a 12-month plan, what cancelling means and your
  trial days left. Nothing changes until you press **Yes, move**, and the
  server refuses a change that was not confirmed.
- **A small café sees a small console.** On a trial or Starter with a few
  units, the console opens on the Simple view once you are set up, and
  the tools for bigger estates (price quotes for chains, meeting notes,
  staff licences, spending limits, the record's fingerprints, the business
  key, the estate add-ons list) wait under a new **Advanced** tab. A
  search still finds everything. Delete everything is at the bottom of the
  page, away from the add form; the cloud, OmniArmor, password and to-do
  forms are styled; the two columns are about the same length.
- **One price story.** The front page, the plans and the console header
  all start "From $59 a month" (Starter). Each plan lists what it gives,
  the extras say they cost extra, and the four plans sit in one row.
- **The businesses that sign up are first.** Sign-up offers Restaurant or
  café, Shop or grocery, Pharmacy and Clinic first. The front page shows
  food and medicine; data centres, hangars, yachts, solar and growers are
  on /for. "CyberTech Data Centers" is now "Data centers".
- **No more overclaiming.** "Loss avoided" counts only a real failure
  caught in time: answered within two hours, out of range for at least 15
  minutes and at least 5°F warmer than normal. A false alarm is not a
  $15,000 save. The proof report shows the hours watched beside the
  percentage online, and no percentage at all for under an hour.
- **A defrost stretched on purpose still alerts.** Attackers in a real
  2026 incident stretched defrost cycles to hold off alarms. A defrost
  much longer than the unit's normal ones, or longer than 45 minutes at
  all, now alerts.
- **Smaller fixes.** The restaurant page's "We format it as Health
  Inspector Log Formatter" now reads properly. The console no longer asks
  for the forecast on a plan without it. "Mark it fixed" in the demo
  updates at once. The front page has a menu on phones and its sections
  show without scrolling. The operator's page (/book) is a plain "page
  not found" to the public. The help says "Press Add it". Alert and report
  numbers count your own (INC-3-QHKX), not everybody's; old numbers still
  work. Codes in the console are words. "Nobody finds out until seven"
  everywhere.

## 3.18.0 — 2026-11-19

Fast and solid: if alerts stop, you hear about it, and the console opens
fast on a bad phone signal.

- **You are told when alerts stop flowing.** The app now watches its own
  alarm path, from a thread of its own: readings arriving from anybody,
  the alert job running, and texts, calls and alert emails getting
  through. When one stops it emails `CYBERLOGIX_OPERATOR_EMAIL` and texts
  the new `CYBERLOGIX_OPERATOR_PHONE`, once, and again once it is fixed,
  even across a restart. One business's wrong phone number or broken
  chat hook does not count as an outage. The daily digest lists anything
  still wrong.
- **Two checks for an uptime monitor.** `/healthz` says the app is up.
  `/readyz` says whether alerts can get out right now: 200, or 503 with
  the reason in a sentence and the state of each part (database, alert
  job, readings, texts, calls, email). No names, numbers, versions or
  error messages, and cheap to ask every minute. Steps to set up a free
  monitor, and what each alert means, are in docs/OPERATIONS.md under
  "If the app is down".
- **The model can no longer hold an alert back.** The line in the alert
  saying what to check was waiting on the model with no time limit. A
  model service that took the connection and then said nothing held the
  alert text back indefinitely. It now gets 6 seconds
  (`CYBERLOGIX_AI_TIMEOUT_SECONDS`), then the alert goes out with the
  plain line. Every other outgoing call already had a time limit; the
  list is in docs/OPERATIONS.md.
- **No model name in the code.** `CYBERLOGIX_GEMINI_MODEL` has no
  default now. Set it to the model's name to turn the written lines on;
  unset, every message uses its template, as with no key.
- **The console opens fast.** Pages, scripts, stylesheets and JSON are
  compressed, and the files a page names carry a version so the browser
  keeps them. First open: 388 KB down to 109 KB, 8.2 s down to 2.9 s on
  a slow phone connection. Opening it again: 0.6 KB. An open console
  fetches the cards that rarely change every fifth poll, so it sends 8
  requests every 20 seconds instead of 28. Numbers in docs/PERFORMANCE.md.

## 3.17.0 — 2026-11-17

The first five minutes, for a restaurant owner.

- **See it live, with no account.** The landing page has a **See it
  live** button. It opens `/demo`: a console with four pretend units (a
  walk-in cooler, a freezer, a prep fridge and a bar fridge). A minute in,
  the walk-in starts to warm. You watch it go over its limit, the alert
  text go out, the calls start when nobody replies, and what replying 1
  does. It ends with **Start your free trial**.
- **The demo is pretend, and says so.** "Demo — pretend data" is at the
  top of every screen. It runs in your browser. Nothing is sent, nothing
  is saved and no account is made. The server only hands over the story
  and the words a real alert would use, and that is rate limited. The
  public copy on GitHub Pages now has it too (`docs/demo.html`).
- **No AI model name anywhere public.** `/api/health` used to say which
  model writes the alert wording, and the public demo copied that out to
  anyone with the link. It now says only whether that part is working.
  The demo builder refuses to build a page that names a model. The
  label on the console's meeting notes card that named the model now
  says "Written for you".
- **Getting started is three steps.** The checklist at the top of a new
  console is now: add your first sensor, add who gets alerts, send a test
  alert. Each one ticks itself, and the card goes when all three are done.
  "Add me" puts you on the alert list with the mobile you signed up with,
  in one tap. "Send a test" sends the test text from the checklist, with
  no "are you sure", because it says TEST and opens nothing. Adding a site
  is no longer a step; you can still add one under Setup.
- **No blank tabs.** Fridges, Alerts, Proof, Money, People and Licences
  each start with one sentence saying what will show there and a button
  for the next step, until there is something to show. Cards that were
  dead ends now offer the next step too (Bills, Claim packets, How you
  compare, and anything not on your plan). An empty "Devices knocking"
  card no longer shows up on the Fridges tab.
- **Plainer words on the way in.** Sign-up asks for a business name and
  "What kind of business?", hides the code box behind "Have a code?", and
  picks restaurant for you when you come from the demo. After adding a
  sensor, the key box says what to do next and links to the setup guide
  for each brand. The business key is marked as only for connecting other
  software. The "Twilio is not configured" banner says texts and calls are
  switched off, in words. The plain view no longer says "all 0 of your
  fridges are fine".
- **Phones.** The sign-up page had no side margin on a phone; it does now,
  and the form comes before the reasons. The landing page scrolled
  sideways at 360px wide (the plan buttons would not wrap); it does not
  now, and "Start a free trial" and "See it live" are on the first screen.
  In the console the last row of tabs no longer lets cards show through,
  only the tab bar stays pinned on a phone, card headings wrap instead of
  squeezing, buttons and boxes are bigger to tap, and checklist buttons
  land the card below the pinned bar.

## 3.16.2 — 2026-11-17

Follow-ups to the pre-launch review, and three decisions on how money and
pauses work.

- **Renewing during a pause, then coming back early, no longer gives free
  days.** The new contract started after the whole pause, and coming back
  early only shortened the pause on the old one. The pause now moves onto
  the new contract, so its first bill comes forward too.
- **A past year's yearly bill with mid-year additions cannot be voided.**
  Its replacement would have charged those additions again. The operator
  is told to issue a credit note instead. This year's bill can still be
  voided and re-issued as before.
- **The quote matches the contract.** `/api/billing/deal` now works out
  each year exactly as a signed contract bills it, and on a yearly plan
  gives two free months in every year of the term, not only the first.
  Its `total_contract_value_usd` is what the term really bills.
- **No bill is ever below $0.** Whatever the settings, discounts are cut
  back so a bill never owes the customer money, and the line says so. A
  bill that comes to $0 is marked paid at once.
- **A repair lead is charged only once it has gone.** If the email to the
  repair company cannot be sent, nothing is recorded or charged, nothing
  is left to go out later, and the business is told it did not go through.
- **Pausing is limited to 3 months a year.** At most 3 months paused in
  any 12, counting the time actually used. The Pause or cancel card says
  "You can pause for up to 3 months a year." and offers only what fits.
- **On a yearly plan, the offer to stay and the founding member code
  come off what the customer actually pays.** 20% off 3 months' worth is
  now 20% of 3/12 of the yearly price ($74.50 on a $1,490 year), not of
  three list-price months. The founder line says "of the yearly price".
- **Partner refunds take back only the share of what was refunded.** A
  part refund takes back the share of that part; a full refund, the whole
  share of that payment. The business's later payments keep earning its
  partner as usual. The operator's refund form takes an invoice number
  and an amount.
- **Documented:** a switched-off insurance partner keeps earning on the
  businesses it already referred, for the months its share runs.

## 3.16.1 — 2026-11-17

Fixes from a review of 3.13 to 3.16 before launch.

- **A voided yearly bill no longer charges an added location twice.**
  When a location or units were added part-way through a paid year, they
  were billed on their own mid-year invoice. If the year's invoice was
  then voided (a wrong PO, say) and issued again, the new one charged the
  whole year at the bigger size, and the mid-year invoice still stood. The
  re-issued invoice now leaves out what the mid-year invoice already
  charges, on a line that says so, and comes to what the voided one did.
- **A pause no longer skews the charge for a location added on a yearly
  plan.** The rest-of-the-year charge was worked out over a year made
  longer by the pause, so a location added after a pause was charged too
  little, and one added just before a pause was charged for the paused
  months too. Paused days now count on neither side.
- **"Get a repair quote" tapped twice at once sends one lead.** Two taps
  in the same moment both got past the "already sent" check, so the unit
  went to two repair companies and both were charged a lead fee. The
  check and the sending now happen together; the second tap is told the
  unit was already sent.
- **A proof of monitoring report cannot be re-sealed from the database.**
  With `CYBERLOGIX_PROOF_REPORT_KEY` set, a report that claimed to be
  sealed with the database's fallback key was still checked with that
  key, so anyone able to edit the database could change a report's
  numbers, write a key of their own and re-seal it as genuine. With the
  setting on, only a seal made with it is trusted; anything else reads
  "Cannot check". Without the setting nothing changes.
- **A test that failed from the 17th of each month** (a monthly report
  held back for a late bill) now measures lateness from the day the
  report runs, not from today.

## 3.16.0 — 2026-11-15

Founding member codes, on our own invoices.

- **A founding member code.** The company site's early-access list
  promises founding members a launch discount by a code. Our billing is
  our own invoices with payment links, not card-processor checkout, so a
  processor's promotion code has nothing to attach to here. The code is
  taken as one of our own invoice lines instead (`founders.py`).
- **What it gives:** 20% off the first 3 monthly invoices, as a line of
  its own on each ("Founding member: 20% off your first 3 months (month 1
  of 3)"). On a yearly plan it is 20% of 3 months' worth, taken once on
  the first yearly invoice, and the line says so. The percentage is of the
  recurring lines after any chain discount, the same base as the offer to
  stay.
- **Where it is entered:** the code box at sign-up, which already takes a
  friend's or a partner's code, or later on the console's Money tab (the
  Bills card), by an owner, before the first paid invoice. Entered after
  an unpaid first invoice, it starts with the next one. Case and spaces
  do not matter.
- **Limits.** Once per business, ever. Never on the same invoice as the
  offer to stay: when both would apply, the bigger line goes on, and the
  other is not used up by that invoice. A last day to enter it can be set;
  a business that entered it in time keeps the discount after it.
- **Off unless set.** New settings `CYBERLOGIX_FOUNDER_CODE` (empty: off,
  and nothing is shown), `CYBERLOGIX_FOUNDER_PERCENT` (20),
  `CYBERLOGIX_FOUNDER_MONTHS` (3) and `CYBERLOGIX_FOUNDER_UNTIL` (a
  YYYY-MM-DD date, optional). Switching the code off stops new codes; a
  discount already given carries on.
- **The numbers.** Each code entered is counted with no business named,
  and the Money tab of `/book` shows founder codes used in the last 30
  days and ever.

## 3.15.0 — 2026-11-15

Repair partners, pause instead of cancel, and pages for seven more trades.

- **Get a repair quote.** When the pattern check calls a unit failing (warm,
  not a defrost or a door, and not back down) or the forecaster sees it
  drifting warm towards its limit, its card in the console offers "Get a
  repair quote". Nothing is sent until the business reads a confirm
  screen showing exactly what would go, picks the contact (a phone, an
  email, or both) and the address, and ticks the box. Operators and owners
  can send; a viewer cannot.
- **A lead holds four things, nothing else:** the business name, the
  contact chosen, the address, and a plain summary of the problem (fridge
  or freezer and its name, what we saw, since when). No sensor, readings,
  keys or account access. The lead email is blanked from our mail log once
  sent, and logs mask the contact.
- **One repair partner per lead, in turn.** It goes to one approved,
  switched-on partner whose ZIP or postcode prefixes or cities cover the
  address; whoever has waited longest goes first. At most one lead per unit
  every three days. Nobody covering the address means nothing is sent.
- **Repair partners apply** at `/repair-partners` (rate limited, 3 a caller
  an hour). Applications are mailed to `CYBERLOGIX_OPERATOR_EMAIL`; the
  operator approves them on the Partners tab of `/book` and sets their
  areas. Each partner gets a key for their dashboard at
  `/repair-partners/dashboard`: their leads and what they owe.
- **A fee per lead, billed by hand.** Each delivered lead is owed at the
  partner's fee: new setting `CYBERLOGIX_REPAIR_LEAD_FEE_USD` ($35), kept
  with each partner when approved. A partner can mark a lead bad within 7
  days; the operator refuses it (no charge) or keeps the charge. The
  operator emails partners a statement and records their payments.
  Nothing moves money. Every page says we do not do repairs and do not
  vouch for any technician.
- **Pause instead of cancel.** The console's Money tab has a Pause or cancel
  card: pause for 1, 2 or 3 months, the one-time offer to stay, and Cancel
  my plan right below, one press as before. During a pause the next bill
  and every one after move later by exactly the pause, so nothing is billed
  and the days already paid for are kept; readings are kept and nobody is
  texted, called or emailed about a unit, and the console says so at the
  top in red. It ends on its own on the date chosen, with a reminder
  email three days before. Coming back early, or cancelling while paused,
  gives back the unused part.
- **The offer to stay:** 25% off the next 2 months, as a line on each of
  those invoices, once per business ever. New settings
  `CYBERLOGIX_SAVE_OFFER_PERCENT` (25) and `CYBERLOGIX_SAVE_OFFER_MONTHS`
  (2).
- **The numbers.** Pauses, saves and cancels are counted with no business
  named, and shown on the Money tab of `/book`: paused now, and each in
  the last 30 days and ever.
- **Pages for seven more trades** at `/for/florists`, `/for/breweries`,
  `/for/ice-cream-shops`, `/for/butchers`, `/for/school-cafeterias`,
  `/for/vaccine-clinics` and `/for/grocery-stores`: the risk, what we do,
  and the price that applies (the restaurant rate and Starter for food
  shops, the pharmacy rate for a vaccine clinic, "Custom pricing — ask for
  a quote" for florists and breweries). Rendered on the server with their
  own titles and descriptions, listed on `/for` and in the sitemap, as is
  `/repair-partners`.
- **Agreements 2.7.** One sentence in the privacy statement: if you ask for
  a repair quote, the one repair company it goes to gets your business
  name, the contact and address you chose and a short note about the
  problem, and nothing else.

## 3.14.0 — 2026-11-14

Proof of monitoring for insurers, and a partner programme for brokers and
insurers.

- **Proof of monitoring report.** An owner can make a one-page report for
  any location and month (console, Proof tab, or `POST
  /api/proof-reports`): the sensors watched and how much of the time each
  was online, every temperature alert and how fast somebody acknowledged
  it, the longest gap in the data, and who gets alerted by role only (no
  names, phone numbers or emails). It opens on its own page to print or
  save as PDF.
- **A link anyone can check.** Each report has a verify code (80 random
  bits) and a link to `/verify/<code>`. That page says whether the report
  is genuine and unchanged, and shows only the business name and the
  headline numbers. The report is frozen and hashed when it is made, and
  the hash is sealed (HMAC-SHA256) with the server's key, so an edited
  report or a made-up code reads "changed" or "not found". An owner can
  switch a link off, and it then says so. The page and its API are rate
  limited (30 checks a caller in ten minutes).
- **Gaps in data are written down as they happen.** Every silence of 10
  minutes or more is kept when it ends, so time online and the longest gap
  no longer depend on how many readings are kept. Time before this version
  is not counted, and a report says so.
- **Insurance partners.** Brokers and insurers apply at
  `/insurance-partners` (rate limited, 3 a caller an hour). The
  application is mailed to `CYBERLOGIX_OPERATOR_EMAIL`, and the operator
  approves it on the new Partners tab of `/book`. An approved partner gets
  a partner code (`PARTNER-XXXX-XXXX`), a sign-up link and a dashboard
  key. A business that signs up with the code gets the same longer trial
  as a friend's referral.
- **Revenue share, recorded as owed.** A partner earns 10% of what each
  business they referred pays us for its first 12 months from its first
  payment, on money that arrived, to the cent. A late charge, money over
  an invoice's total and a business a reseller looks after earn nothing;
  a refund takes the share back. The operator records payouts by hand;
  nothing moves money. New settings:
  `CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT` (10) and
  `CYBERLOGIX_INSURANCE_PARTNER_SHARE_MONTHS` (12), kept with each partner
  when approved.
- **Partners see names and plans only.** Their dashboard
  (`/insurance-partners/dashboard`) shows the businesses that joined with
  their code by name and plan, what they earned and what was paid. The
  partner key opens nothing else; the only window onto a business's
  fridges is a report link the business chose to share.
- **A section on the front page** for insurers and brokers, in plain
  words: show your insurer proof that your fridges are watched 24/7. It
  says we are a monitoring service, not an insurer, and promises no
  discount.
- **Agreements 2.6.** The privacy statement says, in two short paragraphs,
  that a partner whose code a business used sees its name and plan and
  nothing inside, and that a report is seen only by whoever the business
  gives its link to.
- **New setting `CYBERLOGIX_PROOF_REPORT_KEY`**, the seal's key. Unset, a
  key is made once and kept in the database. The Render blueprint and the
  runbook generate one.

## 3.13.0 — 2026-11-14

Yearly plans at two months free, and chain discounts anyone can see.

- **Pay yearly: 12 months for the price of 10.** A year paid up front is
  ten times the monthly price ("2 months free", about 16.7% off), on
  everything recurring: Starter ($590 a year), each restaurant ($1,490),
  each pharmacy ($1,990), extra units ($150 a year each), the Kitchen
  Safety Bundle and the other extras. One invoice for the year, with the
  two free months on their own line, then nothing until it renews.
- **It replaces the 10% annual prepayment discount.** That was a setting
  (`CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT`, now ignored) quoted as
  "10% off that year". There is one yearly offer now, everywhere. A
  contract that took the old prepay is a yearly contract and renews at
  ten months for twelve, which costs it less.
- **Added part-way through a paid year?** A location or units added
  mid-year are billed at once, for the rest of that year at the yearly
  price, on one small invoice. Taking something off refunds nothing, and
  putting it back is not charged twice.
- **Renewal reminders** 30 and 7 days before a yearly plan renews, each
  sent once, with what the next year will cost.
- **Switch between monthly and yearly at renewal.** From the contract
  card in the console (or `POST /api/contracts/billing-interval`); the
  switch starts when what has been paid for runs out. A renewal can also
  switch.
- **Cancelling, plainly:** nothing more is billed, the service carries on
  to the end of the month or year already paid for, and what was paid is
  not refunded. Said at checkout (the contract form), on the pricing page,
  the help page, sign-up and in the agreement; the cancel response gives
  the date the service runs to.
- **Chain discounts, self-serve:** 10% off each restaurant or pharmacy
  location from 5 locations, 20% off from 20, counted together. On its
  own invoice line ("Chain discount (20 locations): −20%"), from the next
  invoice, with nothing to sign. Location prices only: not Starter, not
  the $15 extra-unit fee, not the extras. No chain pays more than a
  bigger one, so 18 or 19 locations pay the 20-location price. Yearly is
  worked out on the discounted price: 20 restaurants are $119.20 each,
  $2,384 a month or $23,840 a year.
- **One volume discount.** The enterprise ladder (2.5% off for every ten
  units, up to 10%) is gone; volume contracts use the chain tiers, and a
  business on a contract is billed from the contract alone, so nobody
  gets two discounts. Every contract price that changed went down.
- **The pricing page** has a monthly/yearly switch on the calculator, the
  chain discount on the calculator's lines, a "Paid yearly" column, a
  chain table for restaurants and pharmacies, the worked example (worked
  out from `pricing.py`, not typed) and a yearly panel with the
  cancellation rule. The help page has a new question, "Can I pay yearly,
  and what if I cancel?", and the chain discounts under "What does it
  cost?". The sales sheets, sign-up, the console's bill and contract
  cards, the price list API and the margin report say the same.
- **Agreements 2.5.** The subscription agreement now says how paying
  yearly works and what cancelling does. No prices are quoted in it.

## 3.12.2 — 2026-11-14

- **The real address, cyberlogix.mtrxhorizyn.com.** DEPLOY.md, the
  README, the staging, operations, alerts, partners, OmniArmor and sensor
  guides, `.env.example` and the comments in `render.yaml` now name it
  instead of a placeholder, with `help@cyberlogix.mtrxhorizyn.com` for
  customers and `no-reply@cyberlogix.mtrxhorizyn.com` for sending. The app
  still reads every address from its setting (`PUBLIC_BASE_URL`,
  `MAIL_FROM`, `CYBERLOGIX_SUPPORT_EMAIL`), and the in-app sensor guide
  still uses the address it is served from. `CYBERLOGIX_ALLOWED_ORIGINS`
  stays empty: MTRX Horizyn's console asks from its server, not a browser.

## 3.12.1 — 2026-11-14

- **The rate card is only for signed-in businesses.** The two price
  lookups behind the console's chain calculator now need a business key.
  The custom trades show "ask for a quote" on every public page, and these
  addresses no longer give their rates to anyone who asks.
## 3.12.0 — 2026-11-14

New, lower prices; no setup fee; a quote for every trade but two; and
signed Kitchen Safety Bundle reports, so the bundle can include
OmniArmor Pro.

- **Starter, $59 a month** (was $79). Still one location and up to 3
  units, with everything else as before.
- **Restaurants, $149 a month per location** (was $249), for up to 10
  units at that location. **Each unit past 10 at the same location is
  $15 a month**, on its own invoice line ("2 extra units past 10 at a
  location, $15 each"): about what each of the first ten costs, rather
  than a second location for one more fridge. The allowance is per
  location, so 11 units at one shop and 9 at another is one extra unit.
- **Pharmacies, $199 a month per pharmacy** (was $1,299), billed per
  pharmacy with the same 10-unit allowance. It used to count every
  fridge as a "pharmacy", the bug restaurants had before.
- **No setup fee**, on any plan (was $1,500 a site). Gone from invoices
  and a contract's first bill, the deal, the pricing page, the help
  page, the sales sheets and the docs.
- **Every other trade is "Custom pricing — ask for a quote"** on the
  pricing page and its calculator, the front page's sector cards, the
  industry pages, the sales sheets, the help page and the public
  catalogues, with a **Talk to us** link (a mail to the support address,
  also printed as text you can copy). Their rates stay in `pricing.py`,
  so businesses already on them are invoiced exactly as before. The
  calculator says "Ask for a quote" instead of adding up a trade it has
  no price for, and asks how many units each location has so it can add
  the $15 ones.
- **Businesses already on the old prices pay the new ones from their
  next invoice.** Invoices already sent are not changed. A restaurant
  that signed up before the change and has more than 10 units at a shop
  never pays more than its old $249 for that shop.
- **The Kitchen Safety Bundle stays +$29 a month per location, and now
  includes OmniArmor Pro for that location.** OmniArmor waives its Pro
  fee for a workspace whose daily reports arrive signed by a CyberLogix
  server: every report now carries `X-CyberLogix-Signature:
  t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>` over the
  exact bytes sent, keyed with the new `KITCHEN_BUNDLE_SECRET` setting
  (the same value on both apps). Unset, no header is sent and reports
  work as before.
- The agreements quote no price or setup fee ("Prices are on your
  order"), so their text and version (2.4) are unchanged.

## 3.11.0 — 2026-11-13

Three ways to earn: a $79 Starter plan, "bring a friend" referrals, and
the CyberLogix side of the Kitchen Safety Bundle with OmniArmor.

- **Starter, $79 a month.** One location and up to 3 fridges or freezers,
  for a small cafe or shop's first yes. Texts, the site-wide alert, the
  inspector's log and the monthly report are on it; phone calls and the
  forecast come with Growth. A real plan beside the others
  (`PLAN_TIERS["starter"]`), priced in `pricing.py`, with no setup fee.
  The fourth unit, a second location, or a unit on another trade's limits
  is refused with a sentence that names Growth and says nothing has been
  charged. Invoices bill one flat line; a move to Growth part-way through
  a period bills, on the next invoice, the rate card less Starter for the
  days after the move, and nothing for the days before. The pricing page,
  its calculator (still tested against `pricing.quote_monthly`), the front
  page's plans, sign-up, the help page, the restaurant sales sheet and the
  console's What you pay card all show it from the code.
- **Bring a friend.** Each business has a referral code and link (Money,
  Bring a friend, owners only). A business that signs up with it gets a
  30-day trial. When it pays its first invoice in full, the referrer gets
  one month free on one location, as a credit on its next invoice. Not for
  yourself or a business that shares a person with yours, once per
  business, nothing for a part payment or a business that cancelled first,
  withdrawn if the payment is refunded (the operator records that on
  /book), and at most 12 a year. A business a reseller partner manages
  earns its partner the commission instead of anyone a referral credit.
  The console shows who joined by business name only, and what was
  earned; the monthly report says so too.
- **Account credit,** which did not exist: a credit comes off the next
  invoice as its own line, never below zero, carries what is left, pays a
  $0 invoice outright so nobody is chased for it, and is given back if
  the invoice it was used on is voided.
- **The Kitchen Safety Bundle, +$29 a month per location,** free on the
  trial. The owner pastes OmniArmor's address and connection key (Setup,
  OmniArmor connection); the key is stored encrypted with AES-GCM under
  the new CYBERLOGIX_CREDENTIAL_KEY setting and never shown again. After
  each site's day ends, one combined report goes to OmniArmor exactly as
  its contract describes: units in range all day, each breach with the
  role that acknowledged it and what was done as fixed codes (never a
  name), the warmest reading, and a link to that day's inspector log.
  429 and 5xx are retried with backoff, respecting Retry-After; each day
  is claimed before it is sent, so it goes once, and again only as a
  correction when what it says has changed. "Last sent 2026-11-12 · OK"
  on the card, a "Send a test report" button, and one email to the owners
  when two days have not got through. The tests send every report through
  OmniArmor's own validation, ported. docs/OMNIARMOR-CONNECTION.md has it
  all.

## 3.10.0 — 2026-11-13

A site-wide alert when many units warm up at once, and a section on the
landing page about fridge controls being taken over.

- **The site-wide alert.** When 3 or more units at one site (or half of
  a site's units, never fewer than 2; 2 at night) start warming within
  20 minutes of each other, or 4 units across 2 or more of a business's
  sites (3 at night), the sweep raises one urgent alert on top of each
  unit's own: "Possible site-wide problem at Main Street: 5 of 7 units
  warming at once. Power cut, open door or tampered controls? Check now."
  It goes straight away to the whole on-call list for those sites and to
  the business's own number, by text, email and, on plans with calls, a
  call to every one of them with no ten-minute wait. Quiet hours do not
  hold it back. It uses the same send path, so spend caps, re-sends and
  outage handling apply, and it is claimed once, so two sweeps at once
  send it once.
- **It is not fooled by a defrost or an outage.** A unit in its own
  learned defrost, or whose defrost is due, is not counted; a defrost
  switched on hours early is. Sensors that have gone silent never count,
  and when half of a site's sensors go silent together it is treated as
  an outage and left to the quiet-sensor text. Another business's units
  never count.
- **"1" acknowledges it,** from anyone it reached including the owner,
  and so do the keypress and the Acknowledge button. Acknowledging it
  also acknowledges the open alerts of the units in it, so one answer
  stops every call for that site.
- **Shown as its own thing:** a red banner above everything in the
  console listing the units, a "Site-wide" card in the incident feed, its
  own section in the inspector's log, and its own lines in the monthly
  summary. It is never counted as an extra excursion.
- **Every figure is a setting** (`CYBERLOGIX_GROUP_ALERT_*`), explained in
  docs/ALERTS.md, and the help page has a new question about it.
- **The landing page** has a section, "Your fridges can be hacked. Your
  alarm shouldn't be.": in October 2026 attackers took over the fridge
  controls at hundreds of supermarkets in France; CyberLogix watches with
  its own sensors and only ever reads, so it still sees the warming and
  cannot be used to change a fridge. It says plainly that it cannot stop
  an attack: it finds out in minutes and calls your team. The restaurant
  and pharmacy pages carry one line saying the same.

## 3.9.0 — 2026-11-03

Server errors reported to one inbox, privacy-friendly usage counts, one
look for every email, and the plan and support address on the About card.

- **A server error is emailed,** if CYBERLOGIX_ERRORS_EMAIL is set: the
  time, the version, the method and route, the error, the reference the
  error page showed, and the stack trace. Never the request body,
  cookies, headers, the caller's IP, or anybody's email address or phone
  number. One email per distinct error an hour and at most 20 a day,
  counted in the database. The fault list is unchanged, and now also
  takes keys and tokens out of the error messages it keeps; before, an
  error message that quoted a key kept it.
- **Daily usage totals** for the operator: sign-ups, sensors added, test
  alerts, alerts acknowledged (by button, text reply and keypress),
  inspector's logs and help page views. Numbers per day, nothing about
  who, deleted after 400 days. In /book under Health, at
  /api/admin/usage, and in /api/stats for MTRX Horizyn's console. The
  privacy statement says so (version 2.4).
- **Every email has a plain-text part and an HTML version** in one
  600px column, made from the same text, with the "CyberLogix is a
  product of MTRX Horizyn" footer once. Before, only the monthly summary
  had an HTML part, and it left out the alerts its text listed.
  Licence reminders and the operator's digest now say where to change
  them; marketing mail keeps its unsubscribe link; alerts, bills and
  password resets say they cannot be switched off.
- **The About and help card shows your plan** and, when the deployment
  sets one, the address to write to for help, beside the version and
  the link to /help.

## 3.8.0 — 2026-11-03

A sheet to print for each industry, an API reference that needs no CDN,
partners set up end to end, and the runbook brought up to date.

- **A one-page sheet for every industry,** at /for/<industry>/sheet and
  linked from each industry page: the problem, the fix, where the alarm
  is set, what the text, email and call say, the prices and the free
  trial. Every figure on it comes from the code that charges and alerts,
  and it prints on one side of A4 or Letter.
- **The pricing page names the one-time setup charge** on a contract's
  first bill, and that terms longer than a year rise each year. Both were
  on the bills and on no page a buyer reads first.
- **An API reference at /developers,** linked from the help page: every
  endpoint a customer can call, the key it needs, its parameters and an
  example body, built from the routes on the server. It loads nothing
  from elsewhere and runs no script, so it works where /docs and /redoc
  are blank. Routes only we can call are not listed, and no key is shown.
- **Partners can be set up from start to finish.** The operator can find
  a business's account ID by its name or email, which attaching it to a
  partner needs and nothing showed before. A business can see the name
  of the partner that looks after it. docs/PARTNERS.md is the whole job.
- **A staging copy that cannot reach real people:** docs/STAGING.md, and
  CYBERLOGIX_NOINDEX, which keeps a copy that is not the live one out of
  search results.
- **DEPLOY.md** now covers the Twilio reply webhook, the support address,
  growing the disk to 5 GB, the restore steps, the monthly summary, the
  demo account, rotations and quiet hours, and checking the version.

## 3.7.0 — 2026-11-03

Paying late never silences an alarm; a monthly summary for owners; five
businesses on one sign-in; how to set up every kind of sensor; a demo
account.

- **A late bill can no longer stop the monitoring.** Under a signed
  contract the licence ran out before the next bill was even due, and
  two weeks later readings were refused and nobody was phoned about a
  warm freezer. While a contract runs, the most an unpaid bill does is
  pause reports, at 45 days, as the agreement says.
- **The console says when a bill is late,** from the day it falls due,
  with a Pay now link for that bill and how long before reports pause.
  It no longer counts a contract customer down to an end date that
  stops nothing.
- **A card payment is listed once.** Paying through the pay link also
  put the same money on the unmatched-payments list, waiting to be
  credited a second time by hand.
- **A monthly compliance summary** goes to each business's owners in the
  first week of the month: units, readings kept, every time something
  went out of range with who answered and how fast, what is still open,
  and a link to the inspector's log for the month. Once per business,
  through restarts; with an unsubscribe link.
- **Signing in opens the business you had open last,** if you have
  several. The link in the monthly summary opens the right business.
- **Setting up sensors, step by step,** at /help/sensors and in
  docs/SENSOR-SETUP.md: your own device, devices that put the key in the
  body, Monnit, SensorPush, a vendor's cloud, and anything that sends
  JSON. Every address, header and field in them is checked against the
  code.
- **A sensor key works where its notice says.** The send-anything
  address now reads X-CyberLogix-Sensor-Key; a device set up as told was
  refused.
- **A new rotation puts the first person on call straight away.** Saved
  before the day's handover time, the last person in the list was on
  call until it.
- **A demo account:** `python demo_account.py --db PATH` seeds a made-up
  restaurant group into a new database, prints how to sign in, refuses a
  database with anything in it, and keeps its backups out of the live
  backup folder.

## 3.6.1 — 2026-11-03

- **Server logs keep people's details out.** Log lines about texts and
  calls show only the last four digits of a phone number, and a provider's
  error text has numbers and addresses blanked. A new trial and a failed
  sign-in log the email address shortened, the way mail log lines already
  did.

## 3.6.0 — 2026-11-03

Who is on call, on a screen; and four fixes from a careful read of the
last two days' changes.

- **On call now.** A card on the People tab, under Who gets called: who
  is on call now and until when, who is next, the next turns in the
  rotation's own time, who is in no rotation and so always on call, and
  each person's quiet hours. Owners and operators build, change and stop
  a rotation there and set quiet hours; a viewer sees it all and can
  change nothing. An empty rotation, or a muted person with turns, is
  named as a gap on the card.
- **A long-named unit's alert costs one text, not two.** A name cut
  short to fit was marked with "~", which a text message sends as two
  characters, so a full-length alert was billed as two. The text is now
  measured the way the network counts it.
- **Removing a location removes its rotation.** Its people were released
  to the whole estate but the rotation still held them to their turns,
  and it could not be stopped or its people put in another one.
- **A sweep that fails waits for the next one.** A pass that raised was
  followed at once by another, back to back, flat out (for example with
  the disk full); it now waits its minute like any other.
- **Phone numbers and email addresses stay out of the fault list.** A
  refused text or email names the number or address, and the fault list
  is kept until somebody clears it, past a business deleting everything.

## 3.5.0 — 2026-11-03

Alerts on time under load, through outages and after a restore.

- **Load tested.** 250 to 5,000 sensors reporting every minute, with
  console users refreshing: no failed or lost readings, and a bad
  reading's text goes out in milliseconds. What we measured, and when to
  move off SQLite, is in docs/LOAD-TEST.md.
- **A restart no longer needs four times the memory.** Readings load one
  at a time, and each takes about a quarter of the room it did, so a
  server that ran all day can start again on the same machine.
- **Backups cannot fill the disk.** A snapshot that would leave the live
  database without room is refused and reported, and snapshots can be
  downloaded, so copying them off the machine is one command.
- **A restore sends nothing twice.** Calls, texts, digests and reports
  already sent before a restore are not sent again, and sensors are not
  reported as gone quiet because the snapshot is old.
- **A call that fails is tried again**, 5 and 15 minutes later, while
  Twilio is down. The alert email waits out a mail outage and goes when
  the mail host is back. If the texts fail, the owner's email says so,
  and the owner is told once if no text ever got through.
- **Rotations and quiet hours.** Take turns on call, handing over at a
  local time, through clock changes. Quiet hours hold back texts to a
  person only while somebody else is awake to get them; a breach always
  reaches somebody.
- **Sweeps run on the minute and one at a time,** and the first one after
  a restart is within seconds.

## 3.4.0 — 2026-11-03

- **Reply 1 to say you have it.** Answering an alert text with "1" or
  "ACK" stops the calls. It only works from a number the alert went to,
  and only for that business's open alert.
- **Alerts say what matters first.** Which unit, where, how far out of
  range, what to check and how to answer, in one text message. The same
  alert goes to the business's email, and the phone call says the same
  things and "press 1".
- **A log for the inspector, in one click.** Pick the dates; every unit's
  temperatures, each time one went out of range and who answered it,
  ready to print or save as PDF.
- **Invoices print on one page,** with the period they cover.
- **Withdraw a join code** you gave to the wrong person, before it is used.
- **Safer pages.** No page runs script or styling written into it, so text
  that reaches a page cannot be made to run.
- The public demo has the help page too.

## 3.3.0 — 2026-11-02

Ready for the first customers.

- **Help when you need it.** A help page answers the questions customers
  actually ask: the trial, prices, how alerts escalate, the test alert,
  and how to download or delete your data. It is linked from the console
  and the front page.
- **A sensor gone quiet comes with steps.** When a sensor shows as
  offline, the console and the simple view link to a short list of
  things to check, in order.
- **A price calculator.** The new pricing page works out a monthly price
  from your sectors and how many units you have, using the same rate card
  the invoices use.
- **A first day that guides itself.** A checklist that ticks itself, and
  a test alert that texts your team without opening a problem.
- **Friendly error pages.** A wrong link shows a page that looks like the
  rest of the site, and a fault never shows its inner workings.
- **Found, and shared, properly.** Icons, link previews, a sitemap of the
  public pages, and private pages kept out of search results.
- **The version is shown** in the console and in the health check.
- The company name is MTRX Horizyn everywhere a person reads it, and the
  service runs on its own subdomain of the company's domain.

## 3.2.0 — 2026-11-02

Alerts you can rely on at 3am.

- A sensor that stops reporting gets the team a text, once.
- An alert text that fails is sent again; a roster change that would leave
  nobody to alert is warned about first.
- A freezer's own defrost cycle no longer wakes anybody.
- An outside scheduler can drive the sweep, and is itself watched.
- Slack, Teams and PagerDuty messages retry once and never hold up a call.
- Readings, forecasts and sector documents use the account's own unit.
- Billing fixes: a part payment, a late fee and a paid period now each
  count for exactly what they should.
- Customer guides in plain words: your data, and the tamper-evident vault.
- Tests on every sector, on two Python versions, with type checks on the
  money code.

## 3.1.0 — 2026-11-01

Safer, and yours to take away.

- One sign-in can run several businesses, with a switcher between them.
- Owners can download everything, or delete everything, themselves.
- Security reviews closed gaps between businesses, in sign-in, in billing
  and in webhook addresses. Keys are stored only as fingerprints, and an
  owner can replace a leaked one.
- Spending caps hold under load, and premium-rate numbers are refused.
- Request sizes are capped, and every package is pinned.
- The tests run on every push.

## 3.0.0 — 2026-10-25

The first release under the CyberLogix AI name: sensors and readings,
breach alerts by text and call, the operations console, its own limits
for each industry, billing and invoices, the tamper-evident record, and
the public front page.
