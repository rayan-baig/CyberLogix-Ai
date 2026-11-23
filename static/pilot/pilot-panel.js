// Pilot core 1.4.3 · pilot-panel.js
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
// Core 1.4.1: under such an answer the welcome's pills come back as a
// compact row (not under the AI's answers), so the next question is one tap
// away; before, they were gone until "Clear chat".
// Core 1.4.2: the "Summarise this" suggestion (it needs the AI) shows only
// once a key is set; an app can leave it out or put its own in its place
// (config.screenChip: screenChip below). And with no key, a typed question
// that clearly asks what one of the app's own answers (or links) is about
// gets that answer at once (matchTyped below), instead of key settings.
// Core 1.4.3: stop words ("does", "this", "is") are dropped before a word
// is stemmed (1.4.2 stemmed first, so "does" and "this" became "doe" and
// "thi" and counted); two items with the same answer, link or words are one
// item (no tie in matchTyped, shown once under an answer); and under a
// typed match the item it matched isn't offered again. With no key, a typed
// question that none of the app's own answers fits no longer jumps to key
// settings: Pilot says in the chat that it needs a key for that one and
// offers what it can do now (noKeyReply), the words waiting in the box.
//
// Every word on screen is placed with textContent or built by
// pilot-render.js from text nodes: nothing is ever set as HTML. No style
// attributes either; the look is all classes in pilot-panel.css.
(function () {
  "use strict";

  const VERSION = "1.4.3";
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
        if (label && !plain.bad) out.push({ icon: icon0, label, action: "", builtin: false, send: false, ...plain, ...matchWords(p.match) });
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
      out.push(plain ? { label: c.label, ...plain, ...matchWords(c.match) } : { label: c.label, prompt: typeof c.prompt === "string" ? c.prompt : "" });
    }
    return out;
  }

  // ---------------------------------------------------------------- a typed question, no key (core 1.4.2)
  // With no key, a typed question went straight to key settings, even when
  // one of the app's own answers plainly fits it ("Is my walk-in fridge
  // OK?" with a "Which fridges need me?" answer). matchTyped compares the
  // typed words with the words of each pill's and suggestion's label that
  // answers with no AI ({answer} or {link}), plus the app's own `match`
  // words on it (match: ["fridge", "warm", "ok"]). All in this browser:
  // nothing is sent, and the answer stays marked as the app's own (never
  // sent to the AI later). It is careful: question words and the like
  // ("what", "is", "my", "help") don't count; it takes an item only when
  // two of the typed words are its own and they are at least half of what
  // was typed (one word will do only when the app listed it in `match` and
  // it is all that was typed), and only when no other item comes as close.
  // Otherwise null, and the no-key flow is as before.
  // Core 1.4.3: stop words are checked before stemming (and after), so
  // "does" and "this" never count ("doe" and "thi" did in 1.4.2); a typed
  // question that has every word of a label also fits it when those words
  // are at least half of what was typed ("How much does it cost?" with
  // "What does it cost?"); and items that are the same (sameItem: one
  // answer, one link, or the same words) are one, never a tie.

  const STOP = new Set(("a an and any are am at be can could did do does for from get give got has have help how i if in "
    + "into is it its just know let like me my need of on or our please show should so some tell than that the their them "
    + "then there these they this those to up us want was we what whats when where which who why will with would you your").split(" "));
  // A word as typed, made plain: lower case, letters and digits only, a
  // "'s" taken off ("what's" is "what").
  const plainWord = (w) => String(w || "").toLowerCase().replace(/['’]s$/, "").replace(/[^a-z0-9]/g, "");
  // A word in its plain form: plainWord, a plural "s" (or "ies") taken off.
  function stem(w) {
    let x = plainWord(w);
    if (x.length > 4 && x.endsWith("ies")) x = `${x.slice(0, -3)}y`;
    else if (x.length > 3 && x.endsWith("s") && !x.endsWith("ss")) x = x.slice(0, -1);
    return x;
  }
  // The words that count in some text: [] for none. A stop word is dropped
  // as typed, before stemming (core 1.4.3), and once stemmed too.
  const wordsOf = (text) => [...new Set(String(text || "").split(/[^A-Za-z0-9'’]+/)
    .filter((w) => !STOP.has(plainWord(w))).map(stem).filter((w) => w.length > 1 && !STOP.has(w)))];
  // A label's words for telling items apart (core 1.4.3): lower case,
  // letters and digits, one space between.
  const labelKey = (label) => String(label || "").toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  // Are two items (pills or suggestions) the same one (core 1.4.3)? The
  // same link, the same answer (one function, or the same words), or the
  // same label once made plain. A page suggestion "What's overdue?" next to
  // a welcome pill "What's due or overdue?" that share one answer function
  // are one item: no tie, and shown once.
  function sameItem(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.link && b.link && a.link === b.link) return true;
    if (a.answer !== undefined && a.answer === b.answer) return true;
    const ka = labelKey(a.label);
    return Boolean(ka) && ka === labelKey(b.label);
  }
  // An item's own `match` words, cleaned: {match: [...]} or {} when none.
  function matchWords(given) {
    const list = (Array.isArray(given) ? given : []).filter((w) => typeof w === "string").slice(0, 20).map(stem).filter((w) => w.length > 1);
    return list.length ? { match: [...new Set(list)] } : {};
  }
  const MATCH_LEAD = "Here's what I can tell you without an AI key:";
  // The one item (of `items`: cleaned pills and suggestions) that clearly
  // fits the typed `text`, or null.
  function matchTyped(text, items) {
    // A pill's or a suggestion's own words, typed (core 1.4.3): always that
    // item, whatever its words are ("what is orbyt", "show my projects",
    // even a label of question words only), a live answer function or not.
    const typed = labelKey(text);
    if (typed) {
      const same = (Array.isArray(items) ? items : []).find((item) => item && (item.link || item.answer !== undefined)
        && typeof item.label === "string" && labelKey(item.label) === typed);
      if (same) return same;
    }
    const asked = wordsOf(text);
    if (!asked.length || asked.length > 12) return null;
    const scored = [];
    for (const item of Array.isArray(items) ? items : []) {
      if (!item || !(item.link || item.answer !== undefined) || typeof item.label !== "string") continue;
      const own = new Set(wordsOf(item.label));
      const listed = new Set(item.match || []);
      const hits = asked.filter((w) => own.has(w) || listed.has(w));
      const half = hits.length * 2 >= asked.length;
      const whole = hits.length >= 2 && half;
      // Every word of the label typed (core 1.4.3), and half of what was typed.
      const label = own.size > 0 && half && [...own].every((w) => asked.includes(w));
      const one = asked.length === 1 && listed.has(asked[0]);
      scored.push({ item, n: hits.length, fits: whole || label || one });
    }
    const best = scored.filter((s) => s.fits).sort((a, b) => b.n - a.n)[0];
    if (!best) return null;
    // Another item as close (by words) means it isn't clear which was meant,
    // unless it is the same item (core 1.4.3: sameItem).
    if (scored.some((s) => s !== best && s.n >= best.n && !sameItem(s.item, best.item))) return null;
    return best.item;
  }

  // The suggestion after this page's own (core 1.4.2), or null. By default
  // "Summarise this" (it sends an outline of the screen to the AI), only
  // once a key is set: before, it showed with no key and asked for one, the
  // one suggestion left that did on sites whose others all work without.
  // config.screenChip: false leaves it out; {label, answer} or {label, link}
  // puts the app's own in its place (no key needed, shown always); {label,
  // prompt} asks the AI that about the screen instead (with a key only).
  // ready: a key is set. base: this page's address.
  const SCREEN_CHIP = { label: "Summarise this", prompt: "Summarise what's on this screen in a few short points.", said: "Summarise this screen" };
  function screenChip(given, ready, base) {
    if (given === false) return null;
    if (given && typeof given === "object" && typeof given.label === "string" && given.label.trim()) {
      const label = given.label.replace(/\s+/g, " ").trim().slice(0, PILL_LABEL_MOST);
      const plain = noAi(given, base);
      if (plain) return plain.bad ? null : { label, ...plain };
      if (typeof given.prompt === "string" && given.prompt.trim()) {
        return ready ? { label, prompt: given.prompt.slice(0, PROMPT_MOST), said: label } : null;
      }
    }
    return ready ? { ...SCREEN_CHIP } : null;
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

  // Where the app's own answer is when it is the chat's newest message (the
  // "Want more?" line may follow it), or -1 (core 1.4.1).
  function localAnswerAt(messages) {
    const msgs = Array.isArray(messages) ? messages : [];
    let i = msgs.length - 1;
    if (i >= 0 && msgs[i] && msgs[i].role === "note" && msgs[i].kind === "more") i -= 1;
    const last = msgs[i];
    return last && last.role === "assistant" && last.local === true ? i : -1;
  }

  // The pills to show again under the chat's last message (core 1.4.1): the
  // welcome's, when that message is the app's own answer (a `local` one,
  // maybe followed by the "Want more?" line), less the pill just pressed and
  // the key pill; [] otherwise (an AI answer, a note, an empty chat).
  // keyPill: keep the key pill too (no key yet, and no "Want more?" line
  // right under this answer: it shows once per chat).
  // Core 1.4.3: after a typed question the answer remembers the label of the
  // item it matched (`of`), and that item is left out too.
  function againPills(messages, given, keyPill) {
    const msgs = Array.isArray(messages) ? messages : [];
    const i = localAnswerAt(msgs);
    if (i < 0) return [];
    const asked = i > 0 && msgs[i - 1] && msgs[i - 1].role === "user" ? msgs[i - 1].text : "";
    const of = typeof msgs[i].of === "string" ? msgs[i].of : "";
    return (given || []).filter((p) => !(p.builtin && p.action === "settings" && !keyPill) && p.label !== asked && !(of && p.label === of));
  }

  // The row under the app's own answer (core 1.4.2): this page's own
  // suggestions first (chips: cleaned, cleanChips), then the welcome's pills
  // (againPills), each once (by its words), the one just asked left out, at
  // most AGAIN_MOST, and the key pill (when it is kept) always last, beyond
  // that. A suggestion comes as {label, icon: "spark", chip}.
  // 1.4.1 showed only the welcome's pills.
  // Core 1.4.3: "each once" is by sameItem (one answer, one link, or the
  // same words made plain), not by the exact label only; and what was just
  // asked is left out with every item that is the same as it: the pill or
  // suggestion pressed, or (after a typed question) the one it matched,
  // which the answer remembers as `of`.
  const AGAIN_MOST = 6;

  // Short while typing (core 1.4.3): the visible height, in CSS pixels,
  // under which the window goes compact while its message box is typed in
  // (a phone's keyboard open at 320 px wide, or 375 x 667): the tagline, the
  // grown-ups line and the row of round buttons step aside, so more than a
  // line or two of the answer shows. view: the visual viewport's height
  // (innerHeight where there is none).
  const SHORT_MOST = 500;
  const isShort = (view) => Number(view) > 0 && Number(view) < SHORT_MOST;
  function againItems(messages, given, chips, keyPill) {
    const pills = againPills(messages, given, keyPill);
    const i = localAnswerAt(messages);
    if (i < 0) return [];
    const msgs = messages;
    const asked = i > 0 && msgs[i - 1] && msgs[i - 1].role === "user" ? String(msgs[i - 1].text) : "";
    const of = typeof msgs[i].of === "string" ? msgs[i].of : "";
    const isKey = (p) => p.builtin && p.action === "settings";
    const all = (Array.isArray(chips) ? chips : []).map((c) => ({ icon: "spark", ...c, chip: true }))
      .concat((Array.isArray(given) ? given : []).filter((p) => p && !isKey(p)));
    // What was just answered: its words, and every item with them.
    const done = [asked, of].map(labelKey).filter(Boolean);
    const gone = all.filter((x) => done.includes(labelKey(x.label)));
    const out = [];
    const take = (item) => {
      if (!labelKey(item.label) || out.length >= AGAIN_MOST) return;
      if (done.includes(labelKey(item.label)) || gone.some((g) => sameItem(g, item)) || out.some((o) => sameItem(o, item))) return;
      out.push(item);
    };
    for (const c of all.filter((x) => x.chip)) take(c);
    for (const p of pills) if (!isKey(p)) take(p);
    return out.concat(pills.filter(isKey).slice(0, 1));
  }

  // ---------------------------------------------------------------- a typed question, no key, no match (core 1.4.3)
  // Before, with no key, a typed question that none of the app's own
  // answers fits opened key settings at once (reviewers found it abrupt).
  // Now Pilot stays in the chat with a short message of its own (a note,
  // kind "nokey": never sent to an AI), and under it what it can do with no
  // key: this page's suggestions that answer or link, then the welcome's
  // pills that do, each once (sameItem), at most AGAIN_MOST, then the key
  // pill (the app's own, or one in the same words as its key settings). The
  // typed words stay in the box and are sent once a key is added, as
  // before. config.noKeyReply: false keeps the old jump to key settings; a
  // string replaces the message's words.
  const NO_KEY_REPLY = {
    site: "I need an AI key to answer that one. Here's what I can help with right now:",
    design: "I need an API key to answer that one. Here's what I can help with right now:",
  };
  const NO_KEY_ALONE = { site: "I need an AI key to answer that one.", design: "I need an API key to answer that one." };
  const NO_KEY_PILL = { site: "Add my AI key", design: "Add my API key" };
  // The row under it: [{label, icon, ...}], the key pill last.
  // given: the cleaned welcome pills (welcomePills); chips: this page's
  // cleaned suggestions; site: the app has its own welcome (the words).
  function noKeyItems(given, chips, site) {
    const isKey = (p) => p && p.builtin && p.action === "settings";
    const plainItem = (x) => x && (x.link || x.answer !== undefined);
    const out = [];
    const take = (item) => {
      if (out.length >= AGAIN_MOST || !labelKey(item.label) || out.some((o) => sameItem(o, item))) return;
      out.push(item);
    };
    for (const c of Array.isArray(chips) ? chips : []) if (plainItem(c)) take({ icon: "spark", ...c, chip: true });
    for (const p of Array.isArray(given) ? given : []) if (plainItem(p) && !isKey(p)) take(p);
    const own = (Array.isArray(given) ? given : []).find(isKey);
    const words = site ? "site" : "design";
    return out.concat([own || { icon: "key", label: NO_KEY_PILL[words], action: "settings", builtin: true, send: false }]);
  }
  // The message's words: the app's own (config.noKeyReply as a string), or
  // the core's, shorter when there is nothing but the key pill to offer.
  function noKeyText(given, site, offers) {
    if (typeof given === "string" && given.trim()) return given.replace(/\s+/g, " ").trim().slice(0, 300);
    const words = site ? "site" : "design";
    return offers ? NO_KEY_REPLY[words] : NO_KEY_ALONE[words];
  }

  // Where to scroll the chat to show a new answer of the app's own (core
  // 1.4.1): to the bottom if everything from its first line down fits (as
  // before), else its first line near the top (8 px under it), so a long
  // answer is read from its start. All in pixels of the chat's scroll box:
  // top (the answer's top within the content), height (the content's
  // height) and view (the box's own height).
  function answerScroll(top, height, view) {
    const bottom = Math.max(0, height - view);
    return height - top <= view ? bottom : Math.max(0, Math.min(bottom, top - 8));
  }

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
    let toppedAt = ""; // the app's own answer last opened at its first line (core 1.4.1)
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
    // Scrolled (core 1.4.2): what passes under the header fades out at the
    // top edge (pilot-panel.css) instead of showing half a line there (a cut
    // "Clear chat" underline).
    scroller.addEventListener("scroll", () => scroller.classList.toggle("pilot-scrolled", scroller.scrollTop > 1), { passive: true });

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
    // lead (core 1.4.2): a line put before the answer (a typed question
    // answered without a key: MATCH_LEAD). of (core 1.4.3): the label of the
    // item a typed question matched, kept on the answer so that item isn't
    // offered again right under it (againItems).
    async function answerWith(label, answer, lead, of) {
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
      const own = { role: "assistant", text: lead ? `${lead}\n\n${text}` : text, local: true };
      if (typeof of === "string" && of && of !== label) own.of = of.slice(0, 200);
      const add = text ? [{ role: "user", text: label }, own]
        : [{ role: "user", text: label }, { role: "note", kind: "error", text: "Pilot couldn't find that answer just now. Try again in a moment." }];
      // Once per chat (core 1.4.1): under the first answer of the app's own only.
      const offered = state.messages.some((m) => m.role === "note" && m.kind === "more");
      if (text && more && !store.ready() && !offered) add.push({ role: "note", kind: "more", text: more });
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
    // Core 1.4.1: with a short form, the note is always that one line with a
    // "More" link that opens the full note in place ("Less" closes it); in
    // 1.3.0 the full note (two or three lines above the box) came back as
    // soon as the chat started. An app with no short form keeps the full
    // note, as the design has it.
    let noteOpen = false;
    function drawFootNote() {
      const want = Boolean(config.keyNote) && !store.ready() && view === "chat";
      footNote.hidden = !want;
      if (!want) return;
      const short = typeof config.keyNoteShort === "string" && config.keyNoteShort.trim() ? config.keyNoteShort.trim() : "";
      const shows = !short ? "full" : noteOpen ? "open" : "short";
      if (footNote.getAttribute("data-shows") === shows) return; // drawn already (a rebuild would drop the link's focus)
      footNote.setAttribute("data-shows", shows);
      footNote.classList.toggle("pilot-foot-note-short", shows === "short");
      if (shows === "full") { footNote.textContent = config.keyNote; footNote.removeAttribute("title"); return; }
      const toggle = el("button", { type: "button", class: "pilot-link pilot-note-more", "aria-expanded": shows === "open" ? "true" : "false",
        text: shows === "open" ? "Less" : "More", on: { click: () => { noteOpen = !noteOpen; drawFootNote(); } } });
      put(footNote, el("span", { class: "pilot-foot-note-words", text: shows === "open" ? config.keyNote : short }), " ", toggle);
      if (shows === "short") footNote.setAttribute("title", config.keyNote); else footNote.removeAttribute("title");
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

    // A typed question none of the app's own answers fits, with no key
    // (core 1.4.3): Pilot answers in the chat that it needs a key for that
    // one, with what it can do now under it (againRow: noKeyItems). The
    // words wait in the box (pending), and go once a key is added. One such
    // message at a time: a second question replaces it.
    function noKeyReply(words) {
      pending = { text: words };
      if (input.value.trim() !== words.trim()) fill(words);
      const site = ownPills(config.welcomePills).length > 0;
      const offers = noKeyItems(pills, pageChips(), site).length > 1;
      const kept = state.messages.slice();
      if (kept.length && kept[kept.length - 1].role === "note" && kept[kept.length - 1].kind === "nokey") kept.pop();
      engine.replace(kept.concat([{ role: "note", kind: "nokey", text: noKeyText(config.noKeyReply, site, offers) }]));
      state = { ...state, messages: engine.messages };
      pilot.post({ type: "chat", messages: state.messages });
      if (view !== "chat") show("chat");
      queueDraw();
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
      // Pilot needs a key for a typed question (core 1.4.3): gone once a key
      // is added (the waiting words are sent then).
      if (m.role === "note" && m.kind === "nokey") {
        if (store.ready()) return null;
        return el("li", { class: "pilot-msg pilot-note pilot-nokey", role: "status", text: m.text });
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
      const answerAt = localAnswerAt(state.messages);
      let answerNode = null;
      let offered = false; // the "Want more?" line shows once per chat (core 1.4.1; older chats may hold several)
      state.messages.forEach((m, i) => {
        for (const item of media) if (item.at === i) nodes.push(mediaNode(item));
        const more = m.role === "note" && m.kind === "more";
        const n = more && offered ? null : messageNode(m);
        if (more) offered = true;
        if (i === answerAt) answerNode = n;
        nodes.push(n);
      });
      for (const item of media) if (item.at >= state.messages.length) nodes.push(mediaNode(item));
      nodes.push(againRow());
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
      // newest message is kept in view. A new answer of the app's own (core
      // 1.4.1) opens at its first line when it is too long to show whole
      // (1.4.0 opened it scrolled to its end); after that the chat is left
      // where the visitor scrolls it.
      if (view !== "chat") return;
      if (answerNode && !live && !cards.size) {
        const key = `${answerAt}:${state.messages[answerAt].text.length}`;
        if (isOpen && key !== toppedAt) {
          toppedAt = key;
          const top = answerNode.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
          scroller.scrollTop = answerScroll(top, scroller.scrollHeight, scroller.clientHeight);
        }
        return;
      }
      scroller.scrollTop = state.messages.length || cards.size ? scroller.scrollHeight : 0;
    }

    // The welcome's pills again, small and in one wrapping row (core 1.4.1),
    // under the app's own answer when it is the last thing in the chat (the
    // "Want more?" line may follow it). Not under the AI's answers, and not
    // while Pilot is busy. The pill just pressed is left out, and the key
    // pill while the "Want more?" line just above offers the key (or a key
    // is set). null when there's none.
    function againRow() {
      if (state.busy || otherBusy || cards.size) return null;
      const last = state.messages[state.messages.length - 1];
      // Core 1.4.3: under "I need an AI key to answer that one", what works
      // now, the key pill last, and a line saying the words are waiting.
      if (last && last.role === "note" && last.kind === "nokey") {
        if (store.ready()) return null;
        const offer = noKeyItems(pills, pageChips(), ownPills(config.welcomePills).length > 0);
        return el("li", { class: "pilot-msg pilot-again pilot-nokey-row" },
          el("div", { class: "pilot-pills pilot-pills-row", role: "group", "aria-label": "What Pilot can help with now" },
            ...offer.map((p) => { const b = pill(p.icon, p.label, () => (p.chip ? runChip(p) : runPill(p))); b.classList.add("pilot-pill-small"); return b; })),
          pending && pending.text ? el("p", { class: "pilot-small pilot-waiting-line", text: "Your message is waiting in the box. It sends as soon as you add a key." }) : null);
      }
      // Core 1.4.2: this page's own suggestions first, then the welcome's pills.
      const list = againItems(state.messages, pills, pageChips(), !store.ready() && !(last && last.role === "note" && last.kind === "more"));
      if (!list.length) return null;
      return el("li", { class: "pilot-msg pilot-again" },
        el("div", { class: "pilot-pills pilot-pills-row", role: "group", "aria-label": "More you can ask" },
          ...list.map((p) => { const b = pill(p.icon, p.label, () => (p.chip ? runChip(p) : runPill(p))); b.classList.add("pilot-pill-small"); return b; })));
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
      // "Summarise this", once a key is set, or the app's own (core 1.4.2).
      const own = screenChip(config.screenChip, store.ready(), location.href);
      if (own) all.push({ label: own.label, run: () => (own.prompt ? shareScreen(own.prompt, own.said) : runChip(own)) });
      put(chips, ...all.map((c) => el("button", { type: "button", class: "pilot-chip", text: c.label, on: { click: c.run } })),
        el("button", { type: "button", class: "pilot-link pilot-clear", text: "Clear chat", on: { click: clearChat } }));
    }

    function clearChat() {
      toppedAt = "";
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
      if (!store.ready()) {
        if (!extra && typedHere(words)) return;
        // Core 1.4.3: in the chat (noKeyReply) unless the app keeps the jump to settings.
        if (extra) show("settings");
        else if (config.noKeyReply === false) hold({ text: words });
        else noKeyReply(words);
        return;
      }
      await engine.send(words, extra);
    }

    // No key, and the typed words clearly ask what one of the app's own
    // answers or links is about (core 1.4.2, matchTyped): shown at once, as
    // the app's own answer, with a line saying so; a link as a link to tap.
    // config.matchTyped: false turns it off. True if it was answered.
    function typedHere(words) {
      if (config.matchTyped === false || answering || state.busy) return false;
      let items = [];
      try { items = ownPills(config.welcomePills, location.href).concat(pageChips()); } catch (_) { items = []; }
      const hit = matchTyped(words, items);
      if (!hit) return false;
      if (input.value.trim() === words) { input.value = ""; grow(); }
      const safe = String(hit.label).replace(/[[\]()\\*_`]/g, "");
      answerWith(words, hit.link ? `[${safe}](${hit.link})` : hit.answer, MATCH_LEAD, hit.label);
      return true;
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
    // Typing on a short screen (core 1.4.3): .pilot-typing on the window
    // while its box has focus, and .pilot-short while the visible height is
    // under SHORT_MOST (the keyboard takes the rest); pilot-panel.css then
    // hides the tagline, the grown-ups line and the round buttons.
    const viewHeight = () => (window.visualViewport && window.visualViewport.height) || window.innerHeight || 0;
    const compact = () => {
      panel.classList.toggle("pilot-typing", document.activeElement === input);
      panel.classList.toggle("pilot-short", isShort(viewHeight()));
    };
    input.addEventListener("focus", compact);
    input.addEventListener("blur", compact);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", compact);
    window.addEventListener("resize", compact);

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
        toppedAt = ""; // opened again: the newest answer of the app's own from its start
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
    DEFAULT_PILLS, DEFAULT_TAGLINE, BUILTINS, siteLink, noAi, cleanChips, DEFAULT_MORE, againPills, localAnswerAt, answerScroll,
    SCREEN_CHIP, screenChip, matchTyped, wordsOf, MATCH_LEAD, againItems, AGAIN_MOST, sameItem, labelKey,
    NO_KEY_REPLY, NO_KEY_ALONE, NO_KEY_PILL, noKeyItems, noKeyText, SHORT_MOST, isShort };
})();
