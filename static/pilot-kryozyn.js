// Pilot on Kryozyn: the small per-app config (docs/PILOT.md).
// The shared core is in static/pilot/ (the same files as on the MTRX
// Horizyn site); this file only says who we are, where the person is, which
// suggestions fit each page, and where the knowledge pack and the console
// tools are (static/pilot-kryozyn-pack.js, fetched only when Pilot is
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
  // The person's own pages: their fridges, once signed in.
  const ownPage = onConsole || onToday;

  function signedIn() {
    if (onConsole) return Boolean(window.KryozynConsole && window.KryozynConsole.signedIn());
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
    "/sample-insurance-pack": "A sample insurance pack, made with pretend data",
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
      const tab = window.KryozynConsole.tab();
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
  // KryozynConsole.overview(), KryozynToday.overview()); elsewhere from
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
    cancelRule: d.cancelRule || "",
    // The chain discount's first step (3.37.0), for a typed "how much for
    // 6 restaurants?": from this many locations, this much off.
    chainFrom: d.chainFrom || "", chainPercent: d.chainPercent || "",
    // The names, from brand.py through Pilot's script tag (main.pilot_facts).
    appName: d.appName || "", appShort: d.appShort || "", companyName: d.companyName || "",
  };
  // The summary's name, a label and a key in the word lists below.
  const DID_LABEL = `What ${F.appShort} did for you`;
  const fig = (v, fallback) => v || fallback;

  // A unit's name as typed by the business, kept as plain words in Pilot's
  // markdown (no links or bold of its own).
  const words = (t) => String(t == null ? "" : t).replace(/[*_`[\]#<>|\\]/g, "").replace(/\s+/g, " ").trim();
  const W = () => window.CLXWords || null;

  // What this page loaded for the person, or null (signed out, a public
  // page, or not loaded yet). Read only: nothing is fetched.
  function overview() {
    try {
      if (onConsole && window.KryozynConsole && window.KryozynConsole.signedIn()
          && typeof window.KryozynConsole.overview === "function") return window.KryozynConsole.overview() || null;
      if (onToday && window.KryozynToday && typeof window.KryozynToday.overview === "function") {
        return window.KryozynToday.overview() || null;
      }
    } catch (_) { /* the answer says where to look instead */ }
    return null;
  }

  // Signed out (3.33.0): "Sign in to see your fridges", with the link, on
  // every page, the public ones included.
  const NOT_LOADED = onConsole || onToday
    ? (signedIn() ? "Your fridges are still loading. Try again in a moment."
      : "Sign in to see your fridges: [sign in](/console). Pilot reads them from the page once you are signed in.")
    : "Sign in to see your fridges: [sign in](/console). No account yet? [See the demo](/demo) with pretend fridges.";

  // "1 reading", "6 readings" (3.33.0), here too, where words.js may not be.
  const count = (n, one, many) => {
    const v = Number(n) || 0;
    return `${v.toLocaleString("en-US")} ${v === 1 ? one : (many || `${one}s`)}`;
  };

  const unitName = (u) => words((u && u.location_name) || "") || words(u && u.sensor_id) || "A unit";
  // A reading, always with its tenth (3.39.0): "35°F" beside "36.4°F"
  // read as two kinds of thermometer, as on /today's list.
  function degrees(value, unit, dd) {
    if (value === null || value === undefined || value === "") return "";
    const n = Number(value);
    const shown = Number.isFinite(n) ? (Math.round(n * 10) / 10).toFixed(1).replace(/^-0\.0$/, "0.0") : value;
    return `${shown}°${(unit && unit.temperature_unit) || (dd && dd.temperature_unit) || "F"}`;
  }
  // A limit as it was set: "41°F".
  function limitDegrees(value, unit, dd) {
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
  // /today's alert card has no "Needs you now" heading under the alert's
  // own headline since 3.38.0: the buttons are at the top of the page.
  const ON_IT = onToday ? "**I'm on it** on the alert at the top of the page" : "**I'm on it** at the top of the page";

  // The words in a question that count, for this file's own matching
  // (3.32.0): lower case, a plural "s" off, and the little words left out
  // before and after, as core 1.4.3 does.
  const SMALL = new Set(("a an and any are am at be can could did do does for from get give got has have help how i if in "
    + "into is it its just know let like me my need of on or our please show should so some tell than that the their them "
    + "then there these they this those to up us want was we what whats when where which who why will with would you your").split(" "));
  function stemWord(w) {
    let x = String(w || "").toLowerCase().replace(/['’]s$/, "").replace(/[^a-z0-9]/g, "");
    if (x.length > 4 && x.endsWith("ies")) x = `${x.slice(0, -3)}y`;
    else if (x.length > 3 && x.endsWith("s") && !x.endsWith("ss")) x = x.slice(0, -1);
    return x;
  }
  // A label made plain, as core 1.4.3 compares a typed label with a pill's
  // (case, spaces, punctuation and accents aside): "which fridges need
  // me" is the pill "Which fridges need me?" pressed, and gets its own
  // answer.
  const sameLabel = (a, b) => {
    const key = (t) => String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
    return Boolean(key(a)) && key(a) === key(b);
  };
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
    const typed = ctx && typeof ctx.label === "string" && !sameLabel(ctx.label, "Which fridges need me?") ? wordsIn(ctx.label) : [];
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
    // One full stop (3.32.1): a unit that needs you already ends its line
    // with one ("since 1:32 AM."), and got two.
    // Two or more asked about (3.39.0: "the bar fridge" at each site) as a
    // list: on lines of their own they ran together into one.
    if (first.length) {
      lines.push(first.length === 1 ? unitLine(first[0], dd).replace(/([^.!?])$/, "$1.")
        : first.map((u) => `- ${unitLine(u, dd).replace(/([^.!?])$/, "$1.")}`).join("\n"));
    }
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
    // A unit named in the question, on a bigger list (3.39.0): the ones
    // asked about, and the rest in one line. "Is the bar fridge ok?" got
    // all twenty.
    const others = units.length - first.length;
    if (named.length && first.length && others > 3) {
      const elsewhere = rows.filter((r) => r.s.state !== "waiting_first" && !first.includes(r.u));
      lines.push(elsewhere.length
        ? `${elsewhere.length === 1 ? "1 other needs you" : `${elsewhere.length} others need you`}: ${elsewhere.map((r) => unitName(r.u)).join(", ")}. Ask **Which fridges need me?** for the list.`
        : `The other ${others} are within their limits.`);
      if (live.some((i) => i.state === "open")) lines.push(`Nobody has answered an alert yet: press ${ON_IT} to say you have it.`);
      return lines.join("\n\n");
    }
    if (!urgent.length && !wide.length && !food.length) {
      const total = units.length;
      if (first.length) {
        lines.push(`Nothing needs you right now.${fine.length ? ` The others:\n${fine.map((u) => `- ${unitLine(u, dd)}`).join("\n")}` : ""}`);
      } else {
        lines.push(total === 1 ? `Nothing needs you right now: ${unitLine(units[0], dd).replace(/([^.!?])$/, "$1.")}`
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
    const limit = cold ? limitDegrees(u.danger_below_display, u, dd) : limitDegrees(u.danger_above_display, u, dd);
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
      // What the business already recorded (3.35.0): never the rule of
      // thumb again over food somebody said went in the bin.
      const at = clock(closed.food_answered_at);
      if (closed.product_disposition === "discarded") {
        return `${measured} You recorded it as thrown away${at ? ` at ${at}` : ""}, so there is nothing left to decide about that food.\n\n${PACK_LINE(closed)}`;
      }
      if (closed.product_disposition && !closed.food_auto) {
        measured += ` You recorded the food as kept${at ? ` at ${at}` : ""}.`;
      }
    }
    const button = onConsole ? "\n\nThe alert's **Is the food still good?** button gives the exact hours from the readings." : "";
    return `${measured}\n\n${RULE}\n\n${NOT_A_RULING}${button}`;
  }

  // Signed in (3.39.0): never "Start with the free 14-day trial" to a
  // business already on it, or past it. On the trial: that it is on it.
  function onTrialNow() {
    const dd = ownPage && signedIn() ? overview() : null;
    return Boolean(dd && ((dd.account && dd.account.on_trial) || (dd.subscription && dd.subscription.plan === "trial")));
  }
  function trialWords() {
    if (!(ownPage && signedIn())) return `Start with the free ${fig(F.trialDays, "14")}-day trial, no card.`;
    return onTrialNow() ? "You're on the free trial now: nothing is billed until you choose a plan on the console's **Money** tab." : "";
  }
  const withTrial = (lead, link) => [lead, trialWords(), link].filter(Boolean).join(" ");

  // A butcher, a bakery or a deli (3.39.0): watched as a restaurant is.
  const FOOD_SHOP = /\bbutchers?\b|\bbutcher(?:'|’)s\b|\bbaker(?:y|ies|s)?\b|\bdeli(?:s|catessens?)?\b/i;
  function shopLead(ctx) {
    const said = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const m = FOOD_SHOP.exec(said);
    if (!m) return "";
    const word = m[0].toLowerCase();
    const shop = /^butcher/.test(word) ? "butcher's" : /^baker/.test(word) ? "bakery" : "deli";
    const page = shop === "butcher's" ? " [For butchers](/for/butchers)" : "";
    return `Yes: a ${shop} is watched the same way as a restaurant, on the restaurant plan with its food limits.${page}`;
  }

  // The visitor's "Will it work for my café?": short, from the real figures.
  function cafeAnswer(ctx) {
    const lead = shopLead(ctx);
    return [
      lead || `Yes: ${F.appShort} is made for cafés and small restaurants first.`,
      `- A wireless sensor goes in each fridge and freezer (we don't sell them: [which sensors work](/help/sensors)).`,
      `- A fridge alarms over ${fig(F.fridgeLimit, "41")}°F and a freezer over ${fig(F.freezerLimit, "10")}°F, only if it stays out (${fig(F.alertAfterFridge, "20")} minutes for a fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer), so a door opened for a minute wakes nobody.`,
      `- Then everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds, and on plans with calls, phoned if nobody answers in ${fig(F.callAfterMinutes, "10")} minutes.`,
      `- Starter is $${fig(F.fromPrice, "59")} a month for one café with up to ${fig(F.starterUnits, "3")} fridges or freezers. [See prices](/pricing)`,
      ownPage && signedIn() ? "[See it live](/demo) with pretend fridges." : `[See it live](/demo) with pretend fridges, or [start a free trial](/signup): ${fig(F.trialDays, "14")} days, no card.`,
    ].join("\n");
  }

  const DOOR = () => `No. A door opened for a minute doesn't alert anybody: a fridge has to **stay** out of range for its wait first (${fig(F.alertAfterFridge, "20")} minutes for a food fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer). You can change the wait per unit with **Alert wait** on its card. A reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past the limit alerts at once.`;
  const POWER = () => `Sensors send their readings over your internet. If the power or the Wi-Fi goes off, readings stop, and after ${fig(F.offlineMinutes, "30")} minutes with nothing from a sensor everyone on call is texted that it has gone quiet, so you hear about the outage too. Alerts go out from our servers, not from your building.\n\nUntil the sensor reports again, check that fridge with a thermometer and write it down. [What to check](/help#sensor-offline)`;
  // "What happens if nobody answers?" (3.38.0) gets the answer to that,
  // not a "Yes" to a question nobody asked.
  const NOBODY_ANSWERS = /\b(?:nobody|no one|no-one)\b.*\b(?:answers?|answered|picks? up|responds?|replies)\b|\b(?:doesn'?t|does not|don'?t|do not|didn'?t|did not)\s+(?:answer|pick up|respond|reply)\b|\bunanswered\b/i;
  const CALLS = (ctx) => `${ctx && typeof ctx.label === "string" && NOBODY_ANSWERS.test(ctx.label) && !/\bnight\b|\b\d{1,2}\s?am\b/i.test(ctx.label) ? "" : "Yes, on plans with calls. "}If nobody answers the alert text within ${fig(F.callAfterMinutes, "10")} minutes, ${F.appShort} phones the people on call in the order you set, until one answers and presses 1. Calls come with the Growth and Enterprise plans; Trial and Starter send texts, and the free trial includes ${fig(F.trialCalls, "5")} alert calls so you can hear one. [Compare plans](/pricing)`;
  const TRIAL = () => {
    const what = `The free trial lasts ${fig(F.trialDays, "14")} days for up to ${fig(F.trialUnits, "5")} sensors, needs no card, and includes ${fig(F.trialCalls, "5")} alert calls.`;
    if (ownPage && signedIn()) {
      return onTrialNow() ? `You're on it now. ${what} Nothing moves you to a paid plan by itself: choose one on the console's **Money** tab when you are ready.`
        : `You're past the free trial: your plan is on the console's **Money** tab. ${what}`;
    }
    return `${what} Nothing moves you to a paid plan by itself: you choose one when you are ready. [Start a free trial](/signup)`;
  };
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
    const where = onToday ? "on the alert at the top of the page" : "at the top of the page, or on the alert under **Alerts**";
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
    ownPage && signedIn() ? withTrial("", "[Work out your price](/pricing#calc-title)")
      : `Either way, start with the free ${fig(F.trialDays, "14")}-day trial, no card. [Work out your price](/pricing#calc-title)`,
  ].join("\n\n");
  const DEMO_WHAT = "A pretend kitchen with four sensors. In about a minute the walk-in starts to warm up, and you see exactly what would happen: the wait, the text, the phone call if nobody answers, and the record once it's fixed. No texts or calls are sent, and nothing is saved.";
  const SIGN_IN = "Use the email and password you signed up with, on [the console](/console). On a phone you can add it to your home screen. No account yet? [Start a free trial](/signup).";

  // "How do I make the insurance pack?" (3.35.0): straight to the pack for
  // the food thrown away in the last day, where there is one; else where
  // the button is, and a sample with pretend data for a visitor.
  const PACK_HREF = (id) => `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#packet=${encodeURIComponent(id)}`;
  const PACK_LINE = (i) => `[Make the insurance pack](${PACK_HREF(i.incident_id)}): the readings, who was told and when, and what happened to the food, ready to print or save as a PDF for your insurer.`;
  function lossNow() {
    const dd = overview();
    return dd && dd.recent_loss && dd.recent_loss.incident_id ? dd.recent_loss : null;
  }
  function packAnswer() {
    const loss = (onConsole || onToday) && signedIn() ? lossNow() : null;
    if (loss) {
      return `For the food thrown away from **${words(loss.unit)}**: ${PACK_LINE(loss)}${loss.note ? "" : "\n\nAdd a line saying what went in the bin first, if you can: it makes the pack complete."}`;
    }
    const where = "In the console, open **Insurance packs** on the **Proof** tab (on a small account, under **More**), and press **Insurance pack** beside the alert. Or press **Make the insurance pack** on the alert itself after you say the food was thrown away.";
    const what = "It holds the readings around the alert, who was texted and when, who answered, and what happened to the food, and it prints or saves as a PDF.";
    if ((onConsole || onToday) && signedIn()) return `${where} ${what}\n\n[What a pack looks like](/sample-insurance-pack)`;
    // "What does my insurer get?" (3.36.0): the sample first.
    return `Your insurer gets an **insurance pack**: the readings around the alert, who was texted and when, who answered, and what happened to the food, ready to print or save as a PDF. [See a sample pack](/sample-insurance-pack), made with pretend data.\n\nEvery plan has it: after a loss it is one button in the console. [Start a free trial](/signup)`;
  }
  // "How do I sign up?" (3.35.0).
  const SIGNUP = () => (signedIn() ? "You are already signed in. To add someone who can sign in too, use **People who can sign in** on the console's **Team** tab."
    : `Press [Start a free trial](/signup): your business name, your name, your email, the mobile number alerts should reach, and a password. It takes about a minute, needs no card, and the trial lasts ${fig(F.trialDays, "14")} days for up to ${fig(F.trialUnits, "5")} sensors. Then add your first fridge or freezer.`);

  // ---------------------------------------------------------------- 3.36.0
  //
  // "Is it hard to set up?", for anybody.
  const EASY = () => [
    "Not hard, and no coding. No electrician either: the sensors are wireless, most run on batteries, and nothing is wired into the fridge. For each fridge or freezer:",
    "1. Buy a wireless sensor that sends its readings to a web address, or one whose maker's cloud we read (Monnit, SensorPush).",
    "2. Put it inside, away from the door, and connect it to your Wi-Fi as its maker says.",
    `3. ${ownPage && signedIn() ? "" : "In the console, "}**Add a fridge or freezer**: the wizard shows your brand's few steps with your key filled in, and ticks when the first reading lands.`,
    "",
    ownPage && signedIn() ? `[Connect your sensor](${CONNECT})` : "[Step-by-step guides](/help/sensors) · [Start a free trial](/signup)",
  ].join("\n");
  // "Does it work in Canada?": only what the product does. Readings in
  // Celsius (the console's °F/°C button, everything follows), a limit of
  // the business's own, texts to a number with its country code (in plain
  // words since 3.37.0: no text provider's name for a visitor), prices in
  // US dollars (docs/FEATURES.md, pricing).
  const CANADA = () => {
    const limit = Number(fig(F.fridgeLimit, "41"));
    const celsius = Math.round(((limit - 32) * 5) / 9);
    return [
      `Yes. ${F.appShort} can show every reading in Celsius: press **°F** at the top of the console to switch to °C, and the alert texts, the inspector's log and the insurance packs follow.`,
      `A fridge alarms over ${limit}°F (${celsius}°C) unless you set your own with **Change limit** on its card, for example 4°C.`,
      "Alert texts and calls reach any mobile number written with its country code: a Canadian number (+1) works the same as a US one.",
      `Prices are in US dollars, and sensors are bought separately. [See prices](/pricing)${ownPage && signedIn() ? "" : " · [Start a free trial](/signup)"}`,
    ].join("\n\n");
  };
  // When it was said: "4:44 AM" today, else the day too.
  // Since 3.37.0 the clock itself adds the day whenever it is not today.
  const saidAt = (iso) => clock(iso);
  // Signed in, "What was thrown away?": the business's own note, quoted,
  // from the loss in the last day or else the newest food thrown away.
  function thrownAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const loss = lossNow();
    let found = loss ? { id: loss.incident_id, unit: words(loss.unit), note: loss.note, at: loss.at } : null;
    if (!found) {
      const i = (dd.incidents || []).filter((x) => x && !x.site_wide && x.product_disposition === "discarded")
        .sort((a, b) => String(b.food_answered_at || b.opened_at).localeCompare(String(a.food_answered_at || a.opened_at)))[0];
      if (i) {
        found = { id: i.incident_id, unit: unitName(units.find((u) => u.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id }),
          note: i.corrective_action, at: i.food_answered_at };
      }
    }
    if (!found) {
      return "Nothing has been recorded as thrown away. When an alert closes you are asked **Food kept** or **Thrown away**, and after Thrown away a line saying what went goes in the record, for the insurance pack.";
    }
    const when = saidAt(found.at);
    const pack = PACK_LINE({ incident_id: found.id });
    if (found.note) return `Recorded for **${found.unit}**${when ? ` at ${when}` : ""}: \u201c${words(found.note)}\u201d\n\n${pack}`;
    return `**${found.unit}**: thrown away${when ? ` at ${when}` : ""}, but nobody has written down what went yet. Add a line on the alert with **Add what was thrown away**: it makes the insurance pack complete.\n\n${pack}`;
  }
  // Signed in, "Why did nobody get a text?": what the alert's own delivery
  // record says (texts off, failed, or how many went), never a list of
  // people.
  function deliveryAnswer() {
    const dd = overview();
    if (!dd) {
      return ownPage && signedIn() ? NOT_LOADED
        : "Sign in to see what happened to an alert's texts: [sign in](/console). Each alert keeps a record of every text: who it went to, and whether it went through.";
    }
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const mine = (dd.incidents || []).filter((i) => i && !i.site_wide);
    const live = liveAlerts(dd).filter((i) => !i.site_wide);
    const i = live[0] || mine.slice().sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))[0];
    const check = `Check each number under **Who gets alerts**, then press **Send a test alert** to see one arrive. [Open Who gets alerts](${TEAM_LINK})`;
    if (!i) return `No alert has gone out yet, so nobody has been texted.\n\n${check}`;
    const name = unitName(units.find((u) => u.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id });
    const at = clock(i.opened_at);
    const sent = Array.isArray(i.sms_fanout) && i.sms_fanout.length ? i.sms_fanout : (i.sms_delivery ? [i.sms_delivery] : []);
    const n = sent.length;
    const through = sent.filter((r) => r && r.delivered).length;
    const people = count(n, "person", "people");
    let said;
    if (i.texts_off) {
      said = `The alert for **${name}**${at ? ` at ${at}` : ""} was written but not delivered: texts are off on this server, so nobody was texted. It showed here all the same.`;
    } else if (!n) {
      said = `Nobody was on the list to text when **${name}** alerted${at ? ` at ${at}` : ""}.`;
    } else if (sent.every((r) => r && r.demo)) {
      // The demo's made-up receipts (3.39.0).
      said = `The alert for **${name}**${at ? ` at ${at}` : ""} was a pretend text to ${people} (demo): nothing is really sent from the demo.`;
    } else if (i.alert_texts_failed) {
      const again = i.next_sms_retry_at ? ` It is sent again at ${clock(i.next_sms_retry_at)}.` : "";
      said = `The alert text for **${name}**${at ? ` at ${at}` : ""} went to ${people} and failed: none went through.${again}`;
    } else if (through === n) {
      said = `The alert for **${name}**${at ? ` at ${at}` : ""} was texted to ${people}, and the phone network took ${n === 1 ? "it" : n === 2 ? "both" : "all of them"}. If a phone still shows nothing, its number may be wrong.`;
    } else {
      said = `The alert for **${name}**${at ? ` at ${at}` : ""} was texted to ${people}: ${through} went through and ${n - through} failed.`;
    }
    return `${said}\n\n${check}`;
  }
  // "How do I add my address to the pack?": the box in Insurance packs.
  const ADDRESS_LINK = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#site-address`;
  const ADDRESS = () => (ownPage && signedIn()
    ? `Open **Insurance packs** in the console (the **Proof** tab; on a small account, under **More**), type it in **Site address** and press **Save**. Every pack shows it from then on. On a pack that has none, **Site: not recorded** has **Add the site address**, which does the same.\n\n[Add the address](${ADDRESS_LINK})`
    : `Once you've signed up, **Insurance packs** in the console has a **Site address** box: saved once, every pack shows it. [See a sample pack](/sample-insurance-pack) · [Start a free trial](/signup)`);

  // ---------------------------------------------------------------- 3.38.0
  //
  // Who is on call, what the account pays, pausing for a closure and
  // changing a limit: from what the page already loaded for the person
  // (the console's On call now card, the overview), first names only.
  const firstName = (full) => words(String(full || "").split(/\s+/)[0]);
  const listed = (names) => (names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`);
  function rotationsNow() {
    try {
      if (onConsole && window.KryozynConsole && typeof window.KryozynConsole.rotations === "function") {
        const r = window.KryozynConsole.rotations();
        return Array.isArray(r) ? r : null;
      }
    } catch (_) { /* the overview's names instead */ }
    return null;
  }
  // When a shift ends (3.39.0): "Fri Dec 4, 8:00 AM", in the rotation's
  // own time, from the card's "Fri 04 Dec 08:00 CST".
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function shiftWords(local) {
    const m = /^([A-Z][a-z]{2}) (\d{1,2}) ([A-Z][a-z]{2}) (\d{1,2}):(\d{2})/.exec(String(local || "").trim());
    if (!m || !MONTHS.includes(m[3])) return words(local);
    const h = Number(m[4]);
    return `${m[1]} ${m[3]} ${Number(m[2])}, ${h % 12 || 12}:${m[5]} ${h < 12 ? "AM" : "PM"}`;
  }
  const shiftEnd = (r) => shiftWords((r.schedule && r.schedule[0] && r.schedule[0].ends_local) || "");
  function oncallAnswer() {
    if (!(ownPage && signedIn())) {
      return `Once you've signed up, everyone under **Who gets alerts** is on call, or set a rotation on the **Team** tab (**On call now**): people take turns by the day or the week, handing over at a time you choose, and the card shows who is on now and who is next. [Start a free trial](/signup)`;
    }
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const rotations = rotationsNow();
    const lines = [];
    if (rotations && rotations.length) {
      const sites = Array.isArray(dd.sites) ? dd.sites : [];
      rotations.forEach((r) => {
        const place = !r.scope || r.scope === "estate" ? ""
          : ` at ${words((sites.find((x) => x.site_id === r.scope) || {}).name || "one location")}`;
        const now = r.on_shift_now ? firstName(r.on_shift_now.full_name) : "";
        const next = r.schedule && r.schedule[1] ? firstName(r.schedule[1].full_name) : "";
        const until = shiftEnd(r);
        lines.push(now ? `On call${place} now: **${now}**${until ? ` until ${until}` : ""}${next ? `, then **${next}**` : ""}.`
          : `Nobody in the rotation${place} is on call right now.`);
      });
    }
    const names = Array.isArray(dd.texted_first_names) ? dd.texted_first_names.map(words).filter(Boolean) : [];
    if (names.length) lines.push(`An alert text goes to **${listed(names)}** right now.`);
    if (!lines.length) return `Nobody is on the list yet, so nobody would be texted. Add people under **Who gets alerts** on the **Team** tab. [Add someone](${TEAM_LINK})`;
    if (!rotations) lines.push("The week's turns are on the **Team** tab, under **On call now**.");
    return `${lines.join("\n\n")}\n\n[Open On call now](${TEAM_LINK})`;
  }
  // "How much am I paying?": the plan and the month from the overview.
  const usd = (v) => `$${Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: Number(v) % 1 ? 2 : 0, maximumFractionDigits: 2 })}`;
  function billAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const sub = dd.subscription || {};
    const acct = dd.account || {};
    const money = onConsole ? "the **Money** tab" : "the console's **Money** tab";
    if (acct.paused) return `${words(acct.paused.words) || "Your account is paused."} Pausing or coming back early is on ${money}, under **Pause or cancel**.`;
    if (acct.on_trial || sub.plan === "trial") {
      const after = sub.after_trial;
      return `You're on the free trial: nothing is billed.${after ? ` Your ${count(sub.units_total, "unit")} would be **${words(after.plan_name)}, ${usd(after.monthly_total_usd)} a month** after it.`
        : sub.monthly_total_usd ? ` Carrying on with what you have would be **${usd(sub.monthly_total_usd)} a month**.` : ""} Nothing moves you to a paid plan by itself. Plans are on ${money}.`;
    }
    const what = (Array.isArray(sub.line_items) ? sub.line_items : []).map((l) => words(l.description)).filter(Boolean).join("; ");
    const late = acct.collections && Number(acct.collections.overdue_usd) > 0 ? ` ${usd(acct.collections.overdue_usd)} is overdue.` : " Nothing is overdue.";
    return `You're on **${words(sub.plan_name) || "your plan"}: ${usd(sub.monthly_total_usd)} a month**${what ? `, for ${what}` : ""}.${late} Invoices, the plan and **Pause or cancel** are on ${money}.`;
  }
  // "Closed for Christmas: can I pause alerts?": the real pause, honestly.
  function pauseAnswer() {
    const dd = ownPage && signedIn() ? overview() : null;
    const paused = dd && dd.account && dd.account.paused;
    if (paused) return `${words(paused.words) || "Alerts are paused."} An owner can come back early on the **Money** tab, under **Pause or cancel**.`;
    return [
      "There's no pause for a few days. The shortest is the account pause, 1, 2 or 3 months: no bill and no alerts until the date you pick, readings still kept, and everything starts again on its own on that date. An owner sets it on the console's **Money** tab, under **Pause or cancel**.",
      "Closed for a few days? Leave alerts on: stock left in a fridge still needs watching. One person away can be muted with **Mute** beside their name under **Who gets alerts**, and unmuted when they're back.",
    ].join("\n\n");
  }
  // "Change the bar fridge limit to 38": the real Change limit button.
  function limitAnswer(ctx) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const to = (/\bto\s+(-?\d{1,3}(?:\.\d)?)\b/i.exec(typed) || [])[1];
    const dd = ownPage && signedIn() ? overview() : null;
    const units = dd && Array.isArray(dd.sensors) ? dd.sensors : [];
    const asked = wordsIn(typed);
    const named = units.filter((u) => wordsIn(u.location_name || "").some((w) => !GENERIC.has(w) && asked.includes(w)));
    const multi = new Set(units.map((u) => u.site_name).filter(Boolean)).size > 1;
    const which = named.slice(0, 4).map((u) => {
      const limit = limitDegrees(u.danger_above_display, u, dd);
      return `**${unitName(u).split("/").pop().trim()}**${multi && u.site_name ? ` at ${words(u.site_name)}` : ""}${limit ? ` (now over ${limit})` : ""}`;
    });
    const how = `Press **Change limit** on its card${onConsole ? "" : " in the console"} (the **Fridges** tab), type ${to || "the new limit"} under **Text us if it goes over**, and press **Save**.`;
    if (which.length) {
      return `${which.length === 1 ? `For ${which[0]}: ${how}` : `${how}\n\nThe ones that fit: ${which.join(", ")}.`} An owner or operator can change it; Pilot doesn't change limits for you.`;
    }
    return `Every fridge and freezer has its own limit. ${how} A fridge starts at ${fig(F.fridgeLimit, "41")}°F and a freezer at ${fig(F.freezerLimit, "10")}°F.${ownPage && signedIn() ? "" : " [Start a free trial](/signup)"}`;
  }

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
    withTrial("Sensors are bought separately.", "[See all prices](/pricing)"),
  ].join("\n\n");
  // The knowledge pack's own words on sensors (pilot-kryozyn-pack.js),
  // short.
  // "Does it work with Monnit?" or "Does this work with my sensor?"
  // (3.33.0): the brand's own yes and its guide first, then the list.
  const BRANDS = {
    monnit: ["Monnit", `iMonnit, Monnit's cloud, sends each reading on to ${F.appShort}`, "monnit"],
    sensorpush: ["SensorPush", `${F.appShort} reads SensorPush's cloud every few minutes, through its G1 gateway`, "sensorpush"],
    elitech: ["Elitech", `its cloud forwards each reading to ${F.appShort}`, "webhook"],
    dickson: ["Dickson", `its cloud forwards each reading to ${F.appShort}`, "webhook"],
  };
  // A walk-in's metal box and the signal through it (3.37.0): "Will a
  // sensor work in a metal walk-in?" got the list of brands and nothing
  // about the walls.
  const THROUGH_METAL = /\bmetal\b|\bsteel\b|\bsignal\b|\bwalk[\s-]*in\b.*\b(?:reach|range|through|wall|walls|wifi|wi-fi)\b/i;
  const METAL_LEAD = "A walk-in's metal walls and door can block a wireless signal. The usual answer is a sensor with a probe on a thin cable: the probe goes inside, the sensor (or its gateway) sits outside the door where the Wi-Fi reaches. A sensor that talks to a gateway near the door (Monnit, SensorPush) works too. Ask the seller about range before you buy; once it is in, **Connect your sensor** ticks when its first reading arrives.";
  function sensorsLead(ctx) {
    const said = ctx && typeof ctx.label === "string" ? ctx.label : "";
    if (said && THROUGH_METAL.test(said) && !/\bno signal\b/i.test(said)) return `${METAL_LEAD}\n\nThe sensors that work:`;
    const typed = said && !sameLabel(said, "What sensors do I need?") ? wordsIn(said) : [];
    const brand = Object.keys(BRANDS).find((b) => typed.includes(b));
    if (brand) {
      const [name, how, guide] = BRANDS[brand];
      return `Yes, **${name}** works: ${how}. [The ${name} guide](/help/sensors#${guide})${onConsole || onToday ? ` · [Connect it](${CONNECT})` : ""}\n\nThe sensors that work:`;
    }
    if (typed.includes("work") || typed.includes("compatible")) {
      return `Most likely, yes. ${F.appShort} works with any wireless sensor that can send its readings to a web address, or whose maker's cloud we can read. The sensors that work:`;
    }
    return "";
  }
  const SENSORS = (ctx) => {
    const lead = sensorsLead(ctx);
    const list = SENSOR_LIST();
    return lead ? `${lead}\n${list.slice(1).join("\n")}` : list.join("\n");
  };
  const SENSOR_LIST = () => [
    `We don't sell sensors. You buy a wireless temperature sensor for each fridge or freezer from a sensor maker, and point it at ${F.appShort}.`,
    "- One that sends each reading to a web address (Elitech, Dickson and most devices set up with a URL), or",
    "- One whose maker's cloud we read every few minutes (Monnit through iMonnit, SensorPush).",
    `A basic Wi-Fi sensor is about $30 to $100; a commercial one with a probe for a walk-in about $100 to $250. ${F.appShort}'s price doesn't include them.`,
    "[Which sensors work, brand by brand](/help/sensors)",
  ];
  // "How do I connect a sensor?" (3.32.0: an answer with the link, so a
  // typed sensor question on the console gets the right answer too).
  const CONNECT_HOW = () => (onConsole || onToday
    ? `**Connect your sensor** walks you through it: say where it is, copy the key it shows once, and it shows your brand's few steps with the key filled in. It ticks when the first reading lands.\n\n[Open Connect your sensor](${CONNECT})`
    : `Start a free trial, then **Add a fridge or freezer** in the console: it gives you a sensor key and shows your brand's few steps with it filled in. [Step-by-step guides](/help/sensors) · [Start a free trial](/signup)`);
  const DEMO_LINK = () => `${DEMO_WHAT}\n\n[See it live](/demo)`;
  const PASSWORD = () => "On [the sign-in page](/console) press **Forgot your password?**, or [reset it here](/reset): we email you a link.";
  const DID = () => (onConsole || onToday
    ? `**What ${F.appShort} did for you** shows this week or this month on one screen: readings checked, alerts and how fast someone answered, what happened to the food, and the printable log for the same days. [Open it](/today#did)`
    : `Once your fridges are watched, **What ${F.appShort} did for you** shows each week on one screen: readings checked, alerts and how fast someone answered, and the printable log. [See it live](/demo)`);
  // Pressed or typed, signed in (3.33.0): the week's own summary line,
  // quoted ("This week: 1,240 readings checked, 2 alerts."), with its few
  // lines and the link. From what /today already loaded, or on the console
  // the same summary through its own session. Never sent to an AI.
  async function didAnswer() {
    if (!(onConsole || onToday) || !signedIn()) return DID();
    let got = null;
    try {
      if (onToday && window.KryozynToday && typeof window.KryozynToday.did === "function") got = window.KryozynToday.did();
      if (!got && onConsole && window.KryozynConsole && typeof window.KryozynConsole.api === "function") {
        got = await window.KryozynConsole.api("/api/console/did-for-you?period=week");
      }
    } catch (_) { got = null; }
    if (!got || !got.figures) return DID();
    const f = got.figures;
    const line = `${words(got.label) || "This week"}: ${count(f.readings, "reading")} checked, ${f.alerts ? count(f.alerts, "alert") : "no alerts"}.`;
    const rest = (Array.isArray(got.lines) ? got.lines : []).slice(1).map((l) => `- ${words(l)}`);
    return `**${line}**${rest.length ? `\n\n${rest.join("\n")}` : ""}\n\n[Open ${DID_LABEL}](${DID_HERE})`;
  }
  // "How do I add my chef to get texts?" (3.33.0).
  const TEAM_LINK = onConsole ? `${window.location.pathname + (window.location.search || "")}#team` : "/console?view=full#team";
  // Signed in (3.35.0): who gets them now, by first name, from the page's
  // own overview (never a surname, a number or an email).
  function textedNow() {
    const dd = overview();
    const names = dd && Array.isArray(dd.texted_first_names) ? dd.texted_first_names.map(words).filter(Boolean) : [];
    if (!names.length) return "";
    const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
    return `Right now an alert text goes to **${list}**.\n\n`;
  }
  const TEAM = () => ((onConsole || onToday) && signedIn()
    ? `${textedNow()}To add someone, use **Who gets alerts** in the console (the **Team** tab): their name and mobile number. Everyone on the list is texted when a fridge stays too warm, and on plans with calls, phoned in the order you set. Then press **Send a test alert** so they know what one looks like.\n\n[Add someone](${TEAM_LINK})`
    : `Once you've signed up, add each person under **Who gets alerts** in the console (the **Team** tab): a name and a mobile number, as many people as you like. Everyone on the list is texted when a fridge stays too warm, and on plans with calls, phoned in the order you set. **Send a test alert** shows them what one looks like. [Start a free trial](/signup)`);
  // "How much for a pharmacy?" (3.33.0), from the plan figures.
  const PHARMACY = () => [
    `**Pharmacies: $${fig(F.pharmacyPrice, "199")} a month per pharmacy**, for up to ${fig(F.unitsPerLocation, "10")} fridges and freezers there; each one past that is $${fig(F.extraUnit, "15")} a month.`,
    `A vaccine fridge gets its own range (36°F to 46°F) and alerts after ${fig(F.alertAfterVaccine, "10")} minutes out, and everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds. No setup fee; sensors are bought separately.`,
    withTrial("", "[See all prices](/pricing)"),
  ].join("\n\n");
  // "How do I cancel my subscription?" (3.33.0): where, and the rule as
  // checkout shows it.
  // "Is there a contract?" (3.38.0): the answer to that first.
  const LOCK_IN = /\bcontract\b|\bcommitment\b|\block(?:ed)?[\s-]*in\b|\btied in\b|\bleave\b|\bany ?time\b/i;
  const CANCEL = (ctx) => [
    ...(ctx && typeof ctx.label === "string" && LOCK_IN.test(ctx.label)
      ? ["No lock-in: you pay by the month, or by the year if you choose, and can leave any time."] : []),
    "In the console: **Money** tab, **Pause or cancel**, then **Cancel my plan**. One press, never hidden behind another screen. You can pause for 1 to 3 months instead.",
    fig(F.cancelRule, "Cancel any time. Nothing more is billed after you cancel, and the service carries on to the end of the month or year you have already paid for. What you have already paid is not refunded, in part or in full."),
    "A free trial has nothing to cancel: it ends by itself, and nothing moves you to a paid plan unless you choose one.",
  ].join("\n\n");
  // "Is my data safe?" (3.33.0): the privacy statement in a few lines.
  const PRIVACY = () => [
    "Yes. What we keep: your readings (about machines, not people), the names and mobile numbers of the people on call, and who signs in, with passwords scrambled.",
    "- Nothing is sold or used to train anything.",
    "- Only Twilio (to send the texts and calls), Google (to word an alert) and our mail host get the bit each needs.",
    "- Every reading is sealed, so a change made later would show.",
    "- An owner can download everything, or delete everything, from the Setup tab.",
    "[Read the privacy statement](/legal#privacy)",
  ].join("\n");

  // ---------------------------------------------------------------- a price for a number (3.37.0)
  //
  // "How much for 2 fridges?", "What would 14 units cost?", "price for 3
  // restaurants with 8 fridges each": the plan worked out from the real
  // plan figures on Pilot's script tag (main.pilot_facts), the same rules
  // the invoice uses (pricing.quote_monthly): Starter for one place with
  // up to its few units, else Growth a location with its allowance and
  // each unit past it extra. A pharmacy is priced as a pharmacy.
  const NUMBER_WORDS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
    eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, forty: 40, fifty: 50, a: 1, an: 1 };
  const UNIT_NOUN = /^(?:fridges?|freezers?|coolers?|units?|sensors?|refrigerators?|chillers?|walk-?ins?|appliances?)$/i;
  const PLACE_NOUN = /^(?:locations?|sites?|restaurants?|stores?|shops?|cafes?|cafés?|kitchens?|branches?|bars?|pharmac(?:y|ies)|bakeries|bakery|outlets?)$/i;
  const PRICE_WORDS = /\bhow much\b|\bcosts?\b|\bprices?\b|\bpricing\b|\bquote\b|\bpay\b|\bcharge\b|\bwhat would\b.*\bbe\b|\ba month\b|\bmonthly\b|\$/i;
  // {units, places, pharmacy, noun} from a question, or null when it gives
  // no number of fridges or places.
  function countsIn(text) {
    const tokens = String(text || "").toLowerCase().replace(/[?!.,;:()]/g, " ").split(/\s+/).filter(Boolean);
    let units = null, places = null, noun = "", unitAt = -1;
    tokens.forEach((t, n) => {
      const value = /^\d{1,4}$/.test(t) ? Number(t) : (NUMBER_WORDS[t] && t.length > 2 ? NUMBER_WORDS[t] : null);
      if (value === null) return;
      // The noun it counts: the next word, or the one after a describing
      // word ("2 small fridges", "3 walk-in freezers").
      const next = [tokens[n + 1], tokens[n + 2]].find((w) => w && (UNIT_NOUN.test(w) || PLACE_NOUN.test(w)));
      if (!next) {
        if (units === null && n === tokens.length - 1) units = value;   // "how much for 2"
        return;
      }
      if (UNIT_NOUN.test(next)) { if (units === null) { units = value; noun = next; unitAt = n; } } else if (places === null) places = value;
    });
    if (units === null && places === null) return null;
    // "3 restaurants with about 8 fridges each" (3.38.0): 8 at each of the
    // 3, so 24 in all, not 8. Also "8 fridges per restaurant", "8 fridges
    // in each", "each with 8 fridges".
    if (units !== null && places !== null && unitAt >= 0) {
      const after = tokens.slice(unitAt + 1, unitAt + 5);
      const each = after.includes("each") || after.includes("apiece")
        || after.some((t, k) => t === "per" && PLACE_NOUN.test(after[k + 1] || ""))
        || tokens.slice(Math.max(0, unitAt - 3), unitAt).includes("each");
      if (each) units *= places;
    }
    const pharmacy = /pharmac/i.test(text);
    return { units, places, pharmacy, noun: noun.replace(/s$/, "") };
  }
  // "15 at one, 4 at the other", "12 fridges in the first, 6 in the
  // second and 3 in the third" (3.39.0): a count for each location, or
  // null. Number words count too ("fifteen at one").
  const PER_PLACE = /\b(\d{1,4})\s+(?:(?:small |big |large )?(?:fridges?|freezers?|units?|sensors?|coolers?|refrigerators?)\s+)?(?:at|in)\s+(?:the\s+)?(?:one|other|another|first|second|third|fourth|fifth|last|main)\b/gi;
  function perPlace(text) {
    const plain = String(text || "").toLowerCase().replace(/\b[a-z]+\b/g, (w) => (NUMBER_WORDS[w] && w.length > 2 && w !== "one" ? String(NUMBER_WORDS[w]) : w));
    const counts = Array.from(plain.matchAll(PER_PLACE), (m) => Number(m[1]));
    return counts.length >= 2 ? counts : null;
  }
  const isQuote = (text) => PRICE_WORDS.test(String(text || "")) && Boolean(countsIn(text) || perPlace(text));
  function QUOTE(ctx) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const split = perPlace(typed);
    if (split) return splitQuote(split, /pharmac/i.test(typed));
    const c = countsIn(typed);
    if (!c) return PRICE();
    const money = (v) => `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    const starter = Number(fig(F.fromPrice, "59")), starterUnits = Number(fig(F.starterUnits, "3"));
    const growth = Number(fig(F.restaurantPrice, "149")), pharmacy = Number(fig(F.pharmacyPrice, "199"));
    const allowance = Number(fig(F.unitsPerLocation, "10")), extra = Number(fig(F.extraUnit, "15"));
    const places = Math.max(1, c.places || 1);
    // Each location's share (3.38.0): 24 fridges over 3 restaurants is 8
    // at each, not 24 at each.
    const each = c.units === null ? null : (places > 1 ? Math.ceil(c.units / places) : c.units);
    const kind = c.noun === "fridge" || c.noun === "freezer" ? c.noun : "fridge or freezer";
    const many = (n) => (kind === "fridge or freezer" ? (n === 1 ? "fridge or freezer" : "fridges or freezers") : n === 1 ? kind : `${kind}s`);
    const what = c.units === null ? (c.pharmacy ? count(places, "pharmacy", "pharmacies") : count(places, "location")) : `${c.units} ${many(c.units)}${places > 1 ? ` across ${places} locations` : ""}`;
    const tail = withTrial("Sensors are bought separately.", "[Work out your price](/pricing#calc-title)");
    const chain = F.chainFrom && F.chainPercent && places >= Number(F.chainFrom)
      ? ` From ${F.chainFrom} locations, ${F.chainPercent}% comes off the location prices: the calculator shows the total.` : "";
    if (c.pharmacy) {
      const over = each === null ? 0 : Math.max(0, each - allowance);
      const total = pharmacy * places + over * extra * places;
      return [`**${what}: ${money(total)} a month**${places > 1 ? `, ${money(pharmacy)} a pharmacy` : " for the pharmacy"}.`,
        `Each pharmacy covers up to ${allowance} fridges and freezers${over ? `; the ${over} past that ${over === 1 ? "is" : "are"} ${money(extra)} a month each` : ""}.${chain}`,
        tail].join("\n\n");
    }
    if (places === 1 && c.units !== null && c.units <= starterUnits) {
      return [`**${what}: Starter, ${money(starter)} a month.** It covers one café or shop with up to ${starterUnits} fridges or freezers: text alerts to everyone on call, the inspector's log and insurance packs.`,
        `Phone calls when a text goes unanswered come with Growth, ${money(growth)} a month for the location.`, tail].join("\n\n");
    }
    const over = each === null ? 0 : Math.max(0, each - allowance);
    const total = growth * places + over * extra * places;
    const why = c.units !== null && places === 1 && c.units > starterUnits ? ` (Starter covers up to ${starterUnits})` : "";
    return [`**${what}: Growth, ${money(total)} a month**${why}.`,
      `${money(growth)} a month a location covers up to ${allowance} fridges and freezers there${over
        ? `, and each one past that is ${money(extra)} a month: ${over} more ${places > 1 ? "at each" : ""} is ${money(over * extra * places)}`.replace(/ {2}/g, " ")
        : ""}, with a phone call when a text goes unanswered.${chain}`, tail].join("\n\n");
  }

  // A count at each location (3.39.0), priced as the /pricing calculator
  // prices a row for each (pricing.js quote): each location's price, and
  // each unit past its allowance at that location extra.
  function splitQuote(counts, pharmacy) {
    const money = (v) => `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    const rate = Number(pharmacy ? fig(F.pharmacyPrice, "199") : fig(F.restaurantPrice, "149"));
    const allowance = Number(fig(F.unitsPerLocation, "10")), extra = Number(fig(F.extraUnit, "15"));
    const places = counts.length;
    const over = counts.map((n) => Math.max(0, n - allowance));
    const past = over.reduce((a, b) => a + b, 0);
    const total = rate * places + past * extra;
    const sum = counts.reduce((a, b) => a + b, 0);
    const listed = counts.length === 2 ? `${counts[0]} and ${counts[1]}` : `${counts.slice(0, -1).join(", ")} and ${counts[counts.length - 1]}`;
    const head = pharmacy
      ? `**${sum} fridges or freezers across ${places} pharmacies (${listed}): ${money(total)} a month**, ${money(rate)} a pharmacy.`
      : `**${sum} fridges or freezers across ${places} locations (${listed}): Growth, ${money(total)} a month.**`;
    const extras = counts.map((n, k) => (over[k] ? `${over[k]} past it at the one with ${n}` : "")).filter(Boolean);
    const chain = F.chainFrom && F.chainPercent && places >= Number(F.chainFrom)
      ? ` From ${F.chainFrom} locations, ${F.chainPercent}% comes off the location prices: the calculator shows the total.` : "";
    const how = `${money(rate)} a month a ${pharmacy ? "pharmacy" : "location"} covers up to ${allowance} fridges and freezers there${extras.length
      ? `; each one past that is ${money(extra)} a month: ${extras.join(", ")}, ${money(past * extra)} in all` : ""}.${chain}`;
    return [head, how, withTrial("Sensors are bought separately.", "[Work out your price](/pricing#calc-title)")].join("\n\n");
  }

  // ---------------------------------------------------------------- 3.39.0: more answers with no key
  //
  // Adding another fridge, trying it with no sensor, an app, this week's
  // alerts, and the warmest unit.
  const ADD_LINK = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#add-unit`;
  function addUnitAnswer() {
    if (!(ownPage && signedIn())) {
      return `Once you've signed up, **Add a fridge or freezer** in the console adds each one: say where it is, and it gives that unit's sensor its own key and shows your brand's few steps. Each fridge or freezer has its own sensor. [Step-by-step guides](/help/sensors) · [Start a free trial](/signup)`;
    }
    const dd = overview();
    const t = (dd && dd.tenant) || {};
    const room = Number.isFinite(Number(t.seats_total)) && Number.isFinite(Number(t.seats_used)) && t.seats_total
      ? ` Your plan has room for ${t.seats_total}; you have ${count(t.seats_used, "unit")}.` : "";
    return `Press **Add a fridge or freezer** in the console (the **Setup** tab): say where it is, and it gives that unit's sensor its own key, shows your brand's few steps with the key filled in, and ticks when the first reading lands.${room}\n\n[Add a fridge or freezer](${ADD_LINK})`;
  }
  function tryAnswer() {
    if (ownPage && signedIn()) {
      return `Yes. On the console's **Fridges** tab an owner can press **Simulate** (or **No hardware yet? Try a simulated one** while the list is empty): pretend units that warm up, so you see the wait, the alert and **It's fixed**. Nothing they do is recorded and nobody is texted.\n\n[See it live](/demo) shows the whole story in a pretend kitchen too.`;
    }
    return [
      "Yes, with no sensor at all:",
      "- [See it live](/demo): a pretend kitchen where the walk-in warms up, and you see the wait, the text, the phone call and the record once it's fixed. No account, nothing sent.",
      `- Or start the free trial and press **Simulate** on the console's **Fridges** tab: simulated units in your own console, never recorded and never texted. [Start a free trial](/signup)`,
    ].join("\n");
  }
  // The manifest (display: standalone) and the console's service worker
  // make it installable from the browser; there is no app-store app.
  const APP = () => `There's no app to download from an app store: ${F.appShort} installs from the browser. Open [the console](/console) on your phone and add it to your home screen (on an iPhone: **Share**, then **Add to Home Screen**; on Android: the browser's menu, then **Install app**). It opens full screen, like an app.\n\nAlerts come by text${F.callAfterMinutes ? ", and on plans with calls by phone," : ""} whether it is open or not.`;
  function weekAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const since = Date.now() - 7 * 24 * 3600 * 1000;
    const all = (dd.incidents || []).filter((i) => i && !i.site_wide).concat((dd.site_wide_alerts || []).filter(Boolean))
      .filter((i) => Date.parse(i.opened_at || "") >= since)
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)));
    if (!all.length) return "**No alerts in the last 7 days.** Every fridge and freezer stayed in range, or came back before anybody needed to be texted.";
    const line = (i) => {
      const name = i.site_wide ? "Several fridges at once" : unitName(units.find((u) => u.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id });
      const at = clock(i.opened_at);
      const answered = i.acknowledged_at ? outFor(i.opened_at, i.acknowledged_at) : "";
      const how = i.state === "resolved" ? `closed${answered ? `, answered in ${answered}` : ""}`
        : i.state === "acknowledged" ? "open, someone is on it" : "open, nobody has answered yet";
      return `- **${name}**${at ? `, ${at}` : ""}: ${how}`;
    };
    const open = all.filter((i) => i.state !== "resolved").length;
    return `**${count(all.length, "alert")} in the last 7 days**${open ? `, ${open} still open` : ""}:\n${all.slice(0, 6).map(line).join("\n")}${all.length > 6 ? `\n- and ${all.length - 6} more` : ""}`;
  }
  // "Which is warmest?": one line, the unit warmest for its limit.
  function warmestAnswer() {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = (Array.isArray(dd.sensors) ? dd.sensors : []).filter((u) => Number.isFinite(Number(u.last_temperature_display))
      && u.last_temperature_display !== null && stateOf(u).state !== "offline");
    if (!units.length) return "None of your fridges or freezers has sent a reading yet.";
    const margin = (u) => Number(u.last_temperature_display) - (Number.isFinite(Number(u.danger_above_display)) && u.danger_above_display !== null
      ? Number(u.danger_above_display) : 1e6);
    const top = units.slice().sort((a, b) => margin(b) - margin(a))[0];
    const now = degrees(top.last_temperature_display, top, dd);
    const limit = limitDegrees(top.danger_above_display, top, dd);
    const gap = Math.round(Math.abs(margin(top)) * 10) / 10;
    const where = !limit ? "" : margin(top) > 0 ? `, ${gap.toFixed(1)}° over its ${limit} limit` : `, ${gap.toFixed(1)}° under its ${limit} limit`;
    return `**${unitName(top)}** is the warmest for its limit: ${now}${where}.`;
  }

  // Words a typed question may use for each answer or link that needs no
  // key (core 1.4.2, matchTyped). With no key, "Is my walk-in fridge OK?"
  // gets "Which fridges need me?" and "How much is it?" the prices, at
  // once, in this browser; nothing is sent. The core takes an item only
  // when two typed words are in its label or here (one, if it is listed
  // here and all that was typed) and no other item on the page comes as
  // close, so a word two of a page's items share makes neither match on its
  // own. Plurals count as one word; "what", "is", "my", "does", "this" and
  // the like never count. (3.32.1: core 1.4.3 drops those before it takes a
  // plural "s" off, so "does" and "this" no longer count as "doe" and "thi";
  // this app's workaround, the two listed on "Which fridges need me?" and
  // "It's fixed now" so they tied, is gone.) Since core 1.4.3 a pill's or a
  // suggestion's own label, typed, always gets that item, and a question
  // nothing fits gets the core's note in the chat, with this page's answers
  // under it (noKeyReply, left at its default), instead of key settings.
  const MATCH = {
    "Which fridges need me?": ["fridge", "freezer", "unit", "warm", "cold", "ok", "fine", "temperature", "temp", "problem", "wrong", "status",
      "walk", "hot", "reading"],
    "What does this alert mean?": ["alert", "alarm", "mean", "warning", "happened", "message", "going"],
    "Is the food still OK?": ["food", "safe", "eat", "keep", "kept", "throw", "thrown", "bin", "spoiled", "bad", "good", "discard", "stock"],
    "How do I connect a sensor?": ["sensor", "connect", "add", "setup", "set", "install", "probe", "device", "new"],
    "Will it work for my café?": ["cafe", "café", "restaurant", "bakery", "kitchen", "coffee", "shop", "bar", "diner", "small", "business"],
    "Who gets the texts?": ["text", "chef", "cook", "staff", "team", "manager", "employee", "colleague", "worker", "person", "people",
      "someone", "add", "roster", "mobile", "invite"],
    "What does it cost?": ["cost", "price", "pricing", "much", "pay", "fee", "month", "monthly", "expensive", "cheap", "subscription", "dollar", "charge",
      "starter", "growth"],
    "See it live": ["demo", "live", "try", "example", "preview", "tour", "sample", "pretend", "watch"],
    "What sensors do I need?": ["sensor", "probe", "device", "hardware", "thermometer", "brand", "buy", "sell", "compatible", "kind", "type", "work"],
    "Log for the inspector": ["log", "inspector", "inspection", "record", "report", "print", "pdf", "haccp", "health", "audit", "history", "export"],
    "It's fixed now": ["fixed", "fix", "close", "closed", "resolve", "resolved", "sorted", "done", "finished", "alert"],
    // 3.35.0: "What did Kryozyn do for me?" ("did" and "do" are little
    // words the matching leaves out, so the ones left must carry it).
    [DID_LABEL]: [F.appShort.toLowerCase(), "summary", "week", "weekly", "worth", "value", "done"],
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
    { id: "sensors", labels: ["What sensors do I need?"], also: ["recommend", "wireless", "model", "monnit", "sensorpush", "elitech", "dickson",
      "metal", "steel", "range", "wall"],
      answer: SENSORS },
    { id: "price", labels: ["What does it cost?"], also: ["afford", "subscribe", "paying"], answer: PRICE },
    // 3.33.0: a pharmacy's price, cancelling, who gets the texts, and
    // whether the data is safe, each with its own words.
    { id: "pharmacy", labels: [], ask: "How much for a pharmacy?",
      also: ["pharmacy", "pharmacist", "chemist", "drugstore", "vaccine", "cost", "price", "much"], answer: PHARMACY },
    // 3.37.0: a price for a number of fridges or places, by phrase only.
    { id: "quote", labels: [], ask: "What would mine cost?", answer: QUOTE },
    { id: "cancel", labels: [], ask: "How do I cancel?",
      also: ["cancel", "cancellation", "unsubscribe", "refund", "quit", "contract", "subscription", "stop", "leave", "commitment"], answer: CANCEL },
    { id: "team", labels: ["Who gets the texts?"], answer: TEAM },
    { id: "privacy", labels: [], ask: "Is my data safe?", also: ["data", "privacy", "private", "secure", "security", "gdpr", "sold", "safe"], answer: PRIVACY },
    { id: "plan", labels: ["Which plan for a café?"], also: ["tier", "option", "upgrade"], answer: PLAN },
    { id: "cafe", labels: ["Will it work for my café?"], answer: cafeAnswer },
    { id: "connect", labels: ["How do I connect a sensor?"], also: ["pair"], answer: CONNECT_HOW },
    { id: "steps", labels: ["Set up step by step"], answer: STEPS },
    { id: "trial", labels: ["What's in the trial?", "Start a free trial"], also: ["credit"], answer: TRIAL },
    { id: "door", labels: ["Will a door left open wake us?"], answer: DOOR },
    { id: "power", labels: ["Wi-Fi or power goes off?", "Power or Wi-Fi goes off?"],
      also: ["battery", "drop", "dropped", "router", "disconnect", "disconnected"], answer: POWER },
    { id: "calls", labels: ["Do you phone at night?", "Do you phone us?"],
      also: ["3am", "2am", "4am", "midnight", "overnight", "asleep", "nighttime", "late", "wake", "nobody", "answer", "unanswered"], answer: CALLS },
    { id: "log", labels: ["Log for the inspector", "A log for the inspector"], answer: LOG },
    // What happens when a fridge is warm, for a visitor: on the person's
    // own pages "which fridges need me" says it with their own fridges.
    { id: "warm", labels: ["A fridge is warm: what now?", "What happens next?"], answer: WARM, only: () => !ownPage },
    { id: "did", labels: [DID_LABEL], also: ["recap", "saved"], answer: didAnswer },
    // 3.35.0: the insurance pack, and signing up.
    // The loss chip itself is a link and carries no words of its own: the
    // connect pill lends them, so the two never tie (a typed question goes
    // to the pill, and from there to this answer).
    { id: "pack", labels: ["Make the insurance pack"], also: ["packet", "claim", "insurer", "adjuster", "evidence", "paperwork"],
      answer: packAnswer },
    { id: "signup", labels: [], ask: "How do I sign up?", also: ["signup", "register", "registration", "join", "create"], answer: SIGNUP },
    // 3.36.0: how hard it is to set up, Canada, the note of what was thrown
    // away, why nobody got a text, and the pack's address.
    { id: "easy", labels: [], ask: "Is it hard to set up?", also: ["hard", "easy", "difficult", "complicated", "technical", "simple",
      "electrician", "install", "installation", "wiring", "installer"], answer: EASY },
    { id: "canada", labels: [], ask: "Does it work in Canada?", also: ["canada", "canadian", "celsius", "metric", "province"], answer: CANADA },
    { id: "lossnote", labels: [], ask: "What was thrown away?", also: ["binned", "note", "went", "lost"], answer: thrownAnswer, only: () => ownPage && signedIn() },
    { id: "delivery", labels: [], ask: "Why did nobody get a text?", also: ["nobody", "didnt", "delivered", "arrive", "arrived", "received", "receive"], answer: deliveryAnswer },
    { id: "address", labels: [], ask: "How do I add my address?", also: ["address", "street"], answer: ADDRESS },
    // 3.38.0: who is on call, what the account pays, pausing for a
    // closure, and changing a limit.
    { id: "oncall", labels: [], ask: "Who is on call?", also: ["rota", "rotation", "shift", "duty", "oncall", "turn"], answer: oncallAnswer },
    { id: "bill", labels: [], ask: "What am I paying?", also: ["paying", "bill", "billed", "invoice", "invoiced", "spend", "spending"],
      answer: billAnswer, only: () => ownPage && signedIn() },
    { id: "pause", labels: [], ask: "Can I pause alerts?", also: ["pause", "paused", "holiday", "christmas", "vacation", "seasonal",
      "snooze", "closure", "away"], answer: pauseAnswer },
    { id: "limit", labels: [], ask: "How do I change a limit?", also: ["limit", "threshold", "adjust", "raise", "lower"], answer: limitAnswer },
    // 3.39.0: another fridge, trying with no sensor, an app, this week's
    // alerts, the warmest unit.
    { id: "addunit", labels: [], ask: "How do I add another fridge?", also: ["second", "another", "extra"], answer: addUnitAnswer },
    { id: "try", labels: [], ask: "Can I try it without a sensor?", also: ["simulate", "simulated", "simulation", "hardware"], answer: tryAnswer },
    { id: "app", labels: [], ask: "Is there an app?", also: ["app", "iphone", "android", "homescreen"], answer: APP },
    { id: "week", labels: [], ask: "How many alerts this week?", answer: weekAnswer, only: () => ownPage && signedIn() },
    { id: "warmest", labels: [], ask: "Which is the warmest?", also: ["warmest", "hottest"], answer: warmestAnswer, only: () => ownPage && signedIn() },
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
  // Phrases whose telling word is a little one the matching leaves out
  // (3.35.0): "sign up" is "sign" alone, the same as "sign in".
  const PHRASES = [
    // 3.39.0, first: "can I see it working before signing up?" is trying
    // it, not signing up.
    [/\bwithout (?:a |any |buying (?:a |any )?)?(?:sensors?|hardware|devices?)\b|\bno (?:sensors?|hardware) yet\b|\b(?:see|watch) it work(?:ing)?\b|\bbefore (?:i |we )?(?:sign(?:ing)? up|buy(?:ing)?|pay(?:ing)?|commit(?:ting)?)\b|\btry it (?:out|first|before)\b|\bsimulat/i, "try"],
    // 3.36.0, first: "my address on the insurance pack" is the address.
    [/\b(?:site|street|shop|business|our|my|the) address\b|\baddress (?:to|on|in|for) (?:the |my |our )?(?:pack|insurance)/i, "address"],
    [/\bsign(?:ing)?[\s-]*up\b|\bregister\b|\bcreate (?:an |my )?account\b/i, "signup"],
    [/\binsurance (?:pack|packet|claim)|\bclaim (?:pack|form)|\bfor (?:my|the) insurer\b/i, "pack"],
    [/\bwhat (?:was |got |were |did (?:we|you|they|i) )?(?:thrown|throw|binned|tossed)\b|\bwhat went in the bin\b/i, "lossnote"],
    // 3.38.0, before the texts and the night: the account's own bill, a
    // pause for a closure, a limit to change, who is on call, and nobody
    // answering.
    [/^(?![\s\S]*\b(?:cancel|pause)\b)[\s\S]*(?:\b(?:how much|what) (?:am|are|do|does) (?:i|we) (?:paying|pay|being charged|charged|billed)\b|\b(?:my|our) (?:bill|invoice)\b|\bwhat (?:plan|tier) (?:am|are) (?:i|we)\b)/i, "bill"],
    [/\bpause\b|\bpausing\b|\bsnooze\b|\b(?:closed|closing|shut) (?:for|over|during)\b|\bholidays?\b|\bvacation\b|\bchristmas\b|\bseasonal\b|\b(?:turn off|switch off|stop|mute) (?:the |all |my )?alerts\b/i, "pause"],
    [/\b(?:change|set|raise|lower|move|adjust|edit)\b.*\b(?:limits?|thresholds?)\b|\blimits? (?:to|at) -?\d|\bthresholds?\b/i, "limit"],
    [/\bwho(?:'s|’s| is| are)?\b.*\bon[\s-]*(?:call|shift|duty)\b|\bon[\s-]*call (?:this|next|now|today|tonight|right now)\b|\bwhose turn\b|\brota\b|\brotation\b/i, "oncall"],
    [NOBODY_ANSWERS, "calls"],
    [/(?=.*\b(?:texts?|sms|messages?|alerts?)\b)(?:\b(?:nobody|no one|no-one)\b|\b(?:didn'?t|did not|never|wasn'?t|was not|weren'?t)\b.*\b(?:get|got|receive|received|arrive|arrived|come|came|sent|texted)\b)/i, "delivery"],
    [/\b(?:hard|difficult|complicated|tricky|technical|easy)\b.*\bset(?:ting)?[\s-]*up\b|\bset(?:ting)?[\s-]*up\b.*\b(?:hard|difficult|complicated|tricky|easy)\b|\bhow (?:hard|easy|difficult) is it\b|\belectrician\b|\bwiring\b|\b(?:hard|easy|difficult) to install\b|\b(?:need|hire|pay) (?:someone|somebody|a professional|an installer|a technician) to install\b/i, "easy"],
    [/\bcanad|\bcelsius\b/i, "canada"],
    [/\b(?:my|the|our) insurer\b|\binsurance company\b/i, "pack"],
    [new RegExp(`\\bwhat (?:did|has|does) ${F.appShort.replace(/[^A-Za-z0-9]/g, "") || "it"}\\b.*\\b(?:do|done)\\b`, "i"), "did"],
    // 3.37.0: a price with a number of fridges or places, worked out.
    [{ test: isQuote }, "quote"],
    // 3.39.0: this week's alerts, the warmest, another fridge, trying with
    // no sensor, an app, and a butcher, a bakery or a deli.
    [/\balerts?\b.*\b(?:this|last|past) (?:week|7 days|seven days)\b|\b(?:this|last) week(?:'|’)?s alerts\b|\bhow many alerts\b/i, "week"],
    [/\b(?:warmest|hottest)\b/i, "warmest"],
    [/\b(?:add|adding|connect|set up|get)\b[^?]*\b(?:second|another|more|extra|third|new|other)\b[^?]*\b(?:fridges?|freezers?|sensors?|units?|coolers?|walk-?ins?)\b|\b(?:second|another|extra|third)\s+(?:fridges?|freezers?|sensors?|units?|coolers?)\b|\badd(?:ing)? (?:a |an |my |one )?(?:\w+ )?(?:fridges?|freezers?|coolers?)\b/i, "addunit"],
    [/\b(?:have|got|is there|are there|download|install|get|need)\b[^?]*\bapps?\b|\bapp store\b|\bplay store\b|\bhome ?screen\b|\b(?:iphone|android|mobile|phone) app\b/i, "app"],
    // A butcher's, a bakery or a deli as a business, not "the deli fridge".
    [{ test: (t) => FOOD_SHOP.test(String(t || "")) && !/\b(?:fridges?|freezers?|coolers?|cases?|ok|okay|warm|cold|temp(?:erature)?s?)\b/i.test(String(t || "")) }, "cafe"],
    // The Wi-Fi or the power dropping, and a card's "No signal".
    [/\b(?:wi-?fi|internet|power|connection|router|network|broadband|electricity)\b.*\b(?:drops?|dropped|dropping|down|off|out|cuts?|fails?|failed|goes|went|lost|lose|loses|outage|offline)\b|\b(?:drops?|dropped|lost|lose|no)\b.*\b(?:wi-?fi|internet|power|connection)\b|\boffline\b|\boutage\b|\bblackout\b|\bpower cut\b|\bno signal\b/i, "power"],
    // The door before the night: "will a door left open wake us at night?".
    [/\bdoors?\b.*\b(?:open|opened|opening|ajar|left)\b|\b(?:open|opened|opening)\b.*\bdoors?\b/i, "door"],
    // The night, 3 AM, a call.
    [/\bnight\b|\bovernight\b|\bmidnight\b|\b\d{1,2}\s?(?:am|a\.m)\b|\basleep\b|\bsleeping\b|\b(?:do|will|would|can) (?:you|it) (?:phone|call|ring)\b/i, "calls"],
    // Metal walls, a walk-in, the signal: the sensors answer, with its lead.
    [THROUGH_METAL, "sensors"],
    // 3.38.0, last: a contract or leaving any time is cancelling; "what's
    // wrong right now?" is which fridges need you.
    [/\bcontract\b|\bcommitment\b|\block(?:ed)?[\s-]*in\b|\btied in\b|\b(?:leave|cancel|quit|stop) (?:at )?any ?time\b/i, "cancel"],
    [/\bwhat(?:'|’)?s (?:wrong|the matter|up with|the status|going on|happening)\b|\bwhat is (?:wrong|happening|going on)\b|\b(?:anything|something) wrong\b|\bany (?:problems?|issues?)\b|\bstatus\b|\bright now\b/i, "status"],
  ];
  function bestTopic(text, own) {
    const phrased = PHRASES.find(([re, id]) => re.test(String(text || "")) && TOPICS.some((t) => t.id === id && (!t.only || t.only())));
    if (phrased) return TOPICS.find((t) => t.id === phrased[1]);
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
  // ---------------------------------------------------------------- a typed question, and every miss (3.37.0)
  //
  // This file works out which built-in answer a typed question is after
  // (sure: a phrase above, or two of its words that one answer has and
  // they are half of what was typed), and tells the core by the words the
  // item it should take carries for that one question (matching below):
  // "How much for 2 fridges?", "Do you call at 3am?", "What if the wifi
  // drops?" and "Will a sensor work in a metal walk-in?" each shared one
  // word with two items, so the core took neither and said it needed a
  // key. The core reads the items while the question is sent; `asking` is
  // the question then, from the chat box's own submit, and only for that
  // moment.
  let asking = null;
  try {
    if (typeof document.addEventListener === "function") {
      document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!form || !form.classList || !form.classList.contains("pilot-compose")) return;
        const box = form.querySelector(".pilot-input");
        const text = box ? String(box.value || "").trim() : "";
        if (!text) return;
        asking = { text, route: undefined };
        setTimeout(() => { asking = null; }, 0);
      }, true);
    }
  } catch (_) { /* no chat box here: the core's own matching, as before */ }

  // The answer a question is surely after, or null.
  function sureTopic(text) {
    const typed = String(text || "");
    const phrased = PHRASES.find(([re, id]) => re.test(typed) && TOPICS.some((t) => t.id === id && (!t.only || t.only())));
    if (phrased) return TOPICS.find((t) => t.id === phrased[1]);
    const asked = wordsIn(typed);
    if (!asked.length) return null;
    const best = bestTopic(typed, null);
    if (!best) return null;
    const hits = asked.filter((w) => best.words.includes(w)).length;
    return (hits >= 2 && hits * 2 >= asked.length) || (asked.length === 1 && hits === 1) ? best : null;
  }
  // For the question being sent: {host, topic, words, stems}, or null. The
  // host is the item on the page that answers it (its own pill or
  // suggestion where there is one); it carries the question's own words,
  // and no other item carries them, for this question only.
  function typedRoute() {
    if (!asking) return null;
    if (asking.route !== undefined) return asking.route;
    asking.route = null;
    const topic = sureTopic(asking.text);
    const asked = wordsIn(asking.text);
    if (topic && asked.length && asked.length <= 12) {
      const items = pageItems().filter((i) => i.answers);
      const host = items.find((i) => TOPIC_OF[i.label] === topic) || items[0];
      if (host) asking.route = { host: host.label, topic, words: asked.slice(0, MOST), stems: new Set(asked) };
    }
    return asking.route;
  }

  // The last question nothing answered, for the note and the two closest
  // answers under it: {text, from, again}. Cleared by any answer of this
  // app's own.
  let lastMiss = null;
  // The answers closest to some typed words, best first: `n` of them,
  // topped up with the page's usual first questions when fewer come close.
  function closest(text, n) {
    const asked = wordsIn(text);
    const near = (a, b) => a === b || (a.length >= 4 && b.length >= 4 && (a.startsWith(b) || b.startsWith(a)));
    const usable = TOPICS.filter((t) => (!t.only || t.only()) && t.id !== "quote");
    const scored = usable.map((t, order) => ({ t, order,
      n: asked.reduce((sum, w) => sum + (t.words.some((x) => near(x, w)) ? 1 / (SHARED[w] || 1) : 0), 0) }))
      .filter((x) => x.n > 0).sort((a, b) => b.n - a.n || a.order - b.order).map((x) => x.t);
    const sure = sureTopic(text);
    const fill = (ownPage && signedIn() ? ["status", "alert", "food", "calls", "power"] : ["price", "cafe", "sensors", "calls"])
      .map((id) => usable.find((t) => t.id === id)).filter(Boolean);
    const out = [];
    [sure].concat(scored, fill).forEach((t) => { if (t && out.length < n && !out.includes(t)) out.push(t); });
    return out;
  }
  const askOf = (t) => t.ask || t.labels[0] || "";
  // The note in the chat when nothing fits (core 1.4.3's noKeyReply, read
  // as each question misses): which question, and that the two closest are
  // under it. A second miss says so in other words, so the chat changes:
  // the core puts each miss's note in place of the last, and with the same
  // words every time a second miss looked like no reply at all.
  function missNote() {
    let text = asking ? asking.text : "";
    if (!text) {
      try { const box = document.querySelector(".pilot-input"); text = box ? String(box.value || "").trim() : ""; } catch (_) { text = ""; }
    }
    if (!text) return "";
    const from = asking || text;
    if (!lastMiss || lastMiss.from !== from) {
      const again = lastMiss && sameLabel(lastMiss.text, text) ? lastMiss.again + 1 : (lastMiss ? 1 : 0);
      lastMiss = { text, from, again };
    }
    const quoted = text.replace(/\s+/g, " ").slice(0, 80) + (text.length > 80 ? "…" : "");
    const lead = lastMiss.again === 0 ? "I don't have a built-in answer for"
      : lastMiss.again % 2 ? "That one isn't built in either:" : "Still no built-in answer for";
    return `${lead} \u201c${quoted}\u201d. The two closest are first below. With an AI key, I can answer anything.`;
  }
  const answered = (fn) => (ctx) => { lastMiss = null; quietLead(); return fn(ctx); };

  // "Here's what I can tell you without an AI key:" once a conversation
  // (3.39.0), not before every typed answer. The core puts the line in
  // front of each one it matches (MATCH_LEAD); this app cannot change the
  // core, so where its own answers are drawn the line is kept on the first
  // of them in the chat and left off the rest. Each drawing of the chat
  // goes through the answers in order in one go, so the first one in a
  // drawing is the first in the chat.
  let leadDrawing = null;
  function quietLead() {
    const R = window.PilotRender;
    if (!R || typeof R.render !== "function" || R.kryozynLead) return;
    const lead = (window.PilotPanel && window.PilotPanel.MATCH_LEAD) || "Here's what I can tell you without an AI key:";
    const draw = R.render;
    R.render = function (text, doc, opts) {
      let shown = text;
      if (typeof text === "string" && opts && opts.local && text.startsWith(lead)) {
        if (!leadDrawing) {
          leadDrawing = { seen: false };
          Promise.resolve().then(() => { leadDrawing = null; });
        }
        if (leadDrawing.seen) shown = text.slice(lead.length).replace(/^\s+/, "");
        leadDrawing.seen = true;
      }
      return draw.call(this, shown, doc, opts);
    };
    R.kryozynLead = true;
  }

  // An item's answer, routed: pressed (the label itself), its own; typed,
  // the answer the question is surely after (3.37.0), else the best of
  // every built-in answer.
  function routed(label, own) {
    return answered((ctx) => {
      const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
      if (!typed || sameLabel(typed, label)) return own(ctx);
      const route = asking && sameLabel(asking.text, typed) ? typedRoute() : null;
      if (route) return route.topic.answer(ctx);
      const pick = bestTopic(typed, TOPIC_OF[label] || null);
      return (pick ? pick.answer : own)(ctx);
    });
  }

  // ---------------------------------------------------------------- the words each item carries (3.36.0)
  //
  // The core takes a typed question to an item only when two of its words
  // are that item's (one, if it is all that was typed and listed), and no
  // other item has as many; from there routed() picks the closest answer
  // of all. Since 3.36.0 the welcome follows the situation (the pack after
  // a loss, no "How do I connect a sensor?" once a sensor reports, no
  // "Is the food still OK?" once it is answered), so which items are on
  // the page changes, and the words of an answer that has no item of its
  // own here go to one that is: each group of words whole, on the item it
  // fits best, never past the core's 20. The groups are in order of how
  // much they matter; one that finds no room is left out. Hand-placed
  // words (3.32.0 to 3.35.0, CARRY and LEND) could not follow a welcome
  // that changes.
  //
  // An item's own words: its label's, MATCH, and a few more for answers
  // only it gives (who was texted, the pack's address).
  const OWN_WORDS = {
    "Which fridges need me?": ["fridge", "freezer", "unit", "warm", "cold", "ok", "fine", "temperature", "temp", "walk", "hot",
      "reading", "work", "working"],
    "Is the food still OK?": ["food", "safe", "eat", "keep", "kept", "throw", "thrown", "away", "bin", "spoiled", "discard", "stock"],
    "How do I connect a sensor?": ["sensor", "connect", "add", "setup", "set", "install", "new", "work", "monnit", "sensorpush",
      "elitech", "dickson", "sell", "buy"],
    "Who gets the texts?": ["text", "chef", "cook", "staff", "team", "manager", "person", "people", "someone", "add", "mobile",
      "invite", "nobody", "didnt", "delivered"],
    "Make the insurance pack": ["insurer", "claim", "adjuster", "evidence", "address", "packet", "paperwork"],
    "Will a door left open wake us?": ["door", "open", "opened", "opening", "wake", "false", "alarm", "trigger", "set", "minute",
      "short", "delay", "wait"],
    "What does it cost?": ["cost", "price", "much", "pay", "month", "expensive", "subscription", "starter", "growth"],
    "Will it work for my café?": ["cafe", "café", "restaurant", "bakery", "kitchen", "coffee", "shop", "small", "business"],
    "What sensors do I need?": ["sensor", "probe", "device", "thermometer", "brand", "buy", "sell", "compatible", "work"],
    "A log for the inspector": ["log", "inspector", "inspection", "record", "report", "print", "pdf", "haccp", "audit"],
  };
  const ownWords = (label) => OWN_WORDS[label] || MATCH[label] || [];
  // Signing up: "sign" only where no "How do I sign in?" is (the console's
  // sign-in page), as a word two items share matches neither.
  const signWords = () => ["signup", "register"].concat(onConsole && !signedIn() ? [] : ["sign"]);
  // [words, the items that answer it themselves (then the group is not
  // needed), the items it would rather sit on]. The person's own pages,
  // then the public ones.
  const OWN_GROUPS = () => [
    [["sensor", "work", "connect", "add", "new", "monnit", "sensorpush", "sell", "buy"], ["How do I connect a sensor?"]],
    [["food", "safe", "eat", "throw", "thrown", "away", "bin", "kept", "discard"], ["Is the food still OK?"]],
    [["cost", "price", "much", "starter", "growth", "pharmacy", "cancel", "subscription", "refund"], [],
      [DID_LABEL, "What does this alert mean?"]],
    [["insurance", "pack", "claim", "insurer", "address"], ["Make the insurance pack"]],
    [["data", "privacy", "safe"], []],
    [["alert", "alarm", "mean"], ["What does this alert mean?"]],
    [[F.appShort.toLowerCase(), "summary"], [DID_LABEL]],
    [["wifi", "power", "goes", "outage", "down"], ["Wi-Fi or power goes off?"]],
    [["phone", "call", "night"], ["Do you phone us?"]],
    [["canada", "celsius", "work"], [], ["Which fridges need me?"]],
    [["hard", "easy", "set"], [], ["How do I connect a sensor?"]],
    [signWords(), []],
    [["plan", "café", "cafe", "best"], []],
    [["door", "trial"], ["Will a door left open wake us?"]],
  ];
  const VISIT_GROUPS = () => [
    [["monnit", "sensorpush", "elitech", "dickson"], [], ["What sensors do I need?"]],
    // Signed out, "Is my walk-in fridge OK?" says where to sign in.
    [["walk", "fridge", "freezer"], [], ["Will it work for my café?"]],
    // "plan" beside the café pill's own "café": "Which plan for my café?".
    [["plan", "best"], ["Which plan for a café?"], ["Will it work for my café?"]],
    [["chef", "staff", "team", "text"], []],
    [["pharmacy", "cancel", "refund"], [], ["What does it cost?"]],
    [["data", "privacy", "safe"], [], ["Will it work for my café?"]],
    [["insurance", "pack", "claim", "insurer"], []],
    [["food", "eat", "safe"], [], ["Will it work for my café?"]],
    // "Does it work in Canada?" is two words wherever these go.
    [["canada", "celsius", "work"], [], ["Will it work for my café?"]],
    [["hard", "easy", "set"], [], ["Set up step by step"]],
    [signWords(), []],
    [["wifi", "power", "goes", "outage", "down"], ["Wi-Fi or power goes off?", "Power or Wi-Fi goes off?"]],
    [["phone", "call", "night"], ["Do you phone at night?", "Do you phone us?"]],
    [["door"], ["Will a door left open wake us?"]],
    [["trial"], ["What's in the trial?", "Start a free trial"]],
  ];
  const MOST = 20;
  // items: [{label, answers}] (answers: an answer of this app's, which a
  // typed question can be routed from; a link cannot). Returns label ->
  // words.
  function allocate(items) {
    const out = {};
    const stems = {};
    items.forEach(({ label }) => {
      const list = [...new Set(ownWords(label))].slice(0, MOST);
      out[label] = list;
      stems[label] = new Set(list.map(stemWord).concat(wordsIn(label)));
    });
    const here = new Set(items.map((i) => i.label));
    const hosts = items.filter((i) => i.answers);
    (ownPage ? OWN_GROUPS() : VISIT_GROUPS()).forEach(([group, homes, prefer]) => {
      if ((homes || []).some((l) => here.has(l))) return;
      const need = (h) => group.filter((w) => !stems[h.label].has(stemWord(w)));
      const room = (h) => MOST - out[h.label].length;
      const liked = (h) => ((prefer || []).includes(h.label) ? 1 : 0);
      const order = hosts.slice().sort((a, b) => liked(b) - liked(a) || room(b) - room(a));
      const host = order.find((h) => room(h) >= need(h).length)
        || order.slice().sort((a, b) => room(b) - room(a))[0];
      if (!host) return;
      need(host).slice(0, room(host)).forEach((w) => { out[host.label].push(w); stems[host.label].add(stemWord(w)); });
    });
    return out;
  }
  // The words for this page as it is now, worked out once a state.
  let lastPlan = { key: "", words: {} };
  function planWords() {
    const items = pageItems();
    const key = items.map((i) => `${i.label}${i.answers ? "+" : ""}`).join("|");
    if (key !== lastPlan.key) lastPlan = { key, words: allocate(items) };
    return lastPlan.words;
  }
  function matching(label) {
    const route = typedRoute();
    if (route) {
      if (label === route.host) return { match: route.words };
      const rest = (planWords()[label] || ownWords(label)).filter((w) => !route.stems.has(stemWord(w)));
      return rest.length ? { match: rest.slice(0, MOST) } : {};
    }
    const list = planWords()[label] || ownWords(label);
    return list.length ? { match: list.slice(0, MOST) } : {};
  }

  // Each a label and what it does: words for the AI, or a {link} / {answer}
  // that needs no key (core 1.4.0).
  const link = (to) => ({ link: to });
  const answer = (fn) => ({ answer: fn });
  // Signed in, under the welcome's own five (so none says the same thing).
  // Signed in: straight to the log for the last seven days (3.30.0), not
  // an answer that says where it is. On the console it only changes the
  // page's #, so nothing reloads. 3.32.0: "What Kryozyn did for you" in
  // place of "Make a proof report", which needed an AI key.
  const LOG_HERE = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#inspector-log?open=1`;
  // A page's own address with the #: a bare "#did" is not a link in Pilot's
  // answers. On /today only the # changes, so nothing reloads.
  const DID_HERE = "/today#did";
  // With an alert open: what it means and "It's fixed now" first. With
  // none (3.33.0), neither: those two were offered beside "Nothing needs
  // you right now". The week's summary and the power instead.
  const LIVE_ALERT = [["What does this alert mean?", answer(alertAnswer)],
    ["It's fixed now", answer(fixedAnswer)],
    ["Log for the inspector", link(LOG_HERE)]];
  const LIVE_CALM = [["Log for the inspector", link(LOG_HERE)],
    [DID_LABEL, answer(didAnswer)],
    ["Wi-Fi or power goes off?", answer(POWER)]];
  function alertOpen() {
    const dd = overview();
    return Boolean(dd) && liveAlerts(dd).length > 0;
  }
  // Food thrown away in the last day (3.35.0): since 3.36.0 the pack is
  // the welcome's first pill, so the suggestions are the calm three.
  function LIVE() {
    return alertOpen() ? LIVE_ALERT : LIVE_CALM;
  }
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
    "/for": [["When do you alert?", `At what temperature, and after how long, does ${F.appShort} alert, and can I change it?`],
      ["A log for the inspector", answer(LOG)]],
    "/demo": [["What am I seeing?", answer(() => DEMO_WHAT)],
      ["What happens next?", answer(WARM)],
      ["Start a free trial", link("/signup")]],
    // 3.35.0: the sample pack's page.
    "/sample-insurance-pack": [["What's in the trial?", answer(TRIAL)],
      ["Start a free trial", link("/signup")]],
    "/console-out": [["How do I sign in?", answer(() => SIGN_IN)],
      ["I forgot my password", link("/reset")],
      ["Start a free trial", link("/signup")]],
    "/for/*": [["How would this work for me?", `How would ${F.appShort} work for a business like mine?`],
      ["When do you alert?", `At what temperature, and after how long, does ${F.appShort} alert for this trade, and can I change it?`],
      ["A log for the inspector", answer(LOG)]],
  };

  const TASKS = {
    live: "Show my open alerts and tell me what to check first",
    "/": "Help me work out what my café needs to start watching its fridges: ",
    "/pricing": "Help me pick a plan for my business: ",
    "/help/sensors": "Help me set up this sensor: ",
  };

  // This page's suggestions as they are now.
  function chipList() {
    if (ownPage && signedIn()) return LIVE();
    if (onConsole) return CHIPS["/console-out"];
    if (path.startsWith("/for/")) return CHIPS["/for/*"];
    return CHIPS[path] || [];
  }
  // Every item on the page now, pills and suggestions, for the words each
  // carries (allocate): [{label, answers}].
  function pageItems() {
    const pills = ownPage ? ownList() : VISITOR;
    return pills.map(([, label, fn]) => ({ label, answers: Boolean(fn) }))
      .concat(chipList().map(([label, what]) => ({ label, answers: typeof what !== "string" && Boolean(what.answer), prompt: typeof what === "string" })))
      .filter((i) => !i.prompt);
  }

  function chips() {
    // After a question nothing answered (3.37.0): the two answers closest
    // to it, as this page's suggestions, so they lead the row under the
    // note. Back to the page's own once anything is answered.
    // (The core may draw before the question's moment is over: the miss
    // of the question being sent counts already.)
    if (lastMiss && (!asking || lastMiss.from === asking)) {
      return closest(lastMiss.text, 2).map((t) => ({ label: askOf(t), answer: answered((ctx) => t.answer({ ...ctx, label: askOf(t) })) }));
    }
    return chipList().map(([label, what]) => {
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
    ["chat", "Is the food still OK?", foodAnswer],
    ["bolt", "How do I connect a sensor?", CONNECT_HOW],
    ["help", "Who gets the texts?", TEAM],
  ];
  // The welcome follows the situation (3.36.0). After food was thrown away
  // (the day /today leads with it), "Make the insurance pack" comes first.
  // "How do I connect a sensor?" goes once a sensor has reported, and "Is
  // the food still OK?" once there is nothing left to answer: no alert
  // live and no closed one waiting for Food kept or Thrown away. Their
  // places go to two questions a business that is set up still asks.
  // Before the page has loaded (or signed out) it is the four above.
  const PACK_PILL = ["download", "Make the insurance pack", packAnswer];
  const SPARE = [
    ["spark", "Do you phone us?", CALLS],
    ["help", "Will a door left open wake us?", DOOR],
  ];
  function ownList() {
    const dd = signedIn() ? overview() : null;
    if (!dd) return OWN;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const reported = units.some((u) => u && u.last_temperature_display !== null && u.last_temperature_display !== undefined);
    const live = liveAlerts(dd).some((i) => !i.site_wide);
    const waiting = (dd.incidents || []).some((i) => i && i.state === "resolved" && !i.site_wide
      && i.food_question && i.food_question.ask_on_today);
    const list = [];
    if (lossNow() && !live) list.push(PACK_PILL);
    list.push(OWN[0]);
    if (live || waiting) list.push(OWN[1]);
    if (!reported) list.push(OWN[2]);
    list.push(OWN[3]);
    SPARE.forEach((p) => { if (list.length < 4) list.push(p); });
    return list;
  }
  const VISITOR = [
    ["chat", "Will it work for my café?", cafeAnswer],
    ["bars", "What does it cost?", PRICE],
    ["spark", "See it live", null],
    ["help", "What sensors do I need?", SENSORS],
  ];
  // Built once a situation, so a pill keeps one answer function (the core
  // tells items apart by it).
  const built = new Map();
  function pillsFor(list) {
    return list.map(([icon, label, fn]) => {
      if (!built.has(label)) built.set(label, fn ? pill(icon, label, fn) : { icon, label, link: "/demo" });
      return { ...built.get(label), ...matching(label) };
    }).concat([{ icon: "key", label: "Add my AI key", action: "settings" }]);
  }
  const OWN_PILLS = () => pillsFor(ownList());
  const VISITOR_PILLS = () => pillsFor(VISITOR);

  // ---------------------------------------------------------------- off the page's words on a phone (3.39.0)
  //
  // On a phone the robot tucked into a bottom corner sat over words: the
  // texts-off notice on /today at 375 and 320, the pricing page's opening
  // lines, the console's "Readings logged" tile. The core lets a tucked
  // robot sit over words and hides it only rather than cover something it
  // is asked to avoid. So, at most every so often, the words actually in
  // the robot's corners (and a small picture there, like the logo) are
  // marked, and `yieldTo` (read with `avoid` on
  // every placement) names them: the robot sits in a free corner, or
  // hides, and its handle shows where it covers nothing. Only on a phone:
  // a desktop corner has room. Never more than the four corners are
  // looked at, so a long page costs no more than a short one.
  const CORNER_W = 80;
  const CORNER_H = 130;
  const NARROW_PX = 720;
  let marked = [];
  let markedAt = 0;
  function textInCorners() {
    try {
      const w = window.innerWidth || 0;
      const h = window.innerHeight || 0;
      if (!w || w > NARROW_PX || typeof document.elementsFromPoint !== "function") return [];
      const now = Date.now();
      if (now - markedAt < 120) return marked.length ? ["[data-pilot-text]"] : [];
      markedAt = now;
      marked.forEach((el) => el.removeAttribute("data-pilot-text"));
      marked = [];
      const zones = [[w - CORNER_W, h - CORNER_H, w, h], [0, h - CORNER_H, CORNER_W, h],
        [w - CORNER_W, 0, w, CORNER_H], [0, 0, CORNER_W, CORNER_H]];
      const own = document.querySelector(".pilot-root");
      const seen = new Set();
      zones.forEach(([l, t, r, b]) => {
        for (let y = t + 2; y < b; y += 8) {
          for (let x = l + 2; x < r; x += 8) {
            // The page under the robot, not the robot.
            const el = document.elementsFromPoint(x, y).find((e) => !(own && own.contains(e)));
            if (el && !seen.has(el)) seen.add(el);
          }
        }
      });
      const inZone = (box) => zones.some(([l, t, r, b]) => box.left < r && box.right > l && box.top < b && box.bottom > t);
      seen.forEach((el) => {
        // A small picture (the logo) counts as the words do; a big one,
        // like the page's backdrop, is scenery.
        if (/^(IMG|SVG|PICTURE)$/i.test(el.tagName || "") && typeof el.getBoundingClientRect === "function") {
          const box = el.getBoundingClientRect();
          if (box.width > 0 && box.width <= 160 && box.height <= 160 && inZone(box)) { el.setAttribute("data-pilot-text", ""); marked.push(el); }
          return;
        }
        const range = document.createRange();
        const words = Array.from(el.childNodes || []).some((n) => {
          if (n.nodeType !== 3 || !n.textContent.trim()) return false;
          range.selectNodeContents(n);
          return Array.from(range.getClientRects()).some((box) => box.width > 0 && inZone(box));
        });
        if (words) { el.setAttribute("data-pilot-text", ""); marked.push(el); }
      });
    } catch (_) { marked = []; }
    return marked.length ? ["[data-pilot-text]"] : [];
  }

  window.Pilot.mount({
    appName: F.appName,
    name: "Pilot",
    // Core 1.2.0: the welcome is Kryozyn's own (docs/PILOT.md): the
    // console's five on the person's own pages, the visitor's five on the
    // public ones (OWN_PILLS, VISITOR_PILLS above).
    tagline: "Your fridge-alarm helper",
    // Read when the window opens and for each typed question (3.36.0):
    // the welcome of the situation the page is in then.
    get welcomePills() { return ownPage ? OWN_PILLS() : VISITOR_PILLS(); },
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
    // Core 1.4.3: anything else typed with no key gets the core's note in
    // the chat ("I need an AI key to answer that one...") with this page's
    // answers and the key pill under it, the words kept in the box
    // (noKeyReply left at its default).
    screenChip: ownPage ? { label: "Sum up my fridges", answer: answered(summaryAnswer) } : undefined,
    // Core 1.4.3's note when nothing fits, in this app's words for each
    // question (3.37.0, missNote above): read as the question misses.
    get noKeyReply() { return missNote(); },
    // Kryozyn has no use for making pictures or videos.
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
      ".thing-temp", ".thing-name", ".demo-unit-temp", ".demo-unit-top", "#needs", ".need",
      // What Kryozyn did for you, opened on /today (3.33.0): at 375px
      // the robot waved over its first lines.
      "#did-lines", "#did-sample-lines", ".did-tabs",
      // The note of what was thrown away, quoted under the month line
      // (3.35.0): at 375px the robot sat on its last words.
      ".answer-note", ".answer-what",
      // The console's header (3.38.0): at 375px with the alert bar pinned
      // under it and the search box and tabs at the bottom, the free top
      // corner was the header's own, beside °F. Listed, the robot keeps
      // clear of it (it hides rather than sit there). Only the console's:
      // the public pages' and /today's headers stay off the list.
      "#app > .nav"],
    // Not the logo (3.31.0): it is a picture and words, not a link, on most
    // pages, so core 1.4.2's handle may peek in beside it at the screen's
    // left edge. At 320px and 375x667 on /today with an alert that is the
    // only free spot on the first screen (the alert and "Needs you now"
    // cards, both listed, fill both edges below it); listed, the robot was
    // nowhere to be found there until a scroll.
    // The words in the robot's corners on a phone (3.39.0, textInCorners).
    get yieldTo() { return textInCorners(); },
    // /today has no <main>: its column gets the phone's side room as the
    // other pages' <main> does (3.39.0), so the tucked robot's edge and
    // its handle stop short of the words there too.
    gutter: onToday ? ["body > .wrap"] : ["main"],
    screen,
    chips,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: F,
  });
})();
