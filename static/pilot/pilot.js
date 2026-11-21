// Pilot core 1.1.0 · pilot.js
// The one Pilot file a page loads. Once the page has finished loading, it
// fetches the store (pilot-store.js), the robot (pilot-mascot.js and the
// owner's picture, pilot-robot-128/256.webp) and the app's config, which
// calls Pilot.mount. Everything else (the chat window, the engine, the
// providers, the renderer and the app's knowledge pack) is fetched only the
// first time the user opens Pilot, so pages stay fast.
//
// When the page is idle, and only if the visitor hasn't asked for less
// motion, it fetches the moving robot (pilot-motion.js, pilot-actor.js,
// pilot-actor.css and the robot's layers: data-motion, data-actor,
// data-actor-css, data-rest-*; the pose pictures in data-poses load when a
// move first needs them).
//
// It keeps the robot out of the way: it steps back (faded, click-through)
// while a pop-up of the page is open, and keeps clear of whatever the app's
// config lists in avoid (things it mustn't cover) and yieldTo (another
// helper character), moving to a free corner or tucking half away at the
// edge until tapped. Pages can reserve room with var(--pilot-gutter) or the
// .pilot-gutter class.
//
// It also keeps Pilot with the user:
// - from page to page: the chat and whether it's open are in sessionStorage
//   (pilot-store.js), and the window opens again on the next page;
// - across tabs of the same site: a BroadcastChannel shares the chat, a key
//   being added or removed, and which tab is answering. Only one tab sends a
//   request at a time (a Web Lock where the browser has them).
//
// An app uses it with one tag (docs/PILOT.md lists every data-* and the
// config fields):
//   <script src="pilot/pilot.js" defer id="pilot-core" data-store=… data-mascot=…
//           data-app="the app's config" data-pack="its knowledge pack" data-css=…
//           data-panel-css=… data-robot=… data-robot2x=… data-render=…
//           data-providers=… data-engine=… data-media=… data-panel=…></script>
(function () {
  "use strict";

  const VERSION = "1.1.0";
  const me = document.currentScript || document.getElementById("pilot-core");
  const assets = me ? { ...me.dataset } : {};
  const loaded = {};
  let config = null;
  let store = null;
  let rootEl = null;
  let launcher = null;
  let dot = null;
  let panel = null;
  let opening = null;
  let channel = null;
  let othersBusy = false;
  let life = null;
  let shownCorner = "br"; // where the robot sits now (the chosen corner, unless it's in the way)
  let fitQueued = false;
  let peekOff = 0; // tapped out of its tucked-away spot until then
  const listeners = new Set();
  const tab = Math.random().toString(36).slice(2, 10);

  function loadScript(url) {
    if (!url) return Promise.reject(new Error("missing"));
    if (!loaded[url]) {
      loaded[url] = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = url;
        s.async = false; // in the order asked for
        s.onload = () => resolve();
        s.onerror = () => { delete loaded[url]; reject(new Error("load")); };
        document.head.appendChild(s);
      });
    }
    return loaded[url];
  }

  function loadStyle(url) {
    if (!url) return Promise.resolve();
    if (!loaded[url]) {
      loaded[url] = new Promise((resolve) => {
        const l = document.createElement("link");
        l.rel = "stylesheet";
        l.href = url;
        l.onload = () => resolve();
        l.onerror = () => resolve();
        document.head.appendChild(l);
      });
    }
    return loaded[url];
  }

  const storage = (name) => { try { return window[name]; } catch (_) { return null; } };

  // ---------------------------------------------------------------- the robot

  function status() {
    if (!dot || !store) return;
    const thinking = (panel && panel.busy) || othersBusy;
    const state = thinking ? "thinking" : store.ready() ? "ready" : "nokey";
    dot.className = `pilot-dot pilot-dot-${state}`;
    rootEl.classList.toggle("pilot-thinking", thinking);
    const words = { thinking: "Pilot is thinking", ready: "Pilot is ready", nokey: "Pilot needs your AI key" }[state];
    dot.setAttribute("title", words);
    launcher.setAttribute("aria-label", `${panel && panel.isOpen ? "Close" : "Open"} Pilot, your AI helper. ${words}.`);
  }

  function placeAt(corner) {
    shownCorner = corner;
    for (const c of ["br", "bl", "tr", "tl"]) rootEl.classList.toggle(`pilot-at-${c}`, c === corner);
  }

  // ---------------------------------------------------------------- staying out of the way

  const M = () => window.PilotMascot;
  const list = (v) => (Array.isArray(v) ? v : typeof v === "string" && v ? [v] : []).filter((x) => typeof x === "string");

  // Where the robot should be now, given pop-ups and things to keep clear of.
  function fit() {
    fitQueued = false;
    if (!rootEl || !config) return;
    const open = Boolean(panel && panel.isOpen);
    const modal = M().modalOpen(document, rootEl, window);
    rootEl.classList.toggle("pilot-yield", modal && !open);
    if (open || modal || rootEl.classList.contains("pilot-dragging")) return;
    const blocks = M().boxesOf(document, [...list(config.avoid), ...list(config.yieldTo)], rootEl, window);
    const size = launcher.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(rootEl).getPropertyValue("--pilot-gap")) || 16;
    const place = M().choosePlace({ corner: store.ui().corner, view: { width: window.innerWidth, height: window.innerHeight },
      size: { width: size.width, height: size.height }, gap, blocks });
    if (place.corner !== shownCorner) placeAt(place.corner);
    rootEl.classList.toggle("pilot-peek", place.peek && Date.now() > peekOff);
  }

  function queueFit() {
    if (fitQueued) return;
    fitQueued = true;
    requestAnimationFrame(fit);
  }

  function watchPage() {
    window.addEventListener("resize", queueFit, { passive: true });
    window.addEventListener("scroll", queueFit, { passive: true, capture: true });
    if (typeof MutationObserver === "function") {
      new MutationObserver((records) => {
        if (records.some((r) => !rootEl.contains(r.target))) queueFit();
      }).observe(document.body, { childList: true, subtree: true, attributes: true,
        attributeFilter: ["open", "aria-modal", "hidden", "class", "role"] });
    }
    queueFit();
  }

  // Busy elsewhere: no wandering (a pop-up, tucked away, or keeping clear of something).
  const held = () => Boolean(panel && panel.isOpen) || ["pilot-yield", "pilot-peek"].some((c) => rootEl.classList.contains(c))
    || shownCorner !== store.ui().corner || (list(config.avoid).length + list(config.yieldTo).length > 0
      && M().boxesOf(document, [...list(config.avoid), ...list(config.yieldTo)], rootEl, window).length > 0);

  // Drag the robot to another corner: it follows the pointer from corner to
  // corner and stays where it's let go (kept on this device).
  function draggable() {
    let start = null;
    let moved = false;
    launcher.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY };
      moved = false;
      if (life) life.settle();
      try { launcher.setPointerCapture(e.pointerId); } catch (_) { /* fine */ }
    });
    launcher.addEventListener("pointermove", (e) => {
      if (!start) return;
      if (!moved && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 12) return;
      moved = true;
      rootEl.classList.add("pilot-dragging");
      const corner = `${e.clientY < window.innerHeight / 2 ? "t" : "b"}${e.clientX < window.innerWidth / 2 ? "l" : "r"}`;
      placeAt(corner);
    });
    const end = () => {
      if (!start) return;
      start = null;
      rootEl.classList.remove("pilot-dragging");
      if (moved) {
        const corner = ["br", "bl", "tr", "tl"].find((c) => rootEl.classList.contains(`pilot-at-${c}`)) || "br";
        store.setUi({ corner });
        queueFit();
      }
    };
    launcher.addEventListener("pointerup", end);
    launcher.addEventListener("pointercancel", end);
    launcher.addEventListener("click", (e) => {
      if (moved) { e.preventDefault(); moved = false; return; }
      // Tucked away: a tap brings it out (for a while) and opens it.
      if (rootEl.classList.contains("pilot-peek")) { peekOff = Date.now() + 20000; rootEl.classList.remove("pilot-peek"); }
      toggle();
    });
  }

  function build() {
    rootEl = document.createElement("div");
    rootEl.className = "pilot-root";
    if (config.themeClass) rootEl.classList.add(config.themeClass);
    placeAt(store.ui().corner);
    launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "pilot-launcher";
    launcher.setAttribute("aria-expanded", "false");
    launcher.appendChild(window.PilotMascot.robot(document, { small: assets.robot, large: assets.robot2x }));
    dot = document.createElement("span");
    dot.setAttribute("aria-hidden", "true");
    launcher.appendChild(dot);
    rootEl.appendChild(launcher);
    document.body.appendChild(rootEl);
    draggable();
    status();
    document.documentElement.classList.add("pilot-on"); // pages may reserve var(--pilot-gutter)
    watchPage();
    wake();
  }

  // The moving robot: fetched once the page is idle, never under "reduce motion".
  function wake() {
    const still = !window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still || !assets.motion || !assets.actor || !assets.restBody) return;
    const poses = {};
    for (const pair of String(assets.poses || "").split(" ")) {
      const i = pair.indexOf("=");
      if (i > 0) poses[pair.slice(0, i)] = pair.slice(i + 1);
    }
    const go = () => Promise.all([loadScript(assets.motion), loadScript(assets.actor), loadStyle(assets.actorCss)]).then(() => {
      life = window.PilotActor.live({ launcher, store, poses, dot,
        art: { body: assets.restBody, head: assets.restHead, eyes: assets.restEyes },
        corner: () => shownCorner, isOpen: held });
      if (life) life.hello();
    }).catch(() => { /* the robot just sits still */ });
    const soon = window.requestIdleCallback || ((fn) => setTimeout(fn, 1));
    setTimeout(() => soon(go, { timeout: 4000 }), 1500);
  }

  // ---------------------------------------------------------------- opening

  function loadPanel() {
    if (!opening) {
      opening = Promise.all([loadStyle(assets.panelCss), loadScript(assets.render), loadScript(assets.providers),
        loadScript(assets.engine), loadScript(assets.media), loadScript(assets.panel), (config.pack || assets.pack) ? loadScript(config.pack || assets.pack) : Promise.resolve()])
        .then(() => {
          panel = window.PilotPanel.create({ root: rootEl, launcher, store, config, pilot: api });
          return panel;
        })
        .catch((e) => { opening = null; throw e; });
    }
    return opening;
  }

  async function open() {
    if (life) life.settle();
    rootEl.classList.add("pilot-loading");
    try {
      await loadPanel();
      panel.open();
      launcher.setAttribute("aria-expanded", "true");
      store.setUi({ open: true });
    } catch (_) {
      rootEl.classList.add("pilot-failed");
      launcher.setAttribute("title", "Pilot couldn't load. Check your connection and reload the page.");
    } finally {
      rootEl.classList.remove("pilot-loading");
      status();
    }
  }

  function close() {
    if (panel) panel.close();
    queueFit();
    launcher.setAttribute("aria-expanded", "false");
    store.setUi({ open: false });
    status();
  }

  function toggle() {
    if (panel && panel.isOpen) close();
    else open();
  }

  // ---------------------------------------------------------------- other tabs

  function post(message) {
    if (channel) { try { channel.postMessage({ ...message, from: tab }); } catch (_) { /* closed */ } }
  }

  function listen() {
    if (typeof BroadcastChannel !== "function") return;
    try { channel = new BroadcastChannel(`${store.prefix}-sync`); } catch (_) { channel = null; return; }
    channel.onmessage = (event) => {
      const m = event.data || {};
      if (m.from === tab) return;
      if (m.type === "hello") {
        // A new tab of this site: share the chat, and a key kept for this
        // visit only, so Pilot carries on there too. Same site only.
        const all = store.all();
        post({ type: "state", to: m.from, messages: store.loadChat(), settings: all.remember || !all.keys.length ? null : all });
      } else if (m.type === "state" && m.to === tab) {
        if (!store.ready() && m.settings) store.saveAll(m.settings);
        if (!store.loadChat().length && Array.isArray(m.messages) && m.messages.length) {
          store.saveChat(m.messages);
          if (panel) panel.replace(store.loadChat());
        }
        changed();
      } else if (m.type === "chat" && Array.isArray(m.messages)) {
        store.saveChat(m.messages);
        if (panel) panel.replace(store.loadChat());
      } else if (m.type === "stream") {
        if (panel) panel.streamFromOtherTab(String(m.text || ""));
      } else if (m.type === "busy") {
        othersBusy = Boolean(m.on);
        if (panel) panel.otherTabBusy(othersBusy);
        status();
      } else if (m.type === "settings") {
        if (m.settings && (m.settings.keys || []).length + (m.settings.image ? 1 : 0) + (m.settings.video ? 1 : 0)) store.saveAll(m.settings);
        else store.forget();
        changed();
      }
    };
    post({ type: "hello" });
  }

  // Run `fn` only if no other tab is running one. Returns false if one is.
  async function exclusive(fn) {
    const busyOn = () => post({ type: "busy", on: true });
    const busyOff = () => post({ type: "busy", on: false });
    if (navigator.locks && typeof navigator.locks.request === "function") {
      return navigator.locks.request(`${store.prefix}-answering`, { ifAvailable: true }, async (lock) => {
        if (!lock) return false;
        busyOn();
        try { await fn(); } finally { busyOff(); }
        return true;
      });
    }
    if (othersBusy) return false;
    busyOn();
    try { await fn(); } finally { busyOff(); }
    return true;
  }

  function changed() {
    status();
    for (const fn of listeners) { try { fn(); } catch (_) { /* a listener's own problem */ } }
  }

  // ---------------------------------------------------------------- the API

  function mount(appConfig) {
    if (config) return false;
    const c = appConfig || {};
    // A child's view never shows Pilot at all (RankUp's kid views, for one).
    try { if (typeof c.kidView === "function" && c.kidView()) return false; } catch (_) { return false; }
    if (!window.PilotStore || !window.PilotMascot) return false;
    config = c;
    store = window.PilotStore.create({ prefix: c.storagePrefix || "pilot", local: storage("localStorage"), session: storage("sessionStorage") });
    listen();
    // The app's own code may be waiting for Pilot (Ask Genesis is).
    try { window.dispatchEvent(new Event("pilot-ready")); } catch (_) { /* very old browser */ }
    loadStyle(assets.css).then(() => {
      build();
      if (store.ui().open) open();
    });
    return true;
  }

  const api = {
    VERSION,
    mount,
    open: () => (config ? open() : null),
    close: () => (config ? close() : null),
    get store() { return store; },
    get config() { return config; },
    // The moving robot, once it's awake (null under "reduce motion").
    get robot() { return life; },
    // A key, a provider and a model are set: the app's own AI features may
    // use them (Ask Genesis does).
    ready: () => Boolean(store && store.ready()),
    providerLabel: () => {
      if (!store || !store.ready() || !window.PilotStore) return "";
      return { anthropic: "Anthropic", openai: "OpenAI", gemini: "Google Gemini", compatible: "your provider" }[store.settings().provider] || "";
    },
    onChange: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    changed: () => changed(),
    exclusive,
    post,
    status: () => status(),
    // One answer for an app feature, with the user's key, from this browser.
    async complete(req) {
      if (!store || !store.ready()) throw new Error("Pilot has no key");
      await Promise.all([loadScript(assets.providers), loadScript(assets.engine)]);
      const engine = window.PilotEngine.create({ store: { ...store, loadChat: () => [], saveChat: () => true },
        providers: window.PilotProviders, config: { compatibleHosts: config.compatibleHosts }, host: {} });
      return engine.complete(req);
    },
  };
  window.Pilot = api;

  // Start once the page itself has finished loading, so Pilot never slows
  // down what the visitor came for.
  function boot() {
    Promise.all([loadScript(assets.store), loadScript(assets.mascot)])
      .then(() => loadScript(assets.app))
      .catch(() => { /* no Pilot on this visit; the page works without it */ });
  }
  if (document.readyState === "complete") setTimeout(boot, 0);
  else window.addEventListener("load", () => setTimeout(boot, 0), { once: true });
})();
