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

  // Signed in, under the welcome's own five (so none says the same thing).
  const LIVE = [["Show open alerts", "Show my open alerts."],
    ["It's fixed now", "The fridge that went warm is fixed. Mark its alert fixed."],
    ["Make a proof report", "Make a proof of monitoring report for last month."]];
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
    // Core 1.2.0: the welcome is CyberLogix's own (docs/PILOT.md). The five
    // pills are what a café asks most; signed in, the console's tools answer
    // them (each change asks first), and on a public page Pilot says where
    // to look (screen().facts).
    tagline: "Your fridge-alarm helper",
    welcomePills: [
      { icon: "bars", label: "Which fridges need me?", action: "Which of my fridges and freezers need me right now, and what should I check first?", send: true },
      { icon: "help", label: "What does this alert mean?", action: "Explain my latest alert in plain words: what happened, where it stands now, and what I should do.", send: true },
      { icon: "chat", label: "Is the food still OK?", action: "Is the food still OK in the fridge or freezer that went warm?", send: true },
      { icon: "bolt", label: "Add a fridge", action: "Help me add a fridge or freezer.", send: true },
      { icon: "key", label: "Add my AI key", action: "settings" },
    ],
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
    keyNote: "An AI key is a private code you make on an AI company's website (OpenAI, Anthropic, Google AI Studio), usually under API keys. Your provider bills you for what Pilot uses. AI keys are for grown-ups (the AI companies require 18+).",
    audienceNote: "The people using this run businesses with fridges and freezers to keep cold: restaurants, pharmacies, labs and more. Many read on a phone in a busy kitchen, so keep answers short and practical. If a unit is warm right now, say to check it first. Pilot never makes a food safety decision: when asked if food is OK, give what was measured and the rule of thumb with its note that it is not a ruling, and say their own written food safety plan and local code decide.",
    kidView: () => false,
    // Never sit on top of an alert, a banner, the buttons that answer one,
    // the console's tabs or the front page's first-screen facts: the robot
    // moves to a free corner instead, and looks again as the page scrolls
    // (core 1.1.1). On a phone it sat on "Everything" and on "5 alert
    // calls"; on a desktop on "Advanced"; and on the Getting started card.
    avoid: ["#site-wide-banner", "#stale-banner", "#account-banner", "#delivery-banner",
      "#offline-banner", "#terms-banner", "#role-banner", "#needs-wrap", ".need-acts",
      "#needs-bar", ".needs-bar-acts", ".finder", ".f-tab",
      ".incident-actions", "#verdict", ".toast", ".hero-cta", ".cta-row", "#facts", ".facts",
      ".kicker", "#add-form button[type=submit]", ".fix-dialog",
      // The Getting started checklist and its buttons (Add a sensor, Add
      // me, Send a test): a new business's first screen.
      "#first-day", ".checklist-acts", ".btn-go"],
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
