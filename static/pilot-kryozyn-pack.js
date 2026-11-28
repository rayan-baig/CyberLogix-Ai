// Pilot's knowledge pack for Kryozyn, and its console tools.
// Fetched only when someone first opens Pilot. Written from the real app,
// its help page and docs: if something here stops being true, change it
// here (tests/test_pilot.py checks the main facts are present). Every
// figure (prices, the trial, the alarm limits, the timings) comes from the
// server through the config (static/pilot-kryozyn.js, main.pilot_facts),
// so the pack never states a number the product doesn't use.
//
// The tools work only on the console and the Simple view, for a person who
// is signed in, through the console's own API with their own session: the
// server checks every call exactly as it checks a button press. Reading
// tools run at once; the four that change something run only after the
// person presses Allow. Nothing here changes the plan, deletes anything,
// changes who gets called, or sends a text or a call, and `call` below
// refuses any address but the few the tools use.
(function () {
  "use strict";

  const F = () => (window.Pilot && window.Pilot.config && window.Pilot.config.facts) || {};
  const deg = (v) => (v ? `${v}°F` : "its limit");
  // Dollar figures arrive as bare numbers; the "$" is written here.
  const usd = (v, fallback) => `$${v || fallback}`;

  function knowledge() {
    const f = F();
    return `## ${f.appName}
Fridge and freezer alarms that call you. A product of ${f.companyName}. Wireless temperature sensors in each fridge and freezer send readings to ${f.appShort} over the internet, day and night. When one goes out of range, ${f.appShort} texts everyone on call, then phones them until someone answers, and keeps a sealed record of every reading for an inspector or an insurer. Built for restaurants and cafés, shops, pharmacies, clinics and labs, and 12 industries in all (also data centers, wine and art storage, growers, cold-chain transport, solar storage, IVF clinics, hangars, yacht engine bays and country clubs).

## How alerts and escalation work
- Every reading is checked against the safe range for the trade, or the one set for that unit. A door opened for a minute does NOT alert anybody: a unit has to STAY out of range for its wait (${f.alertAfterFridge || "20"} minutes for a food fridge, ${f.alertAfterFreezer || "30"} for a freezer, ${f.alertAfterVaccine || "10"} for a vaccine or lab fridge; the owner can change it per unit with "Alert wait" on its card). A reading ${f.alertAtOnceDegrees || "15"}°F or more past the limit alerts at once. Then it opens one alert ("problem") for that unit and texts everyone on call within ${f.textWithinSeconds || "60"} seconds, saying which unit, where, the reading, how far past its limit and what to check first. More readings from the same unit update that alert instead of sending more texts.
- If the unit comes back to normal before anybody answers, the alert closes on its own with a note ("Back to normal after 3 minutes, probably the door"), the people texted get a short all-clear, and nobody is phoned.
- Reply 1 to the text, press 1 on the call, or press Acknowledge in the console to say "I'm on it". That stops the escalation.
- If nobody acknowledges within ${f.callAfterMinutes || "10"} minutes, ${f.appShort} phones the people on call in the order set, until one answers and presses 1. Phone calls come with the Growth and Enterprise plans; Trial and Starter send texts (the trial includes ${f.trialCalls || "5"} alert calls in all, about a minute each, to hear what a call sounds like). A failed text is tried again 1, 5 and 15 minutes after the alert opened.
- Copies can also go to Slack, Teams or PagerDuty.
- Mark it fixed (Resolve) once the fault is sorted. Then record what was done and what happened to the food: that is the record an inspector reads.
- If several units at one site warm up at about the same time, one urgent site-wide alert goes to everyone on call for that site and the business's own number, quiet hours or not, with calls straight away on plans with calls. It can mean a power cut, a door left open, or someone tampering with the fridge controls.
- Freezers warm up on a timer to defrost. Once a unit's defrosts are learned, an on-time defrost no warmer than usual is recorded but doesn't wake anyone; a longer or warmer one alerts as normal.
- A sensor that has sent nothing for ${f.offlineMinutes || "30"} minutes shows as offline, and everyone on call gets one text saying it has gone quiet.
- ${f.appShort} reads its own sensors, not the fridge's controls, and never controls a fridge.
- "Send a test alert" (Team tab, Who gets called) texts everyone on the list a message starting TEST that says nothing is wrong.

## Fridge and freezer limits
When a unit is added, the console asks "Fridge or freezer?". For food (restaurants, cafés, shops, country club kitchens) a fridge alarms over ${deg(f.fridgeLimit)} and a freezer over ${deg(f.freezerLimit)}. Vaccine and specimen units in pharmacies and labs get their own ranges (a fridge between 36°F and 46°F, a freezer between -58°F and 5°F). The limit is saved on the unit, and an owner or operator can change a unit's limits on its card. Temperatures show in °F or °C (the °F button at the top of the console).

## What you need (sensors)
${f.appShort} does not sell sensors. Buy a wireless temperature sensor for each fridge or freezer from a sensor maker. Two kinds work: a sensor that can send each reading to a web address (a webhook; brands such as Elitech and Dickson, and most devices set up with a URL), or a sensor whose maker's cloud has an API ${f.appShort} can read every few minutes (Monnit through iMonnit, and SensorPush). Roughly: a basic Wi-Fi sensor costs about $30 to $100, a commercial one with a probe for a walk-in or freezer about $100 to $250; some need a gateway (about $100 to $300, once per site) and some makers charge a few dollars a month for their cloud. Check with the seller. ${f.appShort}'s price doesn't include sensors. Step-by-step guides for each brand: /help/sensors.

## Setting up, step by step
1. Buy one sensor for each fridge or freezer. Check it can send to a web address, or that its maker's cloud has an API.
2. Put the sensor (or its probe) inside, away from the door, and connect it to your Wi-Fi or gateway as its maker says.
3. In the console, use "Add a fridge or freezer" (Setup tab): say where it is and whether it's a fridge or a freezer, then copy the sensor key it shows once.
4. In the sensor's app or cloud, set it to send readings to the address in the guide for your brand, with that key. Or, for a maker's cloud, add it under "Fetch from a sensor cloud".
5. Wait for the first reading; Getting started ticks "First reading received", usually within a few minutes.
6. Add the people who should get alerts, and send a test alert.
A new business sees "Getting started" at the top of the console with these steps ticked off as they're done.

## Plans and prices
- From ${usd(f.fromPrice, "59")} a month. Starter is ${usd(f.fromPrice, "59")} a month for one restaurant or café location with up to ${f.starterUnits || "3"} fridges or freezers: texts to everyone on call, the site-wide alert, the inspector's log and monthly report, and tamper-evident readings and insurance packs (the evidence for an insurance claim).
- Restaurants: ${usd(f.restaurantPrice, "149")} a month per location. Pharmacies: ${usd(f.pharmacyPrice, "199")} a month per pharmacy. Each covers up to ${f.unitsPerLocation || "10"} fridges and freezers at that location; each one past that is ${usd(f.extraUnit, "15")} a month. Other trades are priced by quote ("Talk to us").
- Plans: Trial, Starter (texts only), Growth (up to 50 sensors, with phone calls and warnings before a unit gets too warm) and Enterprise (up to 1,000, with calls). Paying yearly gives 2 months free. Chains of locations get a discount. No setup fee, and never a share of savings.
- The free trial lasts ${f.trialDays || "14"} days for up to ${f.trialUnits || "5"} sensors, needs no card, and includes ${f.trialCalls || "5"} alert calls. When it ends, sensors are still watched for 14 more days; the owner then picks a paid plan under Money, What you pay. Moving to a paid plan shows the terms first and changes nothing until "Yes, move". Pilot can't change plans.
- The full rate card and a calculator are on /pricing.

## Proof reports for an insurer
An owner can make a proof of monitoring report for one location (or all) and one month: Proof tab, Proof of monitoring, choose the location and month, Make the report, then Open and print or save as PDF. It lists the sensors watched and how much of the time each was online, every temperature alert with how fast it was acknowledged (by role, never a name), the longest gap in data, and who gets alerted (by role). It carries a short code and a link (/verify) so the insurer can check it's genuine; that public page shows only the business name and headline numbers. Nobody sees a report until the owner hands out its link, and the link can be switched off. Insurance packs (Proof tab, and the Insurance pack button on an alert) hold the evidence for an insurance claim after a loss, and "Log for an inspector" prints the temperature log.

## Simple view and the Advanced console
- The Simple view (/today) answers one question in plain words: is there anything I have to do? It shows a headline ("Nothing is spoiling", or what needs you now), what needs you with what to do, and each fridge and freezer as Fine, Too warm, Heading the wrong way or No signal. "All the detail" opens the full console.
- The console (/console) has everything, in tabs: Everything, Fridges, Alerts, Licences, People, Money, Proof, Setup and Advanced. A search box finds any card. A small account (a trial or Starter with a few units) opens on the Simple view once it's set up; the tools for bigger businesses wait under the Advanced tab.
- No sensor yet? An owner can press Simulate on the Fridges tab (or "No hardware yet? Try a simulated one" while the list is empty): pretend units that warm up, never recorded and never texted. /demo shows the whole story in a pretend kitchen, with no account.
- The console can be installed on a phone's home screen. Each unit's card opens a drawer with its history and an "Is the food still good?" button.

## If the power or the internet goes off
Sensors send readings over the site's internet. If the power or the internet goes off, readings stop arriving, and after ${f.offlineMinutes || "30"} minutes with nothing from a sensor everyone on call is texted that it has gone quiet, so you hear about the outage too. Alerts are sent from ${f.appShort}'s servers, not from your building, so they still go out. A battery sensor keeps measuring through a power cut, but the router or gateway may not unless it's on a backup battery; some sensors store readings and send them when the connection is back. Until a sensor reports again, check that unit by hand with a thermometer and write it down. The installed app never shows old temperatures as if they were current.

## Is the food still OK?
${f.appShort} never says food is safe: the sensor reads the air, not the middle of the product. "Is the food still good?" reports how long the unit was above its limit and how warm it got, and quotes the widely taught rule of thumb (under 2 hours: cool it back down; 2 to 4 hours: use it straight away, don't put it back; over 4 hours, or room temperature or warmer: commonly discarded). That's guidance, not a ruling: the business's written food safety plan and local code decide.

## Your data
Owners can download everything or delete everything (Setup tab). Readings: every reading for 30 days, then hourly lows, highs and averages for 13 months, plus the readings around every alert in full. Nothing is sold or used to train anything. The privacy statement is at /legal#privacy.

## Your AI key for alert wording
Each alert has one line saying what to check first, worded by AI when it can and by a plain template when it can't. An owner can save the business's own Google AI Studio (Gemini API) key in the console (Setup, "Your AI key for alert wording"); that line is then written on their key, for their alerts only. It's stored encrypted, only its last four characters are shown, it can be removed, and if a call fails the alert goes out on time with the plain wording. Alerts never wait for AI.

## Pilot (this assistant)
Pilot runs on the person's own AI key from Anthropic, OpenAI, Google Gemini or any OpenAI-compatible API (including a model on their own computer). The key stays in their browser and goes only to that provider; ${f.appShort} never sees it, the chat or the answers, and the provider bills the key. On the console and the Simple view, for a signed-in person, Pilot can show which units are warm or out of range, show open alerts, explain an alert, say what the rule of thumb says about the food, and explain the page; and, after they press Allow, acknowledge an alert, mark one fixed, add a fridge or freezer (it asks which) or make a proof report. It can't change the plan, delete anything, change who gets called, or send texts or calls. Settings are behind the gear button.`;
  }

  // Genuine strengths, from the real features. No rival is named and there
  // are no made-up numbers.
  const STRENGTHS = [
    "It doesn't stop at a text: if nobody acknowledges, it phones the people on call in order until one answers and presses 1 (on plans with calls).",
    "It warns about the silence too: a sensor that goes quiet for 30 minutes texts everyone on call, so a power cut or a dead router isn't mistaken for a cold fridge.",
    `Alerts go out from ${F().appShort}'s servers, not from the building, and never wait for AI: if an AI line is slow, the alert goes with plain wording on time.`,
    "It reads its own sensors, apart from the fridge's controls, and never controls a fridge, so it still sees the warming if those controls fail or are tampered with.",
    "Several units warming at once raise one urgent site-wide alert to everyone, quiet hours or not.",
    "It learns each freezer's defrost cycle, so routine defrosts don't wake anyone, while a longer or warmer one still alerts.",
    "Fridge or freezer is asked when a unit is added, with food limits of 41°F and 10°F, saved on the unit so a later change to a default can never move it.",
    "Use the sensors you choose: any that can send to a web address, or whose maker's cloud we can read. You're not tied to one brand of hardware.",
    "Every reading is sealed in a tamper-evident record, with an inspector's log, insurance packs and monthly proof of monitoring reports an insurer can check by code.",
    "Plain prices from $59 a month, no setup fee, no share of savings, and a free trial with no card.",
    "Honest about food safety: it reports what was measured and the rule of thumb, and leaves the decision to the business's own plan.",
    "A Simple view that answers \"is there anything I have to do?\" in plain words, and the full console when you want the detail.",
    "Your data is yours: download everything or delete everything at any time.",
  ];

  // ---------------------------------------------------------------- the API

  const SESSION = "cyberlogix.session";
  const where = () => window.location.pathname.replace(/\/+$/, "") || "/";
  const bridge = () => window.KryozynConsole || null;
  const onConsole = () => where() === "/console";
  const onLive = () => where() === "/console" || where() === "/today";

  function token() {
    try { return window.localStorage.getItem(SESSION); } catch (_) { return null; }
  }

  function signedIn() {
    if (!onLive()) return false;
    if (onConsole()) return Boolean(bridge() && bridge().signedIn());
    return Boolean(token());
  }

  // The only addresses Pilot's tools may call, and how. Anything else
  // (the plan, deleting, the on-call list, test texts, calls) is refused
  // here, before the request is made.
  const ALLOWED = [
    ["GET", /^\/api\/console\/overview$/],
    ["GET", /^\/api\/disposition\/[^/?#]+\?hours=24$/],
    ["POST", /^\/api\/voice\/acknowledge\/[^/?#]+$/],
    ["POST", /^\/api\/voice\/resolve\/[^/?#]+$/],
    ["GET", /^\/api\/proof-reports$/],
    ["POST", /^\/api\/proof-reports$/],
  ];

  function allowed(method, path) {
    return ALLOWED.some(([m, re]) => m === method && re.test(path));
  }

  function readable(body, status) {
    const detail = body && body.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail.message === "string") return detail.message;
    if (status === 401) return "You're signed out. Sign in to the console again.";
    if (status === 403) return "Your role in this business can't do that.";
    return `${F().appShort} answered ${status}.`;
  }

  async function call(path, options) {
    const o = options || {};
    const method = (o.method || "GET").toUpperCase();
    if (!allowed(method, path)) throw new Error("Pilot isn't allowed to do that.");
    if (!signedIn()) throw new Error("You need to be signed in to the console or the Simple view.");
    const init = { method };
    if (o.body !== undefined) init.body = JSON.stringify(o.body);
    if (bridge()) return bridge().api(path, init);
    const res = await fetch(path, { ...init, credentials: "same-origin",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` } });
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (_) { body = null; }
    if (!res.ok) throw new Error(readable(body, res.status));
    return body;
  }

  // ---------------------------------------------------------------- shaping what the AI sees
  // Only what the question needs: a unit's name, its reading and limits,
  // whether it reports. Never a person's name, a phone number, an email,
  // a key or a business id.

  const known = new Map(); // alert id -> the unit's name, for the Allow card

  function unitName(s) {
    return (s.location_name || "").trim() || s.sensor_id;
  }

  function unit(s, scale) {
    const out = {
      unit: unitName(s), sensor_id: s.sensor_id, kind: s.asset_noun,
      temperature: s.last_temperature_display == null ? "no reading yet" : `${s.last_temperature_display}°${scale}`,
      alarm_above: s.danger_above_display == null ? null : `${s.danger_above_display}°${scale}`,
      alarm_below: s.danger_below_display == null ? null : `${s.danger_below_display}°${scale}`,
      reporting: Boolean(s.online),
      last_heard: s.last_seen || null,
    };
    if (s.site_name) out.location = s.site_name;
    if (s.hours_until_breach != null) out.out_of_range_in_hours = s.hours_until_breach;
    if (s.battery_low) out.battery_low = true;
    return out;
  }

  function alertOf(i, names, scale) {
    const name = names[i.sensor_id] || i.sensor_id;
    known.set(i.incident_id, name);
    const out = {
      alert_id: i.incident_id,
      unit: i.site_wide ? "several units (site-wide alert)" : name,
      state: i.state,
      reading: i.temperature_display == null ? null : `${i.temperature_display}°${scale}`,
      what: i.breach_details || i.catastrophe_type || "",
      opened: i.opened_at,
      minutes_open: i.minutes_open,
      people_texted: i.notified_count || 0,
      // True only when a call really went out (3.28.0), not on a server
      // with calls switched off.
      phone_call_made: Boolean(i.phoned),
      calls_off_on_this_server: Boolean(i.calls_off),
      acknowledged: Boolean(i.acknowledged_at),
      fixed: Boolean(i.resolved_at),
    };
    if (i.record_missing && i.record_missing.length && i.state !== "open") out.record_still_needs = i.record_missing;
    return out;
  }

  async function overview() {
    const o = await call("/api/console/overview");
    const scale = o.temperature_unit || "F";
    const names = {};
    for (const s of o.sensors || []) names[s.sensor_id] = unitName(s);
    const incidents = (o.incidents || []).concat(o.site_wide_alerts || []);
    return { o, scale, names, incidents };
  }

  function findUnit(sensors, wanted) {
    const w = String(wanted || "").trim().toLowerCase();
    if (!w) return null;
    return sensors.find((s) => s.sensor_id.toLowerCase() === w)
      || sensors.find((s) => unitName(s).toLowerCase() === w)
      || sensors.find((s) => unitName(s).toLowerCase().includes(w)) || null;
  }

  const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
  const thisMonth = () => new Date().toISOString().slice(0, 7);

  // ---------------------------------------------------------------- the tools

  const TOOLS = [
    {
      name: "show_units_out_of_range", kind: "read", available: signedIn,
      description: "Which fridges, freezers and other units are too warm, too cold or out of range now, which are heading out of range soon, and which have stopped reporting. Use for \"what's warm now?\".",
      parameters: { type: "object", properties: {} },
      run: async () => {
        const { o, scale } = await overview();
        const sensors = o.sensors || [];
        const out = sensors.filter((s) => s.online && s.breaching).map((s) => unit(s, scale));
        const soon = sensors.filter((s) => s.online && !s.breaching && s.hours_until_breach != null && s.hours_until_breach <= 6)
          .map((s) => unit(s, scale));
        const quiet = sensors.filter((s) => !s.online).map((s) => unit(s, scale));
        return { scale: `°${scale}`, units_total: sensors.length, out_of_range_now: out, heading_out_of_range: soon,
          not_reporting: quiet, fine: sensors.length - out.length - soon.length - quiet.length,
          alerts_paused: Boolean(o.account && o.account.paused) };
      },
    },
    {
      name: "show_open_alerts", kind: "read", available: signedIn,
      description: "The alerts that are open or acknowledged but not yet marked fixed, newest first, with their alert_id.",
      parameters: { type: "object", properties: {} },
      run: async () => {
        const { scale, names, incidents } = await overview();
        const open = incidents.filter((i) => i.state !== "resolved").map((i) => alertOf(i, names, scale));
        return open.length ? { open_alerts: open.slice(0, 20) } : "There are no open alerts.";
      },
    },
    {
      name: "explain_alert", kind: "read", available: signedIn,
      description: "What one alert means: what happened, what was sent, whether a call was made, who has it (as yes or no), and what the record still needs. Leave alert_id out for the newest open alert.",
      parameters: { type: "object", properties: { alert_id: { type: "string", maxLength: 40, description: "The alert_id from show_open_alerts." } } },
      run: async (args) => {
        const { scale, names, incidents } = await overview();
        const i = args.alert_id ? incidents.find((x) => x.incident_id === args.alert_id)
          : incidents.find((x) => x.state !== "resolved");
        if (!i) return args.alert_id ? { error: "There's no alert with that id in this business." } : "There are no open alerts.";
        return {
          ...alertOf(i, names, scale),
          alert_text_sent: i.dispatched_sms_text || null,
          site_wide_units: i.site_wide ? (i.group_units || []).length : undefined,
          meaning: {
            open: "Nobody has said they're on it yet. Texts went out; calls follow if nobody acknowledges.",
            acknowledged: "Someone has it, so nobody else is being chased. Mark it fixed once the fault is sorted.",
            resolved: "Fixed and closed. Record what was done and what happened to the food, for the inspector.",
          }[i.state] || "",
        };
      },
    },
    {
      name: "is_the_food_ok", kind: "read", available: signedIn,
      description: "For one unit: how long it was above its limit in the last 24 hours and how warm it got, and what the widely taught rule of thumb says. It is never a food safety ruling: always repeat its not_a_ruling note. Leave unit out for the unit of the newest open alert.",
      parameters: { type: "object", properties: { unit: { type: "string", maxLength: 200, description: "The unit's name or sensor id." } } },
      run: async (args) => {
        const { o, incidents } = await overview();
        const sensors = o.sensors || [];
        let s = findUnit(sensors, args.unit);
        if (!s && !args.unit) {
          const latest = incidents.find((i) => i.state !== "resolved" && !i.site_wide);
          s = latest ? sensors.find((x) => x.sensor_id === latest.sensor_id) : sensors.find((x) => x.breaching);
        }
        if (!s) return { error: args.unit ? "No unit by that name in this business." : "Say which unit: none is warm or alerting now." };
        const out = await call(`/api/disposition/${encodeURIComponent(s.sensor_id)}?hours=24`);
        return {
          unit: unitName(s), window_hours: out.window_hours, headline: out.headline, detail: out.detail,
          hours_above_limit: out.measured && out.measured.hours_above, peak_f: out.measured && out.measured.peak_f,
          still_warm: Boolean(out.measured && out.measured.still_warm),
          not_a_ruling: out.not_a_ruling,
        };
      },
    },
    {
      name: "explain_this_page", kind: "read", available: () => true,
      description: "The page the person is on: its name and the words shown on it now (never what is typed into boxes, and with emails and phone numbers blanked out). Use to explain the page or what something on it means.",
      parameters: { type: "object", properties: {} },
      run: () => {
        const s = (window.Pilot && window.Pilot.config && typeof window.Pilot.config.screen === "function")
          ? window.Pilot.config.screen() : { name: document.title };
        const words = window.PilotPanel && typeof window.PilotPanel.screenText === "function" ? window.PilotPanel.screenText() : "";
        return { page: s.name, on_screen: words || "(nothing readable)" };
      },
    },
    {
      name: "acknowledge_alert", kind: "change", available: signedIn,
      description: "Acknowledge an alert: say someone is on it, which stops the calls and texts to the next people. Needs the alert_id from show_open_alerts.",
      parameters: { type: "object", properties: { alert_id: { type: "string", maxLength: 40, description: "The alert_id." } }, required: ["alert_id"] },
      describe: (args) => `acknowledge the alert on “${known.get(args.alert_id) || args.alert_id}” (you're on it; nobody else is called about it)`,
      run: async (args) => {
        const r = await call(`/api/voice/acknowledge/${encodeURIComponent(args.alert_id)}`, { method: "POST", body: {} });
        if (bridge()) bridge().refresh();
        return { status: r.status, message: r.status === "ALREADY_ACKNOWLEDGED" ? "Someone else acknowledged it first. Nothing more was sent." : r.message };
      },
    },
    {
      name: "mark_alert_fixed", kind: "change", available: signedIn,
      description: `Mark an alert fixed once the fault is sorted. Needs the alert_id. If the unit is still out of range it is not closed: ${F().appShort} says so and closes it by itself once the unit is back in range; closing it anyway needs a one-line note, which the person gives on the alert's own It's fixed button.`,
      parameters: { type: "object", properties: { alert_id: { type: "string", maxLength: 40, description: "The alert_id." } }, required: ["alert_id"] },
      describe: (args) => `mark the alert on “${known.get(args.alert_id) || args.alert_id}” as fixed (it closes)`,
      run: async (args) => {
        let r;
        try {
          r = await call(`/api/voice/resolve/${encodeURIComponent(args.alert_id)}`, { method: "POST", body: {} });
        } catch (err) {
          // Still out of range: not closed, and it closes by itself once cold.
          if (/^It's still /.test(String(err && err.message))) {
            if (bridge()) bridge().refresh();
            return { status: "STILL_OUT_OF_RANGE", message: `${err.message} To close it anyway, press It's fixed on the alert and add a one-line note.` };
          }
          throw err;
        }
        if (bridge()) bridge().refresh();
        return { status: r.status, message: r.message || "Marked fixed. Record what was done and what happened to the food on the alert's card." };
      },
    },
    {
      name: "add_sensor", kind: "change", available: () => signedIn() && onConsole(),
      description: "Add a fridge or freezer to watch, named by where it is. Always ask the person whether it is a fridge or a freezer if they haven't said; never guess. Its sensor key is shown once in the console's Add a fridge or freezer card, never to you.",
      parameters: {
        type: "object",
        properties: {
          location: { type: "string", maxLength: 200, description: "Where it is, e.g. \"Kitchen walk-in\"." },
          kind: { type: "string", enum: ["fridge", "freezer"], description: "Fridge or freezer, as the person said." },
        },
        required: ["location", "kind"],
      },
      describe: (args) => {
        let alarm = "";
        try {
          const k = (bridge().sector().kinds || []).find((x) => x.kind === args.kind);
          if (k) alarm = `; it alarms if it stays ${k.alarm}`;
        } catch (_) { alarm = ""; }
        return `add a ${args.kind} called “${args.location}”${alarm}`;
      },
      run: async (args) => {
        if (!onConsole() || !bridge() || !bridge().signedIn()) return { error: "Adding a unit works on the console, signed in." };
        const r = await bridge().addSensor(args.location, args.kind);
        return { ...r, next: "The sensor key is in the console's Add a fridge or freezer card, shown once: copy it into the sensor now. Then wait for the first reading." };
      },
    },
    {
      name: "make_proof_report", kind: "change", available: signedIn,
      description: "Make a proof of monitoring report for an insurer, for one month (YYYY-MM, the current month by default) and one location (by name) or every location. Owners only.",
      parameters: {
        type: "object",
        properties: {
          month: { type: "string", maxLength: 7, description: "The month, YYYY-MM." },
          location: { type: "string", maxLength: 80, description: "One location's name. Leave out for every location." },
        },
      },
      describe: (args) => `make a proof of monitoring report for ${args.month || thisMonth()}${args.month && args.month !== thisMonth() ? "" : " (the month so far)"}, ${args.location ? `for “${args.location}”` : "for every location"}`,
      run: async (args) => {
        const month = args.month || thisMonth();
        if (!MONTH.test(month)) return { error: "The month must look like 2026-10." };
        const body = { month };
        if (args.location) {
          const list = await call("/api/proof-reports");
          const w = args.location.trim().toLowerCase();
          const site = (list.locations || []).find((l) => l.name.toLowerCase() === w)
            || (list.locations || []).find((l) => l.name.toLowerCase().includes(w));
          if (!site) return { error: `No location called “${args.location}”. Locations: ${(list.locations || []).map((l) => l.name).join(", ") || "none yet"}.` };
          body.site_id = site.site_id;
        }
        const r = await call("/api/proof-reports", { method: "POST", body });
        if (bridge()) bridge().refresh();
        return { made: true, month: r.month_label, location: r.location, open_it: `/proof-report/${r.report_id}`,
          verify_code: r.verify_code, note: "Open it to print or save as PDF. Nobody sees it until you hand out its link." };
      },
    },
  ];

  window.PilotPack = {
    get knowledge() { return knowledge(); },
    get strengths() { return STRENGTHS; },
    tools: () => TOOLS,
    // Exposed for the tests: the gate every tool call goes through.
    allowed,
  };
  if (typeof module === "object" && module.exports) module.exports = window.PilotPack;
})();
