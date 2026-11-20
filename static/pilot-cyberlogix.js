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
  const CHIPS = {
    "/": [["How does it work?", "How does CyberLogix work, in plain words?"],
      ["What do I need?", "What do I need to start? Which sensors work?"],
      ["What does it cost?", "What does CyberLogix cost, and what's in the free trial?"]],
    "/pricing": [["Which plan fits me?", "Which plan fits my business? Ask me one or two questions first."],
      ["What's in the trial?", "What's in the free trial, and do I need a card?"],
      ["Why is it better?", "What makes CyberLogix better than other ways of watching fridges?"]],
    "/help": [["How do alerts escalate?", "What happens when a fridge goes out of range, and what if nobody answers?"],
      ["A sensor is offline", "A sensor says offline. What should I do?"],
      ["Proof for my insurer", "How do I make a proof of monitoring report?"]],
    "/help/sensors": [["Which sensor should I buy?", "Which sensors work with CyberLogix, and roughly what do they cost?"],
      ["Set up step by step", "Walk me through setting up my first sensor."],
      ["Power or internet goes off?", "What happens if the power or the internet goes off?"]],
    "/for": [["Is it for my trade?", "Does CyberLogix work for my kind of business?"],
      ["What does it cost?", "What does CyberLogix cost?"]],
    "/demo": [["What am I seeing?", "What is this demo showing me?"],
      ["What happens next?", "What happens after the alert text, if nobody answers?"],
      ["Start a free trial", "How do I start a free trial?"]],
    "/console-out": [["How do I sign in?", "How do I sign in to the console?"],
      ["I forgot my password", "I forgot my password. What do I do?"],
      ["Start a free trial", "How do I start a free trial?"]],
  };

  const TASKS = {
    live: "Show my open alerts and tell me what to check first",
    "/": "Help me work out what I need to start watching my fridges: ",
    "/pricing": "Help me pick a plan for my business: ",
    "/help/sensors": "Help me set up this sensor: ",
  };

  function chips() {
    let list;
    if ((onConsole || onToday) && signedIn()) list = LIVE;
    else if (onConsole) list = CHIPS["/console-out"];
    else if (path.startsWith("/for/")) list = [["How would this work for me?", "How would CyberLogix work for a business like mine?"],
      ["What limit is set?", "What temperature limits does CyberLogix use for this trade, and can I change them?"],
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
    screen,
    chips,
    taskPrompt: () => ((onConsole || onToday) && signedIn() ? TASKS.live : TASKS[path] || "Help me get started on this page."),
    facts: {
      fromPrice: d.fromPrice || "", starterUnits: d.starterUnits || "", restaurantPrice: d.restaurantPrice || "",
      pharmacyPrice: d.pharmacyPrice || "", unitsPerLocation: d.unitsPerLocation || "", extraUnit: d.extraUnit || "",
      trialDays: d.trialDays || "", trialUnits: d.trialUnits || "", trialCalls: d.trialCalls || "",
      fridgeLimit: d.fridgeLimit || "", freezerLimit: d.freezerLimit || "", offlineMinutes: d.offlineMinutes || "",
      callAfterMinutes: d.callAfterMinutes || "", textWithinSeconds: d.textWithinSeconds || "",
    },
  });
})();
