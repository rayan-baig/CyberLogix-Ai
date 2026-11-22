// Pilot core 1.4.2 · pilot.js
// The one Pilot file a page loads. After the page has loaded it fetches the
// store, the robot (pilot-mascot.js) and the app's config (which calls
// Pilot.mount); the chat window, engine, providers, renderer and knowledge
// pack come the first time Pilot is opened, and the moving robot when the
// page is idle (never under "reduce motion"). It keeps the robot out of the
// way (pilot-mascot.js placeNow) on scroll, resize, focus and page changes
// (1.4.1: .pilot-away when there's no room; 1.4.2: then a handle at the
// screen's edge once the page is still), keeps the chat from page to
// page (sessionStorage) and across tabs (BroadcastChannel; one tab answers
// at a time). One tag: <script id="pilot-core" data-…> (docs/PILOT.md).
(function () {
  "use strict";

  const VERSION = "1.4.2";
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
  let fitFull = false; // the next look may move to another corner (not just tuck in place)
  let scrolling = 0; // a timer while the page is being scrolled
  let peekOff = 0; // tapped out of its tucked-away spot until then
  let quiet = false; // tucked till a scroll (reveal)
  let scrolledDown = false; // the page is scrolled away from its top (core 1.4.1)
  let handle = null; // shown while it is hidden at rest (core 1.4.2)
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
  const avoid = () => [...list(config.avoid), ...list(config.yieldTo)];

  // Where the robot should be now (pilot-mascot.js placeNow, which says how).
  function fit() {
    fitQueued = false;
    const full = fitFull;
    fitFull = false;
    if (!rootEl || !config) return;
    const open = Boolean(panel && panel.isOpen) || rootEl.classList.contains("pilot-loading"); // (1.4.2: opening counts)
    const modal = M().modalOpen(document, rootEl, window, list(config.modals));
    rootEl.classList.toggle("pilot-yield", modal && !open);
    if (open) rootEl.classList.remove("pilot-away");
    if (open || modal || rootEl.classList.contains("pilot-dragging")) return;
    const place = M().placeNow({ doc: document, win: window, own: rootEl, launcher, avoid: avoid(),
      corner: store.ui().corner, shown: shownCorner, full, text: config.keepClearOfText !== false,
      keepIn: Boolean(config.keepInViewport), tuck: quiet, scrolled: scrolledDown, away: config.hideWhenCrowded !== false });
    if (place.corner !== shownCorner) placeAt(place.corner);
    rootEl.classList.toggle("pilot-away", Boolean(place.hide));
    // A tap brings it out for a while, but never onto a box being typed in.
    rootEl.classList.toggle("pilot-peek", place.peek && (Date.now() > peekOff || place.typing));
    rootEl.classList.toggle("pilot-typing", place.typing);
    if (handle) handle.update(Boolean(place.hide && !place.typing && !scrolling));
  }

  function queueFit(full) {
    if (full !== false) fitFull = true;
    if (fitQueued) return;
    fitQueued = true;
    requestAnimationFrame(fit);
  }

  function watchPage() {
    let resized = 0;
    window.addEventListener("resize", () => {
      queueFit(true);
      clearTimeout(resized);
      resized = setTimeout(gutter, 200);
    }, { passive: true });
    window.addEventListener("scroll", (e) => {
      const t = e && e.target; // scrolled away from the top (1.4.1; never Pilot's own chat)
      if (!(t && t.nodeType === 1 && rootEl.contains(t))) scrolledDown = M().scrolledAway(window, document, t);
      clearTimeout(scrolling);
      scrolling = setTimeout(() => { scrolling = 0; quiet = false; queueFit(true); }, 160);
      queueFit(false);
    }, { passive: true, capture: true });
    // A box gets focus (or loses it): out of its way at once, back after.
    document.addEventListener("focusin", () => queueFit(true));
    document.addEventListener("focusout", () => queueFit(true));
    window.addEventListener("hashchange", () => queueFit(true)); // a CSS :target pop-up
    if (typeof MutationObserver === "function") {
      new MutationObserver((records) => {
        if (records.some((r) => !rootEl.contains(r.target))) queueFit(true);
      }).observe(document.body, { childList: true, subtree: true, attributes: true,
        attributeFilter: ["open", "aria-modal", "hidden", "class", "role"] });
    }
    gutter();
    queueFit(true);
  }

  const gutter = () => M().sideGutter(document, window, config.gutter === false ? [] : list(config.gutter).length ? list(config.gutter) : ["main"]);

  // Busy: no wandering (a pop-up, tucked away, or something to avoid where it strolls).
  const held = () => Boolean(panel && panel.isOpen) || ["pilot-yield", "pilot-away", "pilot-peek", "pilot-typing"].some((c) => rootEl.classList.contains(c))
    || scrolling !== 0
    || shownCorner !== store.ui().corner || (avoid().length > 0 && M().inStroll(M().boxesOf(document, avoid(), rootEl, window), window, launcher));

  // Drag the robot to another corner, kept on this device.
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

  // A visit's first page: a glow for a few seconds, where placement puts
  // it; on a small screen (1.4.0) tucked away until the visitor scrolls.
  function reveal() {
    const ms = M().firstLook(storage("sessionStorage"), `${store.prefix}-hello`, config.revealMs);
    if (!ms) return;
    quiet = M().isCompact({ width: window.innerWidth, height: window.innerHeight });
    rootEl.classList.add("pilot-revealing");
    setTimeout(() => { rootEl.classList.remove("pilot-revealing"); queueFit(true); }, ms);
  }

  function build() {
    rootEl = document.createElement("div");
    rootEl.className = "pilot-root";
    if (config.themeClass) rootEl.classList.add(config.themeClass);
    if (config.keepInViewport) for (const n of [rootEl, document.documentElement]) n.classList.add("pilot-keep-in");
    placeAt(store.ui().corner);
    launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "pilot-launcher";
    launcher.setAttribute("aria-expanded", "false");
    launcher.appendChild(window.PilotMascot.robot(document, { small: assets.robot, large: assets.robot2x, set: assets.robotSet }));
    dot = document.createElement("span");
    dot.setAttribute("aria-hidden", "true");
    launcher.appendChild(dot);
    rootEl.appendChild(launcher);
    document.body.appendChild(rootEl);
    draggable();
    if (config.handle !== false) handle = M().restHandle({ doc: document, win: window, root: rootEl, launcher, open, avoid, corner: () => shownCorner });
    status();
    document.documentElement.classList.add("pilot-on"); // pages may reserve var(--pilot-gutter)
    reveal();
    scrolledDown = M().scrolledAway(window, document, null); // opened partway down
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
      life = window.PilotActor.live({ launcher, store, poses, dot, art: M().restArt(assets),
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
    rootEl.classList.remove("pilot-away");
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
        // A new tab of this site: share the chat, and a key kept for this visit.
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
