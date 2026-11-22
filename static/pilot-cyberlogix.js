// Pilot on CyberLogix AI: the small per-app config (docs/PILOT.md).
// The shared core is in static/pilot/ (the same files as on the MTRX
// Horizyn site); this file only says who we are, where the person is, which
// suggestions fit each page, and where the knowledge pack and the console
// tools are (static/pilot-cyberlogix-pack.js, fetched only when Pilot is
// first opened). pilot.js loads this file once the page has loaded. The
// figures in data-* on Pilot's script tag come from the code that charges
// and alarms (main.pilot_facts), so Pilot never quotes one the product
// does not use.
(function () {
  "use strict";
  if (!window.Pilot) return;
  const me = document.getElementById("pilot-core");
  const d = (me && me.dataset) || {};
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const onConsole = path === "/console";
  const onToday = path === "/today";

  function signedIn() {
    if (onConsole) return Boolean(window.CyberLogixConsole && window.CyberLogixConsole.signedIn());
    if (onToday) {
      try { return Boolean(window.localStorage.getItem("cyberlogix.session")); } catch (_) { return false; }
    }
    return false;
  }

  const PAGES = {
    "/": "Home page",
    "/pricing": "Pricing",
    "/help": "Help",
    "/help/sensors": "Setting up sensors (step-by-step guides)",
    "/for": "Industries",
    "/demo": "See it live (a demo with pretend data)",
    "/today": "Simple view (Today)",
  };

  // Only the page's name and which tab is open: never a unit, a reading or
  // anybody's name. Those reach the AI only through a tool the person's own
  // request runs.
  // A public page (or the console's sign-in) shows Pilot none of the
  // person's fridges, so the welcome's "Which fridges need me?" and the like
  // get an answer that says where to look instead of a guess.
  const SIGNED_OUT = "Not signed in: Pilot can't see their fridges or alerts here. For those, they sign in to the console or the Simple view (Today).";

  function screen() {
    if (onConsole) {
      if (!signedIn()) return { name: "Console sign-in", facts: SIGNED_OUT };
      const tab = window.CyberLogixConsole.tab();
      return { name: `Console (Advanced view)${tab ? `, ${tab} tab` : ""}`, facts: "The person is signed in." };
    }
    if (onToday) return { name: PAGES["/today"], facts: signedIn() ? "The person is signed in." : SIGNED_OUT };
    if (path === "/demo") return { name: PAGES["/demo"], facts: "Not signed in: this is the demo, with pretend fridges and alerts, not the person's own." };
    if (path.startsWith("/for/")) return { name: `Industry page: ${document.title}`, facts: SIGNED_OUT };
    return { name: PAGES[path] || document.title, facts: SIGNED_OUT };
  }

  // ---------------------------------------------------------------- answers with no AI key (core 1.4.0)
  //
  // Pilot shows these at once, as its own message: no key, and nothing is
  // sent anywhere. On the console and /today they are built from what the
  // page already loaded for the person (the same overview the cards and
  // "Needs you now" are drawn from, handed over by the page's own bridge:
  // CyberLogixConsole.overview(), CyberLogixToday.overview()); elsewhere from
  // the figures on Pilot's script tag. Kept short, and never a person's
  // name, number or email: who answered an alert is "someone on your team".

  const F = {
    fromPrice: d.fromPrice || "", starterUnits: d.starterUnits || "", restaurantPrice: d.restaurantPrice || "",
    pharmacyPrice: d.pharmacyPrice || "", unitsPerLocation: d.unitsPerLocation || "", extraUnit: d.extraUnit || "",
    trialDays: d.trialDays || "", trialUnits: d.trialUnits || "", trialCalls: d.trialCalls || "",
    fridgeLimit: d.fridgeLimit || "", freezerLimit: d.freezerLimit || "", offlineMinutes: d.offlineMinutes || "",
    callAfterMinutes: d.callAfterMinutes || "", textWithinSeconds: d.textWithinSeconds || "",
    alertAfterFridge: d.alertAfterFridge || "", alertAfterFreezer: d.alertAfterFreezer || "",
    alertAfterVaccine: d.alertAfterVaccine || "", alertAtOnceDegrees: d.alertAtOnceDegrees || "",
  };
  const fig = (v, fallback) => v || fallback;

  // A unit's name as typed by the business, kept as plain words in Pilot's
  // markdown (no links or bold of its own).
  const words = (t) => String(t == null ? "" : t).replace(/[*_`[\]#<>|\\]/g, "").replace(/\s+/g, " ").trim();
  const W = () => window.CLXWords || null;

  // What this page loaded for the person, or null (signed out, a public
  // page, or not loaded yet). Read only: nothing is fetched.
  function overview() {
    try {
      if (onConsole && window.CyberLogixConsole && window.CyberLogixConsole.signedIn()
          && typeof window.CyberLogixConsole.overview === "function") return window.CyberLogixConsole.overview() || null;
      if (onToday && window.CyberLogixToday && typeof window.CyberLogixToday.overview === "function") {
        return window.CyberLogixToday.overview() || null;
      }
    } catch (_) { /* the answer says where to look instead */ }
    return null;
  }

  const NOT_LOADED = onConsole || onToday
    ? (signedIn() ? "Your fridges are still loading. Try again in a moment."
      : "Sign in to see your fridges: [sign in](/console). Pilot reads them from the page once you are signed in.")
    : "Pilot can see your fridges once you are signed in: [sign in](/console) or [see the demo](/demo).";

  const unitName = (u) => words((u && u.location_name) || "") || words(u && u.sensor_id) || "A unit";
  function degrees(value, unit, dd) {
    if (value === null || value === undefined || value === "") return "";
    return `${value}°${(unit && unit.temperature_unit) || (dd && dd.temperature_unit) || "F"}`;
  }

  // The unit's state in the same words as its card and /today (CLXWords).
  // A unit closed by hand says only "Closed by hand", never by whom.
  const URGENCY = { alert: 0, paused: 1, watching: 2, offline: 3, waiting_first: 4 };
  function stateOf(u) {
    const w = W();
    if (w && typeof w.condition === "function") {
      const c = w.condition(u);
      return { state: c.state, word: c.word, line: c.held ? c.short : c.line };
    }
    const s = (u && u.condition && u.condition.state) || "ok";
    const word = { alert: "Too warm", paused: "Too warm", watching: "Watching", offline: "No signal", waiting_first: "Waiting for its first reading" }[s] || "Fine";
    return { state: s, word, line: "" };
  }
  const outFor = (from, to) => { const w = W(); return w && from ? w.outFor(from, to) : ""; };
  const clock = (iso) => { const w = W(); return w && iso ? w.clock(iso) : ""; };

  function liveAlerts(dd) {
    const live = (dd.incidents || []).filter((i) => i && i.state !== "resolved" && !i.site_wide);
    const wide = (dd.site_wide_alerts || []).filter((i) => i && i.state !== "resolved");
    return wide.concat(live).sort((a, b) => (a.state === "open" ? 0 : 1) - (b.state === "open" ? 0 : 1)
      || String(a.opened_at).localeCompare(String(b.opened_at)));
  }

  const CONNECT = onConsole || onToday ? "/console#connect" : "/help/sensors";
  const ON_IT = onToday ? "**I'm on it** under Needs you now" : "**I'm on it** at the top of the page";

  // "Which fridges need me?": every unit out of range, alerting, quiet or
  // still waiting for its sensor, worst first, from the page's own list.
  function needsAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    if (!units.length) {
      return `You have no fridges or freezers added yet. [Connect your first sensor](${CONNECT}): say where it is, and the wizard shows the few steps for your brand.`;
    }
    const rows = units.map((u) => ({ u, s: stateOf(u) })).filter((r) => r.s.state in URGENCY)
      .sort((a, b) => URGENCY[a.s.state] - URGENCY[b.s.state]);
    const live = liveAlerts(dd);
    const wide = live.filter((i) => i.site_wide);
    const lines = [];
    if (wide.length) lines.push("**Several fridges are warming at once.** That can mean a power cut, a door left open, or someone at the controls: check the site now.");
    const urgent = rows.filter((r) => r.s.state !== "waiting_first");
    if (!urgent.length && !wide.length) {
      const total = units.length;
      lines.push(total === 1 ? "Nothing needs you right now: your one unit is within its limits."
        : `Nothing needs you right now: all ${total} fridges and freezers are within their limits.`);
    } else if (urgent.length) {
      const list = urgent.map(({ u, s }) => {
        const now = degrees(u.last_temperature_display, u, dd);
        return `- **${unitName(u)}**${now && s.state !== "offline" ? ` (${now})` : ""}: ${words(s.line) || s.word}`;
      });
      lines.push(`${urgent.length === 1 ? "This one needs you:" : `These ${urgent.length} need you, worst first:`}\n${list.join("\n")}`);
    }
    const waiting = rows.filter((r) => r.s.state === "waiting_first");
    if (waiting.length) {
      lines.push(`${waiting.length === 1 ? `**${unitName(waiting[0].u)}** is` : `${waiting.length} units are`} waiting for a first reading. [Connect a sensor](${CONNECT}).`);
    }
    if (live.some((i) => i.state === "open")) lines.push(`Nobody has answered an alert yet: press ${ON_IT} to say you have it.`);
    return lines.join("\n\n");
  }

  // Why it alerted: it stayed out past its wait, or it went far enough past
  // its limit to alert at once (the alert opened before the wait was up).
  function waitWords(i, wait) {
    if (!wait) return "";
    const out = Date.parse(i.out_since || ""), opened = Date.parse(i.opened_at || "");
    if (Number.isFinite(out) && Number.isFinite(opened) && opened - out < wait * 60000 - 30000) {
      return `It went far enough past its limit to alert at once, without its ${wait}-minute wait.`;
    }
    return `It stayed out longer than its ${wait}-minute wait, so we alerted.`;
  }

  // "What does this alert mean?": the alert at the top of "Needs you now",
  // from its own record: when it went out, how warm, its limit, who has it.
  function alertAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const live = liveAlerts(dd);
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    if (!live.length) {
      const closed = (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide)
        .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))[0];
      let last = "";
      if (closed) {
        const u = units.find((x) => x.sensor_id === closed.sensor_id) || { sensor_id: closed.sensor_id };
        const long = outFor(closed.out_since || closed.opened_at, closed.out_until || closed.resolved_at);
        last = ` The last one: **${unitName(u)}** was out of range${long ? ` for ${long}` : ""}${
          closed.back_to_normal_on_its_own ? " and came back on its own" : ""}. It is closed.`;
      }
      return `No alert is open right now.${last}\n\nWhen one opens, it shows at the top of the page with **I'm on it** and **It's fixed**, and everyone on call is texted.`;
    }
    const i = live[0];
    const more = live.length - 1;
    const tail = more ? `\n\n${more === 1 ? "There is 1 more alert" : `There are ${more} more alerts`}: ask **Which fridges need me?**` : "";
    if (i.site_wide) {
      return `**Several fridges are warming at once.** ${words(i.breach_details)}\n\nOne alert went to everyone on call for the site. It can mean a power cut, a door left open, or someone at the controls: check the site now, then answer it with **I'm on it**.${tail}`;
    }
    const u = units.find((x) => x.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
    const cold = Boolean(u.condition && u.condition.too === "cold");
    const now = degrees(u.last_temperature_display != null ? u.last_temperature_display : i.temperature_display, u, dd);
    const limit = cold ? degrees(u.danger_below_display, u, dd) : degrees(u.danger_above_display, u, dd);
    const peak = degrees(i.peak_display, u, dd);
    const since = i.out_since || i.opened_at;
    const long = outFor(since);
    const at = clock(since);
    const wait = Number(u.alert_after_minutes) || 0;
    const parts = [
      `**${unitName(u)}** is too ${cold ? "cold" : "warm"}${now ? `: ${now}` : ""}${limit ? `, ${cold ? "below" : "above"} its ${limit} limit` : ""}.`,
      `It went out of range${at ? ` at ${at}` : ""}${long ? ` and has been out for ${long}` : ""}${peak && peak !== now ? `; worst so far ${peak}` : ""}.`,
      waitWords(i, wait),
    ].filter(Boolean);
    let who;
    if (i.state === "acknowledged") {
      who = i.fix_reported_at ? "Someone on your team said it is fixed. It closes by itself once it is back in range."
        : "Someone on your team is on it. Once it is sorted, press **It's fixed**: it closes when it is back in range.";
    } else {
      // "and we have phoned" only when a call really went out (3.28.0).
      const calls = !i.voice_escalated_at ? "" : i.phoned ? ", and we have phoned"
        : i.calls_off ? ". We would have phoned the list now, but calls are off on this server"
          : ", and the call did not go through";
      who = `Nobody has answered yet${calls}. Press ${ON_IT} to say you have it.`;
    }
    const check = cold ? "Check its thermostat setting and that nothing is blocking the vent."
      : "Check the door is shut and sealing, and that the unit is running.";
    const food = onConsole ? " For the food, the alert's **Is the food still good?** button works it out from the readings." : " For the food, ask **Is the food still OK?**";
    return `${parts.join(" ")}\n\n${who}\n\n${check}${food}${tail}`;
  }

  // "Is the food still OK?": what was measured, and the rule of thumb with
  // its note. Never a ruling: the business's own plan and local code decide.
  const RULE = "The widely taught rule of thumb: under 2 hours above the limit, cool it back down; 2 to 4 hours, use it straight away and don't put it back; over 4 hours, or at room temperature or warmer, it is commonly thrown away.";
  const NOT_A_RULING = "That is guidance, not a ruling: the sensor reads the air, not the middle of the food, and your written food safety plan and local code decide.";
  function foodAnswer() {
    const dd = overview();
    if (!dd) return `${RULE}\n\n${NOT_A_RULING}\n\n${NOT_LOADED}`;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const unit = (i) => units.find((x) => x.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
    const live = liveAlerts(dd).filter((i) => !i.site_wide);
    let measured;
    if (live.length) {
      const i = live[0];
      const u = unit(i);
      const peak = degrees(i.peak_display != null ? i.peak_display : i.temperature_display, u, dd);
      const long = outFor(i.out_since || i.opened_at);
      measured = `**${unitName(u)}** has been out of range${long ? ` for ${long}` : ""}${peak ? `, at worst ${peak}` : ""}, and it is not back yet.`;
    } else {
      const closed = (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide)
        .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))[0];
      if (!closed) {
        measured = units.length ? "None of your fridges or freezers has been out of range, so there is nothing to weigh up."
          : "You have no fridges or freezers added yet.";
        return measured;
      }
      const u = unit(closed);
      const peak = degrees(closed.peak_display != null ? closed.peak_display : closed.temperature_display, u, dd);
      const long = outFor(closed.out_since || closed.opened_at, closed.out_until || closed.resolved_at);
      measured = `Nothing is out of range now. The last time, **${unitName(u)}** was out${long ? ` for ${long}` : ""}${peak ? `, at worst ${peak}` : ""}.`;
    }
    const button = onConsole ? "\n\nThe alert's **Is the food still good?** button gives the exact hours from the readings." : "";
    return `${measured}\n\n${RULE}\n\n${NOT_A_RULING}${button}`;
  }

  // The visitor's "Will it work for my café?": short, from the real figures.
  function cafeAnswer() {
    return [
      "Yes: CyberLogix is made for cafés and small restaurants first.",
      `- A wireless sensor goes in each fridge and freezer (we don't sell them: [which sensors work](/help/sensors)).`,
      `- A fridge alarms over ${fig(F.fridgeLimit, "41")}°F and a freezer over ${fig(F.freezerLimit, "10")}°F, only if it stays out (${fig(F.alertAfterFridge, "20")} minutes for a fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer), so a door opened for a minute wakes nobody.`,
      `- Then everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds, and on plans with calls, phoned if nobody answers in ${fig(F.callAfterMinutes, "10")} minutes.`,
      `- Starter is $${fig(F.fromPrice, "59")} a month for one café with up to ${fig(F.starterUnits, "3")} fridges or freezers. [See prices](/pricing)`,
      `[See it live](/demo) with pretend fridges, or [start a free trial](/signup): ${fig(F.trialDays, "14")} days, no card.`,
    ].join("\n");
  }

  const DOOR = () => `No. A door opened for a minute doesn't alert anybody: a fridge has to **stay** out of range for its wait first (${fig(F.alertAfterFridge, "20")} minutes for a food fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer). You can change the wait per unit with **Alert wait** on its card. A reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past the limit alerts at once.`;
  const POWER = () => `Sensors send their readings over your internet. If the power or the Wi-Fi goes off, readings stop, and after ${fig(F.offlineMinutes, "30")} minutes with nothing from a sensor everyone on call is texted that it has gone quiet, so you hear about the outage too. Alerts go out from our servers, not from your building.\n\nUntil the sensor reports again, check that fridge with a thermometer and write it down. [What to check](/help#sensor-offline)`;
  const CALLS = () => `Yes, on plans with calls. If nobody answers the alert text within ${fig(F.callAfterMinutes, "10")} minutes, CyberLogix phones the people on call in the order you set, until one answers and presses 1. Calls come with the Growth and Enterprise plans; Trial and Starter send texts, and the free trial includes ${fig(F.trialCalls, "5")} alert calls so you can hear one. [Compare plans](/pricing)`;
  const TRIAL = () => `The free trial lasts ${fig(F.trialDays, "14")} days for up to ${fig(F.trialUnits, "5")} sensors, needs no card, and includes ${fig(F.trialCalls, "5")} alert calls. Nothing moves you to a paid plan by itself: you choose one when you are ready. [Start a free trial](/signup)`;
  // Signed in on /today, the console is a link to open, never "Sign in"
  // (3.28.0): that was offered to somebody already signed in.
  const LOG = () => `In the console, open **Log for an inspector** on the **Proof** tab (on a small account, under **More**). Pick the dates, and print it or save it as a PDF. It has every unit's temperatures, each time one went out of range, and who answered it.${
    onConsole ? "" : onToday && signedIn() ? " [Open the full console](/console?view=full)" : " [Sign in](/console)"}`;

  // "It's fixed now" (3.28.0): Pilot does not close an alert for anybody.
  // It points at the real **It's fixed** button on the alert, which checks
  // the fridge is back in range and asks what happened to the food.
  function fixedAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const live = liveAlerts(dd);
    if (!live.length) return "No alert is open right now, so there is nothing to mark fixed.";
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const i = live[0];
    const u = units.find((x) => x.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
    const name = i.site_wide ? "the site-wide alert" : `**${unitName(u)}**`;
    const where = onToday ? "under **Needs you now**" : "at the top of the page, or on the alert under **Alerts**";
    const more = live.length > 1 ? ` There ${live.length === 2 ? "is 1 more alert" : `are ${live.length - 1} more alerts`}, each with its own button.` : "";
    return `Press **It's fixed** on ${name}, ${where}.${more}\n\nIf it is back in range it closes, and you are asked one thing: was the food kept or thrown away. If it is still out of range, it stays open and closes by itself once it is cold, so nobody is woken for a fridge that is fine.`;
  }
  const WARM = () => `When a fridge stays out of range past its wait, everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds with which unit, the reading and what to check first. Reply 1, or press **I'm on it**, to say you have it; on plans with calls, nobody answering means a phone call after ${fig(F.callAfterMinutes, "10")} minutes.\n\nCheck the door is shut and the unit is running, and move the food if it stays warm. Once it's sorted, press **It's fixed**: it closes when it is back in range.`;
  const STEPS = () => [
    "1. Buy one sensor for each fridge or freezer that can send to a web address, or whose maker's cloud has an API.",
    "2. Put it inside, away from the door, and connect it to your Wi-Fi as its maker says.",
    "3. In the console, **Add a fridge or freezer**: say where it is, and copy the sensor key it shows once.",
    "4. **Connect your sensor** shows your brand's few steps with that key filled in, and ticks when the first reading lands.",
    "5. Add the people who get alerts, and send a test alert.",
    "",
    "The guide for each brand is on this page, or [start a free trial](/signup) to get a key.",
  ].join("\n");
  // The pricing page's "Which plan for a café?" (3.28.0), from the figures.
  const PLAN = () => [
    `**One café or shop with up to ${fig(F.starterUnits, "3")} fridges or freezers: Starter, $${fig(F.fromPrice, "59")} a month.** Text alerts to everyone on call, the inspector's log and insurance packs.`,
    `More fridges than that, phone calls when a text goes unanswered, or the forecast: Growth, $${fig(F.restaurantPrice, "149")} a month a location for a restaurant, up to ${fig(F.unitsPerLocation, "10")} units there.`,
    `Either way, start with the free ${fig(F.trialDays, "14")}-day trial, no card. [Work out your price](/pricing#calc-title)`,
  ].join("\n\n");
  const DEMO_WHAT = "A pretend kitchen with four sensors. In about a minute the walk-in starts to warm up, and you see exactly what would happen: the wait, the text, the phone call if nobody answers, and the record once it's fixed. No texts or calls are sent, and nothing is saved.";
  const SIGN_IN = "Use the email and password you signed up with, on [the console](/console). On a phone you can add it to your home screen. No account yet? [Start a free trial](/signup).";

  // Each a label and what it does: words for the AI, or a {link} / {answer}
  // that needs no key (core 1.4.0).
  const link = (to) => ({ link: to });
  const answer = (fn) => ({ answer: fn });
  // Signed in, under the welcome's own five (so none says the same thing).
  const LIVE = [["Log for the inspector", answer(LOG)],
    ["It's fixed now", answer(fixedAnswer)],
    ["Make a proof report", "Make a proof of monitoring report for last month."]];
  // Written for the person most of these pages are for: a café or small
  // restaurant with a few fridges and a freezer, reading on a phone.
  const CHIPS = {
    // Under the visitor's welcome buttons, so none of these repeats one.
    "/": [["Will a door left open wake us?", answer(DOOR)],
      ["Wi-Fi or power goes off?", answer(POWER)],
      ["Do you phone at night?", answer(CALLS)]],
    "/pricing": [["Which plan for a café?", answer(PLAN)],
      ["Do you phone us?", answer(CALLS)],
      ["What's in the trial?", answer(TRIAL)]],
    "/help": [["A fridge is warm: what now?", answer(WARM)],
      ["Will a door left open wake us?", answer(DOOR)],
      ["A log for the inspector", answer(LOG)]],
    "/help/sensors": [["Easiest way to set up", "What's the easiest way to set up a sensor, with no coding?"],
      ["Set up step by step", answer(STEPS)],
      ["Power or Wi-Fi goes off?", answer(POWER)]],
    "/for": [["When do you alert?", "At what temperature, and after how long, does CyberLogix alert, and can I change it?"],
      ["A log for the inspector", answer(LOG)]],
    "/demo": [["What am I seeing?", answer(() => DEMO_WHAT)],
      ["What happens next?", answer(WARM)],
      ["Start a free trial", link("/signup")]],
    "/console-out": [["How do I sign in?", answer(() => SIGN_IN)],
      ["I forgot my password", link("/reset")],
      ["Start a free trial", link("/signup")]],
  };

  const TASKS = {
    live: "Show my open alerts and tell me what to check first",
    "/": "Help me work out what my café needs to start watching its fridges: ",
    "/pricing": "Help me pick a plan for my business: ",
    "/help/sensors": "Help me set up this sensor: ",
  };

  function chips() {
    let list;
    if ((onConsole || onToday) && signedIn()) list = LIVE;
    else if (onConsole) list = CHIPS["/console-out"];
    else if (path.startsWith("/for/")) list = [["How would this work for me?", "How would CyberLogix work for a business like mine?"],
      ["When do you alert?", "At what temperature, and after how long, does CyberLogix alert for this trade, and can I change it?"],
      ["A log for the inspector", answer(LOG)]];
    else list = CHIPS[path] || [];
    return list.map(([label, what]) => (typeof what === "string" ? { label, prompt: what } : { label, ...what }));
  }

  // The welcome's five. In the console and on /today (the person's own
  // fridges) they are what a café asks about its fridges, answered from the
  // page with no key (core 1.4.0); on the public pages, where Pilot can see
  // nobody's fridges, what a visitor deciding asks: will it work for me,
  // what it costs, can I see it. Only the key pill and typed questions need
  // the AI.
  const OWN_PILLS = [
    { icon: "bars", label: "Which fridges need me?", answer: needsAnswer },
    { icon: "help", label: "What does this alert mean?", answer: alertAnswer },
    { icon: "chat", label: "Is the food still OK?", answer: foodAnswer },
    { icon: "bolt", label: "How do I connect a sensor?", link: "/console#connect" },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ];
  const VISITOR_PILLS = [
    { icon: "chat", label: "Will it work for my café?", answer: cafeAnswer },
    { icon: "bars", label: "What does it cost?", link: "/pricing" },
    { icon: "spark", label: "See it live", link: "/demo" },
    { icon: "help", label: "What sensors do I need?", link: "/help/sensors" },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ];
  const ownPage = onConsole || onToday;

  window.Pilot.mount({
    appName: "CyberLogix AI",
    name: "Pilot",
    // Core 1.2.0: the welcome is CyberLogix's own (docs/PILOT.md): the
    // console's five on the person's own pages, the visitor's five on the
    // public ones (OWN_PILLS, VISITOR_PILLS above).
    tagline: "Your fridge-alarm helper",
    welcomePills: ownPage ? OWN_PILLS : VISITOR_PILLS,
    // Under an answer Pilot gives without a key (core 1.4.0).
    answerMore: "Want more? Add your AI key to ask anything.",
    // CyberLogix has no use for making pictures or videos.
    hideKeySections: ["image", "video"],
    // With every corner busy the robot shrinks into its corner, wholly on
    // the screen, instead of hanging off the edge (it replaced this app's
    // own stylesheet fix of 3.23.0).
    keepInViewport: true,
    // The phone's Menu on the public pages is a <details> drop-down, not a
    // dialog: the robot steps back while it is open.
    modals: [".menu[open] > nav"],
    storagePrefix: "cyberlogix-pilot",
    pack: d.pack || "",
    // Exactly the hosts the security policy lets a browser reach for an
    // OpenAI-compatible API (main.PILOT_COMPATIBLE_HOSTS) and the person's
    // own computer.
    compatibleHosts: (d.hosts || "").split(/\s+/).filter(Boolean),
    privacyUrl: "/legal#privacy",
    // Core 1.3.0: the one line the welcome shows (the full note is its
    // tooltip, and stays beside the key settings and in help).
    keyNoteShort: "AI keys are for grown-ups (18+).",
    keyNote: "An AI key is a private code you make on an AI company's website (OpenAI, Anthropic, Google AI Studio), usually under API keys. Your provider bills you for what Pilot uses. AI keys are for grown-ups (the AI companies require 18+).",
    audienceNote: "The people using this run businesses with fridges and freezers to keep cold: restaurants, pharmacies, labs and more. Many read on a phone in a busy kitchen, so keep answers short and practical. If a unit is warm right now, say to check it first. Pilot never makes a food safety decision: when asked if food is OK, give what was measured and the rule of thumb with its note that it is not a ruling, and say their own written food safety plan and local code decide.",
    kidView: () => false,
    // Never sit on top of an alert, a banner, the buttons that answer one,
    // the console's tabs or the front page's first-screen facts: the robot
    // moves to a free corner instead, and looks again as the page scrolls
    // (core 1.1.1). On a phone it sat on "Everything" and on "5 alert
    // calls"; on a desktop on "Advanced"; and on the Getting started card.
    // The header bars are no longer listed (3.27.0): core 1.4.0 never tucks
    // the robot into a phone's top corner while a bottom one will do, and
    // weighs a link or button it would cover, so the logo and Sign in stay
    // clear without it (checked in a browser on every page at 320 to 1440).
    avoid: ["#site-wide-banner", "#stale-banner", "#account-banner", "#delivery-banner",
      "#offline-banner", "#terms-banner", "#role-banner", "#needs-wrap", ".need-acts",
      "#needs-bar", ".needs-bar-acts", ".finder", ".f-tab",
      ".incident-actions", "#verdict", ".toast", ".hero-cta", ".cta-row", "#facts", ".facts",
      ".kicker", "#add-form button[type=submit]", ".fix-dialog",
      // The Getting started checklist's buttons (Add a sensor, Add me,
      // Send a test). Its words are kept clear by the core's own check of
      // the page's text. Not the whole card (3.26.0): on a phone it spans
      // the screen, and a big area to avoid lets the robot tuck over its
      // edge only (core 1.4.0 docs, "Moving an app to core 1.4.0").
      ".checklist-acts", ".btn-go",
      // A fridge's status label ("Too warm", "No signal", "Fine") on its
      // card, in Problems, on /today and in the demo, and the alert's
      // own heading: the robot must never hide whether a fridge is OK.
      ".sensor-top", ".incident-top", ".thing-word", ".demo-word", ".badge",
      // A fridge's temperature and name on /today and the demo (3.28.0): at
      // 375px the robot waved over "62.5°" on the Simple view's last row,
      // and over the demo Freezer card's edge. And /today's own "Needs you
      // now" card, buttons and all.
      ".thing-temp", ".thing-name", ".demo-unit-temp", ".demo-unit-top", "#needs", ".need"],
    screen,
    chips,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: F,
  });
})();
