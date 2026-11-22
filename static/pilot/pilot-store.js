// Pilot core 1.4.1 · pilot-store.js
// What Pilot keeps, and where: only in this browser, never on a server.
//
// - Keys (one or more chat keys, an image key, a video key, each with its
//   provider and model): in sessionStorage, so
//   they go when the tab closes, unless the user ticks "Remember on this
//   device"; then in localStorage. "Remove my key" deletes them from both.
// - The chat and whether the panel is open: in sessionStorage (localStorage
//   too only while "remember" is on), so Pilot follows the user from page to
//   page of the same site.
// - Memory ("remember that I like short answers"): off by default. Only while
//   the user has turned it on, in localStorage. Turning it off deletes it.
//
// No DOM here: the same file runs in a browser (window.PilotStore), in Node
// for the tests (module.exports), and later in the desktop app.
(function (root) {
  "use strict";

  const VERSION = "1.4.1";
  const LIMITS = { messages: 60, text: 20000, notes: 20, note: 200, key: 400, model: 200, baseUrl: 300, keys: 8 };
  const PROVIDERS = ["anthropic", "openai", "gemini", "compatible"];
  // Anthropic makes no pictures or videos, so it isn't offered for those.
  const MEDIA_PROVIDERS = ["openai", "gemini", "compatible"];

  // A storage that works, or null: a private window, blocked site data or a
  // full disk throws on every touch.
  function usable(storage, probe) {
    try {
      if (!storage) return null;
      storage.setItem(probe, "1");
      storage.removeItem(probe);
      return storage;
    } catch (_) {
      return null;
    }
  }

  function read(storage, key) {
    if (!storage) return null;
    try {
      const raw = storage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function write(storage, key, value) {
    if (!storage) return false;
    try { storage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; }
  }

  function remove(storage, key) {
    if (!storage) return;
    try { storage.removeItem(key); } catch (_) { /* nothing to do */ }
  }

  const text = (v, most) => (typeof v === "string" ? v.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, most) : "");

  // One key, made safe: {id, provider, key, model, baseUrl, maxTokens,
  // noTools, method}. A user may keep several chat keys and switch between
  // them, plus one key for making images and one for videos.
  function cleanProfile(raw, kinds) {
    const s = raw && typeof raw === "object" ? raw : {};
    const allowed = kinds || PROVIDERS;
    const provider = allowed.includes(s.provider) ? s.provider : "";
    if (!provider) return null;
    return {
      id: typeof s.id === "string" && /^[a-z0-9]{1,16}$/.test(s.id) ? s.id : Math.random().toString(36).slice(2, 10),
      provider,
      key: text(s.key, LIMITS.key),
      model: text(s.model, LIMITS.model),
      baseUrl: provider === "compatible" ? text(s.baseUrl, LIMITS.baseUrl).replace(/\/+$/, "") : "",
      // The most the chosen model may write, from the provider's own list.
      maxTokens: Number.isInteger(s.maxTokens) && s.maxTokens > 0 && s.maxTokens < 10000000 ? s.maxTokens : 0,
      // Set when the model said it can't use tools: Pilot then only chats.
      noTools: s.noTools === true,
      // For an image model: how the provider makes pictures with it.
      method: s.method === "predict" ? "predict" : "",
    };
  }

  // Everything Pilot keeps about keys, made safe.
  function cleanAll(raw) {
    const s = raw && typeof raw === "object" ? raw : {};
    const keys = [];
    for (const k of Array.isArray(s.keys) ? s.keys : []) {
      const p = cleanProfile(k);
      if (p && !keys.some((q) => q.id === p.id)) keys.push(p);
      if (keys.length >= LIMITS.keys) break;
    }
    const active = keys.some((k) => k.id === s.active) ? s.active : (keys[0] ? keys[0].id : "");
    return {
      remember: s.remember === true,
      keys, active,
      image: cleanProfile(s.image, MEDIA_PROVIDERS),
      video: cleanProfile(s.video, MEDIA_PROVIDERS),
    };
  }

  // The chat key in use, in the flat shape the engine and providers use.
  function flat(all) {
    const k = all.keys.find((x) => x.id === all.active) || null;
    return k ? { ...k, remember: all.remember } : { id: "", provider: "", key: "", model: "", baseUrl: "", maxTokens: 0,
      noTools: false, method: "", remember: all.remember };
  }

  // One chat message, made safe. Pilot's own shape (pilot-engine.js):
  // {role: "user" | "assistant" | "tool" | "note", text, ...}.
  function cleanMessage(m) {
    if (!m || typeof m !== "object") return null;
    if (!["user", "assistant", "tool", "note"].includes(m.role)) return null;
    const out = { role: m.role, text: typeof m.text === "string" ? m.text.slice(0, LIMITS.text) : "" };
    if (m.role === "user" && typeof m.shared === "string") out.shared = m.shared.slice(0, LIMITS.text);
    if (m.role === "user" && typeof m.label === "string") out.label = m.label.slice(0, 200);
    // "more" (core 1.4.0): the line after an app's own answer that offers
    // the key ("Want more? Add your AI key to ask anything.").
    if (m.role === "note") out.kind = m.kind === "error" || m.kind === "more" ? m.kind : "info";
    if (m.role === "assistant") {
      // An app's own answer (a pill's `answer`, core 1.4.0), not the AI's:
      // its links to this site work, and it never came from a provider.
      if (m.local === true) out.local = true;
      if (Array.isArray(m.toolCalls)) {
        out.toolCalls = m.toolCalls.filter((c) => c && typeof c.id === "string" && typeof c.name === "string")
          .map((c) => ({ id: c.id.slice(0, 200), name: c.name.slice(0, 64), args: c.args && typeof c.args === "object" ? c.args : {} }));
      }
      if (m.raw && typeof m.raw === "object" && typeof m.raw.provider === "string") out.raw = m.raw;
      if (typeof m.provider === "string") out.provider = m.provider;
      if (typeof m.model === "string") out.model = m.model.slice(0, LIMITS.model);
    }
    if (m.role === "tool" && Array.isArray(m.results)) {
      out.results = m.results.filter((r) => r && typeof r.id === "string")
        .map((r) => ({ id: r.id, name: typeof r.name === "string" ? r.name : "", content: String(r.content || "").slice(0, LIMITS.text),
          isError: r.isError === true, ...(typeof r.shown === "string" && r.shown ? { shown: r.shown.slice(0, 4000) } : {}) }));
    }
    return out;
  }

  // Keep the newest messages, but never start with an answer to a tool call
  // whose question was cut off: the provider would refuse the chat.
  function trim(messages) {
    let list = (Array.isArray(messages) ? messages : []).map(cleanMessage).filter(Boolean);
    if (list.length > LIMITS.messages) list = list.slice(list.length - LIMITS.messages);
    while (list.length && (list[0].role !== "user")) list.shift();
    return list;
  }

  function cleanNotes(raw) {
    const out = [];
    for (const n of Array.isArray(raw) ? raw : []) {
      const t = text(n, LIMITS.note);
      if (t && !out.includes(t)) out.push(t);
      if (out.length >= LIMITS.notes) break;
    }
    return out;
  }

  function create(options) {
    const opts = options || {};
    const prefix = typeof opts.prefix === "string" && /^[a-z0-9-]{1,40}$/.test(opts.prefix) ? opts.prefix : "pilot";
    const local = usable(opts.local, `${prefix}-probe`);
    const session = usable(opts.session, `${prefix}-probe`);
    const K = { settings: `${prefix}-settings`, chat: `${prefix}-chat`, memory: `${prefix}-memory`, ui: `${prefix}-ui` };
    let memorySettings = null; // when neither storage works: this page only
    let memoryChat = [];

    function all() {
      const kept = read(local, K.settings);
      if (kept) return cleanAll({ ...kept, remember: true });
      const now = read(session, K.settings);
      if (now) return cleanAll({ ...now, remember: false });
      return cleanAll(memorySettings);
    }

    function saveAll(next) {
      const s = cleanAll(next);
      memorySettings = s;
      const [to, from] = s.remember ? [local, session] : [session, local];
      remove(from, K.settings);
      // The chat follows the same choice.
      const chat = read(from, K.chat);
      if (chat) { write(to, K.chat, chat); remove(from, K.chat); }
      return write(to, K.settings, s);
    }

    function settings() { return flat(all()); }

    // Change the chat key in use (or add it when there is none yet).
    function saveSettings(next) {
      const a = all();
      const n = next || {};
      if (typeof n.remember === "boolean") a.remember = n.remember;
      const p = cleanProfile({ ...n, id: n.id || a.active || undefined });
      if (p) {
        const at = a.keys.findIndex((k) => k.id === p.id);
        if (at >= 0) a.keys[at] = p; else a.keys.push(p);
        a.active = p.id;
      }
      return saveAll(a);
    }

    function addKey(profile) {
      const a = all();
      if (a.keys.length >= LIMITS.keys) return { error: `Pilot keeps up to ${LIMITS.keys} keys. Remove one first.` };
      const p = cleanProfile({ ...profile, id: undefined });
      if (!p) return { error: "Choose a provider first." };
      a.keys.push(p);
      a.active = p.id;
      saveAll(a);
      return { key: p };
    }

    function useKey(id) {
      const a = all();
      if (!a.keys.some((k) => k.id === id)) return false;
      a.active = id;
      return saveAll(a);
    }

    function removeKey(id) {
      const a = all();
      a.keys = a.keys.filter((k) => k.id !== id);
      if (a.active === id) a.active = a.keys[0] ? a.keys[0].id : "";
      if (!a.keys.length && !a.image && !a.video) { forget(); return true; }
      return saveAll(a);
    }

    // The key for making images or videos (kind "image" or "video"), or null.
    function media(kind) {
      const p = all()[kind];
      return p && mediaReady(p) ? p : null;
    }

    function setMedia(kind, profile) {
      if (kind !== "image" && kind !== "video") return false;
      const a = all();
      a[kind] = profile ? cleanProfile(profile, MEDIA_PROVIDERS) : null;
      if (!a.keys.length && !a.image && !a.video) { forget(); return true; }
      return saveAll(a);
    }

    // "Remove my key": every key, provider and model goes, from both.
    function forget() {
      memorySettings = null;
      remove(local, K.settings);
      remove(session, K.settings);
      const chat = read(local, K.chat);
      if (chat) { write(session, K.chat, chat); remove(local, K.chat); }
    }

    function mediaReady(p) {
      if (!p || !p.provider || !p.model) return false;
      return p.provider === "compatible" ? Boolean(p.baseUrl) : Boolean(p.key);
    }

    // Ready to call a provider: a provider, a model, and a key (an
    // OpenAI-compatible address on the user's own computer may need none).
    function ready(s) {
      return mediaReady(s || settings());
    }

    function chatStore() { return settings().remember ? local : session; }

    function loadChat() {
      const kept = read(chatStore(), K.chat);
      return kept ? trim(kept.messages) : memoryChat.slice();
    }

    function saveChat(messages) {
      const list = trim(messages);
      memoryChat = list;
      return write(chatStore(), K.chat, { messages: list });
    }

    function clearChat() {
      memoryChat = [];
      remove(local, K.chat);
      remove(session, K.chat);
    }

    function memory() {
      const m = read(local, K.memory);
      return m && m.on === true ? { on: true, notes: cleanNotes(m.notes) } : { on: false, notes: [] };
    }

    function setMemoryOn(on) {
      if (on) return write(local, K.memory, { on: true, notes: memory().notes });
      remove(local, K.memory); // off means forgotten
      return true;
    }

    function remember(note) {
      const m = memory();
      if (!m.on) return { error: "Memory is off. Turn it on in Pilot settings first." };
      const t = text(note, LIMITS.note);
      if (!t) return { error: "There's nothing to remember." };
      if (m.notes.includes(t)) return { notes: m.notes };
      if (m.notes.length >= LIMITS.notes) return { error: `Pilot remembers up to ${LIMITS.notes} things. Delete one first.` };
      const notes = m.notes.concat([t]);
      write(local, K.memory, { on: true, notes });
      return { notes };
    }

    function forgetNote(index) {
      const m = memory();
      if (!m.on || !Number.isInteger(index) || index < 0 || index >= m.notes.length) return m.notes;
      const notes = m.notes.filter((_, i) => i !== index);
      write(local, K.memory, { on: true, notes });
      return notes;
    }

    // Whether the chat bubble is open follows the user from page to page in
    // this tab; the corner the robot was dragged to, and how lively it is
    // (calm, low, normal or playful), are kept on this device.
    const CORNERS = ["br", "bl", "tr", "tl"];
    const ENERGIES = ["calm", "low", "normal", "playful"];
    function ui() {
      const u = read(session, K.ui) || {};
      const place = read(local, `${prefix}-corner`);
      const theme = read(local, `${prefix}-theme`);
      const energy = read(local, `${prefix}-energy`);
      return { open: u.open === true, corner: CORNERS.includes(place) ? place : "br", theme: theme === "calm" ? "calm" : "glow",
        energy: ENERGIES.includes(energy) ? energy : "normal" };
    }

    function setUi(next) {
      const n = next || {};
      if (CORNERS.includes(n.corner)) write(local, `${prefix}-corner`, n.corner);
      if (n.theme === "calm" || n.theme === "glow") write(local, `${prefix}-theme`, n.theme);
      if (ENERGIES.includes(n.energy)) write(local, `${prefix}-energy`, n.energy);
      if ("open" in n) return write(session, K.ui, { open: Boolean(n.open) });
      return true;
    }

    return {
      VERSION, prefix, keys: K,
      get canKeep() { return Boolean(local); },
      all, saveAll, settings, saveSettings, addKey, useKey, removeKey, media, setMedia, forget, ready,
      loadChat, saveChat, clearChat,
      memory, setMemoryOn, remember, forgetNote,
      ui, setUi,
    };
  }

  const api = { VERSION, LIMITS, PROVIDERS, MEDIA_PROVIDERS, create, cleanProfile, cleanAll, cleanMessage, trim, cleanNotes };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotStore = api;
})(typeof window !== "undefined" ? window : globalThis);
