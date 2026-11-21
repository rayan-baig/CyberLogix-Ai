// Pilot core 1.2.0 · pilot-mascot.js
// The Pilot robot and the P mark.
//
// Also where the robot may sit (placement): it steps back while a page's
// own pop-up is open, keeps clear of things an app asks it to avoid (and of
// another helper character), and of the page's own words and buttons
// wherever it is scrolled to (contentIn), picking a free corner, or tucks
// half away at the edge until tapped (or, with keepInViewport, shrinks
// into the corner, fully on screen). A box being typed in always comes
// first: the robot never sits on it (hard blocks). A page's own pop-up
// counts even when it is not marked as one: anything fixed that covers the
// whole screen above the page (a backdrop) makes the robot step back too.
//
// The robot is the owner's own picture (pilot-robot-128.webp and
// pilot-robot-256.webp, transparent), shown as an <img> at the size the
// screen needs. pilot.css gives it life: a gentle float, a soft blue glow
// while Pilot is thinking, and nothing moving under "reduce motion".
// The P with its orbit, for the chat window's header, is drawn as SVG with
// attributes only (no style attributes), so it keeps a strict
// Content-Security-Policy. The desktop app reuses this file and the pictures.
(function (root) {
  "use strict";

  const VERSION = "1.2.0";
  const NS = "http://www.w3.org/2000/svg";
  let made = 0;

  function el(doc, name, attrs, kids) {
    const node = doc.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    for (const kid of kids || []) node.appendChild(kid);
    return node;
  }

  // The robot picture. urls: {small, large} (the 128 and 256 pixel files).
  function robot(doc, urls) {
    const d = doc || root.document;
    const u = urls || {};
    const img = d.createElement("img");
    img.className = "pilot-robot";
    img.setAttribute("alt", "");
    img.setAttribute("width", "128");
    img.setAttribute("height", "124");
    img.setAttribute("decoding", "async");
    img.setAttribute("draggable", "false");
    if (u.large) img.setAttribute("srcset", `${u.small} 128w, ${u.large} 256w`);
    img.setAttribute("sizes", "(max-width: 720px) 56px, 88px");
    img.setAttribute("src", u.small || "");
    return img;
  }

  // The "P" with an orbit swoosh around it, for the chat window's header.
  function mark(doc) {
    const d = doc || root.document;
    const n = (made += 1);
    const svg = el(d, "svg", { viewBox: "0 0 80 64", class: "pilot-mark", "aria-hidden": "true", focusable: "false" });
    svg.appendChild(el(d, "defs", {}, [
      el(d, "linearGradient", { id: `pm-p-${n}`, x1: 0, y1: 0, x2: 1, y2: 1 }, [
        el(d, "stop", { offset: "0", "stop-color": "#7cc4ff" }), el(d, "stop", { offset: "0.5", "stop-color": "#3b82f6" }),
        el(d, "stop", { offset: "1", "stop-color": "#4f46e5" })]),
      el(d, "linearGradient", { id: `pm-o-${n}`, x1: 0, y1: 0, x2: 1, y2: 0 }, [
        el(d, "stop", { offset: "0", "stop-color": "#38bdf8" }), el(d, "stop", { offset: "1", "stop-color": "#6366f1" })]),
    ]));
    // The back half of the orbit, behind the P.
    svg.appendChild(el(d, "path", { d: "M14 44 C 2 34, 30 18, 62 16", fill: "none", stroke: `url(#pm-o-${n})`, "stroke-width": 4,
      "stroke-linecap": "round", opacity: 0.75 }));
    // The P: a slanted stem and a round bowl.
    svg.appendChild(el(d, "path", { d: "M30 56 L37 12 H52 A12.5 12.5 0 0 1 52 37 H40", fill: "none", stroke: `url(#pm-p-${n})`,
      "stroke-width": 9, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    // The front half of the orbit, sweeping across the P.
    svg.appendChild(el(d, "path", { d: "M62 16 C 84 15, 74 32, 40 42 C 26 46, 16 47, 14 44", fill: "none", stroke: `url(#pm-o-${n})`,
      "stroke-width": 4, "stroke-linecap": "round" }));
    return svg;
  }

  // ---------------------------------------------------------------- placement

  // A page's own pop-ups: while one is showing, the robot steps back.
  const MODALS = 'dialog[open], [aria-modal="true"], [role="dialog"], [role="alertdialog"]';

  function shown(el, win) {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    if (el.closest && el.closest("[hidden]")) return false;
    const w = win || root;
    const st = w && w.getComputedStyle ? w.getComputedStyle(el) : null;
    return !st || (st.visibility !== "hidden" && st.display !== "none" && Number(st.opacity) !== 0);
  }

  // Is a pop-up of the page showing? own: Pilot's root (its window doesn't
  // count). extra: the app's own pop-up selectors (config.modals), for
  // pop-ups that aren't marked as dialogs (a CSS :target sheet, say).
  // Also true while something fixed covers the whole screen above the page
  // (coveredAt), so an unmarked pop-up's backdrop is never under the robot.
  function modalOpen(doc, own, win, extra) {
    const d = doc || root.document;
    const sels = [MODALS, ...(Array.isArray(extra) ? extra.filter((x) => typeof x === "string" && x) : [])];
    for (const sel of sels) {
      let found = [];
      try { found = d.querySelectorAll(sel); } catch (_) { continue; }
      for (const el of found) {
        if (own && own.contains(el)) continue;
        if (shown(el, win)) return true;
      }
    }
    return covered(d, own, win);
  }

  // A layer that covers the screen: fixed, at least 90% of the window each
  // way, not behind the page (a negative z-index), not see-through to taps
  // (elementsFromPoint skips pointer-events: none, so page scenery like a
  // starry sky never counts).
  function coverLayer(el, win) {
    const w = win || root;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const tag = String(n.tagName || "").toUpperCase();
      if (tag === "BODY" || tag === "HTML") return null;
      const st = w.getComputedStyle ? w.getComputedStyle(n) : null;
      if (!st || st.position !== "fixed") continue;
      if (parseInt(st.zIndex, 10) < 0) return null;
      const r = n.getBoundingClientRect();
      if (r.width >= w.innerWidth * 0.9 && r.height >= w.innerHeight * 0.9) return n;
    }
    return null;
  }

  // Is the screen covered at its middle and near the bottom corner by the
  // same fixed layer (a pop-up's backdrop)?
  function covered(doc, own, win) {
    const d = doc || root.document;
    const w = win || root;
    if (!d || typeof d.elementsFromPoint !== "function" || !w || !w.innerWidth) return false;
    const topAt = (x, y) => {
      let stack = [];
      try { stack = d.elementsFromPoint(x, y) || []; } catch (_) { return null; }
      return stack.find((el) => !(own && own.contains(el))) || null;
    };
    const a = topAt(w.innerWidth / 2, w.innerHeight / 2);
    const layer = a && coverLayer(a, w);
    if (!layer) return false;
    const b = topAt(w.innerWidth - 40, w.innerHeight - 40);
    return Boolean(b && (b === layer || layer.contains(b)));
  }

  const CORNERS = ["br", "bl", "tr", "tl"];
  // The order to try corners in: the chosen one, the other side of the same
  // edge, then the other edge.
  function cornerOrder(corner) {
    const c = CORNERS.includes(corner) ? corner : "br";
    const flipSide = `${c[0]}${c[1] === "r" ? "l" : "r"}`;
    const flipEdge = `${c[0] === "b" ? "t" : "b"}${c[1]}`;
    const both = `${flipEdge[0]}${flipSide[1]}`;
    return [c, flipSide, flipEdge, both];
  }

  // The robot's box in a corner. view: {width, height}; size: {width, height}.
  function cornerBox(corner, view, size, gap) {
    const g = gap === undefined ? 16 : gap;
    const left = corner[1] === "l" ? g : view.width - g - size.width;
    const top = corner[0] === "t" ? g : view.height - g - size.height;
    return { left, top, right: left + size.width, bottom: top + size.height };
  }

  const overlaps = (a, b, pad) => {
    const p = pad || 0;
    return a.left - p < b.right && a.right + p > b.left && a.top - p < b.bottom && a.bottom + p > b.top;
  };

  // The part of the robot still showing while it's tucked away in a corner:
  // a slice `show` pixels wide at the screen's edge (pilot.css moves it by
  // the same amount: --pilot-peek-show).
  // keep: {scale, margin} with keepInViewport. Then nothing hangs off the
  // edge: the robot shrinks to `scale` in its corner, `margin` from the
  // screen's edges, and this is the small robot's box.
  function peekBox(corner, view, size, gap, show, keep) {
    if (keep) {
      const s = Math.max(0.2, Math.min(1, Number(keep.scale) || 0.4));
      const m = Math.max(0, Number(keep.margin) || 0);
      const w = size.width * s;
      const h = size.height * s;
      const left = corner[1] === "l" ? m : view.width - m - w;
      const top = corner[0] === "t" ? m : view.height - m - h;
      return { left, top, right: left + w, bottom: top + h };
    }
    const box = cornerBox(corner, view, size, gap);
    const w = Math.max(1, Math.min(size.width, show || size.width / 2));
    return corner[1] === "l" ? { ...box, left: 0, right: w } : { ...box, left: view.width - w, right: view.width };
  }

  // A box grown by x and y pixels: the room the robot keeps from the page's
  // words (its hand reaches past its middle).
  const grow = (b, x, y) => ({ left: b.left - x, top: b.top - y, right: b.right + x, bottom: b.bottom + y });

  // Where to sit: {corner, peek}.
  //   blocks: boxes to keep clear of (things to avoid, another helper);
  //   busy(box, corner, small): true when the page's words or buttons are
  //     under that box (contentIn), asked only for the corners it needs
  //     (small: the tucked-away slice);
  //   hard: boxes never to cover, not even tucked away (the box being typed in).
  // The chosen corner if it's free, else the first free one. If none is
  // free it tucks half away (peek), in the first corner where the slice that
  // still shows covers nothing; failing that, the first corner clear of the
  // hard boxes; failing that, the chosen corner.
  function choosePlace(opts) {
    const o = opts || {};
    const blocks = (o.blocks || []).filter(Boolean);
    const hard = (o.hard || []).filter(Boolean);
    const busy = typeof o.busy === "function" ? o.busy : () => false;
    const order = cornerOrder(o.corner);
    const clear = (box, list, pad) => !list.some((b) => overlaps(box, b, pad));
    for (const c of order) {
      const box = cornerBox(c, o.view, o.size, o.gap);
      if (clear(box, hard, 8) && clear(box, blocks, 6) && !busy(box, c)) return { corner: c, peek: false };
    }
    for (const c of order) {
      const slice = peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep);
      if (clear(slice, hard, 8) && clear(slice, blocks, 2) && !busy(slice, c, true)) return { corner: c, peek: true };
    }
    if (hard.length) {
      for (const c of order) {
        if (clear(peekBox(c, o.view, o.size, o.gap, o.peekShow, o.keep), hard, 8)) return { corner: c, peek: true };
      }
    }
    return { corner: CORNERS.includes(o.corner) ? o.corner : "br", peek: true };
  }

  // ---------------------------------------------------------------- the page's own words and buttons

  // Things a visitor reads or presses. The robot keeps off them.
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), textarea, select, summary, label, [role="button"], '
    + '[role="link"], [role="checkbox"], [role="tab"], [role="switch"], [contenteditable=""], [contenteditable="true"]';
  // Small pictures count too (icons, logos); big ones are scenery.
  const MEDIA = /^(IMG|SVG|VIDEO|CANVAS|PICTURE)$/i;
  const ICON_MOST = 160;
  const MEANS = '[title]:not(html):not(body), [role="img"], [role="progressbar"], [role="meter"], [data-pilot-clear]';
  const NOT_TYPED = /^(button|submit|reset|checkbox|radio|range|color|file|image|hidden)$/i;

  // Is this a box someone types into: a text input, a textarea or an
  // editable element?
  function isEditable(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "TEXTAREA") return !el.disabled && !el.readOnly;
    if (tag === "INPUT") return !el.disabled && !el.readOnly && !NOT_TYPED.test(el.type || el.getAttribute("type") || "text");
    return Boolean(el.isContentEditable);
  }

  const toBox = (r) => ({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });

  // The part of one element (the top one at a point) that is something to
  // read or press inside `area`, or null.
  function contentBox(el, area, own, doc) {
    if (!el || el.nodeType !== 1) return null;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "HTML" || tag === "BODY") return null;
    const control = el.closest ? el.closest(INTERACTIVE) : null;
    if (control && !(own && own.contains(control))) {
      const r = control.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r);
    }
    if (MEDIA.test(tag)) {
      const r = el.getBoundingClientRect();
      if (r.width <= ICON_MOST && r.height <= ICON_MOST && overlaps(r, area, 0)) return toBox(r);
      return null;
    }
    // Small things that mean something without words (a row of streak
    // dots, a status light): anything with a tooltip or a picture role,
    // or marked data-pilot-clear.
    const marked = el.closest ? el.closest(MEANS) : null;
    if (marked && !(own && own.contains(marked))) {
      const r = marked.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.width <= ICON_MOST * 3 && r.height <= ICON_MOST && overlaps(r, area, 0)) return toBox(r);
    }
    // Words written straight into this element: only the lines that are
    // really there count, not the empty end of a short line.
    const range = doc && doc.createRange ? doc.createRange() : null;
    if (!range) return null;
    for (const node of el.childNodes || []) {
      if (node.nodeType !== 3 || !/\S/.test(node.nodeValue)) continue;
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width > 0 && r.height > 0 && overlaps(r, area, 0)) return toBox(r);
      }
    }
    return null;
  }

  // What the page shows to read or press under `area` (the robot's box in a
  // corner, in screen pixels): the boxes of those things, [] when it's free.
  // Looks at a grid of points (the top element at each one, never Pilot's
  // own), so it costs a handful of lookups, not a walk over the page.
  function contentIn(doc, area, own, grid) {
    const d = doc || root.document;
    if (!d || typeof d.elementsFromPoint !== "function" || !area) return [];
    const n = Math.max(2, grid || 4);
    const inset = { left: area.left + 3, top: area.top + 3, right: area.right - 3, bottom: area.bottom - 3 };
    const out = [];
    const seen = new Set();
    for (let i = 0; i < n; i += 1) {
      for (let j = 0; j < n; j += 1) {
        // From edge to edge, so a line just inside the box is found too.
        const x = inset.left + ((inset.right - inset.left) * i) / (n - 1);
        const y = inset.top + ((inset.bottom - inset.top) * j) / (n - 1);
        let stack = [];
        try { stack = d.elementsFromPoint(x, y) || []; } catch (_) { continue; }
        const top = stack.find((el) => !(own && own.contains(el)));
        if (!top || seen.has(top)) continue;
        seen.add(top);
        const hit = contentBox(top, inset, own, d);
        if (hit) out.push(hit);
      }
    }
    return out;
  }

  // The boxes of the visible elements matching any of the selectors.
  function boxesOf(doc, selectors, own, win) {
    const out = [];
    for (const sel of selectors || []) {
      let list = [];
      try { list = (doc || root.document).querySelectorAll(sel); } catch (_) { continue; }
      for (const el of list) {
        if (own && own.contains(el)) continue;
        if (!shown(el, win)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- where to sit now (pilot.js asks)

  // The box being typed in (an input, a textarea or an editable element of
  // the page, never Pilot's own), or null.
  function typingIn(doc, own) {
    const el = doc.activeElement;
    return el && el !== doc.body && !(own && own.contains(el)) && isEditable(el) ? el : null;
  }

  // Where the robot should sit now: {corner, peek, typing}. o: {doc, win,
  // own (Pilot's root), launcher, avoid (selectors), corner (the visitor's
  // own), shown (where it is now), full, text}. With `full` (not mid-scroll)
  // it may move to another corner; mid-scroll it only checks its own spot
  // and tucks away there or comes back out, so it never hops about. A box
  // being typed in is never covered (hard). `text`: keep off the page's
  // words and buttons (contentIn).
  // `keepIn` (keepInViewport): when tucked away it shrinks into its corner,
  // fully on screen (--pilot-peek-scale, --pilot-keep-margin), instead of
  // hanging off the edge. `reveal`: the first look of a visit, when the
  // robot shows whole for a few seconds wherever it can sit (never on a box
  // being typed in), so people notice it.
  function placeNow(o) {
    const blocks = boxesOf(o.doc, o.avoid, o.own, o.win);
    const field = typingIn(o.doc, o.own);
    // Its own size, not as drawn: tucked away it may be shrunk (keepIn).
    const r = o.launcher.getBoundingClientRect();
    const size = { width: o.launcher.offsetWidth || r.width, height: o.launcher.offsetHeight || r.height };
    const view = { width: o.win.innerWidth, height: o.win.innerHeight };
    const css = o.win.getComputedStyle(o.own);
    const num = (name, dflt) => { const v = parseFloat(css.getPropertyValue(name)); return Number.isFinite(v) ? v : dflt; };
    const gap = num("--pilot-gap", 16);
    const peekShow = num("--pilot-peek-show", size.width / 2);
    const keep = o.keepIn ? { scale: num("--pilot-peek-scale", 0.4), margin: num("--pilot-keep-margin", 4) } : null;
    // The room kept from the page's words: a little more across than up and
    // down, where the robot's hand reaches.
    const cx = num("--pilot-clear-x", 12);
    const cy = num("--pilot-clear-y", 4);
    // (The small tucked-away slice is checked as it is: it sits in the
    // page's side room.)
    const busy = o.text ? (box, c, small) => contentIn(o.doc, small ? box : grow(box, cx, cy), o.own).length > 0 : undefined;
    let place;
    if (o.full || field) {
      place = choosePlace({ corner: o.corner, view, size, gap, blocks, hard: field ? [field.getBoundingClientRect()] : [], busy, peekShow, keep });
    } else {
      const spot = cornerBox(o.shown, view, size, gap);
      place = { corner: o.shown, peek: blocks.some((b) => overlaps(spot, b, 6)) || Boolean(busy && busy(spot)) };
    }
    if (o.reveal && !field && place.peek) place = { ...place, peek: false };
    return { ...place, typing: Boolean(field) };
  }

  // The first look of a visit (pilot.js): how long to show the robot whole
  // before it tucks away, so people notice it: `ms` (4000 by default, 0 for
  // never, at most 10 s), but only once per visit, kept in this tab's
  // storage under `key`. Storage blocked: shown this once.
  function firstLook(tabStore, key, ms) {
    const n = ms === undefined || ms === null || !Number.isFinite(Number(ms)) ? 4000 : Math.max(0, Math.min(10000, Number(ms)));
    if (!n) return 0;
    try {
      if (tabStore && tabStore.getItem(key) === "1") return 0;
      if (tabStore) tabStore.setItem(key, "1");
    } catch (_) { /* blocked */ }
    return n;
  }

  // On phones, room at the side for the tucked-away robot: these elements
  // get .pilot-gutter-side (pilot.css: --pilot-side, 24 px), but only where
  // their own right padding is narrower. An app's own use of the class is
  // left alone.
  function sideGutter(doc, win, selectors) {
    const want = parseFloat(win.getComputedStyle(doc.documentElement).getPropertyValue("--pilot-side")) || 0;
    for (const sel of selectors || []) {
      let found = [];
      try { found = doc.querySelectorAll(sel); } catch (_) { continue; }
      for (const el of found) {
        const auto = el.hasAttribute("data-pilot-gutter-auto");
        if (el.classList.contains("pilot-gutter-side") && !auto) continue;
        el.classList.remove("pilot-gutter-side");
        el.removeAttribute("data-pilot-gutter-auto");
        if (want > 0 && (parseFloat(win.getComputedStyle(el).paddingRight) || 0) < want) {
          el.classList.add("pilot-gutter-side");
          el.setAttribute("data-pilot-gutter-auto", "");
        }
      }
    }
  }

  const api = { VERSION, robot, mark, typingIn, placeNow, sideGutter, MODALS, modalOpen, covered, coverLayer, cornerOrder, cornerBox, peekBox, overlaps,
    choosePlace, boxesOf, shown, grow, firstLook, INTERACTIVE, MEANS, isEditable, contentBox, contentIn };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMascot = api;
})(typeof window !== "undefined" ? window : globalThis);
