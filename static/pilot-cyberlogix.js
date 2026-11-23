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

  // The words in a question that count, for this file's own matching
  // (3.32.0): lower case, a plural "s" off, and the little words left out
  // before and after (core 1.4.2 checks them only after, so it counts
  // "does" and "this").
  const SMALL = new Set(("a an and any are am at be can could did do does for from get give got has have help how i if in "
    + "into is it its just know let like me my need of on or our please show should so some tell than that the their them "
    + "then there these they this those to up us want was we what whats when where which who why will with would you your "
    + "doe thi").split(" "));
  function stemWord(w) {
    let x = String(w || "").toLowerCase().replace(/['’]s$/, "").replace(/[^a-z0-9]/g, "");
    if (x.length > 4 && x.endsWith("ies")) x = `${x.slice(0, -3)}y`;
    else if (x.length > 3 && x.endsWith("s") && !x.endsWith("ss")) x = x.slice(0, -1);
    return x;
  }
  const wordsIn = (text) => [...new Set(String(text || "").split(/[^A-Za-z0-9'’]+/)
    .filter((w) => !SMALL.has(w.toLowerCase())).map(stemWord).filter((w) => w.length > 1 && !SMALL.has(w)))];

  // Fridge or freezer, as the add form saved it (or its name says).
  function kindOf(u) {
    if (u && (u.unit_kind === "fridge" || u.unit_kind === "freezer")) return u.unit_kind;
    const name = `${(u && u.location_name) || ""} ${(u && u.sensor_id) || ""}`.toLowerCase();
    if (/freez/.test(name)) return "freezer";
    const limit = Number(u && u.danger_above_display);
    if (Number.isFinite(limit) && (u.temperature_unit || "F") === "F" && limit <= 20) return "freezer";
    return "fridge";
  }
  const GENERIC = new Set(["fridge", "freezer", "unit", "cooler", "refrigerator", "the", "my", "in"]);

  // One unit in a line: "**Walk-in fridge** 37.9°F, fine" (3.32.0), or
  // one that needs you as before: "**Walk-in fridge** (47°F): Too warm
  // for 12 minutes".
  function unitLine(u, dd) {
    const s = stateOf(u);
    const now = s.state === "offline" || s.state === "waiting_first" ? "" : degrees(u.last_temperature_display, u, dd);
    if (s.state in URGENCY) return `**${unitName(u)}**${now ? ` (${now})` : ""}: ${words(s.line) || s.word}`;
    return `**${unitName(u)}**${now ? ` ${now}` : ""}, fine`;
  }

  // "Which fridges need me?": every unit out of range, alerting, quiet or
  // still waiting for its sensor, worst first, from the page's own list,
  // each with its temperature, and the rest with theirs (3.32.0). Typed
  // ("Is my walk-in fridge OK?", "How is the freezer?"), the unit asked
  // about comes first, and a kind the business hasn't added says so:
  // "You haven't added a freezer".
  function needsAnswer(ctx) {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const typed = ctx && typeof ctx.label === "string" && ctx.label !== "Which fridges need me?" ? wordsIn(ctx.label) : [];
    const wantKind = typed.includes("freezer") ? "freezer" : (typed.includes("fridge") || typed.includes("refrigerator") || typed.includes("cooler")) ? "fridge" : "";
    if (!units.length) {
      return `You have no fridges or freezers added yet. [Connect your first sensor](${CONNECT}): say where it is, and the wizard shows the few steps for your brand.`;
    }
    const lines = [];
    if (wantKind && !units.some((u) => kindOf(u) === wantKind)) {
      lines.push(`You haven't added a ${wantKind}. [Connect one](${CONNECT}): say where it is, and the wizard shows the few steps for your brand.`);
    }
    // The unit named in the question ("walk-in", "prep", "bar"), first.
    const named = typed.length ? units.filter((u) => wordsIn(u.location_name || "").some((w) => !GENERIC.has(w) && typed.includes(w))) : [];
    const asked = named.length ? named : (wantKind ? units.filter((u) => kindOf(u) === wantKind) : []);
    const first = asked.length && asked.length < units.length ? asked : [];
    if (first.length) lines.push(first.map((u) => `${unitLine(u, dd)}.`).join("\n"));
    const rows = units.map((u) => ({ u, s: stateOf(u) })).filter((r) => r.s.state in URGENCY)
      .sort((a, b) => URGENCY[a.s.state] - URGENCY[b.s.state]);
    const live = liveAlerts(dd);
    const wide = live.filter((i) => i.site_wide);
    if (wide.length) lines.push("**Several fridges are warming at once.** That can mean a power cut, a door left open, or someone at the controls: check the site now.");
    const urgent = rows.filter((r) => r.s.state !== "waiting_first");
    // The food on a closed alert nobody has answered (3.30.0): never
    // "Nothing needs you right now" beside it.
    const food = (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide
      && i.food_question && i.food_question.ask_on_today);
    // The unit asked about is said once, above.
    const fine = units.filter((u) => !(stateOf(u).state in URGENCY) && !first.includes(u));
    if (!urgent.length && !wide.length && !food.length) {
      const total = units.length;
      if (first.length) {
        lines.push(`Nothing needs you right now.${fine.length ? ` The others:\n${fine.map((u) => `- ${unitLine(u, dd)}`).join("\n")}` : ""}`);
      } else {
        lines.push(total === 1 ? `Nothing needs you right now: ${unitLine(units[0], dd)}.`
          : `Nothing needs you right now. All ${total} are within their limits:\n${fine.map((u) => `- ${unitLine(u, dd)}`).join("\n")}`);
      }
    } else if (urgent.length) {
      const list = urgent.map(({ u }) => `- ${unitLine(u, dd)}`);
      lines.push(`${urgent.length === 1 ? "This one needs you:" : `These ${urgent.length} need you, worst first:`}\n${list.join("\n")}`);
      if (fine.length) lines.push(`The rest are fine: ${fine.map((u) => `${unitName(u)} ${degrees(u.last_temperature_display, u, dd)}`.trim()).join(", ")}.`);
    } else if (fine.length) {
      lines.push(`${first.length ? "The others are" : "Every unit is"} within ${first.length ? "their" : "its"} limits: ${fine.map((u) => `${unitName(u)} ${degrees(u.last_temperature_display, u, dd)}`.trim()).join(", ")}.`);
    }
    food.forEach((i) => {
      const u = units.find((x) => x.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
      const back = W() ? W().foodBack(i.food_question) : i.food_question.words;
      lines.push(`Check the ${i.food_question.word} in **${unitName(u)}**: ${words(back)}. Nobody has said yet whether it was kept. Press **${i.food_question.word === "stock" ? "Stock kept" : "Food kept"}** or **Thrown away**${onToday ? " at the top of the page" : " on the alert"}, for the record.`);
    });
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
    if (u.condition && u.condition.back_in_range) {
      return `**${unitName(u)}** is back in range${now ? ` at ${now}` : ""}, but its alert is still open. Press **It's fixed** to close it, then say what happened to the food.${tail}`;
    }
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
    (onConsole || onToday) && signedIn() ? ` [Open the log for the last 7 days](${LOG_HERE})` : " [Sign in](/console)"}`;

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

  // "Sum up my fridges" (core 1.4.2's screenChip, on the console and
  // /today): the page in a few lines, from what it already loaded, in
  // place of the core's "Summarise this", which would send the screen to
  // the AI. How many units and how they are, the alerts, and the food
  // nobody has answered for. Never a name, number or email.
  function summaryAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    if (!units.length) return `No fridges or freezers added yet. [Connect your first sensor](${CONNECT}).`;
    const count = { fine: 0, need: 0, waiting: 0 };
    units.forEach((u) => {
      const s = stateOf(u).state;
      if (s === "waiting_first") count.waiting += 1;
      else if (s in URGENCY) count.need += 1;
      else count.fine += 1;
    });
    const total = units.length;
    const parts = [`${count.fine} fine`];
    if (count.need) parts.push(`${count.need} ${count.need === 1 ? "needs" : "need"} you`);
    if (count.waiting) parts.push(`${count.waiting} waiting for a first reading`);
    const lines = [`**${total === 1 ? "1 fridge or freezer" : `${total} fridges and freezers`}:** ${parts.join(", ")}.`];
    const live = liveAlerts(dd);
    const open = live.filter((i) => i.state === "open").length;
    lines.push(!live.length ? "No alert is open."
      : `${live.length === 1 ? "1 alert is open" : `${live.length} alerts are open`}${open ? `, ${open === live.length ? (open === 1 ? "and nobody has answered it" : "and nobody has answered them") : `${open} not answered yet`}` : ", and someone is on it"}.`);
    const food = (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide
      && i.food_question && i.food_question.ask_on_today).length;
    if (food) lines.push(`${food === 1 ? "1 closed alert is" : `${food} closed alerts are`} waiting for an answer about the food.`);
    lines.push(count.need || live.length || food ? "Ask **Which fridges need me?** for each one." : "Nothing needs you right now.");
    return lines.join("\n\n");
  }

  // ---------------------------------------------------------------- built-in answers on every page (3.32.0)
  //
  // The price from the real plan figures, what sensors to buy, and which
  // plan suits a café, with no key, whatever page Pilot is on.
  const PRICE = () => [
    `**Starter $${fig(F.fromPrice, "59")} a month, up to ${fig(F.starterUnits, "3")} fridges or freezers**: one café or shop, text alerts to everyone on call, the inspector's log and insurance packs.`,
    `**Growth $${fig(F.restaurantPrice, "149")} a month a location** for a restaurant, up to ${fig(F.unitsPerLocation, "10")} units there, with a phone call when a text goes unanswered.`,
    `Sensors are bought separately. Start with the free ${fig(F.trialDays, "14")}-day trial, no card. [See all prices](/pricing)`,
  ].join("\n\n");
  // The knowledge pack's own words on sensors (pilot-cyberlogix-pack.js),
  // short.
  const SENSORS = () => [
    "We don't sell sensors. You buy a wireless temperature sensor for each fridge or freezer from a sensor maker, and point it at CyberLogix.",
    "- One that sends each reading to a web address (Elitech, Dickson and most devices set up with a URL), or",
    "- One whose maker's cloud we read every few minutes (Monnit through iMonnit, SensorPush).",
    "A basic Wi-Fi sensor is about $30 to $100; a commercial one with a probe for a walk-in about $100 to $250. CyberLogix's price doesn't include them.",
    "[Which sensors work, brand by brand](/help/sensors)",
  ].join("\n");
  // "How do I connect a sensor?" (3.32.0: an answer with the link, so a
  // typed sensor question on the console gets the right answer too).
  const CONNECT_HOW = () => (onConsole || onToday
    ? `**Connect your sensor** walks you through it: say where it is, copy the key it shows once, and it shows your brand's few steps with the key filled in. It ticks when the first reading lands.\n\n[Open Connect your sensor](${CONNECT})`
    : `Start a free trial, then **Add a fridge or freezer** in the console: it gives you a sensor key and shows your brand's few steps with it filled in. [Step-by-step guides](/help/sensors) · [Start a free trial](/signup)`);
  const DEMO_LINK = () => `${DEMO_WHAT}\n\n[See it live](/demo)`;
  const PASSWORD = () => "On [the sign-in page](/console) press **Forgot your password?**, or [reset it here](/reset): we email you a link.";
  const DID = () => (onConsole || onToday
    ? "**What CyberLogix did for you** shows this week or this month on one screen: readings checked, alerts and how fast someone answered, what happened to the food, and the printable log for the same days. [Open it](/today#did)"
    : "Once your fridges are watched, **What CyberLogix did for you** shows each week on one screen: readings checked, alerts and how fast someone answered, and the printable log. [See it live](/demo)");

  // Words a typed question may use for each answer or link that needs no
  // key (core 1.4.2, matchTyped). With no key, "Is my walk-in fridge OK?"
  // gets "Which fridges need me?" and "How much is it?" the prices, at
  // once, in this browser; nothing is sent. The core takes an item only
  // when two typed words are in its label or here (one, if it is listed
  // here and all that was typed) and no other item on the page comes as
  // close, so a word two of a page's items share makes neither match on its
  // own. Plurals count as one word; "what", "is", "my" and the like never
  // count.
  // "does" and "this" should not count either, but core 1.4.2 takes the
  // plural "s" off before it checks its list of words that don't, so they
  // count as "doe" and "thi", and both are in "What does this alert mean?":
  // with no key, "Does this work with my sensor?" or "What does this button
  // do?" got the alert's answer on the console. Listed on "Which fridges
  // need me?" (and "this" on "It's fixed now") too, they can never pick
  // the alert's answer on their own. (Reported for the core; fixed in 1.4.3.)
  const MATCH = {
    "Which fridges need me?": ["fridge", "freezer", "unit", "warm", "cold", "ok", "fine", "temperature", "temp", "problem", "wrong", "status",
      "does", "this", "walk", "hot", "reading"],
    "What does this alert mean?": ["alert", "alarm", "mean", "warning", "happened", "happening", "text", "message", "going"],
    "Is the food still OK?": ["food", "safe", "eat", "keep", "kept", "throw", "thrown", "bin", "spoiled", "spoilt", "bad", "good", "discard", "stock"],
    "How do I connect a sensor?": ["sensor", "connect", "add", "setup", "set", "install", "wire", "hook", "probe", "device", "new"],
    "Will it work for my café?": ["cafe", "café", "restaurant", "bakery", "kitchen", "coffee", "shop", "bar", "diner", "pub", "deli", "small", "business", "suit", "right"],
    "What does it cost?": ["cost", "price", "pricing", "much", "pay", "fee", "month", "monthly", "expensive", "cheap", "subscription", "dollar", "charge",
      "starter", "growth"],
    "See it live": ["demo", "live", "try", "example", "preview", "tour", "sample", "pretend", "watch"],
    "What sensors do I need?": ["sensor", "probe", "device", "hardware", "thermometer", "brand", "buy", "sell", "compatible", "kind", "type", "work"],
    "Log for the inspector": ["log", "inspector", "inspection", "record", "report", "print", "pdf", "haccp", "health", "audit", "history", "export"],
    "It's fixed now": ["fixed", "fix", "close", "closed", "resolve", "resolved", "sorted", "done", "finished", "alert", "this"],
    "What CyberLogix did for you": ["cyberlogix", "did", "summary", "week", "weekly", "worth", "value"],
    "Will a door left open wake us?": ["door", "open", "opened", "opening", "wake", "false", "alarm", "trigger", "set", "minute", "short", "quick", "delay", "wait"],
    "Wi-Fi or power goes off?": ["wifi", "internet", "power", "outage", "cut", "down", "offline", "signal", "connection", "electricity", "blackout"],
    "Do you phone at night?": ["phone", "call", "night", "ring", "voice", "escalate", "sleep", "weekend"],
    "Do you phone us?": ["phone", "call", "night", "ring", "voice", "escalate", "sleep", "weekend"],
    "Which plan for a café?": ["plan", "cafe", "café", "best", "choose", "pick", "starter", "growth", "small", "restaurant"],
    "What's in the trial?": ["trial", "free", "card", "try", "day", "include"],
    "A fridge is warm: what now?": ["warm", "hot", "too", "freezer", "alarm", "alert", "happen"],
    "A log for the inspector": ["log", "inspector", "inspection", "record", "report", "print", "pdf", "haccp", "health", "audit", "history", "export"],
    "Set up step by step": ["setup", "set", "step", "install", "connect", "guide", "start", "sensor", "instructions"],
    "Power or Wi-Fi goes off?": ["wifi", "internet", "power", "outage", "cut", "down", "offline", "signal", "connection", "electricity", "blackout"],
    "What am I seeing?": ["seeing", "demo", "pretend", "fake", "kitchen", "happening", "screen"],
    "What happens next?": ["happen", "next", "then", "after", "warm", "text", "call", "alert"],
    "Start a free trial": ["trial", "free", "signup", "register", "join", "start"],
    "How do I sign in?": ["sign", "signin", "login", "log", "account", "email", "enter"],
    "I forgot my password": ["forgot", "forgotten", "password", "reset", "lost", "change"],
  };

  // The built-in answers on every page (3.32.0), each with its words: the
  // ones its labels carry above, and a few more. A typed question gets the
  // best of all of them, whichever of the page's pills or suggestions the
  // core matched it to, so "Do you sell sensors?" on the console gets the
  // sensors answer and a tie between two goes to the closer answer rather
  // than to key settings. Read in the browser; nothing is sent.
  const TOPICS = [
    { id: "fixed", labels: ["It's fixed now"], answer: fixedAnswer },
    { id: "status", labels: ["Which fridges need me?"], also: ["refrigerator", "cooler", "degree"], answer: needsAnswer },
    { id: "alert", labels: ["What does this alert mean?"], answer: alertAnswer },
    { id: "food", labels: ["Is the food still OK?"], answer: foodAnswer },
    { id: "sensors", labels: ["What sensors do I need?"], also: ["recommend", "wireless", "model"], answer: SENSORS },
    { id: "price", labels: ["What does it cost?"], also: ["afford", "subscribe"], answer: PRICE },
    { id: "plan", labels: ["Which plan for a café?"], also: ["tier", "option", "upgrade"], answer: PLAN },
    { id: "cafe", labels: ["Will it work for my café?"], answer: cafeAnswer },
    { id: "connect", labels: ["How do I connect a sensor?"], also: ["pair"], answer: CONNECT_HOW },
    { id: "steps", labels: ["Set up step by step"], answer: STEPS },
    { id: "trial", labels: ["What's in the trial?", "Start a free trial"], also: ["credit"], answer: TRIAL },
    { id: "door", labels: ["Will a door left open wake us?"], answer: DOOR },
    { id: "power", labels: ["Wi-Fi or power goes off?", "Power or Wi-Fi goes off?"], also: ["battery"], answer: POWER },
    { id: "calls", labels: ["Do you phone at night?", "Do you phone us?"], answer: CALLS },
    { id: "log", labels: ["Log for the inspector", "A log for the inspector"], answer: LOG },
    // What happens when a fridge is warm, for a visitor: on the person's
    // own pages "which fridges need me" says it with their own fridges.
    { id: "warm", labels: ["A fridge is warm: what now?", "What happens next?"], answer: WARM, only: () => !ownPage },
    { id: "did", labels: ["What CyberLogix did for you"], answer: DID },
    { id: "seeing", labels: ["What am I seeing?"], answer: () => DEMO_WHAT, only: () => path === "/demo" },
    { id: "demo", labels: ["See it live"], answer: DEMO_LINK },
    { id: "signin", labels: ["How do I sign in?"], answer: () => SIGN_IN, only: () => !signedIn() },
    { id: "password", labels: ["I forgot my password"], answer: PASSWORD, only: () => !signedIn() },
  ].map((t) => ({ ...t, words: [...new Set([].concat(...t.labels.map((l) => wordsIn(l).concat((MATCH[l] || []).map(stemWord))),
    (t.also || []).map(stemWord)).filter((w) => w.length > 1 && !SMALL.has(w)))] }));
  const TOPIC_OF = {};
  TOPICS.forEach((t) => t.labels.forEach((l) => { TOPIC_OF[l] = t; }));
  // How common each word is across the answers: a word only one answer has
  // counts for more than one three share.
  const SHARED = {};
  TOPICS.forEach((t) => t.words.forEach((w) => { SHARED[w] = (SHARED[w] || 0) + 1; }));

  // The answer that best fits typed words, or null. `own` wins a tie: the
  // item the core matched was the right one.
  function bestTopic(text, own) {
    const asked = wordsIn(text);
    let best = null;
    let top = 0;
    const score = (t) => asked.reduce((n, w) => n + (t.words.includes(w) ? 1 / SHARED[w] : 0), 0);
    TOPICS.forEach((t) => {
      if (t.only && !t.only()) return;
      const n = score(t);
      if (n > top + 1e-9) { best = t; top = n; }
    });
    if (own && best && score(own) >= top - 1e-9) return own;
    return best;
  }
  // An item's answer, routed: pressed (the label itself), its own; typed,
  // the best of every built-in answer.
  function routed(label, own) {
    return (ctx) => {
      const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
      if (!typed || typed === label) return own(ctx);
      const pick = bestTopic(typed, TOPIC_OF[label] || null);
      return (pick ? pick.answer : own)(ctx);
    };
  }

  // A few words of the answers every page should reach, on an item that has
  // room (3.32.0): the price on the console's alert pill, the café plan on
  // the food pill and the visitor's café pill, sensors on the connect pill.
  // Never a word another item on the same page already uses (in its label
  // or its own words), so the two never tie on it.
  const CARRY = {
    "What does this alert mean?": ["cost", "price", "pricing", "much", "pay", "month", "monthly", "expensive", "starter", "growth"],
    "Is the food still OK?": ["plan", "best", "cafe", "café"],
    "It's fixed now": ["door", "wifi", "power", "outage", "down", "phone", "call", "night", "trial"],
    "Will it work for my café?": ["plan", "starter", "growth", "best", "choose"],
    "How do I connect a sensor?": ["sell", "buy", "hardware", "brand", "thermometer", "compatible"],
  };
  let PAGE_WORDS = new Set();
  function matching(label) {
    const own = MATCH[label] || [];
    if (!own.length) return {};
    const others = new Set([...PAGE_WORDS].filter((w) => !wordsIn(label).includes(w) && !own.map(stemWord).includes(w)));
    const extra = (CARRY[label] || []).filter((w) => !others.has(stemWord(w)));
    return { match: [...new Set(own.concat(extra))].slice(0, 20) };
  }

  // Each a label and what it does: words for the AI, or a {link} / {answer}
  // that needs no key (core 1.4.0).
  const link = (to) => ({ link: to });
  const answer = (fn) => ({ answer: fn });
  // Signed in, under the welcome's own five (so none says the same thing).
  // Signed in: straight to the log for the last seven days (3.30.0), not
  // an answer that says where it is. On the console it only changes the
  // page's #, so nothing reloads. 3.32.0: "What CyberLogix did for you" in
  // place of "Make a proof report", which needed an AI key.
  const LOG_HERE = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#inspector-log?open=1`;
  const DID_HERE = onToday ? "#did" : "/today#did";
  const LIVE = [["Log for the inspector", link(LOG_HERE)],
    ["It's fixed now", answer(fixedAnswer)],
    ["What CyberLogix did for you", link(DID_HERE)]];
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
    "/for/*": [["How would this work for me?", "How would CyberLogix work for a business like mine?"],
      ["When do you alert?", "At what temperature, and after how long, does CyberLogix alert for this trade, and can I change it?"],
      ["A log for the inspector", answer(LOG)]],
  };

  const TASKS = {
    live: "Show my open alerts and tell me what to check first",
    "/": "Help me work out what my café needs to start watching its fridges: ",
    "/pricing": "Help me pick a plan for my business: ",
    "/help/sensors": "Help me set up this sensor: ",
  };

  // Every list of suggestions this page can show (the console's changes
  // when somebody signs in), for the words the pills must leave alone.
  function pageLists() {
    if (onConsole) return [LIVE, CHIPS["/console-out"]];
    if (onToday) return [LIVE];
    if (path.startsWith("/for/")) return [CHIPS["/for/*"]];
    return [CHIPS[path] || []];
  }

  function chips() {
    let list;
    if ((onConsole || onToday) && signedIn()) list = LIVE;
    else if (onConsole) list = CHIPS["/console-out"];
    else if (path.startsWith("/for/")) list = CHIPS["/for/*"];
    else list = CHIPS[path] || [];
    return list.map(([label, what]) => {
      if (typeof what === "string") return { label, prompt: what };
      const plain = what.answer ? { answer: routed(label, what.answer) } : what;
      return { label, ...plain, ...matching(label) };
    });
  }

  // The welcome's five. In the console and on /today (the person's own
  // fridges) they are what a café asks about its fridges, answered from the
  // page with no key (core 1.4.0); on the public pages, where Pilot can see
  // nobody's fridges, what a visitor deciding asks: will it work for me,
  // what it costs (the real plan figures, 3.32.0), can I see it, which
  // sensors. Only the key pill and typed questions the answers don't cover
  // need the AI.
  const pill = (icon, label, fn) => ({ icon, label, answer: routed(label, fn) });
  const OWN = [
    ["bars", "Which fridges need me?", needsAnswer],
    ["help", "What does this alert mean?", alertAnswer],
    ["chat", "Is the food still OK?", foodAnswer],
    ["bolt", "How do I connect a sensor?", CONNECT_HOW],
  ];
  const VISITOR = [
    ["chat", "Will it work for my café?", cafeAnswer],
    ["bars", "What does it cost?", PRICE],
    ["spark", "See it live", null],
    ["help", "What sensors do I need?", SENSORS],
  ];
  function pillsFor(list) {
    // The words every suggestion on this page uses, labels included.
    PAGE_WORDS = new Set();
    pageLists().forEach((l) => l.forEach(([label]) => {
      wordsIn(label).forEach((w) => PAGE_WORDS.add(w));
      (MATCH[label] || []).forEach((w) => PAGE_WORDS.add(stemWord(w)));
    }));
    list.forEach(([, label]) => { wordsIn(label).forEach((w) => PAGE_WORDS.add(w)); (MATCH[label] || []).forEach((w) => PAGE_WORDS.add(stemWord(w))); });
    return list.map(([icon, label, fn]) => (fn ? { ...pill(icon, label, fn), ...matching(label) }
      : { icon, label, link: "/demo", ...matching(label) }))
      .concat([{ icon: "key", label: "Add my AI key", action: "settings" }]);
  }
  const ownPage = onConsole || onToday;
  const OWN_PILLS = ownPage ? pillsFor(OWN) : [];
  const VISITOR_PILLS = ownPage ? [] : pillsFor(VISITOR);

  window.Pilot.mount({
    appName: "CyberLogix AI",
    name: "Pilot",
    // Core 1.2.0: the welcome is CyberLogix's own (docs/PILOT.md): the
    // console's five on the person's own pages, the visitor's five on the
    // public ones (OWN_PILLS, VISITOR_PILLS above).
    tagline: "Your fridge-alarm helper",
    welcomePills: ownPage ? OWN_PILLS : VISITOR_PILLS,
    // Under the first answer Pilot gives without a key (core 1.4.0; once
    // per chat since core 1.4.1, and the welcome's pills come back under it).
    answerMore: "Want more? Add your AI key to ask anything.",
    // Core 1.4.2: the suggestion after the page's own. On the console and
    // /today, this app's own summary of the person's fridges, with no key
    // and nothing sent (summaryAnswer), in place of "Summarise this", which
    // sends the screen to the AI. On the public pages the core's own, shown
    // only once a key is set. Typed questions that fit one of the answers
    // above are answered with no key (MATCH; matchTyped left on), and the
    // handle at the screen's edge while the robot is hidden is left on.
    screenChip: ownPage ? { label: "Sum up my fridges", answer: summaryAnswer } : undefined,
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
    // The one line above the box (core 1.3.0 on the welcome; since core
    // 1.4.1 for the whole chat, with "More" opening the full note in place).
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
    // Core 1.4.1 (3.29.0): on a phone, where every corner would cover a
    // link, a button, a box, a pinned bar or something listed here, the
    // robot fades out instead (hideWhenCrowded, left at its default). So the
    // console's search box and tabs (.finder, .f-tab) are off the list: the
    // core hides the robot rather than tuck it over them (at 320px with an
    // open alert it tucked over the search box's end), the same with or
    // without them (checked in a browser at 320 to 1440). The words listed
    // below (temperatures, status labels) stay: the core lets a tucked robot
    // sit over words, and listing them is what makes it hide instead.
    avoid: ["#site-wide-banner", "#stale-banner", "#account-banner", "#delivery-banner",
      "#offline-banner", "#terms-banner", "#role-banner", "#needs-wrap", ".need-acts",
      "#needs-bar", ".needs-bar-acts",
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
    // Not the logo (3.31.0): it is a picture and words, not a link, on most
    // pages, so core 1.4.2's handle may peek in beside it at the screen's
    // left edge. At 320px and 375x667 on /today with an alert that is the
    // only free spot on the first screen (the alert and "Needs you now"
    // cards, both listed, fill both edges below it); listed, the robot was
    // nowhere to be found there until a scroll.
    screen,
    chips,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: F,
  });
})();
