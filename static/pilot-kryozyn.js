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
    // How long a finished trial is still watched (3.41.0).
    graceDays: d.graceDays || "",
    // 3.42.0: the setup fee (0 is none), the yearly offer (pay for this
    // many months, get that many) and how long readings are kept.
    setupFee: d.setupFee || "", yearlyCharged: d.yearlyCharged || "", yearlyCovered: d.yearlyCovered || "",
    keepReadingsMonths: d.keepReadingsMonths || "", keepDetailDays: d.keepDetailDays || "",
    // 3.43.0: "no" when this server places no phone calls (no phone
    // service switched on), for "Why wasn't I called?".
    callsLive: d.callsLive || "",
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

  const NEEDS_ME = /\bwhich\b[^?]*\bneeds?\b|\bneeds? (?:me|us|my attention|our attention|attention|looking at)\b|\b(?:anything|something) (?:that )?needs?\b/i;
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
    const typed = ctx && typeof ctx.label === "string" && !sameLabel(ctx.label, "Which fridges need me?") ? wordsIn(ctx.label) : [];
    // 3.43.0: the place asked about, and a name nobody here has, said so
    // ("Is the Harbourside walk-in ok?" listed the Old Mill walk-in too).
    const scope = typed.length ? scopeOf(ctx.label, dd) : null;
    if (scope && scope.unknown) return notHere(scope, dd);
    const units = scope && scope.places.length ? scope.here : (Array.isArray(dd.sensors) ? dd.sensors : []);
    const atWhere = scope && scope.places.length ? `At **${placeWord(scope)}**:\n\n` : "";
    // "Which fridges need me right now?" (3.41.0) is the pill's question:
    // it listed fifteen fine fridges first, then "Nothing needs you".
    const needsMe = Boolean(ctx && typeof ctx.label === "string" && NEEDS_ME.test(ctx.label));
    const wantKind = typed.includes("freezer") ? "freezer" : (typed.includes("fridge") || typed.includes("refrigerator") || typed.includes("cooler")) ? "fridge" : "";
    if (!units.length) {
      return `You have no fridges or freezers added yet. [Connect your first sensor](${CONNECT}): say where it is, and the wizard shows the few steps for your brand.`;
    }
    const lines = [];
    if (wantKind && !units.some((u) => kindOf(u) === wantKind)) {
      lines.push(`You haven't added a ${wantKind}. [Connect one](${CONNECT}): say where it is, and the wizard shows the few steps for your brand.`);
    }
    // The unit named in the question ("walk-in", "prep", "bar"), first.
    const named = scope ? scope.units : [];
    const asked = named.length ? named : (wantKind && !needsMe ? units.filter((u) => kindOf(u) === wantKind) : []);
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
    const live = liveAlerts(dd).filter((i) => i.site_wide || !atWhere || units.some((u) => u.sensor_id === i.sensor_id));
    const wide = live.filter((i) => i.site_wide);
    if (wide.length) lines.push("**Several fridges are warming at once.** That can mean a power cut, a door left open, or someone at the controls: check the site now.");
    const urgent = rows.filter((r) => r.s.state !== "waiting_first");
    // The food on a closed alert nobody has answered (3.30.0): never
    // "Nothing needs you right now" beside it.
    const food = (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide
      && i.food_question && i.food_question.ask_on_today && (!atWhere || units.some((u) => u.sensor_id === i.sensor_id)));
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
      return atWhere + lines.join("\n\n");
    }
    if (!urgent.length && !wide.length && !food.length) {
      const total = units.length;
      // One line when nothing needs you (3.41.0), never the whole list.
      if (first.length) {
        lines.push(fine.length ? `Nothing needs you right now: the other ${fine.length === 1 ? "one is" : `${fine.length} are`} within ${fine.length === 1 ? "its" : "their"} limits too.` : "Nothing needs you right now.");
      } else {
        const kinds = wantKind ? units.filter((u) => kindOf(u) === wantKind).length : 0;
        lines.push(total === 1 ? `Nothing needs you right now: ${unitLine(units[0], dd).replace(/([^.!?])$/, "$1.")}`
          : kinds ? `Nothing needs you right now: all ${kinds} ${kinds === 1 ? wantKind : `${wantKind}s`} are within their limits${kinds < total ? `, and the other ${total - kinds} too` : ""}.`
            : `Nothing needs you right now: all ${total} are within their limits.`);
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
    return atWhere + lines.join("\n\n");
  }

  // Why it alerted: it stayed out past its wait, or it went far enough past
  // its limit to alert at once (the alert opened before the wait was up).
  function waitWords(i, wait, u) {
    if (!wait) return "";
    const out = Date.parse(i.out_since || ""), opened = Date.parse(i.opened_at || "");
    if (Number.isFinite(out) && Number.isFinite(opened) && opened - out < wait * 60000 - 30000) {
      // Only when it really was that far past (3.41.0): 43.5°F over a
      // 41°F limit was said to have "alerted at once".
      const first = Number(i.first_out_display ?? i.temperature_display);
      const limit = Number(u && u.danger_above_display);
      const once = Number(fig(F.alertAtOnceDegrees, "15")) * ((u && u.temperature_unit) === "C" ? 5 / 9 : 1);
      if (!(Number.isFinite(first) && Number.isFinite(limit) && u && u.danger_above_display !== null && first - limit >= once - 0.05)) return "";
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
    return explainAlert(live[0], dd, live.length - 1);
  }
  // One live alert in words (3.41.0: also for "the bakery fridge alarm,
  // what should I do?", the alert on the unit named), and how many more.
  function explainAlert(i, dd, more) {
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
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
      waitWords(i, wait, u),
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
  // "Was the food in the walk-in freezer ok on Wednesday?" (3.40.0): the
  // alert on the unit named, on the day named, from the page's own record.
  const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  function askedAbout(ctx, dd) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const asked = wordsIn(typed);
    const named = units.filter((u) => wordsIn(u.location_name || "").some((w) => !GENERIC.has(w) && w !== "food" && asked.includes(w)));
    const kind = asked.includes("freezer") ? "freezer" : asked.includes("fridge") ? "fridge" : "";
    const fits = kind ? named.filter((u) => kindOf(u) === kind) : named;
    const lower = typed.toLowerCase();
    const day = DAYS.findIndex((d) => new RegExp(`\\b${d}\\b`).test(lower));
    const dayWord = day >= 0 ? DAYS[day][0].toUpperCase() + DAYS[day].slice(1)
      : /\byesterday\b/.test(lower) ? "yesterday" : /\btoday\b|\bthis morning\b|\btonight\b/.test(lower) ? "today" : "";
    const onDay = (iso) => {
      const t = Date.parse(iso || "");
      if (!Number.isFinite(t) || !dayWord) return true;
      const d = new Date(t);
      if (day >= 0) return d.getDay() === day && Date.now() - t < 7 * 24 * 3600 * 1000;
      const today = new Date();
      const back = dayWord === "yesterday" ? 1 : 0;
      const want = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back);
      return d.getFullYear() === want.getFullYear() && d.getMonth() === want.getMonth() && d.getDate() === want.getDate();
    };
    return { units: fits.length ? fits : named, dayWord, onDay, any: Boolean(named.length || dayWord) };
  }
  function foodAnswer(ctx) {
    const dd = overview();
    if (!dd) return `${RULE}\n\n${NOT_A_RULING}\n\n${NOT_LOADED}`;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const unit = (i) => units.find((x) => x.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
    const about = askedAbout(ctx, dd);
    if (about.any) {
      const ids = new Set(about.units.map((u) => u.sensor_id));
      const hit = (dd.incidents || []).filter((i) => i && !i.site_wide && (!ids.size || ids.has(i.sensor_id))
        && (about.onDay(i.out_since || i.opened_at) || about.onDay(i.resolved_at)))
        .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))[0];
      const shown = about.units.slice(0, 3).map((u) => `**${unitName(u)}**`);
      const which = !shown.length ? "any fridge or freezer"
        : about.units.length > 3 ? `${shown.join(", ")} and ${about.units.length - 3} more` : listed(shown);
      const when = about.dayWord ? (about.dayWord === "today" || about.dayWord === "yesterday" ? ` ${about.dayWord}` : ` on ${about.dayWord}`) : "";
      if (!hit) {
        return `No alert on ${which}${when}: ${about.units.length === 1 ? "it" : "they"} stayed in range, so there is nothing to weigh up.`;
      }
      const u = unit(hit);
      const peak = degrees(hit.peak_display != null ? hit.peak_display : hit.temperature_display, u, dd);
      const limit = limitDegrees(u.danger_above_display, u, dd);
      const long = outFor(hit.out_since || hit.opened_at, hit.out_until || hit.resolved_at);
      const at = clock(hit.out_since || hit.opened_at);
      const lead = `**${unitName(u)}** went out of range${at ? ` at ${at}` : ""}${long ? ` for ${long}` : ""}${peak ? `, at worst ${peak}` : ""}${limit ? ` (limit ${limit})` : ""}${hit.state === "resolved" ? ", and it is closed" : ", and it is not closed yet"}.`;
      if (hit.product_disposition === "discarded") return `${lead} It was recorded as thrown away.\n\n${PACK_LINE(hit)}`;
      if (hit.product_disposition && !hit.food_auto) return `${lead} It was recorded as **kept**${hit.food_answered_at ? ` at ${clock(hit.food_answered_at)}` : ""}.\n\n${NOT_A_RULING}`;
      return `${lead}\n\n${RULE}\n\n${NOT_A_RULING}`;
    }
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
  // And (3.40.0) a food truck or a caterer: "Does it work for a food
  // truck?" got the food-safety rule.
  const FOOD_SHOP = /\bbutchers?\b|\bbutcher(?:'|’)s\b|\bbaker(?:y|ies|s)?\b|\bdeli(?:s|catessens?)?\b|\bfood (?:trucks?|vans?|trailers?|carts?)\b|\bcater(?:ers?|ing)\b/i;
  function shopLead(ctx) {
    const said = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const m = FOOD_SHOP.exec(said);
    if (!m) return "";
    const word = m[0].toLowerCase();
    if (/^food /.test(word)) {
      return `Yes, while its fridges have internet: a ${word.replace(/s$/, "")} is watched the same way as a restaurant, on the restaurant plan with its food limits. Each sensor sends its readings over Wi-Fi or its maker's gateway, so the truck needs a hotspot or a gateway that stays on. Parked with everything switched off, a sensor goes quiet, and after ${fig(F.offlineMinutes, "30")} minutes everyone on call is texted that it has.`;
    }
    const shop = /^butcher/.test(word) ? "butcher's" : /^baker/.test(word) ? "bakery" : /^cater/.test(word) ? "caterer" : "deli";
    const page = shop === "butcher's" ? " [For butchers](/for/butchers)" : "";
    return `Yes: a ${shop} is watched the same way as a restaurant, on the restaurant plan with its food limits.${page}`;
  }

  // Every sector with a page at /for/<slug> (3.41.0), from Pilot's script
  // tag (main._sector_pages): "Do you do florists?" got "I don't have a
  // built-in answer". Each is named by its address's words ("florists",
  // "ice cream shops", "vaccine clinics") and the short parts of its name
  // ("Data centers", "Blood Banks"), a trailing "shop" or "store" left
  // optional, and by any one of those words no other sector has and no
  // other question would use alone ("cryostorage", "brewery").
  // Restaurants and pharmacies have answers of their own.
  const SECTOR_OWN = new Set(["restaurant", "pharmacy"]);
  const SECTOR_FILLER = new Set(["and", "high", "end", "stake", "fine", "luxury", "licensed", "retail", "private"]);
  const SECTOR_TAIL = new Set(["shop", "store", "clinic", "storage", "infrastructure", "transport", "cultivation", "hangar", "bay"]);
  const SECTOR_COMMON = new Set(["data", "center", "cold", "chain", "school", "medical", "blood", "bank", "hospital", "country",
    "club", "ice", "cream", "art", "engine", "wine", "solar", "vaccine"]);
  const SECTOR_ALIAS = { florists: ["flower shop", "flower store", "flower stall", "flowers"], breweries: ["brewer", "brewpub"] };
  const stemsOf = (text) => String(text || "").split(/[^A-Za-z0-9]+/).map(stemWord).filter(Boolean);
  const SECTORS = (() => {
    const rows = String(d.sectors || "").split(";").map((row) => {
      const [slug, name, price, per] = row.split("|");
      return { slug: slug || "", name: words(name), price: price || "", per: per || "" };
    }).filter((x) => x.slug && x.name && !SECTOR_OWN.has(x.slug));
    rows.forEach((x) => {
      const parts = [x.slug.replace(/[-_]+/g, " ")].concat(x.name.split(/&|,|\band\b/i));
      const phrases = [];
      parts.forEach((part) => {
        let seq = stemsOf(part).filter((w) => !SECTOR_FILLER.has(w));
        if (!seq.length || (seq.length === 1 && SECTOR_TAIL.has(seq[0]))) return;
        phrases.push(seq);
        while (seq.length > 1 && SECTOR_TAIL.has(seq[seq.length - 1])) { seq = seq.slice(0, -1); phrases.push(seq); }
      });
      x.phrases = phrases;
    });
    // Other words for a trade (3.42.0): "I own a flower shop".
    rows.forEach((x) => (SECTOR_ALIAS[x.slug] || []).forEach((a) => x.phrases.push(stemsOf(a))));
    const owners = {};
    rows.forEach((x) => new Set([].concat(...x.phrases)).forEach((w) => { owners[w] = (owners[w] || 0) + 1; }));
    rows.forEach((x) => {
      new Set([].concat(...x.phrases)).forEach((w) => {
        if (owners[w] === 1 && w.length > 2 && !SECTOR_COMMON.has(w) && !SECTOR_TAIL.has(w) && !SMALL.has(w)) x.phrases.push([w]);
      });
    });
    return rows;
  })();
  function sectorIn(text) {
    const said = stemsOf(text);
    const has = (seq) => said.some((_, k) => seq.every((w, j) => said[k + j] === w));
    return SECTORS.find((x) => x.phrases.some(has)) || null;
  }
  function sectorAnswer(ctx) {
    const said = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const x = sectorIn(said);
    if (!x) return cafeAnswer(ctx);
    const who = x.name.toLowerCase();
    const price = x.price
      ? `$${x.price} a month per ${x.per}${x.per === "location" || x.per === "pharmacy" ? `, for up to ${fig(F.unitsPerLocation, "10")} fridges and freezers there` : ""}`
      : "priced for what you run: ask for a quote on its page";
    return [
      `Yes: ${F.appShort} is set up for ${who}, with limits of their own. A wireless sensor in each one sends its readings; when one stays out of range past its wait, everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds, and on plans with calls, phoned if nobody answers.`,
      `The price: ${price}. Sensors are bought separately.`,
      `[${F.appShort} for ${who}](/for/${x.slug})${ownPage && signedIn() ? "" : " · [Start a free trial](/signup)"}`,
    ].join("\n\n");
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

  // "How long is the wait?" (3.40.0) is not a yes-or-no question.
  const DOOR = (ctx) => `${ctx && typeof ctx.label === "string" && !/\bdoors?\b/i.test(ctx.label) && /\bwait\b|\bhow long\b/i.test(ctx.label) ? "" : "No. "}A door opened for a minute doesn't alert anybody: a fridge has to **stay** out of range for its wait first (${fig(F.alertAfterFridge, "20")} minutes for a food fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer). You can change the wait per unit with **Alert wait** on its card. A reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past the limit alerts at once.`;
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
  // The units a question names by their own words ("bakery", "walk-in",
  // "bar"), never by "fridge" or "freezer" alone.
  const unitsNamed = (text, units) => {
    const asked = wordsIn(text);
    return asked.length ? units.filter((u) => wordsIn(u.location_name || "").some((w) => !GENERIC.has(w) && w !== "food" && asked.includes(w))) : [];
  };
  // "The bakery fridge alarm, what should I do?", "the walk-in is beeping"
  // (3.41.0): the alert on the unit named, if one is open; else what that
  // unit reads and what to do. "Bakery" was the sales pitch for bakeries.
  // Kryozyn texts; a beep in the kitchen is the unit's own alarm.
  const ALARM_RE = /\balarm(?:s|ing|ed)?\b|\balert(?:s|ing|ed)?\b|\bbeep(?:s|ing|ed)?\b|\bbuzz(?:es|ing|ed)?\b|\bgo(?:ing|es)? off\b|\bwent off\b|\bsiren\b|\bsounding\b/i;
  const WARM_WORDS = /\bwarm(?:ing|er)?\b|\bhot\b|\bheating up\b/i;
  const BEEP_RE = /\bbeep|\bbuzz|\bgo(?:ing|es)? off\b|\bwent off\b|\bsiren\b|\bsounding\b/i;
  function alarmAnswer(ctx) {
    if (!(ownPage && signedIn())) return ownPage ? NOT_LOADED : WARM();
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    // 3.43.0: within the place named, and a name nobody has, said so.
    const scope = scopeOf(typed, dd);
    if (scope.unknown) return notHere(scope, dd);
    const named = scope.units;
    const live = liveAlerts(dd);
    const near = named.length ? named : scope.places.length ? scope.here : null;
    const mine = live.filter((i) => !i.site_wide && near && near.some((u) => u.sensor_id === i.sensor_id));
    if (mine.length) return explainAlert(mine[0], dd, live.length - 1);
    if (!near && live.length) return explainAlert(live[0], dd, live.length - 1);
    const lines = [];
    if (named.length === 1) {
      const u = named[0];
      const s = stateOf(u);
      const now = degrees(u.last_temperature_display, u, dd);
      const limit = limitDegrees(u.danger_above_display, u, dd);
      lines.push(s.state in URGENCY
        ? `No alert is open on **${unitName(u)}** yet. ${words(s.line) || s.word}`
        : `No alert is open on **${unitName(u)}**: it reads ${now}${limit ? `, under its ${limit} limit` : ""}.`);
    } else if (named.length) {
      const out = named.filter((u) => stateOf(u).state in URGENCY);
      lines.push(`No alert is open on any of the ${named.length} that fit: ${out.length
        ? `${out.map((u) => unitLine(u, dd)).join("; ")}.` : "all are within their limits."}`);
    } else if (scope.places.length) {
      const out = scope.here.filter((u) => stateOf(u).state in URGENCY);
      lines.push(`No alert is open at **${placeWord(scope)}**${out.length ? `: ${out.map((u) => unitLine(u, dd)).join("; ")}.` : ", and every unit there is within its limits."}`);
    } else {
      lines.push("No alert is open right now, and every unit is within its limits.");
    }
    if (BEEP_RE.test(typed)) lines.push(`A beep in the kitchen is the unit's own alarm, not ${F.appShort}'s: ${F.appShort} texts and phones, it doesn't beep.`);
    const wait = named.length === 1 && Number(named[0].alert_after_minutes) ? `${named[0].alert_after_minutes} minutes` : "its wait";
    lines.push(`Check the door is shut and sealing and the unit is running, and check it with a thermometer. If it stays over its limit past ${wait}, everyone on call is texted and the alert shows at the top of the page with **I'm on it**.`);
    const other = live.filter((i) => !i.site_wide)[0];
    if (other) {
      const u = units.find((x) => x.sensor_id === other.sensor_id) || { sensor_id: other.sensor_id };
      lines.push(`The alert open now is on **${unitName(u)}**: ask **What does this alert mean?**`);
    }
    return lines.join("\n\n");
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
  // "What did I pay last month?", "show me my last invoice", "When is my
  // next bill?" (3.41.0): from the bills the console already loaded
  // (KryozynConsole.bills: the Bills card's invoices and the contract's
  // next bill), never the price list. /today loads no bills: it says where
  // they are, with the plan's monthly price.
  const BILLS_HERE = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#bills`;
  const INVOICE_HERE = (id) => `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#invoice=${encodeURIComponent(id)}`;
  function billsNow() {
    try {
      if (onConsole && window.KryozynConsole && typeof window.KryozynConsole.bills === "function") return window.KryozynConsole.bills() || null;
    } catch (_) { /* where the bills are, instead */ }
    return null;
  }
  const PAST_BILL = /\bwhat did (?:i|we) pay\b|\b(?:last|latest|previous|recent|most recent|newest) (?:bill|invoice|payment|charge|receipt)s?\b|\binvoices?\b|\breceipts?\b|\bpaid\b|\blast month\b/i;
  const NEXT_BILL = /\bnext (?:bill|invoice|payment|charge)\b|\bwhen (?:is|will|do|does|am|are)\b[^?]*\b(?:bill|invoice|billed|charged|pay|payment)\b|\bbilling date\b/i;
  const dayWords = (iso) => (W() && typeof W().date === "function" ? W().date(iso) : String(iso || "").slice(0, 10));
  function billAnswer(ctx) {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const sub = dd.subscription || {};
    const acct = dd.account || {};
    const money = onConsole ? "the **Money** tab" : "the console's **Money** tab";
    if (acct.paused) return `${words(acct.paused.words) || "Your account is paused."} Pausing or coming back early is on ${money}, under **Pause or cancel**.`;
    if (acct.on_trial || sub.plan === "trial") {
      const after = sub.after_trial;
      return `You're on the free trial: nothing is billed${PAST_BILL.test(typed) ? ", so there are no bills yet" : ""}.${after ? ` Your ${count(sub.units_total, "unit")} would be **${words(after.plan_name)}, ${usd(after.monthly_total_usd)} a month** after it.`
        : sub.monthly_total_usd ? ` Carrying on with what you have would be **${usd(sub.monthly_total_usd)} a month**.` : ""} Nothing moves you to a paid plan by itself. Plans are on ${money}.`;
    }
    const what = (Array.isArray(sub.line_items) ? sub.line_items : []).map((l) => words(l.description)).filter(Boolean).join("; ");
    const plan = `You're on **${words(sub.plan_name) || "your plan"}: ${usd(sub.monthly_total_usd)} a month**${what ? `, for ${what}` : ""}.`;
    const bills = billsNow();
    if (NEXT_BILL.test(typed)) {
      const next = bills && bills.contract && bills.contract.next_billing_at;
      if (next) return `**Your next bill is on ${dayWords(next)}**: about ${usd(sub.monthly_total_usd)}${what ? `, for ${what}` : ""}, at the units you have today. [Open your bills](${BILLS_HERE})`;
      return `${plan} The date of the next bill is on ${money}, under **Bills**. [Open your bills](${BILLS_HERE})`;
    }
    if (PAST_BILL.test(typed)) {
      if (!bills || !bills.ledger) return `Your bills are on ${money}, under **Bills**: each one, what it covers and whether it is paid. ${plan} [Open your bills](${BILLS_HERE})`;
      const all = (Array.isArray(bills.ledger.invoices) ? bills.ledger.invoices : []).filter((inv) => inv && inv.state !== "void")
        .sort((a, b) => String(b.issued_at).localeCompare(String(a.issued_at)));
      if (!all.length) return `No bills yet. ${plan} [Open your bills](${BILLS_HERE})`;
      const lines = [];
      if (/\blast month\b/i.test(typed)) {
        const now = new Date();
        const want = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        const inMonth = all.filter((inv) => { const t = new Date(Date.parse(inv.issued_at)); return t.getFullYear() === want.getFullYear() && t.getMonth() === want.getMonth(); });
        const month = want.toLocaleDateString("en-US", { month: "long" });
        lines.push(inMonth.length ? `In ${month} you were billed ${usd(inMonth.reduce((n, inv) => n + Number(inv.total_usd || 0), 0))}.`
          : `Nothing was billed in ${month}.`);
      }
      const inv = all[0];
      const state = inv.state === "paid" ? `paid ${dayWords(inv.paid_at)}`
        : inv.overdue ? `${count(inv.days_overdue, "day")} overdue` : `due ${dayWords(inv.due_at)}`;
      const covers = (Array.isArray(inv.lines) ? inv.lines : []).map((l) => words(l.description)).filter(Boolean).join("; ");
      lines.push(`Your last bill: **${usd(inv.total_usd)} on ${dayWords(inv.issued_at)}**, ${state}${covers ? `, for ${covers}` : ""}.${all.length === 1 ? " It's your first bill." : ""}`);
      return `${lines.join(" ")} [Open the bill](${INVOICE_HERE(inv.invoice_id)})`;
    }
    const late = acct.collections && Number(acct.collections.overdue_usd) > 0 ? ` ${usd(acct.collections.overdue_usd)} is overdue.` : " Nothing is overdue.";
    return `${plan}${late} Invoices, the plan and **Pause or cancel** are on ${money}.`;
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
    const to = (/\b(?:to|at)\s+(-?\d{1,3}(?:\.\d)?)\b/i.exec(typed) || [])[1];
    const dd = ownPage && signedIn() ? overview() : null;
    const units = dd && Array.isArray(dd.sensors) ? dd.sensors : [];
    // 3.43.0: the units named, within the place named; a name nobody here
    // has is said so, never another unit's limit.
    const scope = dd ? scopeOf(typed, dd) : null;
    if (scope && scope.unknown) {
      return `${notHere(scope, dd).replace(/ Ask \*\*Which fridges need me\?\*\* for all of them\.$/, "")} Press **Change limit** on the unit's card${onConsole ? "" : " in the console"} (the **Fridges** tab) to change its limit.`;
    }
    const named = scope ? (scope.units.length ? scope.units : scope.places.length ? scope.here : []) : [];
    const multi = new Set(units.map((u) => u.site_name).filter(Boolean)).size > 1;
    // Every unit that fits, each with its limit as it is set (3.41.0):
    // "(now over 10°F)" read as if the freezers were warm, and the fifth
    // walk-in was left out.
    const which = named.map((u) => {
      const limit = limitDegrees(u.danger_above_display, u, dd);
      return `**${unitName(u).split("/").pop().trim()}**${multi && u.site_name ? ` at ${words(u.site_name)}` : ""}${limit ? ` (limit ${limit})` : ""}`;
    });
    const how = `Press **Change limit** on its card${onConsole ? "" : " in the console"} (the **Fridges** tab), type ${to || "the new limit"} under **Text us if it goes over**, and press **Save**.`;
    if (which.length) {
      const who = "An owner or operator can change it; Pilot doesn't change limits for you.";
      return which.length === 1 ? `For ${which[0]}: ${how} ${who}`
        : `${how} ${who}\n\nThe ${which.length} that fit:\n${which.map((w) => `- ${w}`).join("\n")}`;
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
  // "Which sensor should I get for a walk-in freezer?" (3.42.0) names the
  // sensor once: it got the alert procedure.
  const RECOMMEND = /\b(?:recommend(?:ed|ation)?s?|suggest(?:ion)?s?|best|which (?:sensors?|probes?|brand|model)s?\b[^?]*\b(?:buy|get|use|choose|pick))\b[^?]*\b(?:sensors?|probes?|devices?|brands?|models?|thermometers?)\b|\b(?:sensors?|probes?|devices?|brands?|models?|thermometers?)\b[^?]*\b(?:recommend(?:ed)?|suggest(?:ed)?|best)\b|\bwhat (?:sensors?|probes?) should (?:i|we)\b|\bwhich (?:sensors?|probes?|thermometers?|brand of sensor|sensor brand)\b[^?]*\b(?:buy|get|use|choose|pick|need|for)\b|\b(?:best|good|right) (?:sensors?|probes?|thermometers?)\b|\bsensors? (?:do|would|will|should) (?:i|we|you) (?:need|buy|get|use)\b/i;
  const METAL_LEAD = "A walk-in's metal walls and door can block a wireless signal. The usual answer is a sensor with a probe on a thin cable: the probe goes inside, the sensor (or its gateway) sits outside the door where the Wi-Fi reaches. A sensor that talks to a gateway near the door (Monnit, SensorPush) works too. Ask the seller about range before you buy; once it is in, **Connect your sensor** ticks when its first reading arrives.";
  function sensorsLead(ctx) {
    const said = ctx && typeof ctx.label === "string" ? ctx.label : "";
    if (said && THROUGH_METAL.test(said) && !/\bno signal\b/i.test(said)) return `${METAL_LEAD}\n\nThe sensors that work:`;
    // "What sensor do you recommend for a walk-in freezer?" (3.41.0).
    if (said && RECOMMEND.test(said)) {
      const cold = /\bwalk[\s-]*in|\bfreezer|\bcold room/i.test(said);
      return `We don't sell sensors. We don't back one brand either: any sensor that sends its readings to a web address, or whose maker's cloud we read, works.${cold
        ? " For a walk-in or a freezer, pick one made for freezer temperatures with a probe on a thin cable: the probe goes inside, the sensor sits outside the door where the Wi-Fi reaches (metal walls can block the signal)." : ""}\n\nThe sensors that work:`;
    }
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
  // With a rotation (3.43.0), whoever is on call in it is texted, as the
  // Team tab and the proof report say: "Everyone on the list is texted"
  // sat under "Right now an alert text goes to Morgan".
  function rotationTexts() {
    const dd = overview();
    if (dd && dd.texted_by_rotation) return true;
    const r = rotationsNow();
    return Boolean(r && r.some((x) => x && x.on_shift_now));
  }
  const TEAM = () => ((onConsole || onToday) && signedIn()
    ? `${textedNow()}To add someone, use **Who gets alerts** in the console (the **Team** tab): their name and mobile number. ${rotationTexts()
      ? "With your rotation, whoever is on call in it is texted when a fridge stays too warm (anyone in no rotation always is)"
      : "Everyone on the list is texted when a fridge stays too warm"}, and on plans with calls, phoned in the order you set. Then press **Send a test alert** so they know what one looks like.\n\n[Add someone](${TEAM_LINK})`
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
      ? ["No lock-in: you pay by the month, or by the year if you choose, and can leave any time. A signed term sets your price and renews by itself, but cancelling still stops the bills, with no fee for leaving early."] : []),
    "In the console: **Money** tab, **Pause or cancel**, then **Cancel my plan**. One press, never hidden behind another screen. You can pause for 1 to 3 months instead.",
    fig(F.cancelRule, "Cancel any time. Nothing more is billed after you cancel, and the service carries on to the end of the month or year you have already paid for. What you have already paid is not refunded, in part or in full."),
    "A free trial has nothing to cancel: it ends by itself, and nothing moves you to a paid plan unless you choose one.",
  ].join("\n\n");
  // "Is my data safe?" (3.33.0): the privacy statement in a few lines.
  // "Who can see my temperature readings?" (3.41.0): who, first. It got
  // "Sign in to see your fridges" on the home page.
  const WHO_SEES = /\bwho\b[^?]*\b(?:see|sees|view|access|read|look at|has access)\b|\bwho has (?:my|our) data\b|\b(?:can|could|do|does) (?:you|anyone|anybody|someone|somebody|other people|others|your staff|staff)\b[^?]*\b(?:see|view|access|read)\b[^?]*\b(?:my|our)\b/i;
  const PRIVACY = (ctx) => [
    ...(ctx && typeof ctx.label === "string" && WHO_SEES.test(ctx.label)
      ? [`The people who can sign in to your account see your readings: owners, operators and viewers, listed under **People who can sign in** on the console's **Team** tab. Nobody else gets your readings in bulk. An alert text goes to the people on call, and an insurance pack or a log you print goes to whoever you give it to.\n`] : []),
    `${ctx && typeof ctx.label === "string" && WHO_SEES.test(ctx.label) ? "" : "Yes. "}What we keep: your readings (about machines, not people), the names and mobile numbers of the people on call, and who signs in, with passwords scrambled.`,
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
  const PLACE_NOUN = /^(?:locations?|sites?|restaurants?|stores?|shops?|cafes?|cafés?|kitchens?|branches?|bars?|pharmac(?:y|ies)|bakeries|bakery|outlets?|places?|pizzerias?|venues?|diners?|pubs?)$/i;
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
  // "What if I have 12 fridges in one restaurant?" (3.41.0) is a price
  // too: it got the café's Starter pitch.
  const HAVE_COUNT = /\bwhat if (?:i|we) (?:have|had|got|run|own)\b|\b(?:i|we) (?:have|run|own|operate|got) (?:about |around |over |nearly )?\d+\b|\bwith (?:about |around |over |nearly )?\d+ (?:fridges?|freezers?|units?|sensors?|coolers?|locations?|sites?|restaurants?)\b/i;
  const isQuote = (text) => (PRICE_WORDS.test(String(text || "")) || HAVE_COUNT.test(String(text || ""))) && Boolean(countsIn(text) || perPlace(text));
  function QUOTE(ctx) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const split = perPlace(typed);
    if (split) return splitQuote(split, /pharmac/i.test(typed));
    const c = countsIn(typed);
    // "What's the monthly cost for one location?" (3.42.0): no count of
    // fridges, so both plans, not Growth alone.
    if (!c || (c.units === null && (c.places || 1) === 1 && !c.pharmacy)) return PRICE();
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
  // 3.43.0: a unit, a place or a day asked about ("Was the Old Mill
  // freezer alert on Wednesday handled?", "Who answered the Wednesday
  // alert?"): those alerts only, from the page's own record. They got
  // "No alert is open" and today's alert.
  function weekAnswer(ctx) {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const typed = said(ctx);
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const since = Date.now() - 7 * 24 * 3600 * 1000;
    const all = (dd.incidents || []).filter((i) => i && !i.site_wide).concat((dd.site_wide_alerts || []).filter(Boolean))
      .filter((i) => Date.parse(i.opened_at || "") >= since)
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)));
    const unitOf = (i) => units.find((u) => u.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
    const line = (i) => {
      const u = unitOf(i);
      const name = i.site_wide ? "Several fridges at once" : unitName(u);
      const at = clock(i.opened_at);
      const answered = i.acknowledged_at ? outFor(i.opened_at, i.acknowledged_at) : "";
      const worst = !i.site_wide && i.peak_display != null ? degrees(i.peak_display, u, dd) : "";
      const how = i.state === "resolved" ? `closed${answered ? `, answered in ${answered}` : ""}`
        : i.state === "acknowledged" ? "open, someone is on it" : "open, nobody has answered yet";
      const food = i.product_disposition === "discarded" ? ", food thrown away" : i.product_disposition === "unaffected" ? ", food kept" : "";
      return `- **${name}**${at ? `, ${at}` : ""}${worst ? ` (worst ${worst})` : ""}: ${how}${food}`;
    };
    const scope = typed ? scopeOf(typed, dd) : null;
    if (scope && scope.unknown) return notHere(scope, dd);
    const day = typed ? dayIn(typed) : null;
    const narrowed = Boolean(scope && (scope.units.length || scope.places.length));
    if (narrowed || day) {
      const ids = narrowed ? new Set((scope.units.length ? scope.units : scope.here).map((u) => u.sensor_id)) : null;
      const shown = all.filter((i) => (!ids || ids.has(i.sensor_id)) && (!day || day.test(i.opened_at)));
      const which = !narrowed ? "" : scope.units.length && scope.units.length <= 2
        ? ` on ${scope.units.map((u) => `**${unitName(u)}**`).join(" or ")}${scope.places.length && !scope.units.every((u) => stemsOf(u.location_name).join(" ").startsWith(stemsOf(scope.places[0]).join(" "))) ? ` at ${words(placeWord(scope))}` : ""}`
        : scope.units.length ? ` on the ${scope.units.length} ${scope.byKind ? `${scope.byKind}s` : "units that fit"}${scope.places.length ? ` at **${placeWord(scope)}**` : ""}` : ` at **${placeWord(scope)}**`;
      const when = day ? (day.on ? ` on ${day.word}` : ` ${day.word}`) : " in the last 7 days";
      const who = /\bwho\b/i.test(typed) ? "\n\nPilot doesn't name people: who answered and who closed it are on the alert itself, under **Alerts**." : "";
      const between = /\b(?:warmest|hottest|highest|peak|how (?:warm|cold|hot)|coldest|lowest|max(?:imum)?|top)\b/i.test(typed)
        ? `\n\nEach alert keeps its worst reading. For every reading in between, open the **Log for an inspector** on the ${onConsole ? "" : "console's "}**Proof** tab.` : "";
      if (!shown.length) {
        const rest = all.length ? ` ${all.length === 1 ? "There was 1 alert" : `There were ${all.length} alerts`} on other units or days in the last 7 days: ask **How many alerts this week?**`
          : " Every fridge and freezer stayed in range, or came back before anybody needed to be texted.";
        return `No alert${which}${when}.${rest}${between}`;
      }
      return `**${count(shown.length, "alert")}**${which}${when}:\n${shown.slice(0, 6).map(line).join("\n")}${shown.length > 6 ? `\n- and ${shown.length - 6} more` : ""}${who}${between}`;
    }
    if (!all.length) return "**No alerts in the last 7 days.** Every fridge and freezer stayed in range, or came back before anybody needed to be texted.";
    const open = all.filter((i) => i.state !== "resolved").length;
    return `**${count(all.length, "alert")} in the last 7 days**${open ? `, ${open} still open` : ""}:\n${all.slice(0, 6).map(line).join("\n")}${all.length > 6 ? `\n- and ${all.length - 6} more` : ""}`;
  }
  // "Which is warmest?": one line, the unit warmest for its limit.
  // 3.43.0: within the unit or the place asked about ("the warmest fridge
  // at Harbourside"), never another's.
  function warmestAnswer(ctx) {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const typed = said(ctx);
    const scope = typed ? scopeOf(typed, dd) : null;
    if (scope && scope.unknown) return notHere(scope, dd);
    const pool = scope && scope.units.length ? scope.units : scope && scope.places.length ? scope.here : (Array.isArray(dd.sensors) ? dd.sensors : []);
    const units = pool.filter((u) => Number.isFinite(Number(u.last_temperature_display))
      && u.last_temperature_display !== null && stateOf(u).state !== "offline");
    if (!units.length) return "None of your fridges or freezers has sent a reading yet.";
    const margin = (u) => Number(u.last_temperature_display) - (Number.isFinite(Number(u.danger_above_display)) && u.danger_above_display !== null
      ? Number(u.danger_above_display) : 1e6);
    const top = units.slice().sort((a, b) => margin(b) - margin(a))[0];
    const now = degrees(top.last_temperature_display, top, dd);
    const limit = limitDegrees(top.danger_above_display, top, dd);
    const gap = Math.round(Math.abs(margin(top)) * 10) / 10;
    const where = !limit ? "" : margin(top) > 0 ? `, ${gap.toFixed(1)}° over its ${limit} limit` : `, ${gap.toFixed(1)}° under its ${limit} limit`;
    const among = scope && scope.places.length ? ` at ${words(placeWord(scope))}` : scope && scope.units.length > 1 ? ` of the ${scope.units.length} you asked about` : "";
    return `**${unitName(top)}** is the warmest${among} for its limit: ${now}${where}.`;
  }

  // ---------------------------------------------------------------- 3.40.0
  //
  // Hello, what Kryozyn is, the limits, a reading typed in, the phone, the
  // texts, calls left, a cheap alarm, how long it takes, the next step,
  // the trial's end, the food kept, the forecast, each location's count
  // and the sensor key. Each from the page's own data where it has it,
  // else from the figures on Pilot's script tag; nothing is fetched.
  const own = () => (ownPage && signedIn() ? overview() : null);
  function helloAnswer() {
    if (ownPage && signedIn()) {
      return `Hi. Ask me about your fridges and freezers: **Which fridges need me?**, **Is the food still OK?**, who is on call, or what you pay. I answer from this page, with no AI key.`;
    }
    return `Hi. I'm Pilot. I can tell you what ${F.appShort} costs, which sensors work, and what happens when a fridge gets warm, with no AI key. [See it live](/demo) with pretend fridges.`;
  }
  const WHAT = () => [
    `${F.appShort} watches the temperature of your fridges and freezers, day and night. A wireless sensor in each one sends its readings to us; when one stays too warm past its wait, everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds, and on plans with calls, phoned if nobody answers.`,
    "It also tells you when a sensor goes quiet, and keeps the record an inspector or an insurer asks for: the log for an inspector and the insurance pack.",
    ownPage && signedIn() ? "Ask **Which fridges need me?** for yours now." : `From $${fig(F.fromPrice, "59")} a month. [See it live](/demo) · [Start a free trial](/signup)`,
  ].join("\n\n");
  // The limits a food fridge and freezer start on (domain._FOOD_KINDS).
  const LIMITS = () => `**A fridge: under ${fig(F.fridgeLimit, "41")}°F. A freezer: under ${fig(F.freezerLimit, "10")}°F.** Those are the limits ${F.appShort} starts a food fridge and freezer on, and it alerts when one stays over its limit past its wait (${fig(F.alertAfterFridge, "20")} minutes for a fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer).\n\nYour food safety plan or local code may ask for a tighter one: set it per unit with **Change limit** on its card${onConsole ? "" : " in the console"}.`;
  // "My freezer is at 15 degrees": that kind's limit, and what to do.
  const READING_RE = /\b(fridges?|freezers?|coolers?|walk-?ins?|refrigerators?|chillers?)\b[^?\d-]*?\b(?:is|was|at|reads?|reading|says|shows?|showing|sitting at|sits at)\s+(?:at\s+|about\s+|around\s+)?(-?\d{1,3}(?:\.\d+)?)(?!\s*(?:locations?|sites?|restaurants?|stores?|places?|pharmac|branch|kitchens?|shops?|cafes?|bars?|of\b))\s*(°\s*[cf]?|degrees?(?:\s*[cf]\b|\s*(?:celsius|fahrenheit))?|deg\b|[cf]\b)?/i;
  // "Is 38 ok for a fridge?", "is 5 degrees too warm for a freezer?"
  // (3.42.0): the number first, then the kind.
  const READING_FOR = /\b(?:is|are|isn'?t)\s+(-?\d{1,3}(?:\.\d+)?)\s*(°\s*[cf]?|degrees?(?:\s*[cf]\b|\s*(?:celsius|fahrenheit))?|deg\b|[cf]\b)?\s*(?:ok|okay|fine|safe|alright|too (?:warm|hot|cold|high|low)|warm|cold|bad|good|normal|high|low)\b[^?]*?\b(?:for|in) (?:a |an |the |my |our )?(fridges?|freezers?|coolers?|walk-?ins?|refrigerators?|chillers?)\b/i;
  function readingAnswer(ctx) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const f = READING_FOR.exec(typed);
    const m = READING_RE.exec(typed) || (f ? [f[0], f[3], f[1], f[2]] : null);
    if (!m) return LIMITS();
    const freezer = /freez/i.test(m[1]);
    const dd = own();
    const celsius = /c|celsius/i.test(m[3] || "") || (!/f|fahrenheit/i.test(m[3] || "") && dd && dd.temperature_unit === "C");
    const limitF = Number(freezer ? fig(F.freezerLimit, "10") : fig(F.fridgeLimit, "41"));
    const limit = celsius ? Math.round(((limitF - 32) * 5) / 9) : limitF;
    const u = celsius ? "C" : "F";
    const at = Number(m[2]);
    const what = freezer ? "a freezer" : "a fridge";
    if (at <= limit) {
      return `**${m[2]}°${u} is under ${what}'s ${limit}°${u} limit**, so that's fine: ${F.appShort} alerts only when one stays over its limit. If yours has a limit of its own, it is on its card.`;
    }
    const over = Math.round((at - limit) * 10) / 10;
    const wait = freezer ? fig(F.alertAfterFreezer, "30") : fig(F.alertAfterFridge, "20");
    const once = Number(fig(F.alertAtOnceDegrees, "15")) * (celsius ? 5 / 9 : 1);
    return [
      `**${m[2]}°${u} is ${over}° over ${what}'s ${limit}°${u} limit.** Check the door is shut and sealing and the unit is running, with nothing warm just put in and nothing blocking the vent.`,
      `If it stays over for ${wait} minutes, everyone on call is texted${over >= once ? "; this far over, it alerts at once" : ""}. If it doesn't come back down, move the food somewhere cold and write down what you did: that note is what an inspector or an insurer asks for.`,
      "Ask **Is the food still OK?** for the rule of thumb on the food.",
    ].join("\n\n");
  }
  // "Can I see it on my phone?": the console works in a phone's browser.
  const MOBILE = () => `Yes. The console and the Simple view (Today) work in your phone's browser: [sign in](/console) there. It installs to your home screen too (on an iPhone: **Share**, then **Add to Home Screen**; on Android: the browser's menu, then **Install app**) and opens full screen, like an app.\n\nAlerts come by text${F.callAfterMinutes ? ", and on plans with calls by phone," : ""} whether it is open or not.`;
  // "Turn texts on": who gets them is the business's; the text service is
  // the server's (the console's banner says so for whoever runs it).
  const TEXTS_ON = () => [
    `Alert texts go to everyone under **Who gets alerts** on the console's **Team** tab who isn't muted: add a name and mobile number, or press **Unmute** beside someone. Then **Send a test alert** to check they arrive.${(onConsole || onToday) && signedIn() ? ` [Open Who gets alerts](${TEAM_LINK})` : ""}`,
    "If the page says **Texts are not switched on for this server**, that switch isn't in the console: the server's text service hasn't been set up by whoever runs it. Until it is, alerts show on screen but nobody is texted.",
  ].join("\n\n");
  // "How many phone calls do I have left?": the plan's own words.
  function callsLeftAnswer() {
    const dd = own();
    const calls = dd && dd.entitlements && dd.entitlements.calls;
    if (calls && calls.mode === "plan") {
      return "Your plan includes phone calls, so there's no number of calls to use up: if nobody answers the alert text, we phone the people on call until somebody does. There is a daily cap on calls, so a faulty sensor can't run up a bill: nothing for you to set.";
    }
    if (calls && calls.words) return words(calls.words);
    if (ownPage && signedIn() && !dd) return NOT_LOADED;
    return `Signed in, I can tell you from your plan. The free trial includes ${fig(F.trialCalls, "5")} alert calls; Growth and Enterprise include phone calls; Starter sends texts only. [Compare plans](/pricing)`;
  }
  // "How is this different from a cheap thermometer alarm?"
  const VERSUS = () => [
    "A thermometer alarm beeps in the kitchen, so it only helps if somebody is there to hear it. With " + F.appShort + ":",
    `- Everyone on call is texted within ${fig(F.textWithinSeconds, "60")} seconds, wherever they are, and on plans with calls phoned until somebody answers.`,
    `- A door opened for a minute wakes nobody: a unit has to stay out of range for its wait (${fig(F.alertAfterFridge, "20")} minutes for a fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer).`,
    `- A sensor that goes quiet (power, Wi-Fi, a flat battery) is a text too, after ${fig(F.offlineMinutes, "30")} minutes.`,
    "- Every reading is kept: the log for an inspector and the insurance pack are a button, not a clipboard.",
  ].join("\n");
  // "How long does setup take?": the sign-up page's five minutes.
  const SETUP_TIME = () => [
    `About five minutes for the first fridge, once the sensor is in your hands: ${ownPage && signedIn() ? "" : "signing up takes about a minute with no card, then "}**Add a fridge or freezer** in the console, and follow your brand's few steps with the key filled in. **Connect your sensor** ticks when its first reading lands.`,
    "Each one after that is the same few steps. No electrician and no coding: the sensors are wireless.",
    ownPage && signedIn() ? `[Connect your sensor](${CONNECT})` : "[Step-by-step guides](/help/sensors) · [Start a free trial](/signup)",
  ].join("\n\n");
  // "What do I do next?": Getting started's next step, from the overview.
  function nextAnswer() {
    if (!(ownPage && signedIn())) {
      return `[Start a free trial](/signup) (about a minute, no card), then **Add a fridge or freezer** in the console and connect its sensor. Getting started in the console lists the rest: who gets alerts, the first reading and a test alert.`;
    }
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const fd = dd.first_day || {};
    const steps = Array.isArray(fd.steps) ? fd.steps : [];
    const next = steps.find((st) => st && !st.done);
    if (!steps.length) return `**Getting started** is on the console's first screen, with each step and a tick once it's done.${onConsole ? "" : " [Open the console](/console?view=full)"}`;
    if (next) {
      return `Next under **Getting started** (${count(fd.done || 0, "step")} of ${fd.total || steps.length} done): **${words(next.label)}**. ${words(next.detail)}${onConsole ? "" : " It's on the console's first screen. [Open the console](/console?view=full)"}`;
    }
    const live = liveAlerts(dd);
    if (live.some((i) => i.state === "open")) return `Getting started is done. Right now an alert needs somebody: press ${ON_IT}.`;
    return "Getting started is done: every step is ticked. Nothing else needs setting up; alerts go out on their own. Worth a look now and then: **Which fridges need me?** and the log for an inspector.";
  }
  // "When does my trial end?": the licence's own date.
  function trialEndAnswer() {
    const dd = own();
    if (!dd) return TRIAL();
    const acct = dd.account || {};
    if (!(acct.on_trial || (dd.subscription && dd.subscription.plan === "trial"))) {
      return `You're not on the free trial: you're on ${words((dd.subscription || {}).plan_name) || "a paid plan"}. Your plan is on the console's **Money** tab.`;
    }
    const day = W() && typeof W().date === "function" ? W().date(acct.expires_at) : String(acct.expires_at || "").slice(0, 10);
    const left = Number(acct.days_to_expiry);
    const lead = Number.isFinite(left) && left >= 0
      ? `**Your free trial ends${day ? ` on ${day}` : ""}: ${count(left, "day")} left.**` : `**Your free trial ends${day ? ` on ${day}` : " soon"}.**`;
    return `${lead} Nothing is billed during it, and nothing moves you to a paid plan by itself: choose one on the console's **Money** tab to keep the alerts going after it.`;
  }
  // "How much has this saved me?": the food kept figure, as the Money tab
  // and /today show it.
  function savedAnswer() {
    const dd = own();
    if (!dd) {
      return ownPage && signedIn() ? NOT_LOADED
        : `Once you're signed in, the console's **Money** tab and the Simple view count the food kept: each closed alert with the food recorded as kept, at an estimate of what a typical fridge or freezer of each kind holds. [See it live](/demo)`;
    }
    const roi = dd.roi || {};
    const month = dd.this_month || {};
    const usd0 = (v) => `$${Math.round(Number(v)).toLocaleString("en-US")}`;
    if (roi.loss_avoided_usd) {
      return `**About ${usd0(roi.loss_avoided_usd)} of food kept in the last ${roi.period_days || 30} days**: ${count(roi.quantified_saves, "closed alert")} with the food recorded as kept. It is an estimate of what a typical fridge or freezer of each kind holds${roi.return_multiple ? `, ${roi.return_multiple}× the subscription` : ""}.`;
    }
    if (month.food_usd) return `**About ${usd0(month.food_usd)} of food kept this month**: an estimate of what a typical fridge or freezer of each kind holds.`;
    return "Nothing counted yet: the figure is the food recorded as **kept** on closed alerts, and there hasn't been one in the last 30 days. No alert is a good month.";
  }
  // "Is anything about to go wrong?": the forecast's own figure on each
  // unit (hours_until_breach), as /today's "Heading the wrong way".
  const SOON_HOURS = 6;
  function forecastAnswer() {
    const dd = own();
    const what = `On plans with the forecast, ${F.appShort} fits a trend line to each unit's recent readings and warns when one is drifting toward its limit, before it gets there.`;
    if (!dd) return ownPage && signedIn() ? NOT_LOADED : `${what} It comes with Growth. [Compare plans](/pricing)`;
    if (dd.entitlements && dd.entitlements.predictive_forecasting === false) return `${what} Your plan doesn't include it; Growth does. Alerts still go out the moment a unit stays out of range.`;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const out = units.filter((u) => stateOf(u).state in URGENCY && stateOf(u).state !== "offline" && stateOf(u).state !== "waiting_first");
    const soon = units.filter((u) => !out.includes(u) && !["offline", "waiting_first"].includes(stateOf(u).state) && u.hours_until_breach != null && Number(u.hours_until_breach) <= SOON_HOURS)
      .sort((a, b) => Number(a.hours_until_breach) - Number(b.hours_until_breach));
    const hours = (h) => (h < 1 ? "within the hour" : `in about ${count(Math.round(h), "hour")}`);
    const lines = [soon.length
      ? `**Heading the wrong way:**\n${soon.slice(0, 5).map((u) => `- **${unitName(u)}** ${degrees(u.last_temperature_display, u, dd)}: at this rate it passes its limit ${hours(Number(u.hours_until_breach))}`).join("\n")}`
      : `**Nothing is heading for trouble:** no unit is drifting toward its limit fast enough to pass it in the next ${SOON_HOURS} hours.`];
    if (out.length) lines.push(`${out.length === 1 ? `**${unitName(out[0])}** is` : `${out.length} units are`} already out of range: that's an alert, not a forecast. Ask **Which fridges need me?**`);
    return lines.join("\n\n");
  }
  // "How many fridges at Old Mill?" / "at each location": the page's own
  // count. A site is named by the words its name doesn't share with the
  // others ("Old Mill" in "Juniper Old Mill").
  function sitesAnswer(ctx) {
    const dd = overview();
    if (!dd) return NOT_LOADED;
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const asked = wordsIn(typed);
    const names = [...new Set(units.map((u) => u.site_name).filter(Boolean))];
    (Array.isArray(dd.sites) ? dd.sites : []).forEach((x) => { if (x && x.name && !names.includes(x.name)) names.push(x.name); });
    const counts = names.map((name) => ({ name, n: units.filter((u) => u.site_name === name).length }));
    const shared = (w) => names.length > 1 && names.every((nm) => wordsIn(nm).includes(w));
    const named = counts.filter((c) => wordsIn(c.name).some((w) => !shared(w) && asked.includes(w)));
    const noun = (n) => (n === 1 ? "fridge or freezer" : "fridges and freezers");
    if (named.length) return named.map((c) => `**${words(c.name)}**: ${c.n} ${noun(c.n)}.`).join("\n");
    if (!counts.length) return `**${units.length} ${noun(units.length)}**, all at one location.`;
    const loose = units.filter((u) => !u.site_name).length;
    return `**${units.length} ${noun(units.length)} across ${count(counts.length, "location")}:**\n${counts.map((c) => `- ${words(c.name)}: ${c.n}`).join("\n")}${loose ? `\n- Not at a location yet: ${loose}` : ""}`;
  }
  // "What is a sensor key?"
  const SENSOR_KEY = () => `A sensor key is the password one sensor uses to send its readings to ${F.appShort}. Each fridge or freezer gets its own when you add it, shown once: **Connect your sensor** fills it in for your brand, and it can only send readings for that one unit. Lost it? Press **New key** on the unit's card: a new one is made and the old one stops working. It is not the key for other software (under Advanced), which speaks for the whole account.`;

  // ---------------------------------------------------------------- 3.41.0
  //
  // After the trial, a second location, the alert wait, taking someone
  // off the account, and a new phone number: each from what the console
  // really has, with no key.
  // "What happens after the trial?": the licence's grace (auth.LICENCE_GRACE_DAYS).
  function trialAfterAnswer() {
    const dd = own();
    const acct = (dd && dd.account) || {};
    const onIt = Boolean(dd && (acct.on_trial || (dd.subscription && dd.subscription.plan === "trial")));
    if (dd && !onIt) return `You're past the free trial: you're on ${words((dd.subscription || {}).plan_name) || "a paid plan"}. Your plan and bills are on the console's **Money** tab.`;
    const day = onIt && acct.expires_at ? dayWords(acct.expires_at) : "";
    const grace = fig(F.graceDays, "14");
    return [
      `${day ? `Your trial ends on **${day}**. ` : ""}When it ends, nothing is billed and nothing moves you to a paid plan by itself.`,
      `Your fridges are still watched, alerts and all, for ${grace} more days while you decide. After that, alerts stop until you choose a plan. Nothing is deleted: the readings, the alerts and the record stay.`,
      `To carry on, choose a plan on the console's **Money** tab: Starter, $${fig(F.fromPrice, "59")} a month, for one café or shop with up to ${fig(F.starterUnits, "3")} fridges or freezers; or Growth, $${fig(F.restaurantPrice, "149")} a month a location, with phone calls.${ownPage && signedIn() ? "" : " [Start a free trial](/signup)"}`,
    ].join("\n\n");
  }
  // "Can I add a second location?": Your locations (Setup), and what it
  // costs on each plan (sites.py: Starter covers one, and asks first).
  function addSiteAnswer() {
    const dd = own();
    const plan = dd && dd.subscription ? dd.subscription.plan : "";
    const how = `Yes. On the console's **Setup** tab, open **Your locations**, type the new one's name and press **Add site**.`;
    const price = `On Growth a restaurant pays $${fig(F.restaurantPrice, "149")} a month a location, for up to ${fig(F.unitsPerLocation, "10")} fridges and freezers there; each one past that is $${fig(F.extraUnit, "15")} a month.`;
    if (plan === "starter") return `${how} Starter covers one location: a second asks you to move to Growth first, and nothing is charged for typing a name. ${price}`;
    if (plan === "trial") return `${how} The free trial takes as many locations as you like, with up to ${fig(F.trialUnits, "5")} sensors in all. After it: ${price}`;
    if (dd) return `${how} ${price}${dd.sites && dd.sites.length ? ` You have ${count(dd.sites.length, "location")} now.` : ""}`;
    return `${how.replace("Yes. On the console's", "Yes. Once you've signed up, on the console's")} ${price} Starter, $${fig(F.fromPrice, "59")} a month, covers one location. [Work out your price](/pricing#calc-title)`;
  }
  // "Can I change the alert wait to 30 minutes?": the unit card's Alert wait.
  function waitAnswer(ctx) {
    const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
    const mins = (/\b(\d{1,4})\s*(?:min(?:ute)?s?|m)\b/i.exec(typed) || [])[1];
    const pick = mins ? `pick **${mins} min**${[0, 5, 10, 20, 30, 60].includes(Number(mins)) ? "" : " (or type it)"}` : "pick a time or type any number of minutes";
    return [
      `Yes. Press **Alert wait** on the unit's card${onConsole ? "" : " in the console"} (the **Fridges** tab), ${pick}, and press **Save**. Each unit has its own wait: a food fridge starts at ${fig(F.alertAfterFridge, "20")} minutes and a freezer at ${fig(F.alertAfterFreezer, "30")}, and **Use the usual** puts it back.`,
      `However long the wait, a reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past the limit is sent at once. An owner or operator can change it; Pilot doesn't change it for you.`,
    ].join("\n\n");
  }
  // "Remove Theo from the account": both places, on the Team tab.
  const PEOPLE_HERE = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#people`;
  function personNamed(text) {
    const m = /\b(?:remove|delete|kick out|get rid of|take|disable|revoke|block|stop)\s+([A-Z][a-z]+)\b|\b([A-Z][a-z]+)(?:'s|’s)\s+(?:phone|mobile|cell|number)\b/.exec(String(text || ""));
    const name = m ? (m[1] || m[2]) : "";
    return /^(?:The|A|An|My|Our|This|That|It|Him|Her|Them|Someone|Somebody|Pilot)$/.test(name) || name === F.appShort ? "" : words(name);
  }
  function removeAnswer(ctx) {
    const who = personNamed(ctx && ctx.label) || "them";
    const owner = ownPage && signedIn();
    return [
      `Two places, both on the ${owner ? "" : "console's "}**Team** tab:`,
      `- To stop alert texts and calls to ${who}: press **Remove** beside the name under **Who gets alerts**.`,
      `- To stop ${who} signing in: press **Remove** beside the name under **People who can sign in** (an owner can). The sign-in stops at once; what they did stays in the record.`,
      "",
      `Pilot doesn't remove people for you.${owner ? ` [Open People who can sign in](${PEOPLE_HERE})` : ""}`,
    ].join("\n");
  }
  // "How do I change Priya's phone number?": Change number, on Who gets alerts.
  function numberAnswer(ctx) {
    const who = personNamed(ctx && ctx.label);
    return `Under **Who gets alerts** on the console's **Team** tab, press **Change number** beside ${who || "the name"}, type the new mobile number with its country code, and press OK. ${who || "They"} keep${who ? "s" : ""} the same place in the call order. Then press **Send a test alert** to check it arrives.${ownPage && signedIn() ? ` [Open Who gets alerts](${TEAM_LINK})` : ""}`;
  }

  // ---------------------------------------------------------------- 3.42.0
  //
  // Quiet hours, recording the food, fees, how long data is kept, how
  // fast a text comes, paying yearly, HACCP, a sensor with no readings,
  // another sign-in, removing a unit and a flat battery: each from what
  // the console and the plan figures really have, with no key.
  const said = (ctx) => (ctx && typeof ctx.label === "string" ? ctx.label : "");
  // "How do I stop getting texts at 3am?": what exists (oncall.py, the
  // Team tab), never "Yes, on plans with calls".
  const QUIET = () => [
    `Three ways, all on the ${onConsole ? "" : "console's "}**Team** tab:`,
    "- **Quiet hours** (under **On call now**): a person and the hours they are quiet, for example 11 PM to 6 AM. In them they are texted only if nobody else would be, and phoned last. An alert always reaches someone.",
    "- **A rotation** (also under **On call now**): people take turns by the day or the week, so only whoever is on call is texted. Anyone in no rotation is always on call.",
    "- **Mute** beside a name under **Who gets alerts**: no texts or calls to that person until you press **Unmute**.",
    "",
    `A door opened for a minute texts nobody: a fridge has to stay out of range for its wait first.${(onConsole || onToday) && signedIn() ? ` [Open On call now](${TEAM_LINK})` : ""}`,
  ].join("\n");
  // "I threw the food away, how do I record that?": the alert's own
  // buttons (console.js foodAsk), and the closed alert waiting for them.
  function foodRecordAnswer() {
    const dd = ownPage && signedIn() ? overview() : null;
    const units = dd && Array.isArray(dd.sensors) ? dd.sensors : [];
    const waiting = dd ? (dd.incidents || []).filter((i) => i && i.state === "resolved" && !i.site_wide && i.food_question && i.food_question.ask_on_today) : [];
    const where = onToday ? "on the alert at the top of the page" : "on the alert (the **Alerts** tab, or at the top of the page)";
    const lines = [`When an alert closes, it asks one thing ${where}: **Food kept** or **Thrown away**. Press **Thrown away**, then **Add what was thrown away** for a line saying what went in the bin: it goes in the record and in the insurance pack.`];
    if (waiting.length) {
      const u = units.find((x) => x.sensor_id === waiting[0].sensor_id) || { sensor_id: waiting[0].sensor_id };
      lines.push(`**${unitName(u)}** is waiting for that answer now.`);
    } else if (dd && liveAlerts(dd).length) {
      lines.push("An alert is still open: press **It's fixed** once the unit is back in range, and the question comes up when it closes.");
    }
    lines.push(`Pressed the wrong one? Change it on the same alert.${ownPage && signedIn() ? "" : " [See a sample pack](/sample-insurance-pack)"}`);
    return lines.join("\n\n");
  }
  // "Is there a setup fee?", "any hidden fees?" (pricing.SETUP_FEE_PER_SITE_USD).
  const FEES = () => {
    const fee = Number(fig(F.setupFee, "0"));
    return [
      `**${fee ? `A one-off setup fee of $${fee} a location, and no hidden fees.` : "No setup fee, and no hidden fees."}** You pay the plan's monthly price: Starter $${fig(F.fromPrice, "59")} a month for one café or shop with up to ${fig(F.starterUnits, "3")} fridges or freezers, or Growth $${fig(F.restaurantPrice, "149")} a month a location, up to ${fig(F.unitsPerLocation, "10")} there and $${fig(F.extraUnit, "15")} a month for each one past that.`,
      "Alert texts are included, and so are the phone calls on plans with calls. The extras on the pricing page cost only if you switch them on, and a signed term's price rises 5% a year after its first year. Sensors are bought separately, from their makers.",
      withTrial("", "[See all prices](/pricing)"),
    ].filter(Boolean).join("\n\n");
  };
  // "How long do you keep my data?", "What happens to my data if I
  // cancel?": the privacy statement's "How long we keep things".
  const LEAVING = /\bcancel|\bleav(?:e|ing)\b|\bstop (?:paying|using)\b|\bquit\b|\bclose (?:my|our|the) account\b|\bdelete (?:my|our|the) account\b|\bafter (?:i|we)\b/i;
  const RETENTION = (ctx) => {
    const months = fig(F.keepReadingsMonths, "13"), days = fig(F.keepDetailDays, "30");
    const leave = `**If you cancel**, nothing is deleted: the service runs to the end of what you have paid for, and the record stays. An owner can press **Download everything** for one file with every record, or **Delete everything**, on the console's **Setup** tab. Bills already sent are kept, as the law asks.`;
    const lines = [
      `**Readings: ${months} months.** Every reading is kept in full for ${days} days, then each sensor's lowest, highest and average for each hour until it is ${months} months old. The readings around an alert are kept with the alert, in full.`,
      "**Alerts, what was done about them, bills and who did what** are kept while your account is open, for an inspector or an insurer.",
      leave,
      "[How long we keep things](/legal#privacy)",
    ];
    if (LEAVING.test(said(ctx))) lines.unshift(lines.splice(2, 1)[0]);
    return lines.join("\n\n");
  };
  // "How quickly will I get a text?" (domain.DISPATCH_SLA_SECONDS).
  const SPEED = () => `**Within ${fig(F.textWithinSeconds, "60")} seconds of the alert opening**, everyone on call is texted. The alert opens once a unit has stayed out of range for its wait (${fig(F.alertAfterFridge, "20")} minutes for a fridge, ${fig(F.alertAfterFreezer, "30")} for a freezer), so a door opened for a minute wakes nobody; a reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past the limit alerts at once.\n\nOn plans with calls, if nobody answers within ${fig(F.callAfterMinutes, "10")} minutes, we phone. A sensor that goes quiet is a text after ${fig(F.offlineMinutes, "30")} minutes.`;
  // "Is there a yearly discount?" (pricing.yearly_terms).
  const YEARLY = () => {
    const paid = Number(fig(F.yearlyCharged, "10")), covered = Number(fig(F.yearlyCovered, "12"));
    const year = (v) => `$${(Number(v) * paid).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    return [
      `**Yes: pay yearly and you get ${covered} months for the price of ${paid} (${count(covered - paid, "month")} free).** Starter is then ${year(fig(F.fromPrice, "59"))} a year, and Growth ${year(fig(F.restaurantPrice, "149"))} a year a location; a chain's discount comes off first.`,
      `Tick **Pay yearly** when you choose your plan on the console's **Money** tab. Paying by the month has no lock-in either.${ownPage && signedIn() ? "" : " [See all prices](/pricing)"}`,
    ].join("\n\n");
  };
  // "Is it HACCP compliant?": what it records, never a certificate.
  const HACCP = () => [
    `${F.appShort} keeps the temperature side of a HACCP plan: every reading, each time a unit went out of range and for how long, who was texted and who answered, what was done with the food (**Food kept** or **Thrown away**, with a note) and who reviewed it.`,
    `The **Log for an inspector** (the console's **Proof** tab) prints it for the dates you pick. It is a record, not a certificate, and it doesn't write your plan: your HACCP plan sets the limits, and **Change limit** on each unit's card matches them.${ownPage && signedIn() ? ` [Open the log for the last 7 days](${LOG_HERE})` : " [Start a free trial](/signup)"}`,
  ].join("\n\n");
  // "My sensor isn't showing readings": waiting for its first, or quiet.
  function noReadingsAnswer() {
    const dd = ownPage && signedIn() ? overview() : null;
    const units = dd && Array.isArray(dd.sensors) ? dd.sensors : [];
    const waiting = units.filter((u) => stateOf(u).state === "waiting_first");
    const quiet = units.filter((u) => stateOf(u).state === "offline");
    const lines = [];
    if (waiting.length) lines.push(`Waiting for a first reading: ${listed(waiting.slice(0, 4).map((u) => `**${unitName(u)}**`))}.`);
    if (quiet.length) lines.push(`No signal now: ${listed(quiet.slice(0, 4).map((u) => `**${unitName(u)}**`))}.`);
    if (dd && !waiting.length && !quiet.length && units.length) lines.push("Every sensor here is sending readings right now.");
    lines.push([
      "A new sensor shows **Waiting for its first reading** until one arrives. Check, in order:",
      "1. It is switched on and on your Wi-Fi (or its gateway is), as its maker says.",
      "2. It sends to the address and sensor key **Connect your sensor** showed for that unit. Each unit has its own key; **New key** on its card makes another.",
      "3. Give it a few minutes: some sensors send only every 5 or 10 minutes.",
    ].join("\n"));
    lines.push(`A sensor that sent before and stopped shows **No signal**: [what to check](/help#sensor-offline).${onConsole || onToday ? ` [Connect your sensor](${CONNECT})` : " [Step-by-step guides](/help/sensors)"}`);
    return lines.join("\n\n");
  }
  // "Can my manager have their own login?": People who can sign in
  // (accounts.invite_user), and a join code for an existing sign-in.
  const ADD_LOGIN = () => [
    `An owner adds them under **People who can sign in** on the ${onConsole ? "" : "console's "}**Team** tab: **Add someone**, with their name, email, a first password you give them, and a role.`,
    "- **Viewer**: sees everything, changes nothing.",
    "- **Operator**: also answers alerts, adds units and changes limits.",
    "- **Owner**: also the plan, the bills and who can sign in.",
    "",
    `They choose their own password when they first sign in. Someone who already has a ${F.appShort} sign-in joins with a join code instead: press the business name at the top of the console, then **Make a join code**. Alert texts are separate: that is **Who gets alerts**.${ownPage && signedIn() ? ` [Open People who can sign in](${PEOPLE_HERE})` : ""}`,
  ].join("\n");
  // "How do I remove a fridge?": the unit card's Remove.
  const REMOVE_UNIT = () => `Press **Remove** on the unit's card on the console's **Fridges** tab (an owner or operator can). It asks first, then its readings are deleted and its place on your plan is freed; an alert still open on it is closed. Pilot doesn't remove units for you.`;
  // "Make a proof report" (3.42.0): the proof of monitoring report
  // (proofreports.py), not the insurance pack after a loss.
  const PROOF_HERE = `${onConsole ? window.location.pathname + (window.location.search || "") : "/console?view=full"}#proof-report`;
  const PROOF = () => [
    `An owner makes it on the ${onConsole ? "" : "console's "}**Proof** tab, under **Proof of monitoring**: choose the location (or every location) and the month, press **Make the report**, then **Open** it to print or save as a PDF.`,
    "It shows your insurer how much of the month each sensor was watched and online, every temperature alert and how fast it was answered (by role, never a name), the longest gap in data, and who gets alerted. It carries a code and a link the insurer can check at /verify; nobody sees it until you share the link, and you can switch the link off.",
    `After a loss, the **insurance pack** is the one for a claim.${ownPage && signedIn() ? ` [Open Proof of monitoring](${PROOF_HERE})` : " [Start a free trial](/signup)"}`,
  ].join("\n\n");
  // "The sensor battery is dying" (records.Sensor.battery_low: 20% or less).
  function batteryAnswer() {
    const dd = ownPage && signedIn() ? overview() : null;
    const low = dd && Array.isArray(dd.sensors) ? dd.sensors.filter((u) => u && u.battery_low) : [];
    const lines = [];
    if (low.length) lines.push(`Low now: ${listed(low.slice(0, 4).map((u) => `**${unitName(u)}**${u.battery_percent != null ? ` (${Math.round(Number(u.battery_percent))}%)` : ""}`))}. Change ${low.length === 1 ? "its battery" : "their batteries"} in the next week or so.`);
    else if (dd) lines.push("No sensor here is reporting a low battery right now.");
    lines.push(`Most wireless sensors run on batteries; how long one lasts depends on the model and how often it sends, so check its maker's figure. A sensor that reports its battery shows **battery %** on its card, and at 20% or less the console counts it under **Flat batteries** and the Simple view says to change it.`);
    lines.push(`If a battery dies anyway, the sensor goes quiet, and after ${fig(F.offlineMinutes, "30")} minutes everyone on call is texted that it has, so a flat battery never passes unnoticed.`);
    return lines.join("\n\n");
  }

  // ---------------------------------------------------------------- 3.43.0
  //
  // A unit or a place named is that one, never another's numbers ("Warmest
  // the Harbourside walk-in got" gave the bakery fridge); the past is the
  // 7-day history, never what is open now; a unit being defrosted or
  // cleaned; why no call came; staff with no training; alerts by email.
  //
  // The places a question names, from the business's own list (its sites
  // and each unit's site): "Old Mill", "Harbourside".
  function placesIn(text, dd) {
    const asked = stemsOf(text);
    const has = (seq) => seq.length > 0 && asked.some((_, k) => seq.every((w, j) => asked[k + j] === w));
    const names = [];
    (Array.isArray(dd.sites) ? dd.sites : []).forEach((x) => { if (x && x.name) names.push(String(x.name)); });
    (Array.isArray(dd.sensors) ? dd.sensors : []).forEach((u) => { if (u && u.site_name) names.push(String(u.site_name)); });
    return [...new Set(names)].filter((n) => has(stemsOf(n)));
  }
  const atPlace = (u, place) => sameLabel(u.site_name, place)
    || stemsOf(u.location_name).join(" ").startsWith(`${stemsOf(place).join(" ")} `);
  // Words before "fridge" that say which one without naming it.
  const NOT_A_NAME = new Set(["all", "any", "every", "each", "other", "new", "main", "big", "small", "large", "both", "two", "three",
    "which", "what", "whose", "this", "that", "these", "those", "your", "their", "his", "her", "its", "food", "first", "second",
    "third", "last", "one", "ones", "different", "same", "warmest", "coldest", "hottest", "warm", "cold", "hot", "broken", "faulty",
    "spare", "extra", "another", "only", "whole", "entire", "commercial", "kitchen", "restaurant", "own", "single", "my", "our",
    "the", "is", "are", "of", "in", "at", "for", "a", "an", "to", "and", "or", "with", "so", "too", "very", "still", "ok", "okay",
    "fine", "good", "bad", "it", "go", "gone", "get", "got", "how", "how's", "hows", "s"]);
  const UNIT_NOUNS = "fridges?|freezers?|coolers?|walk-?ins?|chillers?|refrigerators?|displays?|cabinets?";
  // A unit or a place asked for by a name nobody here has ("the sushi
  // fridge", "the Downtown site"): said so, never another unit's numbers.
  function unknownIn(text, dd, placeWords) {
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const known = new Set([].concat(...units.map((u) => wordsIn(`${u.location_name || ""} ${u.site_name || ""}`)), [...placeWords]));
    (Array.isArray(dd.sites) ? dd.sites : []).forEach((x) => wordsIn(x && x.name).forEach((w) => known.add(w)));
    const t = String(text || "");
    const unit = new RegExp(`\\b(?:the|my|our|is|are|at|in)\\s+((?:[A-Za-z][\\w'’-]*\\s+){1,3}?)(${UNIT_NOUNS})\\b`, "i").exec(t);
    if (unit) {
      const said = unit[1].trim().split(/\s+/);
      const telling = said.filter((w) => !NOT_A_NAME.has(w.toLowerCase().replace(/['’]s$/, "")) && !placeWords.has(stemWord(w)));
      const parts = [].concat(...telling.map((w) => wordsIn(w)));
      if (parts.length && !parts.some((w) => known.has(w) || GENERIC.has(w))) return `${telling.join(" ")} ${unit[2].replace(/s$/i, "")}`;
    }
    const place = /\b([A-Za-z][\w'’-]*)\s+(?:site|location|branch|store|restaurant)\b/i.exec(t);
    if (place && !NOT_A_NAME.has(place[1].toLowerCase()) && !known.has(stemWord(place[1]))
      && !/^(?:each|every|per|other|new|second|third|another|main|first|one|which|what|this|that|my|our|the|a|an)$/i.test(place[1])) {
      return `${place[1]} ${/\bsite\b/i.test(place[0]) ? "site" : "location"}`;
    }
    return "";
  }
  // {places, here (the units at them, or all), units (the ones named,
  // within those places, the closest fit), unknown} for a question.
  function scopeOf(text, dd) {
    const units = Array.isArray(dd.sensors) ? dd.sensors : [];
    const places = placesIn(text, dd);
    const placeWords = new Set([].concat(...places.map(stemsOf)));
    const here = places.length ? units.filter((u) => places.some((p) => atPlace(u, p))) : units;
    const asked = wordsIn(text).filter((w) => !placeWords.has(w));
    const own = (u) => wordsIn(u.location_name || "").filter((w) => !placeWords.has(w) && !GENERIC.has(w) && w !== "food"
      && !stemsOf(u.site_name).includes(w));
    const fit = (u) => own(u).filter((w) => asked.includes(w)).length;
    const most = Math.max(0, ...here.map(fit));
    let named = most ? here.filter((u) => fit(u) === most) : [];
    const kind = asked.includes("freezer") ? "freezer" : (asked.includes("fridge") || asked.includes("refrigerator")) ? "fridge" : "";
    // "the Old Mill freezer": the freezers there.
    let byKind = "";
    if (!named.length && places.length && kind) {
      named = here.filter((u) => kindOf(u) === kind || wordsIn(u.location_name || "").includes(kind));
      byKind = named.length ? kind : "";
    }
    // "the walk-in freezer" is the walk-ins called freezer, where some are.
    if (kind && named.some((u) => wordsIn(u.location_name || "").includes(kind))) named = named.filter((u) => wordsIn(u.location_name || "").includes(kind));
    const unknown = named.length ? "" : unknownIn(text, dd, placeWords);
    return { places, here, units: named, unknown, byKind };
  }
  const placeWord = (scope) => scope.places.map(words).join(" or ");
  // "I can't find a unit called “sushi fridge”" with the ones there are.
  function notHere(scope, dd) {
    const units = (scope.places.length ? scope.here : (Array.isArray(dd.sensors) ? dd.sensors : []));
    const names = units.slice(0, 8).map((u) => unitName(u));
    const where = scope.places.length ? ` at **${placeWord(scope)}**` : "";
    return `I can't find a unit or a location called “${words(scope.unknown)}”${where}, so I won't guess with another one's numbers.${
      names.length ? ` ${scope.places.length ? "There" : "Yours"}: ${names.join(", ")}${units.length > names.length ? `, and ${units.length - names.length} more` : ""}.` : ""} Ask **Which fridges need me?** for all of them.`;
  }
  // A day asked about: {word, on, test(iso)}, or null.
  function dayIn(text) {
    const lower = String(text || "").toLowerCase();
    const day = DAYS.findIndex((d) => new RegExp(`\\b${d}\\b`).test(lower));
    const ms = (iso) => Date.parse(iso || "");
    const today = new Date();
    const midnight = (back) => new Date(today.getFullYear(), today.getMonth(), today.getDate() - back).getTime();
    if (day >= 0) {
      return { word: DAYS[day][0].toUpperCase() + DAYS[day].slice(1), on: true,
        test: (iso) => { const t = ms(iso); return Number.isFinite(t) && new Date(t).getDay() === day && Date.now() - t < 7 * 24 * 3600 * 1000; } };
    }
    if (/\blast night\b|\bovernight\b/.test(lower)) {
      return { word: "last night", on: false, test: (iso) => { const t = ms(iso); return t >= midnight(1) + 17 * 3600000 && t < midnight(0) + 9 * 3600000; } };
    }
    if (/\byesterday\b/.test(lower)) return { word: "yesterday", on: false, test: (iso) => { const t = ms(iso); return t >= midnight(1) && t < midnight(0); } };
    if (/\btoday\b|\bthis morning\b|\bearlier\b/.test(lower)) return { word: "today", on: false, test: (iso) => ms(iso) >= midnight(0) };
    if (/\b(?:over )?(?:the |this |last )?weekend\b/.test(lower)) {
      return { word: "at the weekend", on: false, test: (iso) => { const t = ms(iso); const d = new Date(t).getDay(); return Number.isFinite(t) && (d === 0 || d === 6) && Date.now() - t < 7 * 24 * 3600 * 1000; } };
    }
    return null;
  }
  // "We're defrosting the ice-cream freezer, stop it alarming": there is
  // no pause for one unit (licenses.py has the wait, the limit and
  // Remove; retention.py pauses the whole account), so what to do.
  const UNIT_PAUSE = () => [
    "There's no switch that pauses one unit's alerts, so a unit being worked on is never forgotten. What to do instead:",
    `- **A freezer's own defrost cycle**: nothing. ${F.appShort} learns each freezer's defrost rhythm from its readings and doesn't text for one; a defrost much longer than its usual ones, or over 45 minutes, still alerts.`,
    `- **A short clean or a delivery**: give it a longer **Alert wait** on its card${onConsole ? "" : " in the console"} (up to 4 hours), then press **Use the usual** when you're done. A reading ${fig(F.alertAtOnceDegrees, "15")}°F or more past its limit still alerts at once.`,
    "- **Switched off or defrosted by hand**: it will go well past its limit, so tell whoever is on call first. When the alert text comes, reply 1 or press **I'm on it**, so nobody is phoned. Once it's cold again, **It's fixed** closes it, and the record shows who had it.",
    "",
    "Closed for weeks? An owner can pause the whole account for 1 to 3 months on the **Money** tab, under **Pause or cancel**.",
  ].join("\n");
  // "Why wasn't I called?": calls are on plans with calls, after the text
  // goes unanswered, and never from a server with calls switched off.
  function whyCallAnswer() {
    const dd = own();
    const calls = dd && dd.entitlements && dd.entitlements.calls;
    const off = F.callsLive === "no" || Boolean(dd && (dd.incidents || []).some((i) => i && i.calls_off));
    const lines = [];
    if (calls && calls.mode === "none") lines.push(`**Your plan sends texts, not phone calls.** ${words(calls.words)}`);
    lines.push(`A phone call comes only on a plan with calls (Growth and Enterprise; the free trial has ${fig(F.trialCalls, "5")} to try), and only when nobody has answered the alert text within ${fig(F.callAfterMinutes, "10")} minutes. Someone replying 1 or pressing **I'm on it** stops the call, and a unit back in range before then closes its alert with nobody phoned.`);
    if (calls && calls.mode === "trial") lines.push(words(calls.words));
    if (off) lines.push("**Calls are off on this server**: it has no phone service switched on, so no call is placed even when one is due, and the alert says “Would have phoned the list”.");
    lines.push(`Who is called, and in what order, is under **Who gets alerts** on the ${onConsole ? "" : "console's "}**Team** tab; someone muted or in quiet hours is called last or not at all.`);
    return lines.join("\n\n");
  }
  // "Can my staff use it without training?"
  const TRAINING = () => [
    "Yes. Nobody has to learn the console to answer an alert: the text says which fridge, how warm it is and what to check first, and they reply 1 (or press **I'm on it**) to say they have it. Once it's sorted, **It's fixed** closes it and asks one thing: was the food kept or thrown away.",
    `The **Simple view** (Today) shows what needs someone now in plain words, and works on a phone. **Send a test alert** on the **Team** tab shows everyone what a real one looks like.${ownPage && signedIn() ? "" : " [See it live](/demo) with pretend fridges."}`,
  ].join("\n\n");
  // "Email alerts instead of text?" (telemetry.send_alert_email: a copy
  // to the business's own address; the roster takes mobile numbers).
  const EMAIL = () => [
    `Alerts go by text to the people on call${F.callAfterMinutes ? ", and on plans with calls by phone" : ""}, because a text wakes someone at 3 AM and an inbox usually doesn't. There's no email-only choice for a person: **Who gets alerts** takes mobile numbers.`,
    "Each alert is also emailed once, as a copy, to the business's own email address (the one on the account), and a weekly summary comes by email too.",
  ].join("\n\n");

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
    "Do you phone at night?": ["phone", "call", "night", "ring", "voice", "escalate"],
    "Do you phone us?": ["phone", "call", "night", "ring", "voice", "escalate"],
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
    // 3.41.0: a sector with its own page, an alarm on a unit named, after
    // the trial, a second location, the alert wait, taking someone off,
    // and a new phone number. By phrase only (PHRASES below).
    { id: "sector", labels: [], ask: "Do you work for my trade?", answer: sectorAnswer },
    { id: "alarm", labels: [], ask: "A fridge alarm is going off", answer: alarmAnswer },
    { id: "trialafter", labels: [], ask: "What happens after the trial?", answer: trialAfterAnswer },
    { id: "addsite", labels: [], ask: "Can I add a location?", answer: addSiteAnswer },
    { id: "wait", labels: [], ask: "Can I change the alert wait?", answer: waitAnswer },
    { id: "removeperson", labels: [], ask: "How do I take someone off?", answer: removeAnswer },
    { id: "number", labels: [], ask: "How do I change someone's number?", answer: numberAnswer },
    { id: "plan", labels: ["Which plan for a café?"], also: ["tier", "option", "upgrade"], answer: PLAN },
    { id: "cafe", labels: ["Will it work for my café?"], answer: cafeAnswer },
    { id: "connect", labels: ["How do I connect a sensor?"], also: ["pair"], answer: CONNECT_HOW },
    { id: "steps", labels: ["Set up step by step"], answer: STEPS },
    { id: "trial", labels: ["What's in the trial?", "Start a free trial"], also: ["credit"], answer: TRIAL },
    { id: "door", labels: ["Will a door left open wake us?"], answer: DOOR },
    { id: "power", labels: ["Wi-Fi or power goes off?", "Power or Wi-Fi goes off?"],
      also: ["battery", "drop", "dropped", "router", "disconnect", "disconnected"], answer: POWER },
    { id: "calls", labels: ["Do you phone at night?", "Do you phone us?"],
      also: ["ring", "ringing", "phoned", "phoning", "nobody", "answer", "unanswered"], answer: CALLS },
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
    // 3.40.0: hello, what it is, the limits, a reading typed in, the
    // phone, turning texts on, calls left, a thermometer alarm, how long
    // it takes, the next step, the trial's end, the food kept, the
    // forecast, each location's count and the sensor key.
    { id: "hello", labels: [], ask: "Hi", also: ["hi", "hello", "hey", "hiya"], answer: helloAnswer },
    { id: "what", labels: [], ask: `What is ${F.appShort}?`, answer: WHAT },
    { id: "limits", labels: [], ask: "What temperature should a fridge be?", also: ["ideal", "recommended", "correct", "range"], answer: LIMITS },
    { id: "reading", labels: [], ask: "My freezer is at 15 degrees", answer: readingAnswer },
    { id: "mobile", labels: [], ask: "Can I see it on my phone?", also: ["tablet", "ipad", "browser"], answer: MOBILE },
    { id: "texts", labels: [], ask: "How do I turn texts on?", also: ["sms", "enable"], answer: TEXTS_ON },
    { id: "callsleft", labels: [], ask: "How many calls do I have left?", also: ["left", "remaining", "allowance"], answer: callsLeftAnswer },
    { id: "versus", labels: [], ask: "How is it different from a thermometer alarm?", also: ["thermometer", "logger", "beep", "buzzer", "different", "difference"],
      answer: VERSUS },
    { id: "setuptime", labels: [], ask: "How long does setup take?", answer: SETUP_TIME },
    { id: "next", labels: [], ask: "What do I do next?", answer: nextAnswer },
    { id: "trialend", labels: [], ask: "When does my trial end?", answer: trialEndAnswer },
    { id: "saved", labels: [], ask: "How much has it saved me?", answer: savedAnswer },
    { id: "forecast", labels: [], ask: "Is anything about to go wrong?", also: ["forecast", "predict", "prediction", "drifting", "risk"], answer: forecastAnswer },
    { id: "sites", labels: [], ask: "How many fridges at each location?", answer: sitesAnswer, only: () => ownPage && signedIn() },
    { id: "sensorkey", labels: [], ask: "What is a sensor key?", also: ["key"], answer: SENSOR_KEY },
    // 3.42.0: quiet hours, recording the food, fees, keeping data, how
    // fast a text comes, paying yearly, HACCP, no readings, another
    // sign-in, removing a unit and a battery. By phrase only (PHRASES).
    // 3.43.0: the night, sleep, a phone buzzing and weekends are quiet
    // hours' words, not the calls answer's.
    { id: "quiet", labels: [], ask: "Can I stop texts at night?", also: ["quiet", "sleep", "sleeping", "asleep", "buzz", "buzzing", "woken",
      "waking", "disturb", "weekend", "overnight", "nighttime", "bedtime"], answer: QUIET },
    { id: "foodrecord", labels: [], ask: "How do I record the food?", answer: foodRecordAnswer },
    { id: "fees", labels: [], ask: "Is there a setup fee?", answer: FEES },
    { id: "retention", labels: [], ask: "How long do you keep my data?", answer: RETENTION },
    { id: "speed", labels: [], ask: "How quickly does a text come?", answer: SPEED },
    { id: "yearly", labels: [], ask: "Is it cheaper yearly?", answer: YEARLY },
    { id: "haccp", labels: [], ask: "Does it help with HACCP?", answer: HACCP },
    { id: "noreadings", labels: [], ask: "My sensor isn't sending readings", answer: noReadingsAnswer },
    { id: "addlogin", labels: [], ask: "How do I add someone who can sign in?", answer: ADD_LOGIN },
    { id: "removeunit", labels: [], ask: "How do I remove a fridge?", answer: REMOVE_UNIT },
    { id: "battery", labels: [], ask: "A sensor's battery is low", answer: batteryAnswer },
    { id: "proof", labels: [], ask: "How do I make a proof report?", answer: PROOF },
    // 3.43.0: a unit being defrosted or cleaned, why no call came, staff
    // with no training, and alerts by email. By phrase, and their words.
    { id: "unitpause", labels: [], ask: "Can I stop one unit alerting while it's cleaned?", also: ["defrost", "defrosting", "maintenance"], answer: UNIT_PAUSE },
    { id: "whycall", labels: [], ask: "Why wasn't I called?", answer: whyCallAnswer },
    { id: "training", labels: [], ask: "Can my staff use it without training?", also: ["training", "train", "trained", "learn"], answer: TRAINING },
    { id: "email", labels: [], ask: "Can I get alerts by email?", also: ["email", "mail", "inbox"], answer: EMAIL },
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
  // "How many fridges at Old Mill?" (3.40.0), never a price ("8 fridges
  // per restaurant" is the quote's).
  const SITES_RE = /\bhow many\b[^?]*\b(?:fridges?|freezers?|units?|sensors?|coolers?)\b[^?]*\b(?:at|in)\b(?![^?]*\b(?:plan|trial|starter|growth|cost|price)\b)|\bhow many\b[^?]*\b(?:each|every|per) (?:location|site|restaurant|kitchen|store|branch)\b|\bhow many (?:fridges?|freezers?|units?|sensors?)(?: and freezers?)? (?:do|have) (?:i|we)\b(?! need)|\b(?:fridges?|freezers?|units?|sensors?) (?:per|at each|in each|at every) (?:location|site|restaurant|kitchen|store|branch)\b/i;
  // For the alarm phrases (3.41.0): a unit's word, a unit the question
  // names, and what is not about one alarm ("the alert wait", "who gets
  // the walk-in alerts").
  const UNIT_WORDS = /\b(?:fridges?|freezers?|coolers?|walk-?ins?|refrigerators?|chillers?|units?|display|cold room)\b/i;
  const ALARM_NOT = /\b(?:wait|delay|limits?|thresholds?|who|whom|turn off|switch off|stop|mute|pause|how many|this week|last week|calls?|texts?|sms|cost|price|false|test|nobody|no one|answer(?:ed)?|mean|means)\b/i;
  // 3.42.0, for the phrases below. The food asked about ("is the food
  // still ok", "should I throw out the milk", "can I still serve the
  // fish"); not food safety in general ("raw chicken above cooked", a
  // recipe, refreezing).
  const FOOD_RE = /\b(?:food|stock)\b[^?]*\b(?:ok|okay|safe|good|fine|alright|all right|spoiled|bad)\b|\b(?:ok|okay|safe|good|fine|alright) to (?:eat|use|serve)\b|\bshould (?:i|we) (?:throw|toss|bin|chuck|discard|keep|serve|use)\b|\b(?:can|could|should) (?:i|we) still (?:serve|use|eat|sell|keep)\b/i;
  const FOOD_NOT = /\b(?:above|below|next to|beside|shelf|shelves|raw|recipes?|cook(?:ed|ing)?|refreez\w*|thaw\w*|defrost\w*|safety (?:plan|compliance|rules|regulations?|law|standards?)|deliver(?:y|ies)|expiry|expiration)\b/i;
  // A trade asked about ("do you support bakeries?"), not a unit named.
  const TRADE_ASK = /\b(?:do you|does it|will it|would it|can it|is it (?:for|good|suitable|ok)|support|suit|work (?:for|with)|good for|suitable)\b/i;
  // "Can I use it for two restaurants?", "I have three restaurants".
  const MULTI_SITE = /\b(?:two|three|four|five|six|several|multiple|many|more than one|\d+|all (?:my|our)|both|each of (?:my|our))\s+(?:of (?:my|our) )?(?:locations?|sites?|restaurants?|stores?|shops?|cafes?|cafés?|kitchens?|branch(?:es)?|venues?|outlets?)\b/i;
  // Not ours to answer with a built-in answer: calling us, a person, an
  // integration. The honest note, never "Yes, on plans with calls".
  const NOT_OURS = /\b(?:your|you(?:r)? guys(?:')?|kryozyn(?:'s)?|company|support|customer service) (?:phone )?(?:number|line|email|address)\b|\b(?:phone number|number) (?:i|we) can (?:call|ring)\b|\b(?:talk|speak|chat) (?:to|with) (?:a |an |someone|somebody|a real|a human|a person|support|sales|an agent)\b|\bcontact (?:you|us|support|sales)\b|\bhuman\b|\b(?:slack|teams|zapier|toast|square|pos|api|webhooks?|integrat\w*)\b|\b(?:customi[sz]e|personali[sz]e|reword|rewrite)\b|\b(?:readings?|temp(?:erature)?s?|sensor) (?:looks?|seems?|is|are) (?:wrong|off|odd|weird|strange|inaccurate|incorrect|too high|too low)\b|\binaccurate\b|\bcalibrat\w*/i;
  const namedNow = (t) => { const dd = own(); return Boolean(dd) && unitsNamed(t, Array.isArray(dd.sensors) ? dd.sensors : []).length > 0; };
  function shopInUnit(t) {
    const text = String(t || "");
    const m = FOOD_SHOP.exec(text);
    const x = m ? null : sectorIn(text);
    if (!m && !x) return false;
    if (ALARM_RE.test(text) || WARM_WORDS.test(text)) return true;
    // A bakery, a butcher's or a deli in a unit's own name ("Prep fridge,
    // bakery"); another trade only with an alarm or warm word ("What
    // about ice cream shops?" is not the ice-cream freezer).
    const dd = own();
    if (!m || !dd) return false;
    // "Do you support bakeries?" (3.42.0) asks about the trade, even with
    // "Prep fridge, bakery" on the list.
    if (TRADE_ASK.test(text) || /ies\b|s\b/i.test(m[0])) return false;
    const shop = wordsIn(m[0]);
    return (dd.sensors || []).some((u) => { const w = wordsIn(u.location_name || ""); return shop.some((s) => w.includes(s)); });
  }
  // 3.43.0. Why no call came ("why wasn't I called?", "nobody phoned me").
  const WHY_NO_CALL = /\bwhy\b[^?]*\b(?:nobody|no one|no-one)\b[^?]*\b(?:call|ring|phone)\w*|\b(?:meant|supposed|should have|should've|promised)\b[^?]*\b(?:to )?(?:call|ring|phone)\w*\b|\bwhy\b[^?]*(?:\bno\b|\bnot\b|n'?t\b|\bnever\b)[^?]*\b(?:calls?|called|phoned?|phone calls?|rang|ring|rung)\b|\b(?:nobody|no one|no-one|you|it|they)\b (?:never |didn'?t |did not )?(?:phoned|called|rang)\b(?! (?:the |my |our )?(?:list|ladder))|\b(?:didn'?t|did not|never) (?:get|receive|have|got) (?:a |any |the )?(?:phone )?calls?\b|\b(?:thought|expected|supposed)\b[^?]*\b(?:call|phone|ring)\b/i;
  // A unit being defrosted, cleaned or worked on, or one unit's alerts
  // paused, muted or snoozed: never "No alert is open... check the door".
  const SERVICE_WORDS = /\brestock\w*|\bdefrost\w*|\bdeep[\s-]?clean\w*|\bclean(?:ing|ed|s)?\b|\bmaintenance\b|\bengineer\b|\btechnician\b|\brepair\w*|\bservic(?:e|ing)\b(?! (?:fee|charge|plan|level))|\bmoving (?:the )?(?:stock|food)\b|\bswitched off\b|\bturned off\b/i;
  const STOP_WORDS = /\balarm\w*|\balert\w*|\btext\w*|\bgo(?:es)? off\b|\bset off\b|\bstop\w*|\bpaus\w*|\bmute\b|\bsilenc\w*|\bsnooze\w*|\bdisable\w*|\bturn (?:it |them )?off\b|\bswitch (?:it |them )?off\b|\bcycles?\b/i;
  function unitPauseAsked(t) {
    const text = String(t || "");
    if (/\b(?:chicken|meat|fish|food|stock is|refreez\w*|eat|safe|sold|scrapped|replaced|for good|permanently|remove|delete|any ?more|no longer|wording|account|card|bill)\b/i.test(text)) return false;
    if (/\bmaintenance (?:mode|window)\b|\bturn (?:a|the|one|this|that|my) (?:unit|fridge|freezer|cooler|walk-?in)s? off\b/i.test(text)) return true;
    const unit = UNIT_WORDS.test(text) || namedNow(text);
    if (SERVICE_WORDS.test(text) && STOP_WORDS.test(text) && !/\bwhat (?:temp|temperature)\b|\b(?:recommend|company|companies|who can|find a)\b/i.test(text)) return true;
    // "We're doing a deep clean on the blast chiller tonight", not "how
    // do I defrost a freezer".
    if (unit && /\bdeep[\s-]?clean\w*|\bdefrost(?:ing|ed)\b|\bbeing (?:cleaned|serviced|repaired|defrosted|fixed)\b|\bserviced\b|\bmaintenance\b/i.test(text)
      && !/^\s*how (?:do|can|should|would) (?:i|we|you)\b|\bhow to\b/i.test(text)) return true;
    if (/\bbeep|\bbuzz/i.test(text)) return false;
    return unit && /\b(?:paus(?:e|ing)|snooze|mute|silence|disable|hold (?:the )?(?:alerts?|alarms?|texts?)|turn off|switch off|stop (?:the )?(?:alerts?|alarms?|texts?|it alarming|alarming)|stop alerts|pause monitoring)\b/i.test(text)
      && !/\bnights?\b|\bweekends?\b|\b\d{1,2}\s?am\b|\bsleep|\bwoken|\bwak(?:e|ing)\b|\bfor (?:[A-Z][a-z]+|him|her|them|me)\b/.test(text);
  }
  // A limit to change ("Change the walk-in freezer alarm to 5 degrees",
  // "I want the walk-in to alert at 39"), never which unit needs you.
  function limitChangeAsked(t) {
    const text = String(t || "");
    if (!/\b(?:change|changing|set|setting|raise|raising|lower|lowering|adjust|adjusting|alter|edit|move|increase|decrease|make|want|put|bump|drop|like)\b(?![\s-]*up\b(?! to))/i.test(text)) return false;
    if (!/\balarms?\b|\balert(?:s|ing)?\b|\blimits?\b|\bthresholds?\b|\bgo(?:es)? off\b|\btriggers?\b/i.test(text)) return false;
    if (/\b(?:wait|delay|how long|minutes?|mins?|wording|message|words|who|whom|people|person|number|phone|night|quiet|mute|snooze|pause|defrost\w*|clean\w*|email|sound|volume|ringtone|language)\b/i.test(text)) return false;
    const figure = /\d|\bdegrees?\b|°|\bhigher\b|\blower\b|\bwarmer\b|\bcolder\b|\btemp(?:erature)?s?\b|\blimits?\b|\bthresholds?\b/i.test(text);
    const strong = /\b(?:change|raise|lower|adjust|alter|edit|increase|decrease|bump|drop)\b/i.test(text) && (UNIT_WORDS.test(text) || namedNow(text));
    return figure || strong;
  }
  // Something in the past: the 7-day history, or for a visitor the honest
  // note, never the alert open now or which unit needs you now.
  const PAST_STRONG = /\b(?:yesterday|last night|last week|this week|past week|(?:last|past) (?:7|seven) days|last month|earlier(?: today| on)?|this morning|ago|over the weekend|last weekend|(?:mon|tues|wednes|thurs|fri|satur|sun)day(?!s))\b/i;
  const PAST_WEAK = /\b(?:was|were|did|had)\b(?:(?!\b(?:will|would|should|could|can|if)\b)[^?])*\b(?:handled|answered|acknowledged|closed|dealt with|responded(?: to)?|sorted|picked up|happen(?:ed)?)\b|\b(?:last|previous|latest|most recent) (?:alert|alarm|warning)s?\b|\bhow many times (?:did|has|have|was)\b|\b(?:any|were there|was there)\b[^?]*\b(?:alerts?|alarms?|problems?|issues?|warm)\b[^?]*\bovernight\b|\b(?:was|were|did)\b[^?]*\b(?:warm|alarm\w*|alert\w*|go off|went off)\b[^?]*\bovernight\b/i;
  const PAST_NOT = /\b(?:pay|paid|bill|billed|billing|invoice|charged?|price|cost|cancel\w*|trial|refund|will|going to|next|tomorrow|tonight|on[\s-]*call|rota|rotation|shift|plan|every|wi-?fi|internet|power|outage|router|electricity|blackout|logs?|inspectors?|inspections?|health|records?|reports?|export|print|pdf|download|summary|saved?|nobody|no one|no-one)\b|\bwhat happens\b|\bdo for (?:me|us)\b|\bdone for\b/i;
  const pastAsked = (t) => {
    const text = String(t || "");
    // "Was the food in the walk-in freezer ok on Wednesday?" is the food's.
    const food = /\b(?:food|stock)\b/i.test(text) && /\b(?:ok|okay|safe|good|fine|alright|spoiled|bad|keep|serve|eat|use)\b/i.test(text);
    return (PAST_STRONG.test(text) || PAST_WEAK.test(text)) && !PAST_NOT.test(text) && !food;
  };
  // Adding someone to the alerts ("add my sous chef Marco to alerts").
  function teamAddAsked(t) {
    const text = String(t || "");
    if (/\b(?:doesn'?t|doesnt|don'?t|dont|not|never|stop|without|no longer|only)\b/i.test(text)) return false;
    if (UNIT_WORDS.test(text) || /\b(?:sensors?|log[\s-]?ins?|sign[\s-]?ins?|accounts?|dashboard|console|access|e-?mail|password|location|site)\b/i.test(text)) return false;
    return /\b(?:add|put|include)\b[^?]*\b(?:to|on|onto|into)\s+(?:the\s+)?(?:alerts?|alert list|texts?|text list|list|roster)\b/i.test(text)
      || /\badd(?:ing)? (?:another|a second|a new|an extra|one more|a) (?:phone |mobile )?(?:number|person|people|phone)\b/i.test(text)
      || /\badd(?:ing)? (?:my |our |the |a )?(?:\w+ )?(?:\w+(?:'|’)s )?(?:phone |mobile |cell )?number\b/i.test(text)
      || /\badd\b[^?]*\b(?:so (?:she|he|they) (?:gets?|can get)|to get|to receive|to be texted|gets?)\b[^?]*\b(?:alerts?|texts?|texted)\b/i.test(text)
      || (!NIGHT_WORDS.test(text) && /\b(?:needs?|wants?|should get)\b (?:the |to get (?:the )?)?(?:alerts?|texts?)\b/i.test(text))
      || /\b(?:alerts?|texts?) (?:on|to) (?:two|2|three|3|more|multiple|several|both) (?:phones?|numbers?|people)\b/i.test(text)
      || /\btext (?:my|our|the) \w+(?: \w+)? (?:too|as well|also)\b/i.test(text)
      || /\bcan (?:\d+|two|three|four|five) people get\b/i.test(text)
      || /\b(?:whole|entire|all (?:the|my|our)) (?:team|staff|crew)\b[^?]*\b(?:texted|alerted|notified|get (?:the )?(?:texts?|alerts?))\b/i.test(text)
      || /^\s*(?:[Hh]ow (?:do|can) (?:[Ii]|we) )?add\s+[A-Z][a-z]+\s*[?.!]*\s*$/.test(text);
  }
  // The night, sleep, a phone buzzing, weekends: quiet hours, a rotation
  // and Mute. The calls answer only when calls or phoning are asked about.
  const NIGHT_WORDS = /\bday ?time\b|\bduring the day\b|\bnights?\b|\bovernight\b|\bmidnight\b|\b\d{1,2}\s?(?:am|a\.m)\b|\basleep\b|\bsleep(?:ing)?\b|\bbuzz(?:es|ing|ed)?\b|\bwak(?:e|es|ing)\b|\bwoken\b|\bwoke\b|\bweekends?\b|\bbed(?:time)?\b|\bdo not disturb\b|\bbusiness hours\b|\b(?:off|opening|working)[\s-]?hours\b|\bdays? off\b|\bannoying\b/i;
  const CALL_ASK = /\b(?:calls?|calling|called|phoned|phoning|rang)\b|\bring(?:s|ing)? (?:me|us|my|our|the|people|someone|them)\b|\b(?:do|will|would|can|does|could) (?:you|it|they) (?:also )?(?:phone|ring)\b|\bphone (?:me|us|people|someone|them)\b/i;
  function nightAsked(t) {
    const text = String(t || "");
    return NIGHT_WORDS.test(text) && !CALL_ASK.test(text) && !NOBODY_ANSWERS.test(text)
      && !/\bwhy\b|\bwhat happens\b|\bdoors?\b|\b(?:nobody|no one|no-one|didn'?t|did not|never|wasn'?t|weren'?t)\b|\b(?:wi-?fi|internet|broadband|network|power|outage|router|electricity|connection|signal)\b|\bfood\b|\b(?:price|cost|trial)\b|\bon[\s-]*call\b|\brota\b|\brotation\b|\bshift\b|\bturns\b|\bcover\b/i.test(text);
  }
  // Alerts by email.
  const EMAIL_ASK = /\b(?:alerts?|notifications?|warnings?|texts?|messages?)\b[^?]*\b(?:by|via|to|through|on|in|over) (?:my |our |an? |the )?e-?mail\b|\be-?mail (?:alerts?|notifications?|instead)\b|\b(?:send|get|receive|do)\b[^?]*\be-?mails?\b|\be-?mails?\b[^?]*\b(?:instead of|rather than|not) (?:a )?(?:texts?|sms)\b|\be-?mail (?:me|us)\b/i;
  const PHRASES = [
    // 3.40.0, first: hello on its own; what Kryozyn is; the sensor key; a
    // reading typed in and the limits ("should" is never which fridge
    // needs you); calls left; the trial's end; the food kept; turning texts
    // on; a cheap alarm; how long setup takes; the next step; the forecast;
    // a count at each location; the food on a unit and a day; a food truck.
    [/^\s*(?:hi|hello|hey|hiya|howdy|yo|good (?:morning|afternoon|evening))(?:\s+(?:there|pilot|team))?[\s!.,?]*$/i, "hello"],
    // 3.41.0, first: who can see the readings, and which sensor to buy,
    // before anything about your fridges ("Who can see my temperature
    // readings?" got "Sign in to see your fridges"); a new phone number
    // before the phone; the alert wait before the door; after the trial
    // before its end date.
    [WHO_SEES, "privacy"],
    [/\b(?:private(?! (?:aviation|jets?|hangars?|planes?))|privacy|confidential)\b|\b(?:my|our) (?:data|readings|information|info)\b[^?]*\b(?:safe|secure|shared?|sold|private|protected)\b|\b(?:shared?|sold) (?:with|to) (?:anyone|anybody|others|third parties|other companies)\b|\b(?:sell|share)s? (?:my|our) (?:data|readings|information)\b/i, "privacy"],
    // 3.42.0, first: what is not ours to answer, then a battery, HACCP,
    // how long data is kept, recording the food, quiet at night, taking
    // a unit off, another sign-in, who gets the alerts, fees, paying
    // yearly, a sensor with no readings. Each needs its intent words: a
    // confident wrong answer is worse than the honest note.
    [NOT_OURS, "miss"],
    // 3.43.0, first: why no call came; a unit being defrosted or cleaned;
    // a limit or a wait to change; anything in the past (the 7-day
    // history, never what is open now); a phone buzzing or a weekend
    // (quiet hours, never "Yes, on plans with calls"); adding someone;
    // and the common questions that had no answer.
    [WHY_NO_CALL, "whycall"],
    [{ test: unitPauseAsked }, "unitpause"],
    [{ test: limitChangeAsked }, "limit"],
    [/\b(?:change|set|adjust|alter|make|increase|decrease|lengthen|shorten|extend)\b(?![\s-]*up\b)[^?]*\bhow long\b[^?]*\b(?:before|until|till)\b[^?]*\b(?:alert|alerts|alerting|texts?|texting|warns?)\b|\b(?:wait|waits) longer before\b|\b(?:longer|shorter) (?:wait|delay) before\b|\b(?:different|own|separate|individual) (?:alert )?(?:waits?|delays?)\b/i, "wait"],
    [{ test: (t) => pastAsked(t) && !(ownPage && signedIn()) }, "miss"],
    [{ test: pastAsked }, "week"],
    [/\b(?:buzzed|woke (?:me|us)|went off|rang|pinged|beeped)\b[^?]*\bwhy\b/i, "alert"],
    [{ test: teamAddAsked }, "team"],
    [{ test: nightAsked }, "quiet"],
    [/\b(?:text|message|tell|notify|let)\b[^?]*\bwhen (?:it(?:'|’)s|it is|the alert is|the (?:fridge|freezer|unit) is|it gets?) (?:fixed|back|ok|okay|closed|normal|sorted)\b/i, "miss"],
    [/\bon (?:mon|tues|wednes|thurs|fri|satur|sun)days\b|\bweekdays\b|\b(?:mon|tues|wednes|thurs|fri|satur|sun)days?\b[^?]*\b(?:off|cover)\b|\bsomeone else (?:cover|take over)\b/i, "oncall"],
    [/\bwait (?:an? |\d+ |one |two |half an )?(?:hours?|minutes?|mins?|longer)\b[^?]*\bbefore\b/i, "wait"],
    [/\brenew\w*\b[^?]*\bcancel\w*|\bcancel\w*\b[^?]*\brenew\w*|\bcan (?:i|we) (?:still )?cancel\b/i, "cancel"],
    [{ test: (t) => /\b(?:is|are) (?:everything|all|anything)\b[^?]*\b(?:ok|okay|fine|alright|all right|good|right)\b/i.test(String(t || ""))
      && !/\b(?:food|stock|plan|price|trial|account|bill|payment|data)\b/i.test(String(t || "")) && !pastAsked(t) }, "status"],
    [/\b(?:weekend|night|evening|holiday) cover\b|\btak(?:e|ing) turns\b|\brotat(?:e|ing)\b[^?]*\b(?:who|alerts?|texts?|woken|on call)\b/i, "oncall"],
    [/\b(?:different|own|separate|individual|custom)\b[^?]*\b(?:limits?|thresholds?|alarm temp(?:erature)?s?)\b/i, "limit"],
    [/\b(?:readings?|temp(?:erature)?s?|numbers?|data)\b[^?]*\b(?:isn'?t|aren'?t|not|never|won'?t|doesn'?t|don'?t)\b[^?]*\b(?:show\w*|appear\w*|com(?:e|es|ing) (?:up|through|in)|updat\w*|load\w*|there)\b|\bno (?:temp(?:erature)?s?|readings?|numbers?|data)\b[^?]*\b(?:on|for|from|showing|coming|sensor|fridge|freezer)\b/i, "noreadings"],
    [/\b(?:charged|billed|pay|charge)\b[^?]*\bafter (?:the )?(?:\d+|fourteen|14) days\b|\bafter (?:the )?(?:\d+|fourteen) days\b[^?]*\b(?:charged|billed|pay|charge)\b/i, "trialafter"],
    [/\btraining\b|\btrain(?:ed)? (?:my |our |the )?(?:\w+ )?(?:staff|team|cooks?|chefs?|employees?|workers?|people|crew)\b|\b(?:staff|team|cooks?|chefs?|employees?|workers?|crew)\b[^?]*\b(?:understand|figure (?:it )?out|learn|cope|manage it)\b|\b(?:easy|simple|straightforward)\b(?: enough)? for (?:my |our |the )?(?:\w+ )?(?:staff|team|cooks?|chefs?|employees?|workers?|crew|kids|teenagers)\b/i, "training"],
    [{ test: (t) => EMAIL_ASK.test(String(t || "")) && !/\b(?:report|summary|digest|invoice|bill|receipt|password|sign[\s-]?in|log[\s-]?in|weekly|monthly)\b/i.test(String(t || "")) }, "email"],
    [/\b(?:wi-?fi|internet|power|connection|router|network|broadband|electricity)\b[^?]*\b(?:dies|died|dead|dying|cuts? out|gone)\b/i, "power"],
    [/\bbatter(?:y|ies)\b(?! (?:storage|banks?|rooms?|backup|back-up|systems?))/i, "battery"],
    [/\bhaccp\b|\bhazard analysis\b|\bfood safety (?:compliance|plans?|regulations?|rules|law|standards?|audits?|inspections?|checks?|requirements?)\b|\bcomplian(?:t|ce)\b/i, "haccp"],
    [/\bhow long (?:do|does|will|is|are|can)\b[^?]*\b(?:keep|kept|store|stored|save|saved|retain|retained|hold|held)\b(?![^?]*\b(?:food|stock|fridge|freezer|chicken|milk|meat|fish|cold|door)\b)|\b(?:data|readings|records?|history|information)\b[^?]*\b(?:kept|stored|retained|deleted|erased|wiped)\b|\bwhat happens to (?:(?:all|my|our|the)\s+){1,3}(?:temp(?:erature)?\s+)?(?:data|readings|records?|history|logs?|alerts?|account|information|info)\b|\bhow far back\b|\bhow long (?:do|does|will|are|is)\b[^?]*\b(?:readings?|data|records?|history|alerts?)\b[^?]*\b(?:stay|last|remain|around)\b|\b(?:delete|deleting|erase|wipe)\b[^?]*\b(?:data|readings|records?|history|everything|account)\b|\bmonths? of (?:history|data|readings|records)\b|\b(?:keep|store|retain)\b[^?]*\b(?:data|readings|records?|history|alerts?|logs?)\b[^?]*\b(?:years?|months?|forever|long)\b|\blose (?:my|our|the|all) (?:data|readings|records?|history)\b|\bdata retention\b|\bexport\b[^?]*\b(?:data|readings|records?|everything)\b|\bdownload (?:all|everything|my data|our data)\b/i, "retention"],
    [/\b(?:record|log|mark|note|enter|write down|put down|report)\b[^?]*\b(?:thrown|threw|throw (?:it |them |the \w+ )?(?:away|out)|binned|tossed|discarded|wasted|disposed)\b|\b(?:thrown|threw|binned|tossed|discarded|disposed of)\b[^?]*\b(?:record|log|mark|note|enter|write)\b|\b(?:record|log|mark|say|enter|note|tell)\b(?=[^?]*\bkept\b)(?=[^?]*\b(?:food|stock|it|them)\b)|\b(?:thrown|threw|binned|tossed|discarded)\b[^?]*\b(?:where|how) (?:do|can|should) (?:i|we)\b|\b(?:food|stock) kept\b[^?]*\b(?:button|press|where)\b/i, "foodrecord"],
    [/\bquiet (?:hours?|times?|periods?|nights?)\b|\bdo not disturb\b|\bmute\b|\bunmute\b|\b(?:stop|stopping|no|not|don'?t|dont|doesn'?t|doesnt|without|avoid|turn off|switch off|silence|block)\b[^?]*\b(?:texts?|texting|texted|alerts?|messages?|calls?|calling|woken|waking|wake|buzz\w*|ringing|going off|disturb\w*|bother\w*)\b[^?]*(?:\bnights?\b|\b\d{1,2}\s?(?:am|a\.m)\b|\bmidnight\b|\bovernight\b|\basleep\b|\bsleep(?:ing)?\b|\bweekends?\b|\bdays? off\b|\boff duty\b|\bbed\b)|(?:\bnights?\b|\b\d{1,2}\s?am\b|\bsleep\b)[^?]*\b(?:stop|without|no|don'?t want)\b[^?]*\b(?:texts?|alerts?)\b/i, "quiet"],
    [{ test: (t) => !personNamed(t) && /\b(?:remove|removing|delete|deleting|get rid of|got rid of|retire|decommission|take off|stop monitoring|stop watching|unregister)\b[^?]*\b(?:fridges?|freezers?|coolers?|units?|sensors?|walk-?ins?|chillers?|refrigerators?)\b(?![^?]*\b(?:food|stock|chicken|meat|milk|key)\b)/i.test(String(t || ""))
      || /\b(?:sold|scrapped|replaced|got rid of|threw out|retired|binned)\b[^?]*\b(?:fridges?|freezers?|coolers?|units?|sensors?|walk-?ins?|chillers?)\b[^?]*\b(?:remove|delete|take (?:it |them )?off)\b/i.test(String(t || "")) }, "removeunit"],
    [/\b(?:own|separate|individual|another|second|extra) (?:logins?|log[\s-]ins?|sign[\s-]?ins?|user accounts?|users?|passwords?)\b|\b(?:add|invite|create|set up|make|give)\b[^?]*\b(?:logins?|log[\s-]ins?|sign[\s-]?ins?|user accounts?|users?)\b|\bcan (?:my|our) (?:staff|team|managers?|chefs?|employees?|partner)\b[^?]*\b(?:see|view|use|open|access)\b[^?]*\b(?:dashboard|console|account)\b|\bwho can (?:log|sign) in\b|\bcan (?:my|our|the|a|another|other|more|staff|managers?|chefs?)\b[^?]*\b(?:log|sign)[\s-]?in\b(?! (?:on|from|with) (?:my|a|the|their) (?:phone|mobile|tablet|ipad))|\bmore than one (?:person|user|people)\b[^?]*\b(?:log|sign|use it|access)\b|\bgive\b[^?]*\baccess\b|\baccess to (?:the |my |our )?(?:console|account|dashboard|business)\b|\binvite\b(?![^?]*\b(?:friend|business|referral)\b)/i, "addlogin"],
    [{ test: (t) => !/\bon[\s-]*(?:call|shift|duty)\b|\brot(?:a|ation)\b/i.test(String(t || "")) && /\bwho\b[^?]*\b(?:gets?|getting|receives?|is sent|are sent|will get|would get)\b[^?]*\b(?:texts?|alerts?|messages?|texted|calls?|notified|notifications?)\b|\bwho (?:is|are|will be|gets?) (?:texted|notified|alerted|called|phoned)\b|\bwhich (?:people|persons?|staff|numbers?|phones?)\b[^?]*\b(?:get|gets|are|is)\b[^?]*\b(?:texted|notified|alerted|texts?|alerts?)\b|\b(?:more than one|several|multiple|many|other|extra|two|2) (?:people|persons?|staff|numbers?|phones?)\b[^?]*\b(?:texts?|alerts?|notified|texted)\b|\b(?:texts?|alerts?) (?:to|for) (?:more than one|several|multiple|everyone|the whole team|my team)\b|\b(?:can|could|will) (?:you|it) (?:also )?(?:text|alert|notify) (?:my|our|the|a|another|other)\b/i.test(String(t || "")) }, "team"],
    [/\bset[\s-]?up (?:fee|cost|charge)s?\b|\bhidden (?:fees?|costs?|charges?|extras?)\b|\b(?:extra|additional|other|surprise) (?:fees?|costs?|charges?)\b|\bany (?:fees?|charges?)\b|\b(?:installation|activation|onboarding|sign[\s-]?up|joining) (?:fees?|costs?|charges?)\b|\bcharge (?:for|per) (?:installation|install|setup|set up|texts?|sms|messages?|alerts?|calls?)\b|\b(?:texts?|sms|messages?|calls?) (?:are |cost )?(?:extra|charged)\b|\bpay (?:extra|more) for\b|\bfees?\b(?![^?]*\bcancel)/i, "fees"],
    [/\byearly\b|\bannual(?:ly)?\b|\bup ?front\b|\bin advance\b|\bpre-?pay\w*\b|\b12 months\b|\bper year\b|\ba year\b|\b(?:whole|full) year\b|\bby the year\b|\bpay(?:ing)? (?:for )?(?:the |a )?year\b/i, "yearly"],
    [/\b(?:no|not|isn'?t|isnt|aren'?t|arent|doesn'?t|doesnt|stopped|never|hasn'?t)\b[^?]*\b(?:readings?|data|reporting|sending|updating)\b(?![^?]*\b(?:texts?|sms|alerts?|sold|shared|safe|private)\b)|\bwaiting for (?:its |a |the )?first reading\b|\bsensor\b[^?]*\b(?:not|isn'?t|won'?t|doesn'?t|stopped)\b[^?]*\b(?:work(?:ing)?|connect(?:ing|ed)?|show(?:ing)?|send(?:ing)?|report(?:ing)?)\b/i, "noreadings"],
    [RECOMMEND, "sensors"],
    [/\b(?:change|update|edit|fix|correct)\b[^?]*\b(?:phone|mobile|cell)(?: number)?\b|\b(?:phone|mobile|cell) number\b[^?]*\b(?:change|changed|update|edit|wrong|new)\b|\bnew (?:phone|mobile|cell) number\b|\bwrong (?:phone |mobile )?number\b|\b(?:change|update|edit|fix|correct)\b[^?]*\b\w+(?:'|’)s (?:phone |mobile |cell )?number\b|\bchange (?:the |my |a |their |his |her )?number\b/i, "number"],
    [{ test: (t) => /\b(?:remove|delete|kick|get rid of|revoke|disable|block)\b[^?]*\b(?:from (?:the |my |our )?(?:account|team|business|console|list)|access|sign(?:ing)?[\s-]*in|log(?:ging)?[\s-]*in|login)\b|\bremove (?:him|her|them)\b/i.test(String(t || "")) || (/\b(?:remove|delete|kick out|get rid of)\s+[A-Z][a-z]+\b/.test(String(t || "")) && Boolean(personNamed(t)))
      || /\b(?:left|leaving|quit|fired|leaves)\b[^?]*\b(?:remove|stop|delete|take)\b[^?]*\b(?:him|her|them|texts?|alerts?|access)\b/i.test(String(t || ""))
      || /\btake (?:[A-Z][a-z]+|him|her|them) off\b/.test(String(t || "")) }, "removeperson"],
    [/\b(?:alert )?wait\b[^?]*\b(?:change|set|longer|shorter|to \d+|\d+ min)|\b(?:change|set|make|increase|decrease|lengthen|shorten|extend|raise|lower)\b[^?]*\b(?:alert )?(?:wait|delay)\b|\bdelay (?:the |an |my )?alerts?\b|\bwait (?:longer|less) before\b|\b(?:change|set|adjust|alter|choose|pick|edit)\b[^?]*\bhow long\b[^?]*\bwaits?\b|\bwaits? (?:longer|less|shorter)\b/i, "wait"],
    // 3.42.0: how fast a text comes, after the wait ("how long does it
    // wait" is the wait's), and never setting up or the trial.
    [/(?:\bhow (?:quick(?:ly)?|fast|soon)\b|\bhow long (?:does|will|until|till|before)\b)(?![^?]*\b(?:waits?|waiting|set ?up|setting up|install\w*|running|going|started|trial|take to (?:set|install))\b)[^?]*\b(?:texts?|alerts?|alerted|notif(?:y|ied|ications?)|messages?|sms|warn(?:ed|ing)?|find out|know|hear|told)\b|\b(?:texts?|alerts?|messages?)\b[^?]*\b(?:instant(?:ly)?|immediate(?:ly)?|real[\s-]?time|right away|straight away|delay(?:ed)?)\b|\bhow (?:fast|quick) (?:are|is) (?:the |your )?(?:alerts?|texts?)\b|\b(?:is there|any) (?:a |any )?(?:lag|delay)\b/i, "speed"],
    [/\bafter (?:the |my |our )?(?:free )?trial\b|\bwhat happens (?:when|once|at the end of) (?:the |my |our )?(?:free )?trial\b|\b(?:when|once) (?:the |my |our )?(?:free )?trial (?:ends|is over|finishes|runs out|expires)\b[^?]*\b(?:what|happen)|\b(?:pay|charged?|billed|bill)\b[^?]*\b(?:when|once|at the end of)\b[^?]*\btrial\b|\b(?:charged|billed)\b[^?]*\btrial\b|\btrial\b[^?]*\b(?:ends?|over|finish(?:es)?|expires?)\b[^?]*\b(?:pay|charged?|billed)\b/i, "trialafter"],
    [new RegExp(`^\\s*(?:so\\s+)?(?:what(?:'|’)?s|what is|what does|who (?:is|are)|tell me about|explain)\\s+(?:${F.appShort.replace(/[^A-Za-z0-9]/g, "") || "kryozyn"}|this(?: app| site| service| product| thing)?|it|you)(?:\\s+do)?\\s*[?.!]*\\s*$|^\\s*how does (?:${F.appShort.replace(/[^A-Za-z0-9]/g, "") || "kryozyn"}|it|this) work\\b`, "i"), "what"],
    [/^\s*(?:so\s+)?how (?:do|can|should|would) (?:i|we|you) (?:set (?:it|this|everything|things) up|get (?:it )?(?:started|set up)|install (?:it|this))\s*[?.!]*\s*$/i, "connect"],
    [/\bsensor[\s-]*keys?\b|\bingest(?:ion)? keys?\b|\bwhat(?:'|’)?s (?:a |the |my )?key\b|\bwhat is (?:a |the |my )?key\b/i, "sensorkey"],
    [{ test: (t) => (READING_RE.test(String(t || "")) || READING_FOR.test(String(t || ""))) && !isQuote(t) }, "reading"],
    // 3.42.0: an alert text got, or who answered it: that alert.
    [/\b(?:got|get|getting|received?|had)\b[^?]*\b(?:texts?|alerts?|messages?|alarms?)\b[^?]*\b(?:what (?:do|should|now|does)|saying|says|said|but)\b|\b(?:the |an |my |this )?(?:alert|text|alarm) (?:says|said|shows|showing|reads)\b/i, "alarm"],
    [/\bwhy (?:did|do|does|was|am|have) (?:i|we|my phone|someone|everyone)\b[^?]*\b(?:get|got|getting|receive|received|texted|woken|called|phoned)\b/i, "alert"],
    [/\b(?:did|has|have) (?:anyone|anybody|someone|somebody|any ?one)\b[^?]*\b(?:answer(?:ed)?|ack(?:nowledged?)?|respond(?:ed)?|picked up|seen it)\b|\bwho (?:answered|acknowledged|responded|is on it|has it|took it)\b|\bis (?:anyone|someone|anybody|somebody) on it\b/i, "alert"],
    [/\bhow long (?:has|have|was|is|did) (?:the |my |our |it |this )?[\w ,-]{0,40}?\b(?:been )?(?:warm|too warm|too cold|out|out of range|over)\b/i, "alert"],
    [/\bhow long\b[^?]*\bwait\b|\bwait (?:before|until) (?:an |the |it )?(?:alert|alerts|text|texts)\b/i, "door"],
    // 3.43.0: "how's the old mill bar fridge", "are all the Old Mill units
    // fine", "is the blast chiller 2 working": that unit or place now.
    [{ test: (t) => !/\b(?:food|stock|chicken|meat|milk|fish|should|vaccines?|plan|price|trial)\b/i.test(String(t || ""))
      && (/^\s*how(?:'|’)?s (?:the |my |our )?[\w ,'’-]{0,40}?\b(?:fridges?|freezers?|coolers?|walk-?ins?|units?|chillers?|displays?)\s*[?.!]*\s*$/i.test(String(t || ""))
        || /\b(?:is|are) (?:all )?(?:the |my |our )?[\w ,'’-]{0,40}?\b(?:fridges?|freezers?|coolers?|walk-?ins?|units?|chillers?|displays?)\b(?: \d+)? (?:all )?(?:ok|okay|fine|alright|all right|good|working|running|cold enough)\b/i.test(String(t || ""))) }, "status"],
    [/\bhow(?:'|’)?s\b[^?]*\b(?:fridges?|freezers?|coolers?|walk-?ins?|units?|chillers?|everything)\b[^?]*\b(?:doing|looking|going)\b|\bhow (?:is|are) (?:the|my|our) [\w ,-]{0,30}?\b(?:fridges?|freezers?|coolers?|walk-?ins?|units?|chillers?)\b[^?]*\b(?:doing|looking|going)\b/i, "status"],
    [/\bwhat(?:'|’)?s (?:the |my |our )?(?:[\w,-]+ ){0,3}?(?:fridge|freezer|cooler|walk-?in)s? (?:at|reading|temp(?:erature)?)\b|\bwhat is (?:the |my |our )?(?:[\w,-]+ ){0,3}?(?:fridge|freezer|cooler|walk-?in)s? (?:at|reading)\b|\bhow (?:cold|warm) is (?:the|my|our)\b/i, "status"],
    [/\bwhat (?:temp(?:erature)?s?|degrees?)\b[^?]*\b(?:alert|alerts|alarm|alarms|text|texts|warn|trigger)s?\b(?! (?:wait|delay))|\b(?:at what|which) (?:temp(?:erature)?|degrees?)\b/i, "limits"],
    [/\bwhat (?:temp(?:erature)?s?|degrees?)\b[^?]*\b(?:should|must|ought|supposed|needs? to)\b|\b(?:should|must) (?:a |my |the |our )?(?:fridges?|freezers?|coolers?|walk-?ins?)\b[^?]*\b(?:be|sit|run|stay)\b|\b(?:right|correct|safe|ideal|recommended|proper|best|good|normal) (?:(?:fridge|freezer|walk-?in|cooler) )?(?:temp(?:erature)?s?|range)\b|\bhow (?:cold|warm) should\b|\bhow (?:cold|warm) (?:does|do|must|has|have) (?:a |an |my |the |our )?(?:fridges?|freezers?|coolers?|walk-?ins?)\s+(?:need to|have to|has to)?\s*be\b|\b(?:temp(?:erature)?|degrees?) (?:limits?|range) for\b|\bwhat(?:'|’)?s (?:the |a )?(?:limit|safe temp(?:erature)?) for\b|\bwhat is (?:the |a )?(?:limit|safe temp(?:erature)?) for\b/i, "limits"],
    [/\bhow many (?:phone )?calls\b|\b(?:phone )?calls?\b[^?]*\b(?:left|remaining|remain|allowance|quota|used up)\b/i, "callsleft"],
    [/\btrial\b[^?]*\b(?:end|ends|ending|over|expire|expires|expiring|left|remaining|finish|finishes|run out|runs out)\b|\bdays? left\b|\bwhen (?:does|will|is) (?:my |the |our )?(?:free )?trial\b|\bhow long (?:is|does) (?:my |the |our )?(?:free )?trial\b/i, "trialend"],
    [/\b(?:how much|what) (?:has|have|did|is)\b[^?]*\b(?:sav(?:e|ed|ing)|kept)\b|\bsav(?:ed|ings?|e) (?:me|us)\b|\bhow much (?:money |food )?(?:have|did|has) (?:i|we|it|this|you)\b[^?]*\bsav(?:e|ed|ings?)\b|\bpaid for itself\b|\bworth (?:it|the money)\b|\breturn on\b|\broi\b|\bfood kept\b|\bmoney saved\b/i, "saved"],
    [/\b(?:turn|switch|set|put|get)(?:ting)? (?:on|up) (?:the |my |our )?(?:texts?|sms|text alerts?|text messages?|texting)\b|\b(?:turn|switch)(?:ing)? (?:the |my |our )?(?:texts?|sms|text alerts?) on\b|\b(?:texts?|sms|text alerts?|texting) (?:on|switched on|turned on)\b|\benabl(?:e|ing) (?:the )?(?:texts?|sms|text alerts?|texting)\b|\b(?:texts?|sms) (?:are |is )?(?:not |n(?:'|’)t )?(?:switched|turned) on\b/i, "texts"],
    [/\b(?:thermometers?|data ?loggers?|temperature alarms?|fridge alarms?|freezer alarms?|beepers?|buzzers?|cheap(?:er)? alarms?)\b[^?]*\b(?:differ|different|difference|better|vs\.?|versus|compare|compared|instead|rather)\b|\b(?:differ|different|difference|why not|instead of|vs\.?|versus|compared? (?:to|with)|better than|rather than|over)\b[^?]*\b(?:thermometers?|data ?loggers?|(?:temperature |fridge |freezer |cheap |\$\d+ )alarms?|beepers?|buzzers?)\b|\bwhy (?:not |wouldn(?:'|’)?t (?:i|we) )?(?:just )?(?:use|buy|get) (?:a |an )?(?:cheap |\$\d+ )?(?:thermometer|alarm)/i, "versus"],
    [/\bhow long\b[^?]*\b(?:set ?up|setting up|install(?:ing|ation)?|get(?:ting)? (?:started|going|running|set up)|to start|connect(?:ing)?)\b|\b(?:set ?up|installation)\b[^?]*\bhow long\b|\bhow (?:quick|quickly|fast)\b[^?]*\b(?:set ?up|start|install|running|going)\b/i, "setuptime"],
    [/\bwhat (?:do|should|can|must) (?:i|we) do next\b|\bwhat(?:'|’)?s next\b|\bwhat is next\b|\bnext steps?\b|\bwhere (?:do|should) (?:i|we) (?:start|begin)\b|\bwhat(?:'|’)?s left to (?:do|set ?up)\b|\bwhat is left to (?:do|set ?up)\b|\bgetting started\b|^\s*(?:ok(?:ay)?,?\s*)?(?:so\s+)?what now\s*\??\s*$/i, "next"],
    [/\bforecast\b|\babout to (?:go wrong|fail|break|warm up|go warm)\b|\b(?:going|likely|heading) to (?:fail|go wrong|break|warm up)\b|\bheading (?:for trouble|the wrong way)\b|\bpredict(?:ion|ions|ive|ed)?\b|\bat risk\b|\bdrifting\b|\b(?:will|might|could) (?:any|a|my|the) (?:fridge|freezer|unit|cooler)s? (?:fail|break|go wrong)\b|\bwhich (?:one|fridge|freezer|unit)s? (?:will|might|could|is going to) (?:fail|break|go wrong)\b/i, "forecast"],
    [{ test: (t) => SITES_RE.test(String(t || "")) && !isQuote(t) }, "sites"],
    // 3.42.0: "I have a food truck, is that ok?" is a food truck, not the
    // food; "food safety compliance" is HACCP's. Storing raw above cooked
    // and recipes are not ours.
    [{ test: (t) => !FOOD_SHOP.test(String(t || "")) && !FOOD_NOT.test(String(t || "")) && FOOD_RE.test(String(t || "")) }, "food"],
    // 3.39.0, first: "can I see it working before signing up?" is trying
    // it, not signing up.
    [/\bwithout (?:a |any |buying (?:a |any )?)?(?:sensors?|hardware|devices?)\b|\bno (?:sensors?|hardware) yet\b|\b(?:see|watch) it work(?:ing)?\b|\bbefore (?:i |we )?(?:sign(?:ing)? up|buy(?:ing)?|pay(?:ing)?|commit(?:ting)?)\b|\btry it (?:out|first|before)\b|\bsimulat/i, "try"],
    // 3.36.0, first: "my address on the insurance pack" is the address.
    [/\b(?:site|street|shop|business|our|my|the) address\b|\baddress (?:to|on|in|for) (?:the |my |our )?(?:pack|insurance)/i, "address"],
    [/\bsign(?:ing)?[\s-]*up\b|\bregister\b|\bcreate (?:an |my )?account\b/i, "signup"],
    // 3.42.0: the proof of monitoring report, before the insurance pack.
    [/\bproof(?:[\s-]of[\s-]monitoring)?(?: of monitoring)? reports?\b|\bproof of monitoring\b|\bproof (?:that|my|our) (?:fridges?|freezers?|units?)\b[^?]*\b(?:watched|monitored)\b|\bmonthly report for (?:my|the|our) insurer\b/i, "proof"],
    [/\binsurance (?:pack|packet|claim)|\bclaim (?:pack|form)|\bfor (?:my|the) insurer\b/i, "pack"],
    [/\bwhat (?:was |got |were |did (?:we|you|they|i) )?(?:thrown|throw|binned|tossed)\b|\bwhat went in the bin\b/i, "lossnote"],
    // 3.41.0: an alarm on a unit named ("the bakery fridge alarm, what
    // should I do", "the walk-in is beeping"), signed in: the alert on it,
    // never the pitch for bakeries. A shop's word in a unit's name counts.
    [{ test: (t) => ownPage && signedIn() && shopInUnit(t) }, "alarm"],
    [{ test: (t) => ALARM_RE.test(String(t || "")) && (UNIT_WORDS.test(String(t || "")) || namedNow(t))
      && !ALARM_NOT.test(String(t || "")) }, "alarm"],
    // 3.38.0, before the texts and the night: the account's own bill, a
    // pause for a closure, a limit to change, who is on call, and nobody
    // answering.
    [/^(?![\s\S]*\b(?:cancel|pause)\b)[\s\S]*(?:\b(?:how much|what) (?:am|are|do|does) (?:i|we) (?:paying|pay|being charged|charged|billed)\b|\b(?:my|our) (?:bill|invoice)\b|\bwhat (?:plan|tier) (?:am|are) (?:i|we)\b|\bwhat did (?:i|we) pay\b|\b(?:last|latest|previous|recent|next|newest) (?:bill|invoice|payment|charge|receipt)s?\b|\binvoices?\b|\bwhen (?:is|will|do|does|am|are)\b[^?]*\b(?:bill|billed|charged|invoice)\b|\bbilling date\b)/i, "bill"],
    // 3.42.0: which plan, by its words.
    [/\b(?:starter|growth|enterprise)\b[^?]*\b(?:or|vs\.?|versus|difference|compared?)\b[^?]*\b(?:starter|growth|enterprise)\b|\b(?:difference|compare)\b[^?]*\b(?:starter|growth|enterprise|plans)\b|\bwhich plan\b|\bwhat plan (?:should|do|would|will)\b/i, "plan"],
    [/\bpause\b|\bpausing\b|\bsnooze\b|\b(?:closed|closing|shut) (?:for|over|during)\b|\bholidays?\b|\bvacation\b|\bchristmas\b|\bseasonal\b|\b(?:turn off|switch off|stop|mute) (?:the |all |my )?(?:alerts|alarms)\b/i, "pause"],
    [/\b(?:change|set|raise|lower|move|adjust|edit)\b.*\b(?:limits?|thresholds?)\b|\blimits? (?:to|at) -?\d|\bthresholds?\b/i, "limit"],
    [/\bwho(?:'s|’s| is| are)?\b.*\bon[\s-]*(?:call|shift|duty)\b|\bon[\s-]*call (?:this|next|now|today|tonight|right now)\b|\bwhose turn\b|\brota\b|\brotation\b/i, "oncall"],
    [NOBODY_ANSWERS, "calls"],
    [/(?=.*\b(?:texts?|sms|messages?|alerts?)\b)(?:\b(?:nobody|no one|no-one)\b|\b(?:didn'?t|did not|never|wasn'?t|was not|weren'?t)\b.*\b(?:get|got|receive|received|arrive|arrived|come|came|sent|texted)\b)/i, "delivery"],
    [/\b(?:hard|difficult|complicated|tricky|technical|easy)\b.*\bset(?:ting)?[\s-]*up\b|\bset(?:ting)?[\s-]*up\b.*\b(?:hard|difficult|complicated|tricky|easy)\b|\bhow (?:hard|easy|difficult) is it\b|\belectrician\b|\bwiring\b|\b(?:hard|easy|difficult) to install\b|\b(?:need|hire|pay) (?:someone|somebody|a professional|an installer|a technician) to install\b/i, "easy"],
    [/\bcanad|\bcelsius\b/i, "canada"],
    [/\b(?:my|the|our) insurer\b|\binsurance company\b/i, "pack"],
    // 3.42.0: the inspector, the health department, a temperature log.
    [/\bhealth (?:department|dept|inspectors?|inspections?|officers?)\b|\binspectors?\b|\binspections?\b|\btemp(?:erature)? (?:logs?|records?|sheets?)\b|\bpaper (?:logs?|records?|sheets?)\b|\blog ?books?\b/i, "log"],
    // 3.40.0: insurance or an insurer, however asked, is the pack.
    [/\binsurance\b|\binsurers?\b|\binsured\b|\bunderwriters?\b/i, "pack"],
    [new RegExp(`\\bwhat (?:did|has|does) ${F.appShort.replace(/[^A-Za-z0-9]/g, "") || "it"}\\b.*\\b(?:do|done)\\b`, "i"), "did"],
    // 3.37.0: a price with a number of fridges or places, worked out.
    [{ test: isQuote }, "quote"],
    // 3.42.0: priced by the fridge or the location; what the trial has;
    // a vaccine fridge.
    [/\bper (?:fridge|freezer|unit|sensor|location|site|restaurant|store)\b[^?]*\b(?:or|price|cost|charge)\b|\b(?:price|priced|cost|charge|pay)\b[^?]*\bper (?:fridge|freezer|unit|sensor|location|site|restaurant|store)\b/i, "price"],
    [/\b(?:included|include|includes|get|comes?) (?:in|with) (?:the |a |my )?(?:free )?trial\b|\btrial\b[^?]*\b(?:include|includes|included|card|credit)\b/i, "trial"],
    [/\b(?:suitable|work|good|use|support|for)\b[^?]*\bvaccines?\b(?![^?]*\b(?:warm|cold|alarm|alert|reading|clinics?|at \d)\b)/i, "pharmacy"],
    // 3.39.0: this week's alerts, the warmest, another fridge, trying with
    // no sensor, an app, and a butcher, a bakery or a deli.
    [/\balerts?\b.*\b(?:this|last|past) (?:week|7 days|seven days)\b|\b(?:this|last) week(?:'|’)?s alerts\b|\bhow many alerts\b/i, "week"],
    [/\b(?:warmest|hottest)\b/i, "warmest"],
    [{ test: (t) => (/\b(?:add|adding|open|opening|set up|another|second|third|new|more|extra)\b[^?]*\b(?:locations?|sites?|branch(?:es)?|stores?|restaurants?|kitchens?|shops?|cafes?|cafés?)\b/i.test(String(t || ""))
      || MULTI_SITE.test(String(t || ""))) && !UNIT_WORDS.test(String(t || "")) && !isQuote(t) }, "addsite"],
    // 3.42.0: "I bought a new freezer, how do I add it?"
    [/\b(?:bought|got|have|installed|getting|buying|ordered)\b[^?]*\b(?:new|another|second|extra)\s+(?:fridges?|freezers?|coolers?|walk-?ins?|units?|chillers?)\b[^?]*\b(?:add|set up|connect|watch|monitor)\b/i, "addunit"],
    [/\b(?:add|adding|connect|set up|get)\b[^?]*\b(?:second|another|more|extra|third|new|other)\b[^?]*\b(?:fridges?|freezers?|sensors?|units?|coolers?|walk-?ins?)\b|\b(?:second|another|extra|third)\s+(?:fridges?|freezers?|sensors?|units?|coolers?)\b|\badd(?:ing)? (?:a |an |my |one )?(?:\w+ )?(?:fridges?|freezers?|coolers?)\b/i, "addunit"],
    [/\b(?:have|got|is there|are there|download|install|get|need)\b[^?]*\bapps?\b|\bapp store\b|\bplay store\b|\bhome ?screen\b|\b(?:iphone|android|mobile|phone) app\b/i, "app"],
    // 3.40.0: "Can I see it on my phone?" is the console in a phone's
    // browser, not a phone call.
    [/\b(?:on|from|with|using|via) (?:my |a |an |the |your |our )?(?:phone|mobile|cell ?phone|cell|smartphone|iphone|android|tablet|ipad)\b(?!\s*(?:calls?|numbers?|list))|\bmobile[\s-]*(?:friendly|version|site)\b|\b(?:phone|mobile)(?:'|’)?s? browser\b/i, "mobile"],
    // A butcher's, a bakery or a deli as a business, not "the deli fridge".
    // "I have a food truck, is that ok?" (3.42.0) asks about the truck.
    [{ test: (t) => FOOD_SHOP.test(String(t || "")) && !/\b(?:fridges?|freezers?|coolers?|cases?|warm|cold|temp(?:erature)?s?)\b/i.test(String(t || ""))
      && (!/\b(?:ok|okay)\b/i.test(String(t || "")) || /\b(?:is (?:that|it|this)|would (?:that|it|this) be|be) (?:ok|okay)\b/i.test(String(t || ""))) }, "cafe"],
    // 3.41.0: any other sector with a page ("Do you do florists?").
    [{ test: (t) => Boolean(sectorIn(t)) && !/\b(?:fridges?|freezers?|coolers?|cases?|warm|temp(?:erature)?s?|alarms?|alerts?)\b/i.test(String(t || ""))
      && (!/\b(?:ok|okay)\b/i.test(String(t || "")) || /\b(?:ok|okay) for\b|\b(?:is (?:that|it|this)|would (?:that|it|this) be|be) (?:ok|okay)\b/i.test(String(t || ""))) }, "sector"],
    // The Wi-Fi or the power dropping, and a card's "No signal".
    [/\b(?:wi-?fi|internet|power|connection|router|network|broadband|electricity)\b.*\b(?:drops?|dropped|dropping|down|off|out|cuts?|fails?|failed|goes|went|lost|lose|loses|outage|offline|reboot\w*|restart\w*|resets?)\b|\b(?:drops?|dropped|lost|lose|no)\b.*\b(?:wi-?fi|internet|power|connection)\b|\boffline\b|\boutage\b|\bblackout\b|\bpower cut\b|\bno signal\b|\b(?:without|needs?|requires?)\b[^?]*\b(?:wi-?fi|internet)\b/i, "power"],
    // The door before the night: "will a door left open wake us at night?".
    [/\bdoors?\b.*\b(?:open|opened|opening|ajar|left)\b|\b(?:open|opened|opening)\b.*\bdoors?\b|\bfalse (?:alarms?|alerts?|positives?)\b|\bnuisance (?:alarms?|alerts?)\b|\bfor no reason\b/i, "door"],
    // The night, 3 AM, a call.
    // 3.42.0: a call and the night together, not "3am" or "night" alone
    // ("Why did I get a text at 3am?" is the alert's).
    [/(?=[^?]*\b(?:call|calls|calling|called|phone|phones|phoning|phoned|ring|rings|ringing|rung|wake|wakes|woken)\b)(?=[^?]*(?:\bnight\b|\bovernight\b|\bmidnight\b|\b\d{1,2}\s?(?:am|a\.m)\b|\basleep\b|\bsleeping\b|\bweekends?\b))|\bwhat happens\b[^?]*(?:\bat night\b|\bovernight\b|\bmidnight\b|\b\d{1,2}\s?(?:am|a\.m)\b)|\b(?:do|will|would|can) (?:you|it) (?:phone|call|ring)\b(?! (?:my|a|the)? ?landline)/i, "calls"],
    // Metal walls, a walk-in, the signal: the sensors answer, with its lead.
    [THROUGH_METAL, "sensors"],
    // 3.42.0: what sensors cost, and does each unit need one.
    [/\b(?:how much|costs?|prices?|included|include)\b[^?]*\bsensors?\b|\bsensors?\b[^?]*\b(?:cost|costs|price|included|how much)\b|\b(?:need|needs)\b[^?]*\b(?:its own|their own|own|separate|one|a|each|every|another)\s+sensors?\b|\bsensors? (?:for|in) (?:each|every)\b|\bhow many sensors (?:do|would|will) (?:i|we) need\b/i, "sensors"],
    // 3.38.0, last: a contract or leaving any time is cancelling; "what's
    // wrong right now?" is which fridges need you.
    [/\bcontract\b|\bcommitment\b|\block(?:ed)?[\s-]*in\b|\btied in\b|\b(?:leave|cancel|quit|stop) (?:at )?any ?time\b|\brefunds?\b/i, "cancel"],
    // 3.42.0: the cheapest plan is the prices; a demo is the demo.
    [/\bcheapest\b|\b(?:least|lowest) (?:expensive|price|cost)\b/i, "price"],
    [/\bdemo\b|\bsee it (?:live|in action|working)\b/i, "demo"],
    [/\bwhat(?:'|’)?s (?:wrong|the matter|up with|the status|going on|happening)\b|\bwhat is (?:wrong|happening|going on)\b|\b(?:anything|something) wrong\b|\b(?:anything|something) (?:i|we) (?:need|have|should) to (?:deal with|do|look at|check|worry about|know)\b|\b(?:cold|warm) enough\b|\bany (?:problems?|issues?)\b|\bstatus\b|(?=[^?]*\b(?:fridges?|freezers?|units?|coolers?|walk-?ins?|temp(?:erature)?s?|everything|anything|ok|okay|fine|wrong|warm|cold|at)\b)[^?]*\bright now\b/i, "status"],
  ];
  // A question with "should", "what is" or "how long" in it is never
  // answered with the list of your fridges by its words alone (3.40.0):
  // "What temperature should my fridge be?" got the walk-in's status. A
  // phrase above can still mean it ("what is wrong?").
  // 3.42.0: nor one about a download, a graph or a report.
  const NOT_STATUS = /\bshould\b|\bwhat is\b|\bhow long\b|\b(?:download|graph|chart|export|print|report|csv|pdf|clean|cleaning|noise|noisy|buy)\b/i;
  // The phrase a question fits, if any: [test, id], where the id "miss"
  // means no built-in answer fits and the honest note is right (3.42.0).
  const phraseFor = (text) => PHRASES.find(([re, id]) => re.test(String(text || ""))
    && (id === "miss" || TOPICS.some((t) => t.id === id && (!t.only || t.only()))));
  function bestTopic(text, own) {
    const phrased = phraseFor(text);
    if (phrased) return phrased[1] === "miss" ? null : TOPICS.find((t) => t.id === phrased[1]);
    const asked = wordsIn(text);
    const barred = (t) => Boolean(t) && t.id === "status" && NOT_STATUS.test(String(text || ""));
    let best = null;
    let top = 0;
    const score = (t) => asked.reduce((n, w) => n + (t.words.includes(w) ? 1 / SHARED[w] : 0), 0);
    TOPICS.forEach((t) => {
      if ((t.only && !t.only()) || barred(t)) return;
      const n = score(t);
      if (n > top + 1e-9) { best = t; top = n; }
    });
    if (own && !barred(own) && best && score(own) >= top - 1e-9) return own;
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
  // One word alone that says nothing about which answer (3.42.0): "ok"
  // got the list of your fridges.
  const VAGUE_ONE = new Set(["ok", "okay", "fine", "good", "yes", "yeah", "yep", "no", "nope", "thank", "thanks", "cool", "great",
    "sure", "alright", "help", "hmm", "lol", "please", "sorry", "test", "wrong", "problem", "work", "working", "new", "set", "add"]);
  function sureTopic(text) {
    const typed = String(text || "");
    const phrased = phraseFor(typed);
    if (phrased) return phrased[1] === "miss" ? null : TOPICS.find((t) => t.id === phrased[1]);
    const asked = wordsIn(typed);
    if (!asked.length) return null;
    const best = bestTopic(typed, null);
    if (!best) return null;
    const hits = asked.filter((w) => best.words.includes(w)).length;
    return (hits >= 2 && hits * 2 >= asked.length) || (asked.length === 1 && hits === 1 && !VAGUE_ONE.has(asked[0])) ? best : null;
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
      // A label's own words count for its item whatever it carries, so an
      // item whose label has more of the question's words would tie with
      // the host (3.42.0: "Who gets the alerts?" with "What does this
      // alert mean?" on the page). Such an item hosts it instead.
      const inLabel = (i) => wordsIn(i.label).filter((w) => asked.includes(w)).length;
      const most = Math.max(0, ...items.map(inLabel));
      const mine = items.find((i) => TOPIC_OF[i.label] === topic);
      const host = (mine && inLabel(mine) >= most ? mine : items.find((i) => inLabel(i) === most)) || items[0];
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
    // Never "It's fixed now" or what an alert means with no alert open
    // (3.41.0: "Can I change the alert wait?" was offered "It's fixed
    // now"), and never on a word three or more answers share alone: the
    // page's usual first questions are better than a guess.
    const live = alertOpen();
    const usable = TOPICS.filter((t) => (!t.only || t.only()) && t.id !== "quote" && (live || (t.id !== "fixed" && t.id !== "alert")));
    const scored = usable.map((t, order) => ({ t, order,
      n: asked.reduce((sum, w) => sum + (t.words.some((x) => near(x, w)) ? 1 / (SHARED[w] || 1) : 0), 0) }))
      .filter((x) => x.n >= 0.34).sort((a, b) => b.n - a.n || a.order - b.order).map((x) => x.t);
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
  const MISS_LEADS = /^(?:I don't have a built-in answer|That one isn't built in either|Still no built-in answer)/;
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
        const rest = text.slice(lead.length).replace(/^\s+/, "");
        // A note that nothing fits (3.42.0) is not something it can tell.
        if (leadDrawing.seen || MISS_LEADS.test(rest)) shown = rest;
        leadDrawing.seen = true;
      }
      return draw.call(this, shown, doc, opts);
    };
    R.kryozynLead = true;
  }

  // An item's answer, routed: pressed (the label itself), its own; typed,
  // the answer the question is surely after (3.37.0), else the best of
  // every built-in answer.
  // Which answer was given last, by its id (3.42.0), or null for a miss
  // note: for the tests' table of phrasings. Reads nothing but the words.
  let lastTopic = null;
  const idOf = (label) => (TOPIC_OF[label] ? TOPIC_OF[label].id : label);
  function routed(label, own) {
    return answered((ctx) => {
      const typed = ctx && typeof ctx.label === "string" ? ctx.label : "";
      if (!typed || sameLabel(typed, label)) { lastTopic = idOf(label); return own(ctx); }
      const route = asking && sameLabel(asking.text, typed) ? typedRoute() : null;
      if (route) { lastTopic = route.topic.id; return route.topic.answer(ctx); }
      // Sure of it, as a typed question sent from the chat box would be.
      const sure = sureTopic(typed);
      if (sure) { lastTopic = sure.id; return sure.answer(ctx); }
      const mine = TOPIC_OF[label] || null;
      const pick = bestTopic(typed, mine);
      if (!pick && mine && mine.id === "status" && NOT_STATUS.test(typed)) {
        lastTopic = null;
        return `${missNote() || "I don't have a built-in answer for that."}`.replace(/ The two closest are first below\./, " Ask **Which fridges need me?** for your fridges now.");
      }
      // Not sure (3.42.0): no phrase fits, and the closest answer shares
      // only one word with the question, or none (the item the core took
      // was "It's fixed now" for "How do I sign in?"). The honest note,
      // with the two closest under it, never a guess.
      const asked = wordsIn(typed);
      const hits = pick ? asked.filter((w) => pick.words.includes(w)).length : 0;
      const phrased = Boolean(pick) && Boolean(phraseFor(typed));
      if (!pick || !(phrased || hits >= 2 || (pick === mine && hits === 1 && asked.length === 1 && !VAGUE_ONE.has(asked[0])))) {
        lastTopic = null;
        return missNote() || "I don't have a built-in answer for that.";
      }
      lastTopic = pick.id;
      return pick.answer(ctx);
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

  // On /today (3.42.0) the alert's cards are kept clear by their words and
  // buttons, not their whole boxes: listed whole, at 320px with an alert
  // open they filled both screen edges from the top of the page down, so
  // neither the robot nor its handle had anywhere to be until a scroll.
  // The words stay listed, so nothing is covered; the handle peeks in at
  // the card's empty edge.
  const TODAY_EDGE = new Set(["#verdict", "#needs-wrap", "#needs", ".need"]);
  const TODAY_WORDS = ["#verdict > *", ".need-what", ".need-when"];

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
      // /today's "Texts are not switched on" note (3.43.0): at 375px with
      // an alert open the robot sat on its edge.
      "#texts-off",
      // The console's header (3.38.0): at 375px with the alert bar pinned
      // under it and the search box and tabs at the bottom, the free top
      // corner was the header's own, beside °F. Listed, the robot keeps
      // clear of it (it hides rather than sit there). Only the console's:
      // the public pages' and /today's headers stay off the list.
      "#app > .nav"].filter((sel) => !(onToday && TODAY_EDGE.has(sel))).concat(onToday ? TODAY_WORDS : []),
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
    // Which built-in answer a typed question gets, by its id (3.40.0): for
    // the tests' table of phrasings, and for anybody checking in the
    // browser's console. Reads nothing but the words.
    topicFor: (text) => { const t = sureTopic(text) || bestTopic(text, null); return t ? t.id : null; },
    // The answer the last typed question really got, by its id, or null
    // when it got the note for a question nothing fits (3.42.0).
    answeredTopic: () => lastTopic,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: F,
  });
})();
