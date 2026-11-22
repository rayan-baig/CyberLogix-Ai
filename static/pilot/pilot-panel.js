// Pilot core 1.4.0 · pilot-panel.js
// The web chat window that opens from the robot: the Pilot header, the
// greeting and its pill buttons (the design's five unless the app sets its
// own: config.welcomePills, with config.tagline under the name; with its
// own key pill there is no key card too), this
// page's suggestions, the messages, the "Type a message…" box, and the
// gear, palette and ? buttons at the bottom. Settings (keys, models, image
// and video keys unless config.hideKeySections hides them, memory) and help
// open inside the same frame. A message typed before there is a key stays
// in the box and is sent once a key is added. Loaded only when the user
// first opens Pilot (pilot.js).
//
// Core 1.4.0: a pill or a page suggestion can also need no AI at all: a
// `link` (a page of this site) or an `answer` (the app's own words, shown as
// Pilot's message at once, with no key and nothing sent anywhere).
//
// Every word on screen is placed with textContent or built by
// pilot-render.js from text nodes: nothing is ever set as HTML. No style
// attributes either; the look is all classes in pilot-panel.css.
(function () {
  "use strict";

  const VERSION = "1.4.0";
  const SCREEN_LIMIT = 3500;
  const R = window.PilotRender;
  const P = window.PilotProviders;
  const M = window.PilotMedia;
  const SVGNS = "http://www.w3.org/2000/svg";

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children) if (child !== null && child !== undefined && child !== false) node.append(child);
    return node;
  }

  // replaceChildren() turns null into the text "null", so empty slots are dropped first.
  function put(node, ...kids) {
    node.replaceChildren(...kids.filter((k) => k !== null && k !== undefined && k !== false));
  }

  // Line icons, drawn with SVG paths (no pictures, no icon font).
  const ICONS = {
    gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm7.4 3a7.5 7.5 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.4 7.4 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.5 7.5 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.4 7.4 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.4 7.4 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z",
    palette: "M12 3a9 9 0 0 0 0 18c1.1 0 1.7-.8 1.7-1.6 0-.9-.7-1.3-.7-2.1 0-.9.7-1.6 1.6-1.6H17a4 4 0 0 0 4-4C21 6.9 17 3 12 3ZM7.5 12a1.3 1.3 0 1 1 0-2.6 1.3 1.3 0 0 1 0 2.6Zm2.5-3.6a1.3 1.3 0 1 1 0-2.6 1.3 1.3 0 0 1 0 2.6Zm4 0a1.3 1.3 0 1 1 0-2.6 1.3 1.3 0 0 1 0 2.6Zm2.5 3.6a1.3 1.3 0 1 1 0-2.6 1.3 1.3 0 0 1 0 2.6Z",
    help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-2.6-11.6A2.7 2.7 0 0 1 12 7.5c1.6 0 2.8 1 2.8 2.4 0 1.9-2.6 2-2.6 3.8M12 16.6v.4",
    send: "M3.5 11.5 20.5 4l-4.6 16.2-4.4-6.1-8-2.6Zm8 2.6 9-10.1",
    stop: "M7 7h10v10H7z",
    mic: "M12 4a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3Zm-6 8a6 6 0 0 0 12 0M12 18v3",
    chat: "M4 5h16v11H9l-5 4V5Zm4 5.5h.01M12 10.5h.01M16 10.5h.01",
    bolt: "M13 2 5 13.5h6L10 22l8-11.5h-6L13 2Z",
    code: "M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5M13.5 5l-3 14",
    bars: "M5 20v-6M10 20V9M15 20v-8M20 20V5",
    key: "M14.5 3.5a5.5 5.5 0 0 0-5.2 7.3L3.5 16.6v3.9h3.9l1-1V18h1.5v-1.5h1.5l1.2-1.2A5.5 5.5 0 1 0 14.5 3.5Zm1.5 5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z",
    copy: "M9 9h10v10H9zM5 15V5h10",
    spark: "M12 2.5l2.2 6.6 6.8 2.4-6.8 2.4L12 20.5l-2.2-6.6L3 11.5l6.8-2.4Z",
    chevron: "M9 5l7 7-7 7",
    back: "M15 6l-6 6 6 6",
    minus: "M5 12h14",
    close: "M6 6l12 12M18 6 6 18",
    download: "M12 4v11m-5-5 5 5 5-5M5 20h14",
  };

  function icon(name) {
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("class", `pilot-icon pilot-icon-${name}`);
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", ICONS[name]);
    svg.appendChild(path);
    return svg;
  }

  // ---------------------------------------------------------------- what the screen shows
  // "Analyze & explain…" and "Summarise this" send a text outline of what is
  // on the page now: headings and visible words. Never what is typed into a
  // box (no input, textarea or select is read), nothing hidden, nothing the
  // app marks data-pilot-private (a key, a code), nothing of Pilot's own,
  // and email addresses and phone numbers are blanked out.

  const SKIP = "script,style,noscript,template,svg,canvas,input,textarea,select,option,button,[hidden],[aria-hidden='true'],"
    + "[data-pilot-private],.pilot-root,dialog:not([open])";
  const BLOCK = "h1,h2,h3,h4,h5,h6,p,li,dt,dd,td,th,figcaption,blockquote,summary,label,legend,a,small,b,strong,span,div,section,article,header,footer,main,nav";

  function redact(text) {
    return text
      .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
      .replace(/\+?\d[\d\s().-]{7,}\d/g, "[number]");
  }

  function screenText(rootNode) {
    const start = rootNode || document.querySelector("main") || document.body;
    const walker = document.createTreeWalker(start, NodeFilter.SHOW_TEXT);
    const lines = [];
    let lastBlock = null;
    let used = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const words = node.nodeValue.replace(/\s+/g, " ").trim();
      if (!words) continue;
      const parent = node.parentElement;
      if (!parent || parent.closest(SKIP)) continue;
      if (!parent.getClientRects().length) continue; // not shown
      const block = parent.closest(BLOCK) || parent;
      const heading = /^H[1-4]$/.test(block.tagName);
      if (block === lastBlock && lines.length) lines[lines.length - 1] += ` ${words}`;
      else lines.push(heading ? `## ${words}` : words);
      lastBlock = block;
      used += words.length;
      if (used > SCREEN_LIMIT * 2) break;
    }
    const out = [];
    for (const line of lines) if (line !== out[out.length - 1]) out.push(line);
    return redact(out.join("\n")).slice(0, SCREEN_LIMIT);
  }

  // ---------------------------------------------------------------- the welcome's pills
  // The design's five, unless the app gives its own (config.welcomePills):
  // up to five {icon, label, action}. `action` is one of the built-ins
  // below, or any other words: a prompt put in the box (with `send: true`,
  // sent at once; before there is a key it waits in the box).
  // Core 1.4.0: instead of `action`, a pill may have
  //   `link`: a page of this site ("/demo"): pressing it goes there;
  //   `answer`: the app's own words (text, or a function (ctx) returning
  //     text or a promise of it), shown as Pilot's message at once, with no
  //     key and no network call (markdown, rendered safely; links to this
  //     site work).
  // Page suggestions (config.chips) take `link` and `answer` too.

  const DEFAULT_TAGLINE = "Your AI copilot. Powered by you.";
  const BUILTINS = ["ask", "task", "code", "explain-screen", "summarise-screen", "summarize-screen", "settings", "help"];
  const DEFAULT_PILLS = [
    { icon: "chat", label: "Ask anything…", action: "ask" },
    { icon: "bolt", label: "Get things done…", action: "task" },
    { icon: "code", label: "Help with code…", action: "code" },
    { icon: "bars", label: "Analyze & explain…", action: "explain-screen" },
    { icon: "gear", label: "Customize with your API key", action: "settings" },
  ];
  const PILL_LABEL_MOST = 40;
  const PROMPT_MOST = 1000;
  const ANSWER_MOST = 4000;
  const ANSWER_WAIT = 8000;
  // The line under an app's own answer while there is no key (core 1.4.0);
  // config.answerMore changes it, or false leaves it out.
  const DEFAULT_MORE = "Want more? Add your AI key to ask anything.";

  // A page of this site, as a full address, or "" (core 1.4.0). Only the
  // same site: "/demo", "pricing", "#faq", or a full address on this site.
  // base: this page's address.
  function siteLink(link, base) {
    if (typeof link !== "string" || !link.trim() || link.length > 500) return "";
    const where = base || (typeof location !== "undefined" ? location.href : "");
    try {
      const url = new URL(link.trim(), where);
      const here = new URL(where);
      if ((url.protocol !== "https:" && url.protocol !== "http:") || url.origin !== here.origin) return "";
      return url.href;
    } catch (_) {
      return "";
    }
  }

  // What a pill or a suggestion does, cleaned (core 1.4.0): {link} for a
  // page of this site, {answer} for the app's own words, or null for an
  // AI prompt or a built-in.
  function noAi(p, base) {
    if (!p || typeof p !== "object") return null;
    if (p.link !== undefined && p.link !== null) { const link = siteLink(p.link, base); return link ? { link } : { bad: true }; }
    if (typeof p.answer === "function") return { answer: p.answer };
    if (typeof p.answer === "string") return p.answer.trim() ? { answer: p.answer.slice(0, ANSWER_MOST) } : { bad: true };
    return null;
  }

  // The app's own pills, cleaned: words only, a known icon, at most five.
  // [] when it gives none that work. A link to another site, or an empty
  // answer, is left out (core 1.4.0). base: this page's address.
  function ownPills(given, base) {
    const out = [];
    for (const p of Array.isArray(given) ? given : []) {
      if (out.length >= 5) break;
      if (!p || typeof p !== "object") continue;
      const label = typeof p.label === "string" ? p.label.replace(/\s+/g, " ").trim().slice(0, PILL_LABEL_MOST) : "";
      const icon0 = typeof p.icon === "string" && Object.prototype.hasOwnProperty.call(ICONS, p.icon) ? p.icon : "spark";
      const plain = noAi(p, base);
      if (plain) {
        if (label && !plain.bad) out.push({ icon: icon0, label, action: "", builtin: false, send: false, ...plain });
        continue;
      }
      // A prompt keeps its words as given (a trailing space leaves the cursor
      // ready after "My idea: "); a built-in's name is matched trimmed.
      const raw = typeof p.action === "string" ? p.action.slice(0, PROMPT_MOST) : "";
      const action = BUILTINS.includes(raw.trim()) ? raw.trim() : raw;
      if (!label || !raw.trim()) continue;
      out.push({ icon: icon0, label, action, builtin: BUILTINS.includes(action), send: p.send === true });
    }
    return out;
  }

  // This page's suggestions, cleaned (at most three): {label, prompt} (an AI
  // prompt, as before), or {label, link} / {label, answer} (core 1.4.0).
  function cleanChips(list, base) {
    const out = [];
    for (const c of Array.isArray(list) ? list : []) {
      if (out.length >= 3) break;
      if (!c || typeof c.label !== "string" || !c.label.trim()) continue;
      const plain = noAi(c, base);
      if (plain && plain.bad) continue;
      out.push(plain ? { label: c.label, ...plain } : { label: c.label, prompt: typeof c.prompt === "string" ? c.prompt : "" });
    }
    return out;
  }

  // The pills to show: the app's, or, if it gives none that work, the
  // design's five.
  function welcomePills(given, base) {
    const out = ownPills(given, base);
    return out.length ? out : DEFAULT_PILLS.map((p) => ({ ...p, builtin: true, send: false }));
  }

  // Does the app's own welcome already have the key pill (the built-in
  // "settings")? Then the welcome asks for the key once (core 1.3.0): no
  // key card under the pills. The design's five keep their card.
  const keyPillGiven = (given) => ownPills(given).some((p) => p.builtin && p.action === "settings");

  // The words about keys. The design's (Pilot for Mac and the default
  // welcome) say "API key"; a website with its own welcome says "AI key",
  // the words its visitors know (core 1.3.0).
  const KEY_WORDS = {
    design: { title: "Bring Your Own API Key", sub: "Use your OpenAI, Claude, Gemini, or any compatible API key.",
      paste: "Paste your API key", label: "API key ", first: "Paste your API key first." },
    site: { title: "Your own AI key", sub: "Use an AI key from OpenAI, Claude, Gemini or a compatible service.",
      paste: "Paste your AI key", label: "AI key ", first: "Paste your AI key first." },
  };
  const keyWords = (given) => (ownPills(given).length ? KEY_WORDS.site : KEY_WORDS.design);

  // The key settings an app hides (config.hideKeySections): "image", "video".
  const hiddenSections = (given) => new Set((Array.isArray(given) ? given : []).filter((x) => x === "image" || x === "video"));

  // ---------------------------------------------------------------- the window

  function create(options) {
    const { root, launcher, store, config, pilot } = options;
    let isOpen = false;
    let view = "chat";
    let drawQueued = false;
    let state = { messages: store.loadChat(), busy: false, streaming: "", status: "" };
    let otherStream = "";
    let otherBusy = false;
    const cards = new Set(); // confirm and plan cards waiting for an answer
    const media = []; // pictures and videos made on this page: {at, kind, ...}
    let lastShared = 0;
    let editing = null; // which key form is open in settings
    // Asked for before there was a key: kept (and left in the box) until a
    // key is added, then sent. {text} or {screen: [prompt, label]}.
    let pending = null;
    const pills = welcomePills(config.welcomePills, location.href);
    const oneKeyAsk = keyPillGiven(config.welcomePills);
    const words = keyWords(config.welcomePills);
    const hidden = hiddenSections(config.hideKeySections);

    const screen = () => {
      try { return (typeof config.screen === "function" && config.screen()) || { name: document.title }; } catch (_) { return { name: document.title }; }
    };
    const hosts = config.compatibleHosts || [];

    // ---------------------------------------------------------------- the engine's host

    let streamTimer = null;
    let streamLatest = "";
    function streamPost(text) {
      streamLatest = text;
      if (streamTimer) return;
      streamTimer = setTimeout(() => { streamTimer = null; pilot.post({ type: "stream", text: streamLatest }); }, 300);
    }

    const host = {
      fetch: window.fetch.bind(window),
      exclusive: (fn) => pilot.exclusive(fn),
      update(next) {
        const before = state.messages.length;
        state = next;
        if (next.messages.length !== before || !next.busy) pilot.post({ type: "chat", messages: next.messages });
        if (next.streaming) streamPost(next.streaming);
        queueDraw();
        pilot.status();
      },
      confirm(label, step) { return card("confirm", { label, step }); },
      plan(labels) { return card("plan", { labels }); },
      progress(i, how) {
        const live = cardBox.querySelector(".pilot-plan-live");
        if (live) {
          const li = live.querySelectorAll("li")[i];
          if (li) li.className = `pilot-step-${how}`;
        }
      },
      cancelCards() {
        for (const c of Array.from(cards)) c.answer(c.kind === "plan" ? "cancel" : false);
      },
      image: (prompt) => makeImage(prompt),
      video: (prompt) => makeVideo(prompt),
    };

    const engineConfig = {
      appName: config.appName,
      compatibleHosts: hosts,
      kidView: config.kidView,
      audienceNote: config.audienceNote,
      knowledge: () => (window.PilotPack && window.PilotPack.knowledge) || config.knowledge || "",
      strengths: () => (window.PilotPack && window.PilotPack.strengths) || config.strengths || [],
      tools: () => (window.PilotPack && typeof window.PilotPack.tools === "function" ? window.PilotPack.tools() : config.tools || []),
      screen: () => {
        const s = screen();
        const extra = window.PilotPack && typeof window.PilotPack.facts === "function" ? window.PilotPack.facts(s) : "";
        return { name: s.name, facts: [s.facts, extra].filter(Boolean).join("\n") };
      },
    };
    const engine = window.PilotEngine.create({ store, providers: P, config: engineConfig, host });

    // ---------------------------------------------------------------- building the frame

    const minBtn = el("button", { type: "button", class: "pilot-win", "aria-label": "Minimise Pilot", title: "Minimise" }, icon("minus"));
    const closeBtn = el("button", { type: "button", class: "pilot-win", "aria-label": "Close Pilot", title: "Close" }, icon("close"));
    const head = el("header", { class: "pilot-head" },
      el("div", { class: "pilot-winbtns" }, minBtn, closeBtn),
      el("div", { class: "pilot-brand" }, window.PilotMascot.mark(document),
        el("div", { class: "pilot-brand-words" },
          el("b", { class: "pilot-word", text: config.name || "Pilot" }),
          el("span", { class: "pilot-tagline", text: (typeof config.tagline === "string" && config.tagline.trim()) || DEFAULT_TAGLINE }))));

    const welcome = el("div", { class: "pilot-welcome" });
    const log = el("ol", { class: "pilot-log", "aria-live": "polite", "aria-label": "Chat with Pilot" });
    const cardBox = el("div", { class: "pilot-cards" });
    const chips = el("div", { class: "pilot-chips", role: "group", "aria-label": "Suggestions" });
    // Once a chat has started, this page's suggestions come first, then the
    // conversation; the greeting and its five pills are for starting.
    const chatView = el("div", { class: "pilot-view pilot-chat-view" }, welcome, chips, log, cardBox);
    const settingsView = el("div", { class: "pilot-view pilot-settings-view", hidden: true });
    const helpView = el("div", { class: "pilot-view pilot-help-view", hidden: true });
    const scroller = el("div", { class: "pilot-scroll" }, chatView, settingsView, helpView);

    const input = el("textarea", { class: "pilot-input", rows: "1", maxlength: "4000", placeholder: "Type a message…",
      "aria-label": "Message to Pilot", autocomplete: "off" });
    const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = Speech ? el("button", { type: "button", class: "pilot-mic", "aria-label": "Speak your message",
      title: "Speak (your browser's own speech recognition)" }, icon("mic")) : null;
    const sendBtn = el("button", { type: "submit", class: "pilot-send", "aria-label": "Send" }, icon("send"));
    const form = el("form", { class: "pilot-compose" }, input, mic, sendBtn);

    const statusLine = el("span", { class: "pilot-state", role: "status" }, el("i", { class: "pilot-state-dot" }), el("span"));
    const gear = el("button", { type: "button", class: "pilot-round", "aria-label": "Settings", title: "Settings" }, icon("gear"));
    const palette = el("button", { type: "button", class: "pilot-round", "aria-label": "Change Pilot's look", title: "Theme" }, icon("palette"));
    const helpBtn = el("button", { type: "button", class: "pilot-round", "aria-label": "Help", title: "Help" }, icon("help"));
    const bottom = el("div", { class: "pilot-bottom" }, statusLine, el("span", { class: "pilot-rounds" }, gear, palette, helpBtn));
    // The grown-ups note sits just above the message box, so it is always
    // in full view (on a phone it was under the box, half cut off).
    const footNote = el("p", { class: "pilot-grownup pilot-foot-note", hidden: true });
    const foot = el("footer", { class: "pilot-foot" }, footNote, form, bottom);

    const panel = el("section", { class: "pilot-panel", role: "dialog", "aria-label": `${config.name || "Pilot"} chat`, hidden: true },
      head, scroller, foot);
    root.appendChild(panel);
    applyTheme(store.ui().theme);

    function applyTheme(theme) {
      root.classList.toggle("pilot-theme-calm", theme === "calm");
    }

    // ---------------------------------------------------------------- the welcome

    function pill(name, label, run) {
      return el("button", { type: "button", class: "pilot-pill", on: { click: run } }, icon(name), el("span", { text: label }));
    }

    function keyCard() {
      return el("button", { type: "button", class: "pilot-keycard", on: { click: () => show("settings") } },
        icon("key"),
        el("span", { class: "pilot-keycard-words" }, el("b", { text: words.title }), el("small", { text: words.sub })),
        icon("chevron"));
    }

    // ---------------------------------------------------------------- no AI needed (core 1.4.0)

    // A page of this site: go there (Pilot closes first, so the page shows).
    function go(link) {
      try { pilot.close(); } catch (_) { /* fine */ }
      location.assign(link);
    }

    // The app's own answer, shown as Pilot's message: no key, nothing sent.
    // The question shows as the visitor's message (the pill's words). With
    // no key yet, a line under it offers the key (config.answerMore).
    let answering = false;
    async function answerWith(label, answer) {
      if (answering || state.busy) return;
      answering = true;
      let text = "";
      try {
        const ctx = { screen: screen(), facts: config.facts, ready: store.ready(), label };
        const got = typeof answer === "function" ? answer(ctx) : answer;
        text = await Promise.race([Promise.resolve(got), new Promise((resolve) => setTimeout(() => resolve(""), ANSWER_WAIT))]);
      } catch (_) {
        text = "";
      } finally {
        answering = false;
      }
      text = typeof text === "string" ? text.trim().slice(0, ANSWER_MOST) : "";
      const more = config.answerMore === false ? "" : (typeof config.answerMore === "string" && config.answerMore.trim()) || DEFAULT_MORE;
      const add = text ? [{ role: "user", text: label }, { role: "assistant", text, local: true }]
        : [{ role: "user", text: label }, { role: "note", kind: "error", text: "Pilot couldn't find that answer just now. Try again in a moment." }];
      if (text && more && !store.ready()) add.push({ role: "note", kind: "more", text: more });
      engine.replace(state.messages.concat(add));
      state = { ...state, messages: engine.messages };
      pilot.post({ type: "chat", messages: state.messages });
      if (view !== "chat") show("chat");
      queueDraw();
    }

    // A suggestion or pill that needs no AI: true if it was one.
    function plainRun(item, label) {
      if (item.link) { go(item.link); return true; }
      if (item.answer !== undefined) { answerWith(label, item.answer); return true; }
      return false;
    }

    // What a pill does when pressed.
    function runPill(p) {
      const s = screen();
      if (plainRun(p, p.label)) return;
      if (!p.builtin) { if (p.send) ask(p.action); else fill(p.action); return; }
      if (p.action === "ask") { input.focus(); return; }
      if (p.action === "task") {
        let task = "";
        try { task = typeof config.taskPrompt === "function" ? config.taskPrompt(s) : ""; } catch (_) { task = ""; }
        fill(task || "What can you do for me on this page?");
        return;
      }
      if (p.action === "code") { fill("Help me with this code: "); return; }
      if (p.action === "explain-screen") { shareScreen("Explain this screen: what is it for, and what can I do here?", "Analyze & explain this screen"); return; }
      if (p.action === "summarise-screen" || p.action === "summarize-screen") { shareScreen("Summarise what's on this screen in a few short points.", "Summarise this screen"); return; }
      if (p.action === "help") { show("help"); return; }
      show("settings");
    }

    // This page's suggestions (config.chips), at most three: a prompt, or
    // (core 1.4.0) a link or the app's own answer.
    function pageChips() {
      let list = [];
      try { list = (typeof config.chips === "function" ? config.chips(screen()) : config.chips) || []; } catch (_) { list = []; }
      return cleanChips(list, location.href);
    }
    const runChip = (c) => { if (!plainRun(c, c.label)) ask(c.prompt || c.label); };

    function drawWelcome() {
      // The page's suggestions are on the welcome too, before a key is
      // added: pressing one with no key keeps it waiting in the box.
      const here = pageChips();
      put(welcome,
        el("div", { class: "pilot-hello" }, icon("spark"),
          el("p", {}, el("span", { text: "Hello! I'm Pilot." }), el("br"), el("span", { text: "How can I help you today?" }))),
        el("div", { class: "pilot-pills" }, ...pills.map((p) => pill(p.icon, p.label, () => runPill(p)))),
        here.length ? el("div", { class: "pilot-chips pilot-chips-welcome", role: "group", "aria-label": "Suggestions for this page" },
          ...here.map((c) => el("button", { type: "button", class: "pilot-chip", text: c.label, on: { click: () => runChip(c) } }))) : null,
        // The app's own key pill already asks for the key: no card as well.
        store.ready() || oneKeyAsk ? null : keyCard());
      welcome.hidden = state.messages.length > 0;
    }

    // The grown-ups note above the box, until a key is added. On the
    // welcome an app may give a one-line version (config.keyNoteShort, core
    // 1.3.0), so the pills and suggestions keep their room; the full note
    // is then its tooltip, and beside the key settings.
    function drawFootNote() {
      const want = Boolean(config.keyNote) && !store.ready() && view === "chat";
      footNote.hidden = !want;
      if (!want) return;
      const short = typeof config.keyNoteShort === "string" && config.keyNoteShort.trim() && !state.messages.length
        ? config.keyNoteShort.trim() : "";
      const text = short || config.keyNote;
      if (footNote.textContent !== text) footNote.textContent = text;
      footNote.classList.toggle("pilot-foot-note-short", Boolean(short));
      if (short) footNote.setAttribute("title", config.keyNote); else footNote.removeAttribute("title");
    }

    // ---------------------------------------------------------------- before there is a key
    // A message (or a pill or suggestion) asked for with no key isn't
    // thrown away: it stays in the box, key settings open, and it is sent
    // as soon as a key is added (here or in another tab).

    function hold(job) {
      pending = job;
      if (job.text) fill(job.text);
      show("settings");
    }

    function sendPending() {
      if (!pending || !store.ready() || state.busy || otherBusy) return;
      const job = pending;
      pending = null;
      if (view !== "chat") show("chat");
      if (job.text) {
        if (input.value.trim() === job.text.trim()) { input.value = ""; grow(); }
        ask(job.text);
      } else if (job.screen) {
        lastShared = 0;
        shareScreen(job.screen[0], job.screen[1]);
      }
    }

    function fill(text) {
      input.value = text;
      grow();
      input.focus();
      try { input.setSelectionRange(text.length, text.length); } catch (_) { /* fine */ }
    }

    // ---------------------------------------------------------------- drawing the chat

    function queueDraw() {
      if (drawQueued) return;
      drawQueued = true;
      requestAnimationFrame(() => { drawQueued = false; draw(); });
    }

    function drawStatus() {
      const s = store.settings();
      let words;
      let kind;
      if (state.busy || otherBusy) { words = state.status === "acting" ? "Working…" : "Thinking…"; kind = "thinking"; }
      else if (!store.ready(s)) { words = "No key yet"; kind = "nokey"; }
      else { words = `Online · ${P.providerName(s)} · ${s.model}`; kind = "ready"; }
      statusLine.className = `pilot-state pilot-state-${kind}`;
      statusLine.lastChild.textContent = words;
      statusLine.setAttribute("title", words);
    }

    function copyButton(text, opts) {
      const b = el("button", { type: "button", class: "pilot-copy", "aria-label": "Copy this answer", title: "Copy" }, icon("copy"));
      b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(R.plain(text, opts)); b.classList.add("done"); setTimeout(() => b.classList.remove("done"), 1200); }
        catch (_) { /* the browser said no */ }
      });
      return b;
    }

    function messageNode(m) {
      if (m.role === "user") {
        const li = el("li", { class: "pilot-msg pilot-from-user" }, el("p", { text: m.label || m.text }));
        if (m.shared) {
          li.append(el("details", { class: "pilot-shared" },
            el("summary", { text: `What Pilot sent from this screen (${m.shared.length} characters)` }),
            el("pre", { text: m.shared })));
        }
        return li;
      }
      if (m.role === "assistant") {
        if (!m.text) return null;
        const body = el("div", { class: "pilot-md" });
        // The app's own answer (core 1.4.0): its links to this site work.
        const opts = m.local ? { local: location.href } : undefined;
        body.append(R.render(m.text, document, opts));
        return el("li", { class: "pilot-msg pilot-from-pilot" }, body, copyButton(m.text, opts));
      }
      if (m.role === "tool") {
        const shown = m.results.map((r) => r.shown).filter(Boolean);
        if (shown.length) {
          return el("li", { class: "pilot-msg pilot-done" },
            el("ul", {}, ...shown.join("\n").split("\n").map((line) => el("li", { text: line }))));
        }
        const names = m.results.map((r) => r.name.replace(/_/g, " "));
        return el("li", { class: "pilot-msg pilot-tool", text: `Pilot looked at: ${names.join(", ")}` });
      }
      // The line after an app's own answer, offering the key (core 1.4.0):
      // gone once a key is added.
      if (m.role === "note" && m.kind === "more") {
        if (store.ready()) return null;
        return el("li", { class: "pilot-msg pilot-note pilot-more" }, el("span", { text: m.text }), " ",
          el("button", { type: "button", class: "pilot-link", text: "Key settings", on: { click: () => show("settings") } }));
      }
      if (m.role === "note") return el("li", { class: `pilot-msg pilot-note${m.kind === "error" ? " bad" : ""}`, text: m.text });
      return null;
    }

    function mediaNode(item) {
      if (item.kind === "image") {
        if (item.error) return el("li", { class: "pilot-msg pilot-note bad", text: item.error });
        if (item.url) {
          return el("li", { class: "pilot-msg pilot-from-pilot" }, el("p", {}, el("span", { text: "The picture is ready at your provider: " }),
            el("a", { href: item.url, target: "_blank", rel: "noopener noreferrer", text: "open it" })));
        }
        const src = `data:${item.mime};base64,${item.data}`;
        return el("li", { class: "pilot-msg pilot-from-pilot pilot-media" },
          el("img", { src, alt: `Picture: ${item.prompt}`, width: "512", height: "512", class: "pilot-image" }),
          el("a", { class: "pilot-btn pilot-dl", href: src, download: `pilot-image.${item.mime.split("/")[1] || "png"}` }, icon("download"),
            el("span", { text: "Download" })),
          el("small", { class: "pilot-small", text: "Kept on this page only. Download it to keep it." }));
      }
      // A video.
      if (item.error) return el("li", { class: "pilot-msg pilot-note bad", text: item.error });
      if (item.blobUrl) {
        return el("li", { class: "pilot-msg pilot-from-pilot pilot-media" },
          el("video", { src: item.blobUrl, controls: true, playsinline: true, class: "pilot-video", width: "512", height: "288" }),
          el("a", { class: "pilot-btn pilot-dl", href: item.blobUrl, download: "pilot-video.mp4" }, icon("download"), el("span", { text: "Download" })),
          el("small", { class: "pilot-small", text: "Kept on this page only. Download it to keep it." }));
      }
      const bar = el("progress", { class: "pilot-progress", max: "100" });
      if (item.progress !== null && item.progress !== undefined) bar.value = item.progress;
      return el("li", { class: "pilot-msg pilot-from-pilot pilot-media" },
        el("p", { text: `Making your video${item.progress ? ` · ${Math.round(item.progress)}%` : "…"}` }), bar,
        el("small", { class: "pilot-small", text: "This can take a few minutes. You can keep chatting; stay on this page to see it." }));
    }

    function draw() {
      drawStatus();
      drawFootNote();
      panel.classList.toggle("pilot-busy", state.busy || otherBusy);
      drawWelcome();
      const nodes = [];
      state.messages.forEach((m, i) => {
        for (const item of media) if (item.at === i) nodes.push(mediaNode(item));
        nodes.push(messageNode(m));
      });
      for (const item of media) if (item.at >= state.messages.length) nodes.push(mediaNode(item));
      const live = state.busy ? state.streaming : otherBusy ? otherStream : "";
      if (live) {
        const body = el("div", { class: "pilot-md" });
        body.append(R.render(live, document));
        nodes.push(el("li", { class: "pilot-msg pilot-from-pilot pilot-live" }, body));
      } else if ((state.busy || otherBusy) && !cards.size) {
        nodes.push(el("li", { class: "pilot-msg pilot-from-pilot pilot-wait" },
          el("span", { class: "pilot-dots", role: "img",
            "aria-label": otherBusy && !state.busy ? "Pilot is answering in another tab" : "Pilot is thinking" },
          el("i"), el("i"), el("i"))));
      }
      put(log, ...nodes.filter(Boolean));
      // A plan's live progress goes once the turn is over: its summary is in the chat.
      if (!state.busy) { const live = cardBox.querySelector(".pilot-live-plan"); if (live) live.remove(); }
      put(sendBtn, icon(state.busy ? "stop" : "send"));
      sendBtn.setAttribute("aria-label", state.busy ? "Stop" : "Send");
      sendBtn.classList.toggle("pilot-stop", state.busy);
      sendBtn.type = state.busy ? "button" : "submit";
      sendBtn.disabled = otherBusy && !state.busy;
      drawChips();
      // The greeting stays in view until a conversation starts; then the
      // newest message is kept in view.
      if (view === "chat") scroller.scrollTop = state.messages.length || cards.size ? scroller.scrollHeight : 0;
    }

    // Suggestions for this page, first once a conversation has started
    // (above it, so they never move while an answer streams in; they wait
    // while Pilot is busy or asking about a change).
    function drawChips() {
      const busy = state.busy || otherBusy;
      chips.hidden = state.messages.length === 0;
      chips.classList.toggle("pilot-chips-wait", busy || cards.size > 0);
      if (chips.hidden) return;
      if (busy || cards.size > 0) {
        chips.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        return;
      }
      const all = pageChips().map((c) => ({ label: c.label, run: () => runChip(c) }));
      all.push({ label: "Summarise this", run: () => shareScreen("Summarise what's on this screen in a few short points.", "Summarise this screen") });
      put(chips, ...all.map((c) => el("button", { type: "button", class: "pilot-chip", text: c.label, on: { click: c.run } })),
        el("button", { type: "button", class: "pilot-link pilot-clear", text: "Clear chat", on: { click: clearChat } }));
    }

    function clearChat() {
      engine.clear();
      media.length = 0;
      state = { messages: [], busy: false, streaming: "", status: "" };
      pilot.post({ type: "chat", messages: [] });
      draw();
    }

    // ---------------------------------------------------------------- cards (confirm and plan)

    function card(kind, data) {
      return new Promise((resolve) => {
        const box = el("div", { class: `pilot-card pilot-card-${kind}`, role: "group" });
        const entry = { kind, answer: null };
        const done = (value) => {
          if (!cards.has(entry)) return;
          cards.delete(entry);
          box.remove();
          if (kind === "plan" && (value === "all" || value === "step")) {
            // The plan stays on screen while it runs, with a mark per step.
            const run = el("ol", { class: "pilot-plan-live" }, ...data.labels.map((l) => el("li", { text: l })));
            const old = cardBox.querySelector(".pilot-live-plan");
            if (old) old.remove();
            cardBox.prepend(el("div", { class: "pilot-card pilot-live-plan" }, el("p", { class: "pilot-card-title", text: "Pilot is doing (Stop works at any time):" }), run));
          }
          drawChips();
          resolve(value);
        };
        entry.answer = done;
        if (kind === "confirm") {
          const step = data.step;
          box.append(
            el("p", { class: "pilot-card-title", text: step ? `Step ${step.step} of ${step.of}: Pilot wants to` : "Pilot wants to:" }),
            el("p", { class: "pilot-card-what", text: data.label }),
            el("div", { class: "pilot-card-actions" },
              el("button", { type: "button", class: "pilot-btn pilot-btn-go", text: "Allow", on: { click: () => done(true) } }),
              el("button", { type: "button", class: "pilot-btn", text: "Cancel", on: { click: () => done(false) } })));
          box.setAttribute("aria-label", `Pilot wants to ${data.label}`);
        } else {
          box.append(
            el("p", { class: "pilot-card-title", text: `Pilot's plan (${data.labels.length} step${data.labels.length === 1 ? "" : "s"}):` }),
            el("ol", { class: "pilot-plan" }, ...data.labels.map((l) => el("li", { text: l }))),
            el("p", { class: "pilot-card-hint", text: "Nothing changes until you choose. You can stop at any point." }),
            el("div", { class: "pilot-card-actions" },
              el("button", { type: "button", class: "pilot-btn pilot-btn-go", text: "Approve all", on: { click: () => done("all") } }),
              el("button", { type: "button", class: "pilot-btn", text: "Step through", on: { click: () => done("step") } }),
              el("button", { type: "button", class: "pilot-btn", text: "Cancel", on: { click: () => done("cancel") } })));
          box.setAttribute("aria-label", "Pilot's plan");
        }
        cards.add(entry);
        cardBox.append(box);
        drawChips();
        scroller.scrollTop = scroller.scrollHeight;
        const first = box.querySelector(".pilot-btn-go");
        if (first) first.focus({ preventScroll: true });
      });
    }

    // ---------------------------------------------------------------- pictures and videos

    async function makeImage(prompt) {
      const key = store.media("image");
      if (!key) return { error: "No image key is set." };
      const item = { at: state.messages.length, kind: "image", prompt: String(prompt).slice(0, 300) };
      try {
        const pic = await M.image(key, prompt, { fetch: window.fetch.bind(window), compatibleHosts: hosts });
        if (pic.data && !/^[A-Za-z0-9+/=\s]+$/.test(pic.data.slice(0, 2000))) throw new Error("bad picture");
        Object.assign(item, pic.data ? { mime: /^image\/(png|jpeg|webp|gif)$/.test(pic.mime) ? pic.mime : "image/png", data: pic.data.replace(/\s+/g, "") } : pic);
        media.push(item);
        queueDraw();
        return "The picture was made and is shown to the user, with a download button.";
      } catch (e) {
        item.error = (e && e.kind && e.message) || "The picture couldn't be made.";
        media.push(item);
        queueDraw();
        return { error: item.error };
      }
    }

    async function makeVideo(prompt) {
      const key = store.media("video");
      if (!key) return { error: "No video key is set." };
      const opts = { fetch: window.fetch.bind(window), compatibleHosts: hosts };
      const item = { at: state.messages.length, kind: "video", prompt: String(prompt).slice(0, 300), progress: null };
      let job;
      try { job = await M.startVideo(key, prompt, opts); } catch (e) {
        item.error = (e && e.kind && e.message) || "The video couldn't be started.";
        media.push(item); queueDraw();
        return { error: item.error };
      }
      media.push(item);
      queueDraw();
      // Check on it every few seconds, for up to 20 minutes, on this page.
      (async () => {
        const until = Date.now() + 20 * 60 * 1000;
        while (Date.now() < until) {
          await new Promise((r) => setTimeout(r, 6000));
          let now;
          try { now = await M.checkVideo(key, job.id, opts); } catch (e) { item.error = (e && e.message) || "Lost track of the video."; break; }
          if (now.state === "working") { item.progress = now.progress; queueDraw(); continue; }
          if (now.state === "failed") { item.error = now.message; break; }
          try {
            const blob = await M.fetchVideo(key, job.id, now, opts);
            item.blobUrl = URL.createObjectURL(blob);
          } catch (e) { item.error = (e && e.message) || "The video was made but couldn't be fetched."; }
          break;
        }
        if (!item.blobUrl && !item.error) item.error = "The video is taking too long. Check it with your provider.";
        queueDraw();
      })();
      return "The video has started. The chat shows its progress and the video when it's ready.";
    }

    // ---------------------------------------------------------------- sending

    async function ask(text, extra) {
      const words = String(text || "").trim();
      if (!words) return;
      // "Remember that …" with memory on: kept in this browser, not sent.
      const remember = words.match(/^(?:please\s+)?remember(?:\s+that)?[:,]?\s+(.{3,})$/i);
      if (remember && store.memory().on && !extra) {
        if (input.value.trim() === words) { input.value = ""; grow(); }
        const r = store.remember(remember[1].replace(/[.!]+$/, ""));
        const next = state.messages.concat([{ role: "user", text: words },
          { role: "note", kind: r.error ? "error" : "info", text: r.error || "Saved to Pilot's memory in this browser. See it in settings." }]);
        engine.replace(next);
        state = { ...state, messages: engine.messages };
        pilot.post({ type: "chat", messages: state.messages });
        queueDraw();
        return;
      }
      if (!store.ready()) { if (!extra) hold({ text: words }); else show("settings"); return; }
      await engine.send(words, extra);
    }

    function shareScreen(prompt, label) {
      if (Date.now() - lastShared < 800) return;
      lastShared = Date.now();
      if (!store.ready()) { hold({ screen: [prompt, label] }); return; }
      const s = screen();
      const outline = (window.PilotPack && typeof window.PilotPack.screenText === "function" && window.PilotPack.screenText()) || screenText();
      ask(prompt, { shared: `Screen: ${s.name || document.title}\n${outline}`, label });
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (state.busy) return;
      const text = input.value;
      // No key yet: the words stay in the box (ask keeps them waiting).
      if (store.ready() || !text.trim()) {
        input.value = "";
        grow();
        if (view !== "chat") show("chat");
      }
      ask(text);
    });
    sendBtn.addEventListener("click", (e) => { if (state.busy) { e.preventDefault(); engine.stop(); } });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    function grow() { input.classList.toggle("pilot-tall", input.value.length > 48 || input.value.includes("\n")); }
    input.addEventListener("input", grow);

    // Voice: the browser's own speech recognition, only where it has one.
    // The words land in the box to check before sending. Nothing goes to the
    // app's server; some browsers send the sound to their own maker to turn
    // it into words (help says so).
    let listening = null;
    if (mic) {
      mic.addEventListener("click", () => {
        if (listening) { listening.stop(); return; }
        let rec;
        try { rec = new Speech(); } catch (_) { mic.hidden = true; return; }
        rec.lang = document.documentElement.lang || "en";
        rec.interimResults = true;
        rec.continuous = false;
        const before = input.value ? `${input.value.trim()} ` : "";
        rec.onresult = (event) => {
          let words = "";
          for (let i = 0; i < event.results.length; i += 1) words += event.results[i][0].transcript;
          input.value = (before + words).slice(0, 4000);
          grow();
        };
        rec.onend = () => { listening = null; mic.classList.remove("on"); input.focus(); };
        rec.onerror = () => { listening = null; mic.classList.remove("on"); };
        listening = rec;
        mic.classList.add("on");
        try { rec.start(); } catch (_) { listening = null; mic.classList.remove("on"); }
      });
    }

    // A link to this site in an app's own answer (core 1.4.0) opens in this
    // tab: Pilot closes first, so the page it goes to shows (on a phone the
    // window would cover it).
    log.addEventListener("click", (e) => {
      const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (a && !a.hasAttribute("target") && !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
        try { pilot.close(); } catch (_) { /* fine */ }
      }
    });
    gear.addEventListener("click", () => show(view === "settings" ? "chat" : "settings"));
    helpBtn.addEventListener("click", () => show(view === "help" ? "chat" : "help"));
    palette.addEventListener("click", () => {
      const next = store.ui().theme === "calm" ? "glow" : "calm";
      store.setUi({ theme: next });
      applyTheme(next);
    });
    minBtn.addEventListener("click", () => { pilot.close(); launcher.focus(); });
    closeBtn.addEventListener("click", () => { pilot.close(); launcher.focus(); });
    panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { pilot.close(); launcher.focus(); } });

    // ---------------------------------------------------------------- settings

    function show(which) {
      view = which;
      chatView.hidden = which !== "chat";
      settingsView.hidden = which !== "settings";
      helpView.hidden = which !== "help";
      gear.classList.toggle("on", which === "settings");
      helpBtn.classList.toggle("on", which === "help");
      drawFootNote();
      if (which === "settings") drawSettings();
      if (which === "help") drawHelp();
      scroller.scrollTop = which === "chat" && state.messages.length ? scroller.scrollHeight : 0;
      if (which === "chat") { draw(); input.focus({ preventScroll: true }); }
    }

    const backButton = () => el("button", { type: "button", class: "pilot-link pilot-back", on: { click: () => show("chat") } },
      icon("back"), el("span", { text: "Back to chat" }));

    // A form for one key: chat ("chat"), pictures ("image") or videos ("video").
    function keyForm(kind, existing, done) {
      const providers = kind === "chat" ? P.LIST : P.LIST.filter((p) => p.id !== "anthropic");
      const e = existing || {};
      let models = [];
      const provider = el("select", { class: "pilot-field", "aria-label": "Provider" },
        el("option", { value: "", text: "Choose a provider…" }),
        ...providers.map((p) => el("option", { value: p.id, text: p.id === "compatible" ? "Any compatible API" : p.label })));
      provider.value = e.provider || "";
      const baseUrl = el("input", { class: "pilot-field", type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false",
        placeholder: "https://…/v1 or http://localhost:…/v1", "aria-label": "Its base address" });
      baseUrl.value = e.baseUrl || "";
      const baseRow = el("label", { class: "pilot-row" }, el("span", { text: "Base URL (OpenAI-compatible)" }), baseUrl,
        el("small", { text: `This site's security settings let your browser reach: ${hosts.join(", ")}.` }));
      const key = el("input", { class: "pilot-field", type: "password", autocomplete: "off", spellcheck: "false",
        placeholder: words.paste, "aria-label": words.label.trim(), maxlength: "400" });
      key.value = e.key || "";
      const showKey = el("button", { type: "button", class: "pilot-link", text: "Show" });
      showKey.addEventListener("click", () => { key.type = key.type === "password" ? "text" : "password"; showKey.textContent = key.type === "password" ? "Show" : "Hide"; });
      const modelSelect = el("select", { class: "pilot-field", "aria-label": "Model", hidden: true });
      const modelText = el("input", { class: "pilot-field", type: "text", autocomplete: "off", spellcheck: "false",
        placeholder: "Or type a model name", "aria-label": "Model name", maxlength: "200" });
      modelText.value = e.model || "";
      const msg = el("p", { class: "pilot-msg-line", role: "status" });
      const say = (text, bad) => { msg.textContent = text; msg.classList.toggle("bad", Boolean(bad)); };
      const where = el("small", { class: "pilot-where" });
      function drawProvider() {
        const p = P.LIST.find((x) => x.id === provider.value);
        baseRow.hidden = provider.value !== "compatible";
        key.placeholder = p ? p.keyHint : words.paste;
        where.textContent = p && p.where ? `Get a key at ${p.where}. A key with a spending limit is safest.` : "";
      }
      provider.addEventListener("change", () => { models = []; modelSelect.hidden = true; drawProvider(); });
      drawProvider();
      modelSelect.addEventListener("change", () => { if (modelSelect.value) modelText.value = modelSelect.value; });
      const current = () => ({ provider: provider.value, key: key.value.trim(), baseUrl: baseUrl.value.trim(), model: modelText.value.trim() });

      const check = el("button", { type: "button", class: "pilot-btn", text: "Check key and list models" });
      check.addEventListener("click", async () => {
        const c = current();
        if (!c.provider) { say("Choose a provider first.", true); return; }
        say("Checking with the provider…");
        check.disabled = true;
        try {
          const opts = { fetch: window.fetch.bind(window), compatibleHosts: hosts };
          models = kind === "chat" ? await P.listModels(c, opts) : await M.listModels(kind, c, opts);
          put(modelSelect, el("option", { value: "", text: "Choose a model…" }),
            ...models.map((m) => el("option", { value: m.id, text: m.label && m.label !== m.id ? `${m.label} (${m.id})` : m.id })));
          modelSelect.hidden = models.length === 0;
          if (models.some((m) => m.id === c.model)) modelSelect.value = c.model;
          say(models.length ? `Key works. ${models.length} model${models.length === 1 ? "" : "s"} to choose from.`
            : "Key works, but no models were listed. Type a model name instead.");
        } catch (err) {
          models = [];
          modelSelect.hidden = true;
          say(`${(err && err.kind && err.message) || "Couldn't list the models."} You can still type a model name.`, true);
        } finally {
          check.disabled = false;
        }
      });

      const save = el("button", { type: "button", class: "pilot-btn pilot-btn-go", text: "Save" });
      save.addEventListener("click", () => {
        const c = current();
        if (!c.provider) { say("Choose a provider first.", true); return; }
        if (c.provider === "compatible") {
          const bad = P.checkBaseUrl(c.baseUrl, hosts);
          if (bad) { say(bad.message, true); return; }
        } else if (!c.key) { say(words.first, true); return; }
        if (!c.model) { say("Choose or type a model.", true); return; }
        const picked = models.find((m) => m.id === c.model);
        const same = e.provider === c.provider && e.model === c.model;
        done({ ...c, id: e.id, maxTokens: picked ? picked.maxTokens || 0 : (same ? e.maxTokens || 0 : 0),
          method: picked ? picked.method || "" : (same ? e.method || "" : ""), noTools: same ? Boolean(e.noTools) : false });
      });
      const cancel = el("button", { type: "button", class: "pilot-btn pilot-btn-quiet", text: "Cancel",
        on: { click: () => { editing = null; drawSettings(); } } });

      return el("div", { class: "pilot-keyform" },
        el("label", { class: "pilot-row" }, el("span", { text: "Provider" }), provider, where),
        baseRow,
        el("label", { class: "pilot-row" }, el("span", {}, el("span", { text: words.label }), showKey), key),
        el("div", { class: "pilot-row" }, check),
        el("label", { class: "pilot-row" }, el("span", { text: "Model" }), modelSelect, modelText),
        el("div", { class: "pilot-card-actions" }, save, cancel),
        msg);
    }

    function changed() {
      pilot.post({ type: "settings", settings: store.all() });
      pilot.changed();
      sendPending();
    }

    const label = (k) => `${k.provider === "compatible" ? P.providerName(k) : (P.NAMES[k.provider] || k.provider)} · ${k.model || "no model"}`;

    function drawSettings() {
      const a = store.all();
      const keyRows = a.keys.map((k) => el("li", { class: k.id === a.active ? "on" : "" },
        el("span", { class: "pilot-key-name" }, el("b", { text: label(k) }), el("small", { text: k.id === a.active ? "In use" : "" })),
        k.id === a.active ? null : el("button", { type: "button", class: "pilot-link", text: "Use",
          on: { click: () => { store.useKey(k.id); changed(); drawSettings(); } } }),
        el("button", { type: "button", class: "pilot-link", text: "Edit", on: { click: () => { editing = `chat:${k.id}`; drawSettings(); } } }),
        el("button", { type: "button", class: "pilot-link", text: "Remove", "aria-label": `Remove the ${label(k)} key`,
          on: { click: () => { store.removeKey(k.id); changed(); drawSettings(); } } })));

      const chatForm = editing && editing.startsWith("chat:")
        ? keyForm("chat", a.keys.find((k) => `chat:${k.id}` === editing) || null, (profile) => {
          if (profile.id) store.saveSettings(profile);
          else { const r = store.addKey(profile); if (r.error) return; }
          editing = null; changed(); drawSettings();
        }) : null;
      const addBtn = el("button", { type: "button", class: "pilot-btn", text: a.keys.length ? "+ Add another key" : "+ Add a key",
        on: { click: () => { editing = "chat:new"; drawSettings(); } } });

      const remember = el("input", { type: "checkbox" });
      remember.checked = a.remember;
      remember.addEventListener("change", () => { store.saveAll({ ...store.all(), remember: remember.checked }); changed(); });

      function mediaSection(kind, title, what) {
        const m = a[kind];
        const open = editing === kind;
        return el("details", { class: "pilot-section", open: open || Boolean(m) || undefined },
          el("summary", { text: title }),
          el("p", { class: "pilot-small", text: what }),
          m && !open ? el("ul", { class: "pilot-keys" }, el("li", { class: "on" },
            el("span", { class: "pilot-key-name" }, el("b", { text: label(m) })),
            el("button", { type: "button", class: "pilot-link", text: "Edit", on: { click: () => { editing = kind; drawSettings(); } } }),
            el("button", { type: "button", class: "pilot-link", text: "Remove",
              on: { click: () => { store.setMedia(kind, null); changed(); drawSettings(); } } }))) : null,
          open ? keyForm(kind, m, (profile) => { store.setMedia(kind, profile); editing = null; changed(); drawSettings(); })
            : (m ? null : el("button", { type: "button", class: "pilot-btn", text: `+ Add a ${kind} key`,
              on: { click: () => { editing = kind; drawSettings(); } } })));
      }

      // Memory: off by default; notes stay in this browser only.
      const mem = store.memory();
      const memOn = el("input", { type: "checkbox" });
      memOn.checked = mem.on;
      memOn.addEventListener("change", () => {
        if (!memOn.checked && mem.notes.length && !window.confirm("Turn memory off and forget everything Pilot remembers?")) {
          memOn.checked = true; return;
        }
        store.setMemoryOn(memOn.checked);
        drawSettings();
      });
      const noteInput = el("input", { class: "pilot-field", type: "text", maxlength: "200", autocomplete: "off",
        placeholder: "e.g. I prefer short answers", "aria-label": "Something for Pilot to remember" });
      const memMsg = el("p", { class: "pilot-msg-line", role: "status" });
      const addNote = el("button", { type: "button", class: "pilot-btn", text: "Add" });
      addNote.addEventListener("click", () => {
        const r = store.remember(noteInput.value);
        if (r.error) { memMsg.textContent = r.error; memMsg.classList.add("bad"); return; }
        drawSettings();
      });
      const notes = el("ul", { class: "pilot-notes" }, ...mem.notes.map((n, i) => el("li", {}, el("span", { text: n }),
        el("button", { type: "button", class: "pilot-link", text: "Delete", "aria-label": `Forget “${n}”`,
          on: { click: () => { store.forgetNote(i); drawSettings(); } } }))));

      // How lively the robot is: calm (it stays put), low, normal or playful
      // (flights and climbing too). "Reduce motion" keeps it still anyway.
      const energy = el("select", { class: "pilot-field", "aria-label": "How Pilot's robot moves" },
        ...[["calm", "Calm: stays put"], ["low", "Low: now and then"], ["normal", "Normal"], ["playful", "Playful: flies and climbs too"]]
          .map(([v, t]) => el("option", { value: v, text: t, selected: store.ui().energy === v || undefined })));
      energy.addEventListener("change", () => store.setUi({ energy: energy.value }));

      const forgetAll = el("button", { type: "button", class: "pilot-btn pilot-btn-quiet", text: "Remove my keys from this browser" });
      forgetAll.hidden = !a.keys.length && !a.image && !a.video;
      forgetAll.addEventListener("click", () => { store.forget(); editing = null; changed(); drawSettings(); });

      put(settingsView,
        backButton(),
        el("div", { class: "pilot-keycard pilot-keycard-head" }, icon("key"),
          el("span", { class: "pilot-keycard-words" }, el("b", { text: words.title }), el("small", { text: words.sub })), icon("chevron")),
        config.keyNote ? el("p", { class: "pilot-grownup", text: config.keyNote }) : null,
        pending ? el("p", { class: "pilot-waiting", role: "status",
          text: pending.text ? "Your message is waiting in the box. It sends as soon as you add a key." : "Pilot will look at this screen as soon as you add a key." }) : null,
        el("h3", { class: "pilot-h3", text: "Chat keys" }),
        a.keys.length ? el("ul", { class: "pilot-keys" }, ...keyRows) : el("p", { class: "pilot-small", text: "No key yet. Add one to start chatting." }),
        chatForm || addBtn,
        el("label", { class: "pilot-check" }, remember,
          el("span", { text: "Remember my keys on this device (otherwise they're forgotten when this tab closes)" })),
        hidden.has("image") ? null : mediaSection("image", "Image generation", "Lets Pilot make pictures in the chat, with your own image key."),
        hidden.has("video") ? null : mediaSection("video", "Video generation", "Lets Pilot make short videos, with your own video key. Videos can cost more: your provider bills each one."),
        el("h3", { class: "pilot-h3", text: "What Pilot remembers" }),
        el("label", { class: "pilot-check" }, memOn, el("span", { text: "Let Pilot remember notes about how you like answers (only in this browser)" })),
        mem.on ? notes : null,
        mem.on && !mem.notes.length ? el("p", { class: "pilot-small", text: "Nothing yet. Say “Remember that …” in the chat, or add one here." }) : null,
        mem.on ? el("div", { class: "pilot-inline" }, noteInput, addNote) : null,
        memMsg,
        el("h3", { class: "pilot-h3", text: "The robot" }),
        el("label", { class: "pilot-row" }, el("span", { text: "How it moves around the page" }), energy),
        el("p", { class: "pilot-small", text: "It never moves while this window is open, or if your device is set to reduce motion." }),
        forgetAll);
    }

    function drawHelp() {
      put(helpView,
        backButton(),
        el("h3", { class: "pilot-h3", text: "How Pilot works" }),
        el("ul", { class: "pilot-facts" },
          el("li", { text: "Pilot runs on your own AI key. Your key stays in this browser and goes only to the provider you chose, never to us." }),
          el("li", { text: "What you type goes to that provider. “Analyze & explain” and “Summarise” also send an outline of the words on this screen; you can see exactly what was sent. Typed boxes and hidden things are never read." }),
          el("li", { text: "Pilot asks before it changes anything. Several changes are shown as a plan first: approve all, step through, or cancel. Stop works at any time." }),
          el("li", { text: "Pilot never pays for anything, deletes anything, changes a plan or messages anyone." }),
          el("li", { text: Speech ? "The microphone uses your browser's own speech recognition. Nothing goes to us, but some browsers send the sound to their maker to turn it into words."
            : "Voice isn't offered in this browser because it has no speech recognition." }),
          el("li", { text: "Your AI provider bills your key for what Pilot uses. A key with a spending limit is safest." }),
          el("li", { text: "The chat follows you from page to page and tab to tab, and is forgotten when you close the tab (unless you choose to remember your keys)." })),
        config.keyNote ? el("p", { class: "pilot-grownup", text: config.keyNote }) : null,
        config.privacyUrl ? el("p", { class: "pilot-small" }, el("a", { href: config.privacyUrl, text: "Read the privacy page" })) : null);
    }

    // ---------------------------------------------------------------- the API for pilot.js

    const api = {
      VERSION,
      get isOpen() { return isOpen; },
      get busy() { return state.busy; },
      open() {
        isOpen = true;
        panel.hidden = false;
        root.classList.add("pilot-open");
        show(view);
        draw();
      },
      close() {
        isOpen = false;
        panel.hidden = true;
        root.classList.remove("pilot-open");
      },
      replace(list) {
        engine.replace(list);
        state = { ...state, messages: engine.messages };
        otherStream = "";
        queueDraw();
      },
      streamFromOtherTab(text) { otherStream = text; queueDraw(); },
      otherTabBusy(on) { otherBusy = on; if (!on) otherStream = ""; queueDraw(); },
      engine,
      screenText,
    };
    pilot.onChange(() => { if (view === "chat") queueDraw(); if (pending && store.ready()) setTimeout(sendPending, 0); });
    return api;
  }

  window.PilotPanel = { VERSION, create, screenText, redact, welcomePills, ownPills, keyPillGiven, keyWords, KEY_WORDS, hiddenSections,
    DEFAULT_PILLS, DEFAULT_TAGLINE, BUILTINS, siteLink, noAi, cleanChips, DEFAULT_MORE };
})();
