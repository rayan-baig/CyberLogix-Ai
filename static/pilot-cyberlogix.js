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
  function screen() {
    if (onConsole) {
      if (!signedIn()) return { name: "Console sign-in" };
      const tab = window.CyberLogixConsole.tab();
      return { name: `Console (Advanced view)${tab ? `, ${tab} tab` : ""}`, facts: "The person is signed in." };
    }
    if (onToday) return { name: PAGES["/today"], facts: signedIn() ? "The person is signed in." : "Not signed in." };
    if (path.startsWith("/for/")) return { name: `Industry page: ${document.title}` };
    return { name: PAGES[path] || document.title };
  }

  const LIVE = [["What's warm now?", "Which units are warm or out of range right now?"],
    ["Show open alerts", "Show my open alerts."],
    ["Is the food still OK?", "Is the food still OK in the unit that went warm?"]];
  // Written for the person most of these pages are for: a café or small
  // restaurant with a few fridges and a freezer, reading on a phone.
  const CHIPS = {
    "/": [["Will it work for my café?", "I run a small café with a couple of fridges and a freezer. How would CyberLogix work for me, in plain words?"],
      ["What do I need to start?", "What do I need to start watching my fridges and freezer? What's the easiest kind of sensor?"],
      ["What would it cost me?", "What would CyberLogix cost a café with three fridges, and what's in the free trial?"]],
    "/pricing": [["Which plan for a café?", "Which plan fits a café with a few fridges and a freezer? Ask me one or two questions first."],
      ["Do you phone us?", "Which plans phone us if nobody answers the text?"],
      ["What's in the trial?", "What's in the free trial, and do I need a card?"]],
    "/help": [["A fridge is warm: what now?", "A fridge is too warm. What happens, and what should I do?"],
      ["Will a door left open wake us?", "If someone leaves the walk-in door open for a minute, does everyone get texted?"],
      ["A log for the inspector", "How do I print a temperature log for a health inspector?"]],
    "/help/sensors": [["Easiest way to set up", "What's the easiest way to set up a sensor, with no coding?"],
      ["Set up step by step", "Walk me through setting up my first fridge sensor."],
      ["Power or Wi-Fi goes off?", "What happens if the power or the Wi-Fi goes off?"]],
    "/for": [["Is it for my café?", "Does CyberLogix work for a café or small restaurant?"],
      ["What would it cost me?", "What would CyberLogix cost a café with three fridges?"]],
    "/demo": [["What am I seeing?", "What is this demo showing me?"],
      ["What happens next?", "What happens after the alert text, if nobody answers?"],
      ["Start a free trial", "How do I start a free trial?"]],
    "/console-out": [["How do I sign in?", "How do I sign in to the console?"],
      ["I forgot my password", "I forgot my password. What do I do?"],
      ["Start a free trial", "How do I start a free trial?"]],
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
      ["What does it cost?", "What does CyberLogix cost?"]];
    else list = CHIPS[path] || [];
    return list.map(([label, prompt]) => ({ label, prompt }));
  }

  window.Pilot.mount({
    appName: "CyberLogix AI",
    name: "Pilot",
    tagline: "Your AI copilot. Powered by you.",
    storagePrefix: "cyberlogix-pilot",
    pack: d.pack || "",
    // Exactly the hosts the security policy lets a browser reach for an
    // OpenAI-compatible API (main.PILOT_COMPATIBLE_HOSTS) and the person's
    // own computer.
    compatibleHosts: (d.hosts || "").split(/\s+/).filter(Boolean),
    privacyUrl: "/legal#privacy",
    keyNote: "Pilot uses your own AI key, and your AI provider bills you for it. AI keys are for grown-ups (the AI companies require 18+).",
    audienceNote: "The people using this run businesses with fridges and freezers to keep cold: restaurants, pharmacies, labs and more. Many read on a phone in a busy kitchen, so keep answers short and practical. If a unit is warm right now, say to check it first. Pilot never makes a food safety decision: when asked if food is OK, give what was measured and the rule of thumb with its note that it is not a ruling, and say their own written food safety plan and local code decide.",
    kidView: () => false,
    // Never sit on top of an alert, a banner or the buttons that answer
    // one: the robot moves to a free corner instead (core 1.1.0).
    avoid: ["#site-wide-banner", "#stale-banner", "#account-banner", "#delivery-banner",
      "#offline-banner", "#terms-banner", "#role-banner", "#needs-wrap", ".need-acts",
      ".incident-actions", "#verdict", ".toast", ".hero-cta", ".cta-row", "#add-form button[type=submit]"],
    screen,
    chips,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: {
      fromPrice: d.fromPrice || "", starterUnits: d.starterUnits || "", restaurantPrice: d.restaurantPrice || "",
      pharmacyPrice: d.pharmacyPrice || "", unitsPerLocation: d.unitsPerLocation || "", extraUnit: d.extraUnit || "",
      trialDays: d.trialDays || "", trialUnits: d.trialUnits || "", trialCalls: d.trialCalls || "",
      fridgeLimit: d.fridgeLimit || "", freezerLimit: d.freezerLimit || "", offlineMinutes: d.offlineMinutes || "",
      callAfterMinutes: d.callAfterMinutes || "", textWithinSeconds: d.textWithinSeconds || "",
      alertAfterFridge: d.alertAfterFridge || "", alertAfterFreezer: d.alertAfterFreezer || "",
      alertAfterVaccine: d.alertAfterVaccine || "", alertAtOnceDegrees: d.alertAtOnceDegrees || "",
    },
  });
})();
